"""Project-provided field editors and operations; no game-specific vocabulary."""
from collections import Counter
import json

SCHEMA = 'skein.authoring/v1'


def empty_description():
    return dict(schema=SCHEMA, fields=[], validation_method=None, previews=[])


def reference(value):
    if not isinstance(value, dict):
        raise ValueError('Invalid reflected field reference')
    target = value.get('type', value)
    if not isinstance(target, dict) or not isinstance(target.get('$ref', ''), str):
        raise ValueError('Invalid reflected field reference')
    return target.get('$ref', '').removeprefix('#/$defs/')


def _text(value, name):
    if not isinstance(value, str) or not value.strip() or any(ord(c) < 32 for c in value):
        raise ValueError(f'Invalid authoring {name}')
    return value


def validate_description(value, registry):
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, RecursionError) as error:
        raise ValueError('Invalid authoring JSON') from error
    if not isinstance(value, dict) or value.get('schema') != SCHEMA:
        raise ValueError('Unsupported Skein authoring description')
    if not isinstance(value.get('fields'), list) or not isinstance(value.get('previews'), list):
        raise ValueError('Authoring fields and previews must be lists')
    method = value.get('validation_method')
    if method is not None:
        _text(method, 'validation method')
    seen = set()
    for item in value['fields']:
        if not isinstance(item, dict) or item.get('widget') != 'animation_clip':
            raise ValueError('Unsupported authoring field editor')
        type_path = _text(item.get('type_path'), 'type path')
        field = _text(item.get('field'), 'field')
        target = registry.get(reference(registry.get(type_path, {}).get('properties', {}).get(field, {})), {})
        if target.get('kind') != 'Value' or target.get('type') != 'string':
            raise ValueError(f'{type_path}.{field}: animation clip editor requires a String field')
        if (type_path, field) in seen:
            raise ValueError(f'Duplicate authoring field: {type_path}.{field}')
        playback = item.get('playback')
        if playback is not None:
            if not isinstance(playback, dict):
                raise ValueError('Invalid clip playback settings')
            for key, expected in (('loop_field', 'boolean'), ('speed_field', 'float')):
                if key not in playback:
                    continue
                sibling = _text(playback[key], key)
                schema = registry.get(reference(registry.get(type_path, {}).get('properties', {}).get(sibling, {})), {})
                if schema.get('kind') != 'Value' or schema.get('type') != expected:
                    raise ValueError(f'{type_path}.{sibling}: playback requires a {expected} field')
        seen.add((type_path, field))
    seen.clear()
    for operation in value['previews']:
        if not isinstance(operation, dict):
            raise ValueError('Invalid preview operation')
        for key in ('component_type', 'list_field', 'identity_field', 'label', 'method'):
            _text(operation.get(key), 'preview ' + key)
        key = operation['component_type'], operation['list_field']
        if key in seen:
            raise ValueError('Duplicate preview operation')
        seen.add(key)
        component = registry.get(operation['component_type'], {})
        sequence = registry.get(reference(component.get('properties', {}).get(operation['list_field'], {})), {})
        element = registry.get(reference(sequence.get('items', {})), {})
        properties = element.get('properties', {})
        fields = operation.get('request_fields')
        if (sequence.get('kind') != 'List' or not isinstance(fields, dict) or not fields
                or operation['identity_field'] not in properties):
            raise ValueError('Preview requires a reflected list with an identity field')
        for target, source in fields.items():
            _text(target, 'request key')
            _text(source, 'request source field')
            if source not in properties:
                raise ValueError(f'Unknown preview source field: {source}')
        stop = operation.get('stop')
        if stop is not None:
            if not isinstance(stop, dict) or 'params' not in stop:
                raise ValueError('Invalid preview stop operation')
            _text(stop.get('method'), 'stop method')
            json.dumps(stop['params'], allow_nan=False)
    return value


