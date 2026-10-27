"""Generic local checks and project-advertised export preflight."""
import json

from .authoring import empty_description, validate_description, clip_fields, clip_diagnostics
from .connection import Unavailable, RpcError


def current_registry(context=None):
    import bpy
    context = context or bpy.context
    return json.loads(context.window_manager.skein.registry or '{}')


def export_description(registry):
    """Malformed cached metadata is an error; absent metadata remains unverified."""
    import bpy
    text = bpy.data.texts.get('skein-authoring.json')
    if text is None:
        return empty_description(), [dict(severity='WARNING', object='', field='authoring metadata',
            message='Project authoring checks are unverified: sync runtime authoring metadata first.')]
    try:
        return validate_description(json.loads(text.as_string()), registry), []
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        return empty_description(), [dict(severity='ERROR', object='', field='authoring metadata',
            message=f'Cached authoring metadata is invalid; sync it again: {error}')]


def validate_export(records, registry, description, clip_names, request=None):
    """Check current exported values, then ask the advertised project validator."""
    diagnostics = []
    for record in records:
        names = clip_names.get(record['name'], []) if record['kind'] == 'Object' else []
        for component in record['components']:
            for type_path, value in component.items():
                for field, clip in clip_fields(registry, type_path, value, description):
                    for diagnostic in clip_diagnostics(clip, names, f'{type_path}.{field}'):
                        diagnostics.append(dict(object=record['name'], **diagnostic))
    method = description.get('validation_method')
    if method:
        try:
            if request is None:
                from .connection import post, endpoint
                result = post(endpoint(), method, {'objects': records})
            else:
                result = request(method, {'objects': records})
            if not isinstance(result, dict) or not isinstance(result.get('diagnostics'), list):
                raise ValueError('Project validator returned no diagnostics list')
            project = []
            for item in result['diagnostics']:
                if (not isinstance(item, dict) or item.get('severity') not in ('ERROR', 'WARNING')
                        or any(not isinstance(item.get(key), str) for key in ('object', 'field', 'message'))):
                    raise ValueError('Project validator returned an invalid diagnostic')
                project.append(item)
            diagnostics.extend(project)
        except (Unavailable, OSError, TimeoutError) as error:
            diagnostics.append(dict(severity='WARNING', object='', field='project validation',
                                    message=f'Project checks are unverified: {error}'))
        except RpcError as error:
            severity = 'WARNING' if error.code == -32601 else 'ERROR'
            diagnostics.append(dict(severity=severity, object='', field='project validation',
                                    message=f'Project validation failed: {error}'))
        except (ValueError, RuntimeError) as error:
            diagnostics.append(dict(severity='ERROR', object='', field='project validation',
                                    message=f'Project validation failed: {error}'))
    return diagnostics


def get_owner_diagnostics(owner, registry=None):
    """Panel checks against cached clip discovery; network validation runs at export."""
    from .component_values import read_component, ERRORS
    from .clip_picker import cached_clip_names, cached_clip_error
    registry = current_registry() if registry is None else registry
    description, diagnostics = export_description(registry)
    names = cached_clip_names(owner) if getattr(owner, 'type', None) is not None else None
    for component in getattr(owner, 'skein_two', ()):
        try:
            value = read_component(component, registry)
            for field, clip in clip_fields(registry, component.selected_type_path, value, description):
                if names is None:
                    diagnostics.append(dict(severity='WARNING', object=owner.name,
                        field=f'{component.selected_type_path}.{field}', message=cached_clip_error(owner) or 'Exported animation clips have not been discovered yet.'))
                else:
                    diagnostics.extend(dict(object=owner.name, **item) for item in
                        clip_diagnostics(clip, names, f'{component.selected_type_path}.{field}'))
        except ERRORS as error:
            diagnostics.append(dict(severity='ERROR', object=owner.name,
                                    field=component.selected_type_path, message=str(error)))
    return diagnostics


def format_diagnostic(item):
    return f'{item["object"]}: {item["field"]}: {item["message"]}'
