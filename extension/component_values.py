"""Whole-component value operations against existing reflected Blender forms."""
import json
import math
import struct
import bpy

from .form_to_object import get_data_from_active_editor
from .object_to_form import object_to_form
from .op_insert_component import touch_all_fields
from .property_groups import hash_over_64

SCHEMA = 'skein.component/v1'
MAX_BYTES = 256 * 1024
ERRORS = (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError, RuntimeError)
_MATH = {'glam::' + name for name in (
    'Vec2 Vec3 Vec3A Vec4 DVec2 DVec3 DVec4 Quat DQuat Mat2 Mat3 Mat3A Mat4 '
    'DMat2 DMat3 DMat4 Affine2 Affine3A DAffine2 DAffine3 '
    'I8Vec2 I8Vec3 I8Vec4 U8Vec2 U8Vec3 U8Vec4 I16Vec2 I16Vec3 I16Vec4 '
    'U16Vec2 U16Vec3 U16Vec4 IVec2 IVec3 IVec4 UVec2 UVec3 UVec4 '
    'I64Vec2 I64Vec3 I64Vec4 U64Vec2 U64Vec3 U64Vec4 BVec2 BVec3 BVec4').split()}


def canonical(value):
    return json.dumps(value, sort_keys=True, allow_nan=False, separators=(',', ':'))


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key: ' + key)
        result[key] = value
    return result


def _constant(value):
    raise ValueError('Nonfinite JSON number: ' + value)


def _loads(text):
    if len(text.encode('utf8')) > MAX_BYTES:
        raise ValueError('Value exceeds the 256 KiB limit')
    return json.loads(text, object_pairs_hook=_unique, parse_constant=_constant)


def _ref(field):
    return field['type']['$ref'].removeprefix('#/$defs/')


def _children(schema):
    kind = schema['kind']
    if kind == 'Struct':
        return list(schema.get('properties', {}).values())
    if kind == 'List':
        return [schema['items']]
    if kind in {'Tuple', 'TupleStruct'}:
        if len(schema['prefixItems']) != 1:
            raise ValueError('Multi-field tuples have no lossless editor')
        return schema['prefixItems']
    return []


def ensure_supported(registry, type_path):
    """Reject any reachable schema which the current form can silently discard."""
    visited = set()
    def visit(path, schema=None):
        if schema is None:
            if path in visited:
                return
            visited.add(path)
            schema = registry[path]
        if (path.endswith('::Entity') or '::Handle<' in path or '::AssetId<' in path
                or path == 'Entity'):
            raise ValueError(f'{path} needs reference remapping and cannot be copied')
        kind = schema['kind']
        if kind == 'Value':
            if schema.get('type') not in {'boolean', 'int', 'uint', 'float', 'string'}:
                raise ValueError(f'{path} has no lossless scalar editor')
        elif kind == 'Enum':
            if schema.get('type') == 'object':
                for variant in schema['oneOf']:
                    if 'kind' in variant:
                        visit(variant['typePath'], variant)
            elif schema.get('type') != 'string':
                raise ValueError(f'{path} has no supported enum editor')
        elif kind in {'Struct', 'List', 'Tuple', 'TupleStruct'}:
            for field in _children(schema):
                visit(_ref(field))
        else:
            raise ValueError(f'{path}: {kind} has no lossless editor')
    try:
        visit(type_path)
    except (KeyError, TypeError) as error:
        raise ValueError(f'Incomplete registry for {type_path}: {error}') from error


def validate_value(registry, type_path, value):
    """Validate JSON shape and scalar types without game-specific policy."""
    ensure_supported(registry, type_path)
    def validate(path, data, schema=None):
        schema = registry[path] if schema is None else schema
        kind = schema['kind']
        if path in _MATH:
            leaves = []
            def flatten(s):
                if s['kind'] == 'Struct':
                    for field in s.get('properties', {}).values():
                        flatten(registry[_ref(field)])
                else:
                    leaves.append(s)
            flatten(schema)
            if not isinstance(data, list) or len(data) != len(leaves):
                raise ValueError(f'{path} expects {len(leaves)} numbers')
            for item, leaf in zip(data, leaves):
                validate(leaf['typePath'], item, leaf)
        elif kind == 'Value':
            scalar = schema['type']
            valid = ((scalar == 'boolean' and type(data) is bool)
                     or (scalar == 'string' and type(data) is str)
                     or (scalar in {'int', 'uint'} and type(data) is int)
                     or (scalar == 'float' and type(data) in (int, float) and math.isfinite(data)))
            if not valid or (scalar == 'uint' and data < 0):
                raise ValueError(f'{path} expects {scalar}')
        elif kind == 'Struct':
            fields = schema.get('properties', {})
            if type(data) is not dict or set(data) != set(fields):
                raise ValueError(f'{path} fields differ from the loaded registry')
            for key, field in fields.items():
                validate(_ref(field), data[key])
        elif kind == 'List':
            if type(data) is not list:
                raise ValueError(f'{path} expects a list')
            for item in data:
                validate(_ref(schema['items']), item)
        elif kind in {'Tuple', 'TupleStruct'}:
            validate(_ref(schema['prefixItems'][0]), data)
        elif kind == 'Enum':
            if schema['type'] == 'string':
                if type(data) is not str or data not in schema['oneOf']:
                    raise ValueError(f'{path} has an unknown enum value')
                return
            variants = {item['shortPath']: item for item in schema['oneOf']}
            if schema.get('modulePath') == 'core::option' and 'Option<' in path:
                if data is not None:
                    validate(variants['Some']['typePath'], data, variants['Some'])
                return
            if type(data) is str and data in variants and 'kind' not in variants[data]:
                return
            if type(data) is not dict or len(data) != 1:
                raise ValueError(f'{path} expects one enum variant')
            key, payload = next(iter(data.items()))
            if key not in variants or 'kind' not in variants[key]:
                raise ValueError(f'{path} has an unknown payload variant')
            validate(variants[key]['typePath'], payload, variants[key])
    try:
        validate(type_path, value)
        canonical(value)
    except (KeyError, TypeError) as error:
        raise ValueError(f'Invalid {type_path}: {error}') from error


