import inspect
import bpy # type: ignore
import json
from .property_groups import hash_over_64, make_property
# --------------------------------- #
#  Fetch and store the bevy type    #
#  registry, for panel display      #
# --------------------------------- #

class FetchRemoteTypeRegistry(bpy.types.Operator):
    """Synchronize registry, defaults and authoring data without blocking Blender"""
    bl_idname = "wm.fetch_type_registry"
    bl_label = "Sync Skein Authoring"

    def execute(self, context):
        from .authoring_sync import request_sync
        request_sync(context)
        return {'FINISHED'}


class ReloadSkeinRegistryJson(bpy.types.Operator):
    """Restore the cached authoring registry without a network request"""
    bl_idname = "wm.reload_skein_registry"
    bl_label = "Restore Cached Skein Registry"

    def execute(self, context):
        from .authoring_sync import restore_cache
        restore_cache(context)
        return {'FINISHED'}


def _references(value):
    if isinstance(value, dict):
        if '$ref' in value:
            reference = value['$ref']
            if not isinstance(reference, str) or not reference.startswith('#/$defs/') or not reference.removeprefix('#/$defs/'):
                raise ValueError('Registry references must be nonempty #/$defs/ strings')
            yield reference.removeprefix('#/$defs/')
        for child in value.values():
            yield from _references(child)
    elif isinstance(value, list):
        for child in value:
            yield from _references(child)


def validate_registry(registry):
    if not isinstance(registry, dict):
        raise ValueError('Registry schema must be an object')
    def field(value):
        if not isinstance(value, dict) or not isinstance(value.get('type'), dict) or '$ref' not in value['type']:
            raise ValueError('Registry fields must contain a reflected type reference')
        list(_references(value))
    def schema(value, name):
        if not isinstance(value, dict) or value.get('kind') not in {
            'Struct', 'Tuple', 'TupleStruct', 'Enum', 'List', 'Array', 'Map', 'Set', 'Value'}:
            raise ValueError(f'Invalid reflected schema kind: {name}')
        if value.get('typePath') != name or not isinstance(value.get('shortPath'), str):
            raise ValueError(f'Invalid reflected type path: {name}')
        if 'reflectTypes' in value and (not isinstance(value['reflectTypes'], list) or any(not isinstance(item, str) for item in value['reflectTypes'])):
            raise ValueError(f'Invalid reflectTypes: {name}')
        kind = value['kind']
        if kind == 'Struct':
            properties = value.get('properties', {})
            if not isinstance(properties, dict) or any(not isinstance(key, str) for key in properties):
                raise ValueError(f'Invalid struct properties: {name}')
            for item in properties.values():
                field(item)
            required = value.get('required', [])
            if not isinstance(required, list) or any(not isinstance(key, str) or key not in properties for key in required):
                raise ValueError(f'Invalid required fields: {name}')
        elif kind in ('Tuple', 'TupleStruct'):
            fields = value.get('prefixItems', [])
            if not isinstance(fields, list):
                raise ValueError(f'Invalid tuple prefixItems: {name}')
            for item in fields:
                field(item)
        elif kind in ('List', 'Array', 'Set'):
            field(value.get('items'))
        elif kind == 'Map':
            field(value.get('keyType'))
            field(value.get('valueType'))
        elif kind == 'Enum':
            variants = value.get('oneOf')
            if value.get('type') not in ('string', 'object') or not isinstance(variants, list):
                raise ValueError(f'Invalid enum variants: {name}')
            for variant in variants:
                if value['type'] == 'string':
                    if not isinstance(variant, str) or not variant:
                        raise ValueError(f'Invalid enum variant: {name}')
                elif not isinstance(variant, dict) or not isinstance(variant.get('shortPath'), str) or not isinstance(variant.get('typePath'), str):
                    raise ValueError(f'Invalid enum variant: {name}')
                elif 'kind' in variant:
                    schema(variant, variant['typePath'])
        elif kind == 'Value' and value.get('type') not in ('boolean', 'int', 'uint', 'float', 'string', 'object', 'array'):
            raise ValueError(f'Invalid scalar type: {name}')
    for name, value in registry.items():
        if not isinstance(name, str) or not name:
            raise ValueError('Registry type paths must be nonempty strings')
        schema(value, name)
        # Opaque engine schemas can refer to types omitted by registry.schema.
        # Used component closures and value operations validate their own references.
        list(_references(value))


