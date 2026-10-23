"""Manual animation playback in the running ARForge preview, through BRP."""
from urllib.parse import urlsplit

import bpy
import requests

from .action_validation import CHARACTER_CONTROLLER, validate_character_controller
from .form_to_object import get_data_from_active_editor
from .property_groups import hash_over_64


def request_preview(context, params):
    if not bpy.app.online_access:
        raise ValueError('Enable Online Access in Blender Preferences to connect to ARForge.')
    addon = context.preferences.addons.get(__package__)
    preferences = addon.preferences if addon else None
    host = (preferences.host.strip() if preferences else '') or 'http://127.0.0.1'
    port = (preferences.port.strip() if preferences else '') or '15702'
    if '://' not in host:
        host = 'http://' + host
    try:
        parsed = urlsplit(host)
        valid_host = (parsed.scheme in ('http', 'https') and parsed.hostname
                      and parsed.port is None and parsed.username is None
                      and parsed.password is None and parsed.path in ('', '/')
                      and not parsed.query and not parsed.fragment)
    except ValueError:
        valid_host = False
    if not valid_host:
        raise ValueError('Check Skein Host in Blender Preferences. Use an HTTP host and set Port separately.')
    if not port.isascii() or not port.isdecimal() or not 1 <= int(port) <= 65535:
        raise ValueError('Check Skein Port in Blender Preferences. Use a number from 1 to 65535.')
    endpoint = f'{parsed.scheme}://{parsed.netloc}:{int(port)}'
    payload = dict(jsonrpc='2.0', id=1, method='arforge/action_preview', params=params)
    try:
        response = requests.post(endpoint, json=payload, timeout=2)
        response.raise_for_status()
    except requests.Timeout as error:
        raise ValueError(f'ARForge preview request timed out at {endpoint}. Check the running preview and Skein Host/Port.') from error
    except requests.RequestException as error:
        raise ValueError(f'Could not connect to ARForge at {endpoint}. Start ARForge Preview and check Skein Host/Port. {error}') from error
    try:
        body = response.json()
    except ValueError as error:
        raise ValueError('Invalid ARForge response. Check Skein Host/Port points to ARForge Preview.') from error
    if isinstance(body, dict) and isinstance(body.get('error'), dict):
        error = body['error']
        if error.get('code') == -32601:
            raise ValueError('This runtime has no action preview method; update and restart ARForge Preview.')
        raise ValueError(str(error.get('message', 'ARForge preview request failed.')))
    if (not isinstance(body, dict) or body.get('jsonrpc') != '2.0' or body.get('id') != 1
            or not isinstance(body.get('result'), dict) or body['result'].get('ok') is not True):
        raise ValueError('Invalid ARForge response. Check Skein Host/Port and update ARForge Preview.')


def current_action(owner, path, expected_id):
    try:
        target = owner.path_resolve(path) if owner else None
    except (ValueError, TypeError):
        target = None
    for component in getattr(owner, 'skein_two', ()):
        if component.selected_type_path != CHARACTER_CONTROLLER:
            continue
        controller = getattr(component, hash_over_64(CHARACTER_CONTROLLER), None)
        for index, entry in enumerate(getattr(getattr(controller, 'actions', None), 'entries', ())):
            if entry.value != target:
                continue
            data = get_data_from_active_editor(component, hash_over_64(CHARACTER_CONTROLLER))
            action = data['actions'][index]
            if action.get('id') != expected_id:
                raise ValueError('The action changed; use Preview in ARForge on the current action row.')
            fields = {f'actions[{index}].{name}' for name in ('id', 'clip', 'looped', 'speed')}
            diagnostics = [item for item in validate_character_controller(data) if item['field'] in fields]
            if diagnostics:
                raise ValueError('; '.join(f'{item["field"]}: {item["message"]}' for item in diagnostics))
            if sum(item.get('id') == expected_id for item in data['actions']) != 1:
                raise ValueError(f'Duplicate action ID {expected_id!r}; choose a unique ID before previewing.')
            return dict(action_id=action['id'], clip=action['clip'], looped=action['looped'], speed=action['speed'])
    raise ValueError('The CharacterController action is unavailable; use Preview in ARForge on the current action row.')


class SkeinActionPreview(bpy.types.Operator):
    """Play this exported action in ARForge. Export and reload the model first"""
    bl_idname = 'wm.skein_action_preview'
    bl_label = 'Preview in ARForge'
    target_object: bpy.props.StringProperty(options={'HIDDEN'}) # type: ignore
    target_path: bpy.props.StringProperty(options={'HIDDEN'}) # type: ignore
    action_id: bpy.props.StringProperty(options={'HIDDEN'}) # type: ignore

    def execute(self, context):
        try:
            # Carry the RNA path because UI popup context does not survive execution.
            owner = bpy.data.objects.get(self.target_object)
            params = current_action(owner, self.target_path, self.action_id)
            request_preview(context, params)
        except ValueError as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        self.report({'INFO'}, 'Playing in ARForge. Looped actions play until Stop preview; one-shots return automatically.')
        return {'FINISHED'}


class SkeinStopActionPreview(bpy.types.Operator):
    """Stop the manual action and return to state animation in ARForge"""
    bl_idname = 'wm.skein_stop_action_preview'
    bl_label = 'Stop preview'

    def execute(self, context):
        try:
            request_preview(context, {'stop': True})
        except ValueError as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        self.report({'INFO'}, 'Manual action preview stopped in ARForge.')
        return {'FINISHED'}


classes = (SkeinActionPreview, SkeinStopActionPreview)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
