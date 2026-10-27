"""Restore .blend caches immediately and synchronize BRP data off the UI thread."""
from datetime import datetime, timezone
import json
from queue import Empty, SimpleQueue
import threading
import time

import bpy

from . import authoring, connection
from .op_registry_loading import component_owners, process_registry, validate_registry, validate_registry_refresh

_results = SimpleQueue()
_generation = 0
_pending = None
_next_refresh = 0
_registered = False
_description = authoring.empty_description()
_status = dict(state='offline', message='Cached authoring data; runtime not connected.', endpoint='', fetched_at='')
RETRY_SECONDS = 10


def status():
    return dict(_status)


def description():
    return _description


def _online():
    return bpy.app.online_access


def _read(name, default):
    text = bpy.data.texts.get(name)
    return json.loads(text.as_string()) if text else default


def _write(name, value):
    encoded = json.dumps(value, indent=2, allow_nan=False)
    text = bpy.data.texts.get(name) or bpy.data.texts.new(name)
    text.clear()
    text.write(encoded)


def _presets(value, registry, errors):
    from .component_values import ensure_supported, validate_value
    if not isinstance(value, dict):
        raise ValueError('Skein presets must be an object')
    result = {}
    for type_path, presets in value.items():
        if type_path not in registry:
            continue
        if not isinstance(presets, dict):
            raise ValueError(f'Invalid presets for {type_path}')
        try:
            ensure_supported(registry, type_path)
        except ValueError as error:
            if 'default' in presets:
                errors[type_path] = str(error)
            continue
        supported = {}
        for name, preset in presets.items():
            try:
                validate_value(registry, type_path, preset)
            except (ValueError, TypeError, OverflowError, RecursionError) as error:
                if name == 'default':
                    errors[type_path] = 'Rust Default cannot be represented losslessly: ' + str(error)
                continue
            supported[name] = preset
        if supported:
            result[type_path] = supported
    return result


def _raw(value):
    if hasattr(value, 'items'):
        return {key: _raw(child) for key, child in value.items()}
    if hasattr(value, 'to_list'):
        return [_raw(child) for child in value.to_list()]
    if isinstance(value, (tuple, list)):
        return [_raw(child) for child in value]
    return value


def _snapshot(registry):
    from .component_values import read_component, ERRORS
    result = []
    for owner in component_owners():
        components = getattr(owner, 'skein_two', ())
        if not components:
            continue
        values = []
        for index, component in enumerate(components):
            try:
                values.append((index, read_component(component, registry)))
            except ERRORS:
                pass
        result.append((owner, [_raw(component) for component in components], values, owner.active_component_index))
    return result


def _rebuild(context, registry):
    """Keep raw data for unsupported types and verify supported values after rebind."""
    from .component_values import read_component
    previous = json.loads(context.window_manager.skein.registry)
    original = _snapshot(previous)
    try:
        process_registry(context, registry)
        for owner, raw, values, active in original:
            if [_raw(component) for component in owner.skein_two] != raw:
                raise ValueError(f'Registry refresh changed stored component data on {owner.name}')
            for index, expected in values:
                if read_component(owner.skein_two[index], registry) != expected:
                    raise ValueError(f'Registry refresh changed component values on {owner.name}')
    except Exception:
        process_registry(context, previous)
        for owner, raw, values, active in original:
            owner.skein_two.clear()
            for item in raw:
                component = owner.skein_two.add()
                for key, value in item.items():
                    component[key] = value
            owner.active_component_index = active
        raise


def restore_cache(context=None):
    """Invalidate old-file work before rebuilding forms from this file's cache."""
    global _generation, _pending, _description
    context = context or bpy.context
    _generation += 1
    _pending = None
    _description = authoring.empty_description()
    _status.update(state='offline', message='Cached authoring data; runtime not connected.', endpoint='', fetched_at='')
    try:
        info = _read('skein-cache-info.json', {})
        if isinstance(info, dict) and info.get('schema') == 'skein.cache/v1':
            _status.update(endpoint=info.get('endpoint', ''), fetched_at=info.get('fetched_at', ''))
        registry = _read('skein-registry.json', {})
        validate_registry(registry)
        _rebuild(context, registry)
        try:
            _description = authoring.validate_description(_read('skein-authoring.json', authoring.empty_description()), registry)
        except (ValueError, TypeError, KeyError, AttributeError):
            _status['message'] = 'Cached authoring description is incompatible; generic fields remain available.'
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError) as error:
        _status.update(state='error', message=f'Cannot restore cached authoring data: {error}')
    return status()


def bootstrap():
    restore_cache()
    if _online():
        request_sync()
    return status()