def component_owners():
    for collection in ('objects', 'meshes', 'materials', 'scenes', 'cameras', 'lights', 'collections'):
        yield from getattr(bpy.data, collection)
    for armature in bpy.data.armatures:
        yield from armature.bones


def validate_registry_refresh(context, registry):
    """Reject changes to schemas which currently own authored component data."""
    validate_registry(registry)
    previous = json.loads(context.window_manager.skein.registry)
    pending = {component.selected_type_path for owner in component_owners()
               for component in getattr(owner, 'skein_two', ())}
    visited = set()
    def shape(value):
        if isinstance(value, dict):
            # Runtime metadata does not change the serialized value or its editor.
            # Apply this only to schema nodes, never to authored field names.
            is_schema = isinstance(value.get('kind'), str) and isinstance(value.get('typePath'), str)
            return {key: sorted(child) if is_schema and key in ('reflectTypes', 'required') else shape(child)
                    for key, child in value.items()
                    if not (is_schema and key in ('description', 'shortPath', 'componentInfo'))}
        if isinstance(value, list):
            return [shape(child) for child in value]
        return value
    def compatible(before, after):
        if shape(before) == shape(after):
            return True
        if before.get('kind') != 'Struct' or after.get('kind') != 'Struct':
            return False
        old_fields = before.get('properties', {})
        new_fields = after.get('properties', {})
        added = new_fields.keys() - old_fields.keys()
        if not added or not old_fields.keys() <= new_fields.keys():
            return False
        from .component_values import ensure_supported
        for name in added:
            path = next(_references(new_fields[name]), None)
            child = registry.get(path, {})
            if not (child.get('kind') == 'List' or
                    (child.get('kind') == 'Enum' and child.get('modulePath') == 'core::option'
                     and path.startswith('core::option::Option<'))):
                return False
            try:
                ensure_supported(registry, path)
            except ValueError:
                return False
        # Existing fields and requirements stay exact. Only empty List / None
        # additions are safe without inventing a project-specific default.
        reduced = dict(after, properties={key: new_fields[key] for key in old_fields})
        if 'required' in after:
            required = [key for key in after['required'] if key not in added]
            if required or 'required' in before:
                reduced['required'] = required
            else:
                reduced.pop('required')
        return shape(before) == shape(reduced)

    while pending:
        type_path = pending.pop()
        if type_path in visited:
            continue
        visited.add(type_path)
        if (type_path not in previous or type_path not in registry
                or not compatible(previous[type_path], registry[type_path])):
            raise ValueError(f'Cannot refresh used schema {type_path}; existing component data was kept. Migrate it with a compatible project version first.')
        pending.update(_references(previous[type_path]))


