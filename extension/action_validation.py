"""ARForge character authoring checks; does not change animation playback."""
from collections import Counter
import math
import re
import unicodedata

CHARACTER_CONTROLLER = 'poc_design_editor::CharacterController'
RESERVED_KEYS = {'Escape', 'F5', 'F6', 'F7', 'F8', 'F9', 'Unidentified'}
ACTION_ID = re.compile(r'^[a-z][a-z0-9_]{0,63}$')


def validate_character_controller(data, available_clip_names=None):
    """Return field-specific errors. None means clip availability is not yet known."""
    diagnostics = []
    clips = Counter(available_clip_names) if available_clip_names is not None else None

    def error(field, message):
        diagnostics.append(dict(severity='ERROR', field=field, message=message))

    def clip(field, value):
        if (not isinstance(value, str) or not value.strip()
                or len(value.encode('utf-8')) > 256
                or any(unicodedata.category(c) == 'Cc' for c in value)):
            error(field, 'Clip must be nonempty, at most 256 UTF-8 bytes, without control characters.')
        elif clips is not None:
            if not clips[value]:
                error(field, f'Clip {value!r} is absent from this export; check animation export settings.')
            elif clips[value] > 1:
                error(field, f'Clip {value!r} has duplicate exported names.')

    bindings = data.get('bindings', {})
    used_keys = {value: f'bindings.{field}' for field, value in bindings.items() if isinstance(value, str)}
    used_ids = set()
    actions = data.get('actions', [])
    if not isinstance(actions, list):
        error('actions', 'Actions must be a list.')
        actions = []
    if len(actions) > 64:
        error('actions', 'At most 64 actions are supported.')
    for index, action in enumerate(actions):
        field = f'actions[{index}]'
        if not isinstance(action, dict):
            error(field, 'Action must contain id, clip, binding, looped and speed fields.')
            continue
        action_id = action.get('id')
        if not isinstance(action_id, str) or not ACTION_ID.fullmatch(action_id):
            error(f'{field}.id', 'Use 1–64 lowercase letters, digits or underscores, starting with a letter.')
        elif action_id in {'idle', 'move', 'jump'}:
            error(f'{field}.id', 'idle, move and jump are reserved for the existing state animations.')
        elif action_id in used_ids:
            error(f'{field}.id', f'Duplicate action ID {action_id!r}.')
        if isinstance(action_id, str):
            used_ids.add(action_id)
        clip(f'{field}.clip', action.get('clip'))
        speed = action.get('speed')
        if isinstance(speed, bool) or not isinstance(speed, (int, float)) or not math.isfinite(speed) or not 0 < speed <= 10:
            error(f'{field}.speed', 'Speed must be finite and greater than 0, up to 10.')
        if not isinstance(action.get('looped'), bool):
            error(f'{field}.looped', 'Looped must be true or false.')
        binding = action.get('binding')
        if 'binding' not in action:
            error(f'{field}.binding', 'Binding must be a key or None.')
        if binding is not None:
            if not isinstance(binding, str) or not binding:
                error(f'{field}.binding', 'Binding must be a key or None.')
            elif binding in RESERVED_KEYS:
                error(f'{field}.binding', f'{binding} is reserved by the editor.')
            elif binding in used_keys:
                error(f'{field}.binding', f'{binding} is already used by {used_keys[binding]}.')
            if isinstance(binding, str):
                used_keys[binding] = f'{field}.binding'
    animations = data.get('animations')
    if isinstance(animations, dict):
        for field in ('idle', 'jump'):
            clip(f'animations.{field}', animations.get(field))
        move_field = 'move_clip' if 'move_clip' in animations else 'move'
        clip(f'animations.{move_field}', animations.get(move_field))
    return diagnostics


def get_object_controller_data(obj):
    """Read the same reflected form used by the Skein glTF exporter."""
    from .form_to_object import get_data_from_active_editor
    from .property_groups import hash_over_64
    for component in getattr(obj, 'skein_two', ()):
        if component.selected_type_path == CHARACTER_CONTROLLER:
            return get_data_from_active_editor(component, hash_over_64(CHARACTER_CONTROLLER))
    return None


def get_object_action_diagnostics(obj):
    """Panel checks. Only the exporter knows the final animation names."""
    data = get_object_controller_data(obj)
    if data is None:
        return []
    diagnostics = validate_character_controller(data)
    if data.get('actions') or data.get('animations'):
        diagnostics.append(dict(
            severity='WARNING', field='clips',
            message='Clip names are checked against the final export. Export settings can exclude animations; rig retargeting is not validated.'))
    return diagnostics


def format_diagnostics(object_name, diagnostics):
    return '\n'.join(f'{object_name}: {item["field"]}: {item["message"]}' for item in diagnostics)
