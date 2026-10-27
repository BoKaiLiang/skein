"""Bounded JSON-RPC transport shared by authoring operations.

This module can be imported without Blender; preferences are read only in endpoint().
"""
import json
import math
import urllib.error
import urllib.parse
import urllib.request

MAX_BYTES = 8 * 1024 * 1024


class Unavailable(ValueError):
    """The configured runtime cannot currently be contacted."""


class RpcError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def post(url, method, params, timeout=2):
    try:
        payload = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method,
                              'params': params}, allow_nan=False).encode('utf-8')
    except (TypeError, UnicodeError, RecursionError) as error:
        raise ValueError('Invalid authoring request JSON') from error
    if len(payload) > MAX_BYTES:
        raise ValueError('Authoring request exceeds 8 MiB')
    request = urllib.request.Request(url, payload, {'Content-Type': 'application/json'})
    # Ignore proxy environment variables for the explicitly configured BRP host.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = response.read(MAX_BYTES + 1)
    except (urllib.error.URLError, OSError, TimeoutError) as error:
        raise Unavailable(f'Runtime unavailable: {error}') from error
    if len(raw) > MAX_BYTES:
        raise ValueError('Authoring response exceeds 8 MiB')
    def invalid_number(value):
        raise ValueError(f'Non-finite JSON number: {value}')
    def finite_float(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError('Non-finite JSON number')
        return result
    try:
        value = json.loads(raw, parse_constant=invalid_number, parse_float=finite_float)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError('Runtime returned invalid JSON') from error
    if (not isinstance(value, dict) or value.get('jsonrpc') != '2.0'
            or type(value.get('id')) is not int or value['id'] != 1
            or ('result' in value) == ('error' in value)):
        raise ValueError('Runtime returned an invalid JSON-RPC response')
    if 'error' in value:
        error = value['error']
        if not isinstance(error, dict) or type(error.get('code')) is not int or not isinstance(error.get('message'), str):
            raise ValueError('Runtime returned an invalid JSON-RPC error')
        raise RpcError(error['code'], error['message'])
    return value['result']


def endpoint(context=None):
    import bpy
    if not bpy.app.online_access:
        raise Unavailable('Enable Blender Online Access to connect to the runtime')
    context = context or bpy.context
    addon = context.preferences.addons.get(__package__)
    preferences = addon.preferences if addon else None
    host = (getattr(preferences, 'host', '') or 'http://127.0.0.1').strip()
    port = (getattr(preferences, 'port', '') or '15702').strip()
    parsed = urllib.parse.urlsplit(host)
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname
            or parsed.username or parsed.password or parsed.port
            or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
        raise ValueError('Skein Host must be an http(s) hostname without port or path')
    if not port.isdecimal() or not 1 <= int(port) <= 65535:
        raise ValueError('Skein Port must be between 1 and 65535')
    return f'{parsed.scheme}://{parsed.netloc}:{int(port)}'