def read_component(component, registry):
    path = component.selected_type_path
    ensure_supported(registry, path)
    value = get_data_from_active_editor(component, hash_over_64(path))
    validate_value(registry, path, value)
    return value


def _owner_signature(owner):
    return canonical([(str(c.as_pointer()), c.name, c.selected_type_path) for c in owner.skein_two])


def target_token(component):
    if not hasattr(component.id_data, 'skein_two'):
        raise ValueError('This owner has no supported whole-component value editor')
    return dict(owner_pointer=str(component.id_data.as_pointer()),
                component_pointer=str(component.as_pointer()), type_path=component.selected_type_path,
                owner_signature=_owner_signature(component.id_data))


def _owners():
    for collection in ('objects', 'meshes', 'materials', 'scenes', 'lights', 'collections', 'cameras'):
        yield from getattr(bpy.data, collection)


def resolve_target(operator, writable=False):
    for owner in _owners():
        if str(owner.as_pointer()) != operator.owner_pointer:
            continue
        if _owner_signature(owner) != operator.owner_signature:
            raise ValueError('The component list changed; open its menu again')
        if writable and (not owner.is_editable or owner.library or owner.override_library):
            raise ValueError('This component owner is read-only')
        for index, component in enumerate(owner.skein_two):
            if (str(component.as_pointer()) == operator.component_pointer
                    and component.selected_type_path == operator.type_path):
                return owner, index, component
    raise ValueError('The clicked component changed or is unavailable; open its menu again')


def _registry(context):
    return json.loads(context.window_manager.skein.registry)


def _default(path):
    info = bpy.data.texts.get('skein-cache-info.json')
    if info is not None:
        reason = json.loads(info.as_string()).get('default_errors', {}).get(path)
        if reason:
            raise ValueError(reason)
    text = bpy.data.texts.get('skein-presets.json')
    if text is None:
        raise ValueError('Default unavailable: sync component presets first')
    presets = json.loads(text.as_string())
    if path not in presets or 'default' not in presets[path]:
        raise ValueError('Default unavailable for this component in the cached presets')
    return presets[path]['default']


def default_reason(path):
    try:
        _default(path)
    except ERRORS as error:
        return str(error)
    return ''


def _comparison_value(registry, path, value, schema=None):
    """Compare f32 by its stored IEEE value; retain exact JSON for every other type."""
    schema = registry[path] if schema is None else schema
    kind = schema['kind']
    if path == 'f32' and kind == 'Value' and schema.get('type') == 'float':
        return struct.unpack('!f', struct.pack('!f', value))[0]
    if kind == 'Value' and schema.get('type') == 'float':
        return float(value)
    if path in _MATH:
        leaves = []
        def flatten(node):
            if node['kind'] == 'Struct':
                for field in node.get('properties', {}).values():
                    flatten(registry[_ref(field)])
            else:
                leaves.append(node)
        flatten(schema)
        return [_comparison_value(registry, leaf['typePath'], item, leaf)
                for leaf, item in zip(leaves, value)]
    if kind == 'Struct':
        return {key: _comparison_value(registry, _ref(field), value[key])
                for key, field in schema.get('properties', {}).items()}
    if kind == 'List':
        return [_comparison_value(registry, _ref(schema['items']), item) for item in value]
    if kind in {'Tuple', 'TupleStruct'}:
        return _comparison_value(registry, _ref(schema['prefixItems'][0]), value)
    if kind == 'Enum' and schema.get('type') == 'object':
        variants = {item['shortPath']: item for item in schema['oneOf']}
        if schema.get('modulePath') == 'core::option' and 'Option<' in path:
            return (None if value is None else
                    _comparison_value(registry, variants['Some']['typePath'], value, variants['Some']))
        if isinstance(value, dict):
            key, payload = next(iter(value.items()))
            return {key: _comparison_value(registry, variants[key]['typePath'], payload, variants[key])}
    return value


