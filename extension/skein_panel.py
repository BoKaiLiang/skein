import bpy # type: ignore
import json
import inspect
import textwrap

from . import clip_picker
from .property_groups import hash_over_64
from .authoring import cached_description, field_widget
from . import authoring_sync, validation

# ---------------------------------- #
#  Skein Panel for adding components #
#  This shows in the Properties      #
#  tabs for the relevant objects     #
# ---------------------------------- #

class SkeinPanelObject(bpy.types.Panel):
    """Creates a Panel in the Object Properties Panel for an object"""
    bl_label = "Skein Bevy Panel"
    bl_idname = "OBJECT_PT_skein"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = 'object'

    @classmethod
    def poll(cls, context):
        return bpy.ops.object.insert_component.poll()

    def draw(self, context):
        obj = context.object
        draw_generic_panel(context, obj, self.layout, "object", "OBJECT_PT_skein_preset_panel")

class SkeinPanelMesh(bpy.types.Panel):
    """Creates a Panel in the Object Properties Panel for a mesh"""
    bl_label = "Skein Bevy Panel"
    bl_idname = "MESH_PT_skein"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = 'data'

    @classmethod
    def poll(cls, context):
        return bpy.ops.mesh.insert_component.poll()

    def draw(self, context):
        obj = context.mesh
        draw_generic_panel(context, obj, self.layout, "mesh", "MESH_PT_skein_preset_panel")

class SkeinPanelMaterial(bpy.types.Panel):
    """Creates a Panel in the Object Properties Panel for a material"""
    bl_label = "Skein Bevy Panel"
    bl_idname = "MATERIAL_PT_skein"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = 'material'

    @classmethod
    def poll(cls, context):
        return bpy.ops.material.insert_component.poll()

    def draw(self, context):
        obj = context.material
        draw_generic_panel(context, obj, self.layout, "material", "MATERIAL_PT_skein_preset_panel")

class SkeinPanelScene(bpy.types.Panel):
    """Creates a Panel in the Object Properties Panel for a scene"""
    bl_label = "Skein Bevy Panel"
    bl_idname = "SCENE_PT_skein"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = 'scene'

    @classmethod
    def poll(cls, context):
        return bpy.ops.scene.insert_component.poll()

    def draw(self, context):
        obj = context.scene
        draw_generic_panel(context, obj, self.layout, "scene", "SCENE_PT_skein_preset_panel")

class SkeinPanelLight(bpy.types.Panel):
    """Creates a Panel in the Object Properties Panel for a light"""
    bl_label = "Skein Bevy Panel"
    bl_idname = "LIGHT_PT_skein"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = 'data'

    @classmethod
    def poll(cls, context):
        return bpy.ops.light.insert_component.poll()

    def draw(self, context):
        obj = context.light
        draw_generic_panel(context, obj, self.layout, "light", "LIGHT_PT_skein_preset_panel")

class SkeinPanelCollection(bpy.types.Panel):
    """Creates a Panel in the Object Properties Panel for a collection"""
    bl_label = "Skein Bevy Panel"
    bl_idname = "COLLECTION_PT_skein"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = 'collection'

    @classmethod
    def poll(cls, context):
        return bpy.ops.collection.insert_component.poll()

    def draw(self, context):
        obj = context.collection
        draw_generic_panel(context, obj, self.layout, "collection", "COLLECTION_PT_skein_preset_panel")

class SkeinPanelBone(bpy.types.Panel):
    """Creates a Panel in the Object Properties Panel for a bone"""
    bl_label = "Skein Bevy Panel"
    bl_idname = "BONE_PT_skein"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = 'bone'

    @classmethod
    def poll(cls, context):
        return bpy.ops.bone.insert_component.poll()

    def draw(self, context):
        # we use context.bone because context.active_bone will return 
        # an EditBone *or* a Bone and we want a Bone
        obj = context.bone
        draw_generic_panel(context, obj, self.layout, "bone", "BONE_PT_skein_preset_panel")

def wrapped(layout, text, icon='INFO'):
    region = bpy.context.region
    scale = bpy.context.preferences.system.ui_scale
    width = max(16, min(55, int(region.width / (7.5 * scale)) - 4)) if region else 55
    for line in textwrap.wrap(text, width=width):
        layout.label(text=line, icon=icon)


