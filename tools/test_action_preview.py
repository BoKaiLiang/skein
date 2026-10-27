"""Generic preview operators: Blender --background --factory-startup --online-mode --python."""
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import socket
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import extension
from extension import action_preview, authoring, connection
from extension.form_to_object import get_data_from_active_editor
from extension.object_to_form import object_to_form
from extension.op_registry_loading import process_registry
from extension.property_groups import hash_over_64


def ref(path):
    return {'type': {'$ref': '#/$defs/' + path}}


def schema(kind, path, **kwargs):
    return dict(kind=kind, typePath=path, shortPath=path.split('::')[-1], **kwargs)


COMPONENT = 'demo::Palette'
OTHER = 'demo::Unadvertised'
REGISTRY = {
    'String': schema('Value', 'String', type='string'),
    'f32': schema('Value', 'f32', type='float'),
    'bool': schema('Value', 'bool', type='boolean'),
    'demo::Swatch': schema('Struct', 'demo::Swatch', properties={
        'key': ref('String'), 'shade': ref('String'), 'rate': ref('f32'), 'enabled': ref('bool')}),
    'VecSwatch': schema('List', 'VecSwatch', type='array', items=ref('demo::Swatch')),
    COMPONENT: schema('Struct', COMPONENT, reflectTypes=['Component'],
                      properties={'swatches': ref('VecSwatch'), 'title': ref('String')}),
    OTHER: schema('Struct', OTHER, reflectTypes=['Component'], properties={'swatches': ref('VecSwatch')}),
}
DESCRIPTION = dict(schema='skein.authoring/v1', fields=[], validation_method=None, previews=[dict(
    component_type=COMPONENT, list_field='swatches', identity_field='key', label='Paint sample',
    method='demo/paint', request_fields=dict(token='key', color='shade', weight='rate', active='enabled'),
    stop=dict(method='demo/cancel_paint', params={'cancel': 'palette'}))])
DATA = dict(title='Example', swatches=[dict(key='orange', shade='Warm', rate=-0.5, enabled=True),
                                      dict(key='blue', shade='Cool', rate=1.25, enabled=False)])


class PreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        extension.register()
        cls.addon = bpy.context.preferences.addons.get('extension') or bpy.context.preferences.addons.new()
        cls.addon.module = 'extension'
        text = bpy.data.texts.get('skein-registry.json') or bpy.data.texts.new('skein-registry.json')
        text.clear()
        text.write(json.dumps(REGISTRY))
        process_registry(bpy.context, REGISTRY)
        cls.requests = []
        cls.reply = {'jsonrpc': '2.0', 'id': 1, 'result': {'accepted': True}}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                cls.requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps(cls.reply).encode())

            def log_message(self, *_args):
                pass

        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.worker = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.worker.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.worker.join()

    def setUp(self):
        self.assertTrue(hasattr(action_preview, 'capture_target'), 'Preview needs generic metadata-driven target capture')
        self.requests.clear()
        type(self).reply = {'jsonrpc': '2.0', 'id': 1, 'result': {'accepted': True}}
        bpy.ops.object.empty_add()
        self.obj = bpy.context.object
        self.obj.name = 'Clicked owner'
        bpy.context.window_manager.selected_component = COMPONENT
        bpy.ops.object.insert_component()
        self.component = self.obj.skein_two[0]
        object_to_form(self.component, COMPONENT, copy.deepcopy(DATA))
        self.value = getattr(self.component, hash_over_64(COMPONENT))
        self.item = self.value.swatches.entries[0].value
        self.description = copy.deepcopy(DESCRIPTION)
        self.cache_description()
        self.preferences = self.addon.preferences
        self.preferences.host = 'http://127.0.0.1'
        self.preferences.port = str(self.server.server_port)

    def cache_description(self):
        text = bpy.data.texts.get('skein-authoring.json') or bpy.data.texts.new('skein-authoring.json')
        text.clear()
        text.write(json.dumps(self.description))

    def props(self, component=None, item=None, field='swatches'):
        result = SimpleNamespace()
        action_preview.capture_target(result, self.obj, component or self.component, field, item or self.item)
        return vars(result)

    def start(self, props=None):
        return bpy.ops.wm.skein_action_preview(**(props or self.props()))

    def stop(self):
        return bpy.ops.wm.skein_stop_action_preview(component_type=COMPONENT, list_field='swatches')

    def test_metadata_drives_method_keys_and_current_serialized_values(self):
        before = get_data_from_active_editor(self.component, hash_over_64(COMPONENT))
        settings = self.preferences.host, self.preferences.port, bpy.context.scene.frame_current
        props = self.props()
        self.item.rate = -2.5
        self.item.shade = 'Edited after drawing'
        self.assertEqual(self.start(props), {'FINISHED'})
        self.assertEqual(self.requests[-1], dict(jsonrpc='2.0', id=1, method='demo/paint', params=dict(
            token='orange', color='Edited after drawing', weight=-2.5, active=True)))
        self.item.rate, self.item.shade = DATA['swatches'][0]['rate'], DATA['swatches'][0]['shade']
        self.assertEqual(get_data_from_active_editor(self.component, hash_over_64(COMPONENT)), before)
        self.assertEqual((self.preferences.host, self.preferences.port, bpy.context.scene.frame_current), settings)

    def test_capture_uses_clicked_owner_and_component_instead_of_active_selection(self):
        props = self.props()
        bpy.ops.object.empty_add()
        bpy.context.window_manager.selected_component = OTHER
        bpy.ops.object.insert_component()
        self.assertEqual(self.start(props), {'FINISHED'})
        self.assertEqual(self.requests[-1]['params']['token'], 'orange')
        self.assertEqual(props['target_object'], self.obj.name)
        self.assertEqual(self.obj.path_resolve(props['component_path']), self.component)
        self.assertEqual(self.obj.path_resolve(props['target_path']), self.item)

    def test_stop_uses_advertised_method_and_payload_without_selected_object(self):
        bpy.context.view_layer.objects.active = None
        self.assertEqual(self.stop(), {'FINISHED'})
        self.assertEqual(self.requests[-1], dict(jsonrpc='2.0', id=1, method='demo/cancel_paint',
                         params={'cancel': 'palette'}))

    def test_changed_runtime_description_is_used_without_hardcoded_response_policy(self):
        props = self.props()
        operation = self.description['previews'][0]
        operation['method'] = 'another/render_sample'
        operation['request_fields'] = {'current_name': 'shade'}
        self.cache_description()
        type(self).reply['result'] = ['any', 'project', 'result']
        self.assertEqual(self.start(props), {'FINISHED'})
        self.assertEqual(self.requests[-1]['method'], 'another/render_sample')
        self.assertEqual(self.requests[-1]['params'], {'current_name': 'Warm'})

    def test_changed_identity_removed_row_and_wrong_component_do_not_send(self):
        props = self.props()
        self.item.key = 'renamed'
        with self.assertRaisesRegex(RuntimeError, 'changed|identity'):
            self.start(props)
        self.item.key = 'orange'
        self.value.swatches.entries.move(0, 1)
        with self.assertRaisesRegex(RuntimeError, 'changed|unavailable'):
            self.start(props)
        self.value.swatches.entries.move(1, 0)
        props = self.props()
        props['component_pointer'] = '0'
        with self.assertRaisesRegex(RuntimeError, 'changed|unavailable'):
            self.start(props)
        props = self.props()
        props['target_pointer'] = '0'
        with self.assertRaisesRegex(RuntimeError, 'changed|unavailable'):
            self.start(props)
        props = self.props()
        props['target_path'] = 'location'
        with self.assertRaisesRegex(RuntimeError, 'changed|unavailable'):
            self.start(props)
        props = self.props()
        props['component_type'] = OTHER
        with self.assertRaisesRegex(RuntimeError, 'changed|unavailable|advertised'):
            self.start(props)
        props = self.props()
        self.value.swatches.entries.remove(0)
        with self.assertRaisesRegex(RuntimeError, 'changed|unavailable'):
            self.start(props)
        self.assertEqual(self.requests, [])

    def test_missing_or_invalid_metadata_and_missing_stop_are_actionable(self):
        props = self.props()
        self.description['previews'] = []
        self.cache_description()
        for execute in (lambda: self.start(props), self.stop):
            with self.assertRaisesRegex(RuntimeError, 'advertised|available'):
                execute()
        self.description = copy.deepcopy(DESCRIPTION)
        self.description['previews'][0]['request_fields']['bad'] = 'missing'
        self.cache_description()
        with self.assertRaisesRegex(RuntimeError, 'advertised|field|description'):
            self.start(props)
        self.description = copy.deepcopy(DESCRIPTION)
        del self.description['previews'][0]['stop']
        self.cache_description()
        with self.assertRaisesRegex(RuntimeError, '[Ss]top|available'):
            self.stop()
        self.assertEqual(self.requests, [])

    def test_nonfinite_current_value_and_duplicate_identity_never_send(self):
        props = self.props()
        self.item['rate'] = float('inf')
        self.assertTrue(math.isinf(self.item.rate), 'Exercise an actual nonfinite stored form value')
        with self.assertRaisesRegex(RuntimeError, 'finite|float|fields|Invalid'):
            self.start(props)
        self.item.rate = 1
        self.value.swatches.entries[1].value.key = self.item.key
        with self.assertRaisesRegex(RuntimeError, '[Dd]uplicate|unique|identity'):
            self.start(props)
        self.assertEqual(self.requests, [])

    def test_panel_buttons_use_metadata_labels_and_capture_real_targets(self):
        from extension.skein_panel import draw_generic_panel
        from tools.test_lists import RecordingLayout
        operations = []
        class Layout(RecordingLayout):
            def operator(self, name, **kwargs):
                props = SimpleNamespace()
                operations.append((name, kwargs, props))
                return props
            def prop_search(self, *_args, **_kwargs):
                pass
            def template_list(self, *_args, **_kwargs):
                pass
            def popover(self, *_args, **_kwargs):
                pass
        draw_generic_panel(bpy.context, self.obj, Layout(), 'object', 'OBJECT_PT_skein_preset_panel')
        starts = [(kwargs, props) for name, kwargs, props in operations if name == 'wm.skein_action_preview']
        self.assertEqual(len(starts), 2)
        for index, (kwargs, props) in enumerate(starts):
            self.assertEqual(kwargs['text'], 'Paint sample')
            self.assertEqual(props.target_object, self.obj.name)
            self.assertEqual(self.obj.path_resolve(props.target_path), self.value.swatches.entries[index].value)
            self.assertEqual(self.start(vars(props)), {'FINISHED'})
        stops = [props for name, _, props in operations if name == 'wm.skein_stop_action_preview']
        self.assertEqual(len(stops), 1)
        self.assertEqual(vars(stops[0]), dict(component_type=COMPONENT, list_field='swatches'))
        self.assertEqual(bpy.ops.wm.skein_stop_action_preview(**vars(stops[0])), {'FINISHED'})

    def test_generic_transport_errors_are_reported(self):
        for code, message in [(-32601, 'Project preview method is missing'),
                              (-32602, 'Export and reload the selected palette first')]:
            type(self).reply = dict(jsonrpc='2.0', id=1, error=dict(code=code, message=message))
            with self.subTest(code=code), self.assertRaisesRegex(RuntimeError, message):
                self.start()
        self.requests.clear()
        with socket.socket() as reserved:
            reserved.bind(('127.0.0.1', 0))
            self.preferences.port = str(reserved.getsockname()[1])
            with self.assertRaisesRegex(RuntimeError, '[Uu]navailable'):
                self.start()
        self.assertEqual(self.requests, [])
        with patch.object(connection, 'endpoint', side_effect=connection.Unavailable('Online Access disabled')):
            with self.assertRaisesRegex(RuntimeError, 'Online Access'):
                self.stop()


if __name__ == '__main__':
    sys.argv = [__file__]
    result = unittest.main(exit=False, verbosity=2).result
    if not result.wasSuccessful():
        sys.exit(1)