def cached_description():
    import bpy
    text = bpy.data.texts.get('skein-authoring.json')
    if text:
        try:
            value = json.loads(text.as_string())
            registry = json.loads(bpy.context.window_manager.skein.registry)
            return validate_description(value, registry)
        except (ValueError, AttributeError, TypeError, KeyError):
            pass
    return empty_description()


def field_editor(description, type_path, field):
    return next((item for item in description.get('fields', [])
                 if item['type_path'] == type_path and item['field'] == field), None)


def field_widget(description, type_path, field):
    return next((item['widget'] for item in description.get('fields', [])
                 if item['type_path'] == type_path and item['field'] == field), None)


def preview_operation(description, component_type, list_field):
    return next((item for item in description.get('previews', [])
                 if item['component_type'] == component_type and item['list_field'] == list_field), None)


def preview_params(operation, value):
    if not isinstance(value, dict) or operation['identity_field'] not in value:
        raise ValueError('Preview item no longer exists')
    try:
        result = {key: value[field] for key, field in operation['request_fields'].items()}
        json.dumps(result, allow_nan=False)
        return result
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError('Preview fields no longer match the project description') from error


def clip_fields(registry, type_path, value, description, path='', depth=0):
    if depth > 64:
        raise ValueError('Reflected value nesting exceeds 64 levels')
    schema = registry.get(type_path, {})
    kind = schema.get('kind')
    if kind == 'Struct' and isinstance(value, dict):
        for name, field in schema.get('properties', {}).items():
            if name not in value:
                continue
            child_path = f'{path}.{name}' if path else name
            if field_widget(description, type_path, name) == 'animation_clip':
                yield child_path, value[name]
            else:
                yield from clip_fields(registry, reference(field), value[name], description, child_path, depth + 1)
    elif kind in ('Tuple', 'TupleStruct'):
        items = schema.get('prefixItems', [])
        if len(items) == 1:
            yield from clip_fields(registry, reference(items[0]), value, description, path, depth + 1)
    elif kind in ('List', 'Array') and isinstance(value, list):
        for index, item in enumerate(value):
            yield from clip_fields(registry, reference(schema.get('items', {})), item,
                                   description, f'{path}[{index}]', depth + 1)
    elif kind == 'Enum':
        if schema.get('modulePath') == 'core::option':
            if value is not None:
                variant = next((v for v in schema.get('oneOf', []) if v.get('shortPath') == 'Some'), {})
                items = variant.get('prefixItems', [])
                if len(items) == 1:
                    yield from clip_fields(registry, reference(items[0]), value, description, path, depth + 1)
        elif isinstance(value, dict) and len(value) == 1:
            name, data = next(iter(value.items()))
            variant = next((v for v in schema.get('oneOf', []) if v.get('shortPath') == name), {})
            yield from _variant_fields(registry, variant, data, description, path, depth)


def _variant_fields(registry, variant, value, description, path, depth):
    if variant.get('kind') == 'Struct' and isinstance(value, dict):
        overlay = dict(registry)
        overlay[variant['typePath']] = variant
        yield from clip_fields(overlay, variant['typePath'], value, description, path, depth + 1)
    elif variant.get('kind') in ('Tuple', 'TupleStruct'):
        items = variant.get('prefixItems', [])
        values = [value] if len(items) == 1 else value
        if isinstance(values, list):
            for index, (field, data) in enumerate(zip(items, values)):
                yield from clip_fields(registry, reference(field), data, description,
                                       path if len(items) == 1 else f'{path}[{index}]', depth + 1)


def selectable_clips(names):
    counts = Counter(name for name in names if isinstance(name, str) and name.strip())
    return sorted(name for name, count in counts.items() if count == 1)


def clip_diagnostics(value, names, field):
    if not isinstance(value, str) or not value.strip():
        message = 'Choose a named animation clip.'
    elif names.count(value) == 0:
        message = f'Animation clip {value!r} is not exported for this Object hierarchy.'
    elif names.count(value) > 1:
        message = f'Animation clip {value!r} is ambiguous; give exported animations unique names.'
    else:
        return []
    return [dict(severity='ERROR', field=field, message=message)]
