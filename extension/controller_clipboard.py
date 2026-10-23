"""Copy one ARForge controller through the system clipboard."""
import json
import bpy

from . import clip_picker
from .action_validation import CHARACTER_CONTROLLER, validate_character_controller
from .form_to_object import get_data_from_active_editor
from .object_to_form import object_to_form
from .op_insert_component import touch_all_fields
from .property_groups import hash_over_64

SCHEMA = 'arforge.character-controller/v1'
MAX_BYTES = 256 * 1024


def _canonical(data):
    # JSON distinguishes booleans, integers and floats that Python equality conflates.
    return json.dumps(data, sort_keys=True, allow_nan=False, separators=(',', ':'))


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'Duplicate JSON key: {key}')
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError(f'Nonfinite JSON number: {value}')


def _registry(context):
    registry = json.loads(context.window_manager.skein.registry)
    pending = [CHARACTER_CONTROLLER]
    visited = set()
    while pending:
        type_path = pending.pop()
        if type_path in visited:
            continue
        visited.add(type_path)
        if type_path not in registry:
            raise ValueError(f'Load a compatible registry; missing type {type_path}')
        def references(value):
            if isinstance(value, dict):
                if '$ref' in value:
                    pending.append(value['$ref'].removeprefix('#/$defs/'))
                for child in value.values():
                    references(child)
            elif isinstance(value, list):
                for child in value:
                    references(child)
        references(registry[type_path])
    if CHARACTER_CONTROLLER not in context.window_manager.skein_property_groups:
        raise ValueError('Reload the CharacterController registry before copying or pasting')
    return registry


def _controllers(obj):
    indices = [index for index, component in enumerate(obj.skein_two)
               if component.selected_type_path == CHARACTER_CONTROLLER]
    if len(indices) > 1:
        raise ValueError('Object has multiple CharacterControllers; remove the duplicate first')
    return indices


def _validate_actions(data):
    diagnostics = validate_character_controller(data)
    if diagnostics:
        first = diagnostics[0]
        raise ValueError(f'{first["field"]}: {first["message"]}')


class CopyControllerSettings(bpy.types.Operator):
    """Copy the entire CharacterController, including animations and actions"""
    bl_idname = 'object.skein_copy_controller'
    bl_label = 'Copy controller settings'

    @classmethod
    def poll(cls, context):
        return getattr(context, 'object', None) is not None and context.mode == 'OBJECT'

    def execute(self, context):
        try:
            _registry(context)
            indices = _controllers(context.object)
            if not indices:
                raise ValueError('This object has no CharacterController to copy')
            data = get_data_from_active_editor(context.object.skein_two[indices[0]], hash_over_64(CHARACTER_CONTROLLER))
            _validate_actions(data)
            text = _canonical(dict(schema=SCHEMA, type_path=CHARACTER_CONTROLLER, data=data))
            if len(text.encode('utf-8')) > MAX_BYTES:
                raise ValueError('Controller exceeds the 256 KiB clipboard limit')
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError) as error:
            self.report({'ERROR'}, f'Cannot copy controller: {error}')
            return {'CANCELLED'}
        context.window_manager.clipboard = text
        self.report({'INFO'}, 'Controller settings copied')
        return {'FINISHED'}


class PasteControllerSettings(bpy.types.Operator):
    """Replace or add one CharacterController without changing the model"""
    bl_idname = 'object.skein_paste_controller'
    bl_label = 'Paste controller settings'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = getattr(context, 'object', None)
        return (obj is not None and context.mode == 'OBJECT'
                and obj.is_editable and obj.library is None and obj.override_library is None)

    def execute(self, context):
        obj = context.object
        staged_index = None
        try:
            registry = _registry(context)
            indices = _controllers(obj)
            text = context.window_manager.clipboard
            if len(text.encode('utf-8')) > MAX_BYTES:
                raise ValueError('Clipboard exceeds the 256 KiB limit')
            envelope = json.loads(text, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
            if (not isinstance(envelope, dict) or set(envelope) != {'schema', 'type_path', 'data'}
                    or envelope['schema'] != SCHEMA or envelope['type_path'] != CHARACTER_CONTROLLER
                    or not isinstance(envelope['data'], dict)):
                raise ValueError('Clipboard must contain an ARForge CharacterController v1 envelope')
            data = envelope['data']
            expected = _canonical(data)
            _validate_actions(data)
            old_index = indices[0] if indices else None
            old_active = obj.active_component_index
            name = obj.skein_two[old_index].name if indices else registry[CHARACTER_CONTROLLER]['shortPath']
            staged_index = len(obj.skein_two)
            staged = obj.skein_two.add()
            staged.name = name
            staged.selected_type_path = CHARACTER_CONTROLLER
            key = hash_over_64(CHARACTER_CONTROLLER)
            touch_all_fields(staged, key)
            object_to_form(staged, key, data)
            if _canonical(get_data_from_active_editor(staged, key)) != expected:
                raise ValueError('Controller fields, types or values do not match the loaded registry exactly')
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError, RuntimeError) as error:
            if staged_index is not None and staged_index < len(obj.skein_two):
                obj.skein_two.remove(staged_index)
            self.report({'ERROR'}, f'Cannot paste controller: {error}')
            return {'CANCELLED'}
        if old_index is not None:
            obj.skein_two.remove(old_index)
            obj.skein_two.move(len(obj.skein_two) - 1, old_index)
            obj.active_component_index = old_active
        else:
            obj.active_component_index = staged_index
        clip_picker.request_clip_refresh(context, obj)
        diagnostics = validate_character_controller(data, clip_picker.cached_clip_names(obj))
        detail = diagnostics[0]['message'] + ' ' if diagnostics else ''
        self.report({'WARNING'}, 'Controller pasted. ' + detail
                    + 'Target clip names must match; final export validation is required.')
        return {'FINISHED'}


register, unregister = bpy.utils.register_classes_factory((CopyControllerSettings, PasteControllerSettings))