def _download(generation, url, request):
    try:
        registry = request(url, 'registry.schema', {})
        optional = {}
        warnings = []
        for method in ('skein/presets', 'skein/authoring'):
            try:
                optional[method] = request(url, method, {})
            except connection.RpcError as error:
                if error.code != -32601:
                    raise
                warnings.append(f'{method} unavailable: {error}')
            except (connection.Unavailable, OSError, TimeoutError) as error:
                warnings.append(f'{method} unavailable: {error}')
        _results.put((generation, url, registry, optional, warnings, None))
    except Exception as error:
        # Every worker result, including malformed JSON, must release the pending slot.
        _results.put((generation, url, None, {}, [], str(error)))


def request_sync(context=None):
    global _pending, _next_refresh
    if _pending == _generation:
        return status()
    _next_refresh = time.monotonic() + RETRY_SECONDS
    if not _online():
        _status.update(state='offline', message='Offline: cached registry, defaults and authoring data remain available.')
        return status()
    try:
        url = connection.endpoint(context)
    except ValueError as error:
        _status.update(state='error', message=str(error))
        return status()
    _pending = _generation
    _status.update(state='syncing', message='Syncing registry, defaults and authoring data…')
    threading.Thread(target=_download, args=(_generation, url, connection.post), daemon=True).start()
    return status()


def _apply(url, registry, optional, warnings):
    global _description
    context = bpy.context
    # Validate every cache payload before changing RNA forms or any Text datablock.
    json.dumps(registry, allow_nan=False)
    json.dumps(optional, allow_nan=False)
    validate_registry_refresh(context, registry)
    if 'skein/authoring' in optional:
        description_value = authoring.validate_description(optional['skein/authoring'], registry)
    else:
        try:
            description_value = authoring.validate_description(_read('skein-authoring.json', authoring.empty_description()), registry)
        except (ValueError, TypeError, KeyError, AttributeError):
            description_value = authoring.empty_description()
            warnings.append('Incompatible cached authoring description discarded.')
    default_errors = {}
    if 'skein/presets' in optional:
        presets = _presets(optional['skein/presets'], registry, default_errors)
    else:
        try:
            presets = _presets(_read('skein-presets.json', {}), registry, default_errors)
            default_errors.update(_read('skein-cache-info.json', {}).get('default_errors', {}))
        except (ValueError, TypeError, KeyError, AttributeError):
            presets = {}
            warnings.append('Incompatible cached defaults discarded.')
    json.dumps(description_value, allow_nan=False)
    json.dumps(presets, allow_nan=False)
    for name in ('skein-registry.json', 'skein-presets.json', 'skein-authoring.json', 'skein-cache-info.json'):
        text = bpy.data.texts.get(name)
        if text is not None and not text.is_editable:
            raise ValueError(f'Cached {name} is read-only; authoring data was kept')
    if json.loads(context.window_manager.skein.registry) != registry:
        _rebuild(context, registry)
    fetched_at = datetime.now(timezone.utc).isoformat()
    _write('skein-registry.json', registry)
    _write('skein-presets.json', presets)
    _write('skein-authoring.json', description_value)
    _write('skein-cache-info.json', dict(schema='skein.cache/v1', endpoint=url, fetched_at=fetched_at, default_errors=default_errors))
    _description = description_value
    _status.update(state='unverified' if warnings else 'ready', endpoint=url, fetched_at=fetched_at,
                   message=('Unverified cached authoring data. ' + ' '.join(warnings) if warnings else
                            'Authoring data synced from runtime.' +
                            (' Some Rust defaults are unavailable; see Reset for the reason.' if default_errors else '')))


def poll():
    """Timer entry point; all bpy reads/writes occur on Blender's main thread."""
    global _pending
    while True:
        try:
            generation, url, registry, optional, warnings, error = _results.get_nowait()
        except Empty:
            break
        if generation != _generation:
            continue
        _pending = None
        if error is not None:
            _status.update(state='offline', message=error + ' Cached authoring data remains available.')
            continue
        try:
            if not _online() or connection.endpoint() != url:
                _status.update(state='offline', message='Connection settings changed; stale runtime response was ignored.')
                continue
            _apply(url, registry, optional, warnings)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError) as error:
            _status.update(state='error', message=str(error))
    if _registered and _pending is None and time.monotonic() >= _next_refresh:
        request_sync()
    return .2


class SyncAuthoring(bpy.types.Operator):
    """Refresh runtime authoring data while keeping cached fields available"""
    bl_idname = 'wm.skein_sync_authoring'
    bl_label = 'Sync Skein Authoring'

    def execute(self, context):
        request_sync(context)
        return {'FINISHED'}


def register():
    global _registered
    if _registered:
        return
    bpy.utils.register_class(SyncAuthoring)
    _registered = True
    bpy.app.timers.register(poll, first_interval=.2, persistent=True)
    bootstrap()


def unregister():
    global _registered, _generation, _pending
    _generation += 1
    _pending = None
    if _registered:
        _registered = False
        if bpy.app.timers.is_registered(poll):
            bpy.app.timers.unregister(poll)
        bpy.utils.unregister_class(SyncAuthoring)