def component_has_clip_fields(registry, type_path, description):
    """Check reachable reflected types, including nested lists and optional values."""
    editors = {item['type_path'] for item in description.get('fields', [])
               if item.get('widget') == 'animation_clip'}
    visited = set()
    def visit(path):
        if path in visited:
            return False
        visited.add(path)
        if path in editors:
            return True
        def references(node):
            if isinstance(node, dict):
                if '$ref' in node:
                    yield node['$ref'].removeprefix('#/$defs/')
                for child in node.values():
                    yield from references(child)
            elif isinstance(node, list):
                for child in node:
                    yield from references(child)
        return any(visit(child) for child in references(registry.get(path, {})))
    return visit(type_path)


def draw_diagnostics(layout, diagnostics):
    if not diagnostics:
        return
    for severity, title, icon in (('ERROR', 'Errors', 'ERROR'), ('WARNING', 'Warnings', 'ERROR'), ('INFO', 'Info', 'INFO')):
        messages = [item for item in diagnostics if item.get('severity', 'INFO') == severity]
        if not messages:
            continue
        box = layout.box()
        row = box.row()
        row.alert = severity == 'ERROR'
        row.label(text=f'{title} ({len(messages)})', icon=icon)
        row.alert = False
        for item in messages:
            row = box.row()
            row.alert = severity == 'ERROR'
            wrapped(row, validation.diagnostic_label(item), icon)
            row.alert = False
    header, body = layout.panel('skein_diagnostic_details', default_closed=True)
    header.label(text='Details')
    if body is not None:
        for item in diagnostics:
            wrapped(body, validation.format_diagnostic(item, details=True),
                    'ERROR' if item.get('severity') == 'ERROR' else 'INFO')


def draw_sync_status(layout, state):
    labels = {'ready': 'Ready', 'syncing': 'Syncing…', 'offline': 'Offline · cached data',
              'error': 'Sync failed · cached data', 'unverified': 'Cached data · checks unverified'}
    row = layout.row(align=True)
    row.alert = state['state'] == 'error'
    row.label(text=labels.get(state['state'], 'Component data unavailable'),
              icon='CHECKMARK' if state['state'] == 'ready' else 'ERROR' if state['state'] == 'error' else 'INFO')
    row.operator('wm.skein_sync_authoring', text='Sync', icon='FILE_REFRESH')
    header, body = layout.panel('skein_connection_details', default_closed=True)
    header.label(text='Details')
    if body is not None:
        wrapped(body, state['message'])
        if state.get('endpoint'):
            wrapped(body, 'Source: ' + state['endpoint'])
        if state.get('fetched_at'):
            wrapped(body, 'Cache updated: ' + state['fetched_at'])


def draw_generic_panel(context, obj, layout, execute_mode, skein_preset_panel_id):
    registry = json.loads(context.window_manager.skein.registry)
    draw_sync_status(layout, authoring_sync.status())
    if not registry:
        wrapped(layout, 'Connect your game and click Sync to load component types.')
        return
    layout.label(text='Add Component')
    box = layout.box()
    box.prop_search(context.window_manager, 'selected_component', context.window_manager.skein,
                    'components', text='Type', icon='BOIDS')
    box.operator(execute_mode + '.insert_component')
    layout.label(text='Components')
    layout.template_list('SKEIN_UL_components', 'components list', obj, 'skein_two', obj, 'active_component_index')
    if not obj.skein_two:
        return
    index = min(max(obj.active_component_index, 0), len(obj.skein_two) - 1)
    component = obj.skein_two[index]
    type_path = component.selected_type_path
    layout.label(text=registry.get(type_path, {}).get('shortPath', type_path.rsplit('::', 1)[-1]), icon='BOIDS')
    layout.operator(execute_mode + '.remove_component')
    row = layout.row()
    row.popover(panel=skein_preset_panel_id, icon='PRESET', text='Presets')
    header, body = layout.panel('skein_component_details', default_closed=True)
    header.label(text='Details')
    if body is not None:
        wrapped(body, type_path)
        wrapped(body, 'Right-click a component row to copy, paste or reset its values.')
    owner_types = [item.selected_type_path for item in obj.skein_two]
    diagnostics = [item for item in validation.get_owner_diagnostics(obj)
                   if not any(item.get('field', '') == other or item.get('field', '').startswith(other + '.')
                              for other in owner_types if other != type_path)]
    description = cached_description()
    if execute_mode == 'object' and component_has_clip_fields(registry, type_path, description):
        clip_picker.request_clip_refresh(context, obj)
        box = layout.box()
        row = box.row(align=True)
        row.label(text='Animation Clips', icon='ACTION')
        row.operator('wm.skein_refresh_clips', text='Refresh Clips', icon='FILE_REFRESH')
        header, body = layout.panel('skein_animation_advanced', default_closed=True)
        header.label(text='Advanced Export Settings')
        if body is not None:
            body.prop(context.scene.skein_extension_properties, 'clip_export_mode', text='Clip Source')
            body.prop(context.scene.skein_extension_properties, 'clip_export_animations', text='Export Animations')
            wrapped(body, 'Animation Tracks group actions into exported clips. Use the same animation settings for final export.')
        error = clip_picker.cached_clip_error(obj)
        if error:
            diagnostics.append(dict(severity='WARNING', field='Animation clips', message=error))
    draw_diagnostics(layout, diagnostics)
    groups = context.window_manager.skein_property_groups
    if type_path not in groups:
        draw_diagnostics(layout, [dict(severity='ERROR', field='', message='This component type is unavailable. Its saved values are retained.')])
        return
    if inspect.isclass(groups[type_path]):
        render_two(layout, component, hash_over_64(type_path), clip_object=obj if execute_mode == 'object' else None, component=component)
    else:
        layout.prop(component, hash_over_64(type_path))