def process_registry(context, registry):
    """
    registry is a dict
    """

    debug = False
    if __package__ in context.preferences.addons:
        debug = context.preferences.addons[__package__].preferences.debug

    global_skein = context.window_manager.skein
    skein_property_groups = context.window_manager.skein_property_groups

    global_skein.registry = json.dumps(registry)

    component_list = []

    # Here's where we build up the PropertyGroup that
    # represents the hypothetical variants that account
    # for every possible component. These annotations
    # gain a field for every component type_path
    #
    # this approach is required because Blender's implementation
    # of Python and Properties *does not* include modern language
    # features like ADTs (aka: enums that can carry data).
    fake_component_enum_annotations = {
        "name": bpy.props.StringProperty(name="Name", default="Unknown"),
        "selected_type_path": bpy.props.StringProperty(name="Selected Type Path", default="Unknown"),
    }

    # clear the list we use as a component type selector for the UI
    # because we are about to re-fill it.
    global_skein.components.clear()
    # unregister all of the PropertyGroups that were created the
    # last time we processed a registry schema
    for type_path, property_group in skein_property_groups.items():
        try:
            bpy.utils.unregister_class(property_group)
        except:
            # unregister_class is recursive and we re-use classes
            # in many cases. So unregistering one class that uses 
            # another causes that class to *already* be unregistered
            # when we go to unregister it directly.
            pass

    # Clear the list that held the PropertyGroups because we are about
    # to re-fill it.
    skein_property_groups.clear()

    # for each user-defined type, make a PropertyGroup that represents
    # that type. These will be used to build out user-accessible forms
    # allowing users to edit their structured data
    for type_path, value in registry.items():
        # TODO: for debugging purposes, it can be useful to filter
        # the schema being processed without modifying the schema 
        # file. Only types that pass this test will be processed.
        # This should become an addon preference to allow users
        # to gain information should they want to
        #
        # if "avian3d" not in type_path:
        #     continue
        try:
            property_group_or_property = make_property(
                skein_property_groups,
                registry,
                type_path
            )
            if "reflectTypes" in value and "Component" in value["reflectTypes"]:
                component = global_skein.components.add()
                component.name = type_path
                component.value = type_path
                component.type_path = type_path
                component.short_path = value["shortPath"]

                component_list.append((type_path, value["shortPath"], type_path))

                # hash type_paths that are longer than 63 characters because they
                # will make the type class registration fail:
                # TypeError: 'bevy_render::camera::manual_texture_view::ManualTextureViewHandle' too long, max length is 63
                # 
                # We try to only do it for type_paths that exceed
                # the limit, because the hash shows up in error messages, reducing readability
                # and debuggability... or blender's python implementation could allows key lengths...
                maybe_hashed_type_path = hash_over_64(type_path)
                if inspect.get_annotations(property_group_or_property):
                    fake_component_enum_annotations[maybe_hashed_type_path] = bpy.props.PointerProperty(
                        type=property_group_or_property,
                        override={"LIBRARY_OVERRIDABLE"},
                    )
                else:
                    fake_component_enum_annotations[maybe_hashed_type_path] = property_group_or_property


        except Exception as e:
            if debug:
                print("failed to make_property for: ", type_path)
                print(repr(e))
    
    # Create the type we'll use as every component
    component_container = type("ComponentContainer", (bpy.types.PropertyGroup,), {
        '__annotations__': fake_component_enum_annotations,
    })

    # adding the container to the `skein_property_groups` list
    # means it will get unregistered alongside other data.
    # We don't rely on this list being "only component types"
    # so adding to it is fine
    skein_property_groups["skein_internal_container"] = component_container
    bpy.utils.register_class(component_container)

    # new component list data. Must be set to read component data from .blend file
    bpy.types.Object.skein_two = bpy.props.CollectionProperty(
        type=component_container,
        override={"LIBRARY_OVERRIDABLE"},
    )
    bpy.types.Mesh.skein_two = bpy.props.CollectionProperty(
        type=component_container,
        override={"LIBRARY_OVERRIDABLE"},
    )
    bpy.types.Material.skein_two = bpy.props.CollectionProperty(
        type=component_container,
        override={"LIBRARY_OVERRIDABLE"},
    )
    bpy.types.Scene.skein_two = bpy.props.CollectionProperty(
        type=component_container,
        override={"LIBRARY_OVERRIDABLE"},
    )
    bpy.types.Camera.skein_two = bpy.props.CollectionProperty(
        type=component_container,
        override={"LIBRARY_OVERRIDABLE"},
    )
    bpy.types.Light.skein_two = bpy.props.CollectionProperty(
        type=component_container,
        override={"LIBRARY_OVERRIDABLE"},
    )
    bpy.types.Collection.skein_two = bpy.props.CollectionProperty(
        type=component_container,
        override={"LIBRARY_OVERRIDABLE"},
    )
    bpy.types.Bone.skein_two = bpy.props.CollectionProperty(
        type=component_container,
        override={"LIBRARY_OVERRIDABLE"},
    )