def replace_component(owner, index, registry, value):
    """Stage a lossless form before replacing the existing component in place."""
    original = owner.skein_two[index]
    path, name = original.selected_type_path, original.name
    validate_value(registry, path, value)
    expected = canonical(_comparison_value(registry, path, value))
    active = owner.active_component_index
    staged_index = len(owner.skein_two)
    try:
        staged = owner.skein_two.add()
        staged.name, staged.selected_type_path = name, path
        key = hash_over_64(path)
        touch_all_fields(staged, key)
        object_to_form(staged, key, value)
        if canonical(_comparison_value(registry, path, read_component(staged, registry))) != expected:
            raise ValueError('The editor cannot preserve these field types or values exactly')
    except ERRORS:
        if len(owner.skein_two) > staged_index:
            owner.skein_two.remove(staged_index)
        owner.active_component_index = active
        raise
    owner.skein_two.remove(index)
    owner.skein_two.move(len(owner.skein_two) - 1, index)
    owner.active_component_index = active


class _Target:
    # Copied into each RNA operator: inherited deferred properties are not registered.
    @classmethod
    def poll(cls, context):
        return getattr(context, 'mode', 'OBJECT') == 'OBJECT'

    def execute(self, context):
        try:
            owner, index, component = resolve_target(self, self.operation != 'copy')
            registry = _registry(context)
            if self.operation == 'copy':
                text = canonical(dict(schema=SCHEMA, type_path=self.type_path,
                                      data=read_component(component, registry)))
                if len(text.encode('utf8')) > MAX_BYTES:
                    raise ValueError('Component exceeds the 256 KiB clipboard limit')
                context.window_manager.clipboard = text
            else:
                if self.operation == 'paste':
                    envelope = _loads(context.window_manager.clipboard)
                    if (type(envelope) is not dict or set(envelope) != {'schema', 'type_path', 'data'}
                            or envelope['schema'] != SCHEMA or envelope['type_path'] != self.type_path):
                        raise ValueError('Paste requires a Skein v1 component of this exact type')
                    value = envelope['data']
                else:
                    value = _default(self.type_path)
                replace_component(owner, index, registry, value)
        except ERRORS as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


def _properties():
    return {key: bpy.props.StringProperty(options={'HIDDEN'}) for key in
            ('owner_pointer', 'component_pointer', 'type_path', 'owner_signature')}


class CopyComponent(_Target, bpy.types.Operator):
    bl_idname = 'wm.skein_component_copy'
    bl_label = 'Copy Component Values'
    operation = 'copy'
    __annotations__ = _properties()


class PasteComponent(_Target, bpy.types.Operator):
    bl_idname = 'wm.skein_component_paste'
    bl_label = 'Paste Component Values'
    bl_options = {'REGISTER', 'UNDO'}
    operation = 'paste'
    __annotations__ = _properties()


class ResetComponent(_Target, bpy.types.Operator):
    bl_idname = 'wm.skein_component_reset'
    bl_label = 'Reset Component to Default'
    bl_options = {'REGISTER', 'UNDO'}
    operation = 'reset'
    __annotations__ = _properties()


class SKEIN_UL_components(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.context_pointer_set('skein_component', item)
        layout.label(text=item.name, icon='FILE_3D')


def draw_component_menu(self, context):
    ui_list = getattr(context, 'ui_list', None)
    if ui_list is None or ui_list.bl_idname != 'SKEIN_UL_components':
        return
    component = getattr(context, 'list_item', None) or getattr(context, 'skein_component', None)
    if component is None or not hasattr(component, 'selected_type_path'):
        return
    if not hasattr(component.id_data, 'skein_two'):
        self.layout.label(text='This owner has no supported whole-component value editor', icon='INFO')
        return
    token = target_token(component)
    try:
        ensure_supported(_registry(context), component.selected_type_path)
        reason = ''
    except ERRORS as error:
        reason = str(error)
    writable = (component.id_data.is_editable and component.id_data.library is None
                and component.id_data.override_library is None and context.mode == 'OBJECT')
    for action in ('copy', 'paste', 'reset'):
        row = self.layout.row()
        unavailable = reason or (default_reason(component.selected_type_path) if action == 'reset' else '')
        row.enabled = not unavailable and (action == 'copy' or writable)
        operator = row.operator('wm.skein_component_' + action)
        for key, value in token.items():
            setattr(operator, key, value)
        if unavailable:
            self.layout.label(text=unavailable, icon='INFO')


_classes = (CopyComponent, PasteComponent, ResetComponent, SKEIN_UL_components)

def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.UI_MT_list_item_context_menu.append(draw_component_menu)


def unregister():
    bpy.types.UI_MT_list_item_context_menu.remove(draw_component_menu)
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