def render_two(layout, context, context_key, clip_object=None, component=None):
    if context_key not in context:
        # A schema update may introduce an empty list on an existing component.
        value = getattr(context, context_key, None)
        if not (getattr(value, "_skein_is_list", False) or getattr(value, "is_core_option", False)):
            layout.label(text=context_key + " not in context")
            return
    
    # The current PropertyGroup we're working with
    obj = getattr(context, context_key)

    try:
        match obj.force_default:
            case "object":
                layout.label(text=context_key + " will be an empty object")
                return
            case "list":
                layout.label(text=context_key + " will be an empty array")
                return
    except AttributeError:
        pass

    # get the annotations, which will give us all of the field names
    # and their value types for this PropertyGroup
    annotations = getattr(obj, "__annotations__")

    if getattr(obj, "_skein_is_list", False):
        box = layout.box()
        row = box.row(align=True)
        row.label(text=f"{context_key} ({len(obj.entries)})")
        row.context_pointer_set("skein_list", obj)
        row.operator("wm.skein_list_add", text="", icon="ADD")
        for index, entry in enumerate(obj.entries):
            item = box.box()
            row = item.row(align=True)
            row.label(text=f"Item {index + 1}")
            row.context_pointer_set("skein_list", obj)
            for direction, icon in (('UP', 'TRIA_UP'), ('DOWN', 'TRIA_DOWN')):
                button = row.operator('wm.skein_list_move', text='', icon=icon)
                button.index, button.direction = index, direction
            row.operator("wm.skein_list_remove", text="", icon="REMOVE").index = index
            if entry.__annotations__["value"].function.__name__ == "PointerProperty":
                render_two(item, entry, "value", clip_object=clip_object, component=component)
            else:
                item.prop(entry, "value", text="Value")
        return

    # Handle core::option::Option specially, before other enums
    # because "None" and "Some" have special meaning: null and "just the value"
    try:
        if obj.is_core_option:
            layout.prop(obj, "skein_enum_index")
            match getattr(obj, "skein_enum_index"):
                case "None":
                    # layout.label(text="None")
                    pass
                case "Some":
                    layout.separator(type="LINE")
                    if "PointerProperty" == annotations["Some"].function.__name__:
                        render_two(layout, obj, "Some", clip_object=clip_object, component=component)
                    else:
                        layout.prop(obj, "Some")
            return
    except AttributeError as e:
        # Not all PropertyGroups have the is_core_option attribute, so
        # this is a common failure case that doesn't actually mean failure
        pass

    # If we have a `skein_enum_index`, then we have the representation
    # of a Rust Enum. The index holds the currently selected enum 
    # variant name as a string
    if "skein_enum_index" in annotations:
        layout.prop(obj, "skein_enum_index")
        layout.separator(type="LINE")
        variant = getattr(obj, "skein_enum_index")
        match variant:
            # If the enum variant name doesn't exist in the fields,
            # then we have a "unit variant" that has no data to edit
            case value if not hasattr(obj, value):#value not in obj and obj[value] is None:
                layout.label(text="Unit variants have no data to edit")
            # recurse down into the enum
            case value:
                if "PointerProperty" == annotations[value].function.__name__:
                    render_two(layout.box(), obj, value, clip_object=clip_object, component=component)
                else:
                    layout.prop(obj, value)
        return

    # attempt to handle any type overrides, like glam::Vec3
    # Currently all of the types here are *also* hardcoded 
    # because their serialization format differs from their
    # reflection data. That means this handling is unlikely 
    # to change. If it does, we need to ship a new version 
    # of the addon anyway.
    try:
        match obj.type_override:
            case "glam::Vec2" | "glam::DVec2" | "glam::I8Vec2" | "glam::U8Vec2" | "glam::I16Vec2" | "glam::U16Vec2" | "glam::IVec2" | "glam::UVec2" | "glam::I64Vec2" | "glam::U64Vec2" | "glam::BVec2":
                col = layout.column(align=True)
                col.label(text=context_key + ":")
                col.prop(obj, "x")
                col.prop(obj, "y")
                return
            case "glam::Vec3" | "glam::Vec3A" | "glam::DVec3" | "glam::I8Vec3" | "glam::U8Vec3" | "glam::I16Vec3" | "glam::U16Vec3" | "glam::IVec3" | "glam::UVec3" | "glam::I64Vec3" | "glam::U64Vec3" | "glam::BVec3":
                col = layout.column(align=True)
                col.label(text=context_key + ":")
                col.prop(obj, "x")
                col.prop(obj, "y")
                col.prop(obj, "z")
                return
            case "glam::Vec4" | "glam::DVec4" | "glam::I8Vec4" | "glam::U8Vec4" | "glam::I16Vec4" | "glam::U16Vec4" | "glam::IVec4" | "glam::UVec4" | "glam::I64Vec4" | "glam::U64Vec4" | "glam::BVec4":
                col = layout.column(align=True)
                col.label(text=context_key + ":")
                col.prop(obj, "x")
                col.prop(obj, "y")
                col.prop(obj, "z")
                col.prop(obj, "w")
                return
            case "glam::Quat" | "glam::DQuat":
                col = layout.column(align=True)
                col.label(text=context_key + ":")
                col.prop(obj, "x")
                col.prop(obj, "y")
                col.prop(obj, "z")
                col.prop(obj, "w")
                return
    #         case "glam::Mat2" | "glam::DMat2":
    #             x_axis = getattr(obj, "x_axis")
    #             y_axis = getattr(obj, "y_axis")
                
    #             return [
    #                 getattr(x_axis, "x"),
    #                 getattr(x_axis, "y"),

    #                 getattr(y_axis, "x"),
    #                 getattr(y_axis, "y"),
    #             ]

    #         case "glam::Mat3" | "glam::Mat3A" | "glam::DMat3":
    #             x_axis = getattr(obj, "x_axis")
    #             y_axis = getattr(obj, "y_axis")
    #             z_axis = getattr(obj, "z_axis")
                
    #             return [
    #                 getattr(x_axis, "x"),
    #                 getattr(x_axis, "y"),
    #                 getattr(x_axis, "z"),

    #                 getattr(y_axis, "x"),
    #                 getattr(y_axis, "y"),
    #                 getattr(y_axis, "z"),

    #                 getattr(z_axis, "x"),
    #                 getattr(z_axis, "y"),
    #                 getattr(z_axis, "z"),
    #             ]
    #         case "glam::Mat4" | "glam::DMat4":
    #             x_axis = getattr(obj, "x_axis")
    #             y_axis = getattr(obj, "y_axis")
    #             z_axis = getattr(obj, "z_axis")
    #             w_axis = getattr(obj, "w_axis")
                
    #             return [
    #                 getattr(x_axis, "x"),
    #                 getattr(x_axis, "y"),
    #                 getattr(x_axis, "z"),
    #                 getattr(x_axis, "w"),

    #                 getattr(y_axis, "x"),
    #                 getattr(y_axis, "y"),
    #                 getattr(y_axis, "z"),
    #                 getattr(y_axis, "w"),

    #                 getattr(z_axis, "x"),
    #                 getattr(z_axis, "y"),
    #                 getattr(z_axis, "z"),
    #                 getattr(z_axis, "w"),

    #                 getattr(w_axis, "x"),
    #                 getattr(w_axis, "y"),
    #                 getattr(w_axis, "z"),
    #                 getattr(w_axis, "w"),
    #             ]
  
    #         case "glam::Affine2" | "glam::DAffine2":
    #             mat = getattr(obj, "matrix2")
    #             x_axis = getattr(mat, "x_axis")
    #             y_axis = getattr(mat, "y_axis")
    #             translation = getattr(obj, "translation")
                
    #             return [
    #                 getattr(x_axis, "x"),
    #                 getattr(x_axis, "y"),
    #                 getattr(y_axis, "x"),
    #                 getattr(y_axis, "y"),
    #                 getattr(translation, "x"),
    #                 getattr(translation, "y"),
    #             ]
    #         case "glam::Affine3A" | "glam::DAffine3":
    #             mat = getattr(obj, "matrix3")
    #             x_axis = getattr(mat, "x_axis")
    #             y_axis = getattr(mat, "y_axis")
    #             z_axis = getattr(mat, "z_axis")
    #             translation = getattr(obj, "translation")
                
    #             return [
    #                 getattr(x_axis, "x"),
    #                 getattr(x_axis, "y"),
    #                 getattr(x_axis, "z"),
    #                 getattr(y_axis, "x"),
    #                 getattr(y_axis, "y"),
    #                 getattr(y_axis, "z"),
    #                 getattr(z_axis, "x"),
    #                 getattr(z_axis, "y"),
    #                 getattr(z_axis, "z"),
    #                 getattr(translation, "x"),
    #                 getattr(translation, "y"),
    #                 getattr(translation, "z"),
    #             ]
            
    except AttributeError:
        # Not all PropertyGroups have the type_override attribute, so
        # this is a common failure case that doesn't actually mean failure
        pass

    # No more special handling, just take the keys and values that are
    # in the annotations, and plug them into the object
    for key, value in annotations.items():
        if "PointerProperty" == value.function.__name__:
            try:
                next_type = getattr(obj, key)
                match next_type.type_override:
                    case "glam::Vec2" | "glam::DVec2" | "glam::I8Vec2" | "glam::U8Vec2" | "glam::I16Vec2" | "glam::U16Vec2" | "glam::IVec2" | "glam::UVec2" | "glam::I64Vec2" | "glam::U64Vec2" | "glam::BVec2":
                        render_two(layout, obj, key, clip_object=clip_object, component=component)
                    case "glam::Vec3" | "glam::Vec3A" | "glam::DVec3" | "glam::I8Vec3" | "glam::U8Vec3" | "glam::I16Vec3" | "glam::U16Vec3" | "glam::IVec3" | "glam::UVec3" | "glam::I64Vec3" | "glam::U64Vec3" | "glam::BVec3":
                        render_two(layout, obj, key, clip_object=clip_object, component=component)
                    case "glam::Vec4" | "glam::DVec4" | "glam::I8Vec4" | "glam::U8Vec4" | "glam::I16Vec4" | "glam::U16Vec4" | "glam::IVec4" | "glam::UVec4" | "glam::I64Vec4" | "glam::U64Vec4" | "glam::BVec4":
                        render_two(layout, obj, key, clip_object=clip_object, component=component)
                    case "glam::Quat" | "glam::DQuat":
                        render_two(layout, obj, key, clip_object=clip_object, component=component)
                    case _:
                        layout.label(text=key + ":")
                        render_two(layout.box(), obj, key, clip_object=clip_object, component=component)
            except AttributeError:
                layout.label(text=key + ":")
                render_two(layout.box(), obj, key, clip_object=clip_object, component=component)
        else:
            if clip_object is not None and field_widget(cached_description(), getattr(obj, '_skein_type_path', ''), key) == 'animation_clip' and value.function.__name__ == 'StringProperty':
                clip_picker.draw_clip_field(layout, obj, key, clip_object, type_path=getattr(obj, '_skein_type_path', ''))
            else:
                layout.prop(obj, key)
    return

classes = (
    SkeinPanelObject,
    SkeinPanelMesh,
    SkeinPanelMaterial,
    SkeinPanelScene,
    SkeinPanelLight,
    SkeinPanelCollection,
    SkeinPanelBone,
)

register, unregister = bpy.utils.register_classes_factory(classes)
