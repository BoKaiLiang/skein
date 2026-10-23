"""Real Blender operators + loopback BRP. Run with --background --factory-startup --online-mode --python."""
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import extension
from extension.action_validation import CHARACTER_CONTROLLER
from extension.form_to_object import get_data_from_active_editor
from extension.object_to_form import object_to_form
from extension.op_registry_loading import process_registry
from extension.property_groups import hash_over_64
from tools.test_lists import REGISTRY, DATA, RecordingLayout, ref


class PreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        extension.register()
        cls.addon = bpy.context.preferences.addons.new()
        cls.addon.module = 'extension'
        registry = copy.deepcopy(REGISTRY)
        registry['Action']['properties'].pop('settings')
        registry['Action']['properties'].update(looped=ref('bool'), speed=ref('f32'))
        bpy.data.texts.new('skein-registry.json').write(json.dumps(registry))
        process_registry(bpy.context, registry)
        cls.requests = []
        cls.response = {'jsonrpc': '2.0', 'id': 1, 'result': {'ok': True}}
        cls.delay = 0
        cls.status = 200

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                cls.requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                time.sleep(cls.delay)
                self.send_response(cls.status)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                try:
                    body = cls.response if isinstance(cls.response, bytes) else json.dumps(cls.response).encode()
                    self.wfile.write(body)
                except BrokenPipeError:
                    pass

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
        self.assertTrue(hasattr(extension, 'action_preview'), 'Addon needs ARForge action preview operators')
        self.preview = extension.action_preview
        self.requests.clear()
        type(self).response = {'jsonrpc': '2.0', 'id': 1, 'result': {'ok': True}}
        type(self).delay = 0
        type(self).status = 200
        self.obj = bpy.context.object
        self.obj.skein_two.clear()
        bpy.context.window_manager.selected_component = CHARACTER_CONTROLLER
        bpy.ops.object.insert_component()
        self.container = self.obj.skein_two[0]
        self.data = dict(DATA, actions=[dict(id='wave', clip='Wave', binding=None, looped=True, speed=1.25),
                                        dict(id='roll', clip='Roll', binding='ShiftLeft', looped=False, speed=0.5)])
        object_to_form(self.container, CHARACTER_CONTROLLER, self.data)
        self.controller = getattr(self.container, hash_over_64(CHARACTER_CONTROLLER))
        self.action = self.controller.actions.entries[0].value
        self.preferences = self.addon.preferences
        self.preferences.host = 'http://127.0.0.1'
        self.preferences.port = str(self.server.server_port)

    def props(self, action=None):
        action = action or self.action
        return dict(target_object=self.obj.name, target_path=action.path_from_id(), action_id=action.id)

    def test_exact_current_serialized_actions_for_none_and_some_bindings(self):
        before = get_data_from_active_editor(self.container, hash_over_64(CHARACTER_CONTROLLER))
        before_settings = (self.preferences.host, self.preferences.port, bpy.context.scene.frame_current)
        props = self.props()
        self.action.speed = 2.0
        self.assertEqual(bpy.ops.wm.skein_action_preview(**props), {'FINISHED'})
        self.assertEqual(self.requests[-1], dict(jsonrpc='2.0', id=1, method='arforge/action_preview',
                         params=dict(action_id='wave', clip='Wave', looped=True, speed=2.0)))
        other = self.controller.actions.entries[1].value
        self.assertEqual(bpy.ops.wm.skein_action_preview(**self.props(other)), {'FINISHED'})
        self.assertEqual(self.requests[-1]['params'], dict(action_id='roll', clip='Roll', looped=False, speed=0.5))
        self.action.speed = 1.25
        self.assertEqual(get_data_from_active_editor(self.container, hash_over_64(CHARACTER_CONTROLLER)), before)
        self.assertEqual((self.preferences.host, self.preferences.port, bpy.context.scene.frame_current), before_settings)

    def test_manual_preview_is_independent_of_input_binding_conflicts(self):
        self.action.binding.skein_enum_index = 'Some'
        self.action.binding.Some.skein_enum_index = 'ShiftLeft'
        other = self.controller.actions.entries[1].value
        self.assertEqual(bpy.ops.wm.skein_action_preview(**self.props(other)), {'FINISHED'})
        self.assertEqual(self.requests[-1]['params']['action_id'], 'roll')

    def test_stop_has_no_selection_or_action_requirement(self):
        bpy.context.view_layer.objects.active = None
        try:
            self.assertEqual(bpy.ops.wm.skein_stop_action_preview(), {'FINISHED'})
            self.assertEqual(self.requests[-1]['params'], {'stop': True})
        finally:
            bpy.context.view_layer.objects.active = self.obj

    def test_old_runtime_and_loaded_model_errors_are_reported(self):
        for code, message, expected in [
            (-32601, 'Method not found', 'update.*ARForge'),
            (-32602, 'Action differs from loaded model; export and reload first', 'export and reload'),
            (-32000, 'No player model is loaded', 'No player model'),
        ]:
            with self.subTest(message=message):
                type(self).response = {'jsonrpc': '2.0', 'id': 1, 'error': {'code': code, 'message': message}}
                with self.assertRaisesRegex(RuntimeError, expected):
                    bpy.ops.wm.skein_action_preview(**self.props())

    def test_invalid_and_stale_targets_never_send_a_request(self):
        for field, value, expected in [('id', 'Bad ID', 'actions\\[0\\].id'),
                                       ('clip', '', 'actions\\[0\\].clip'),
                                       ('speed', 0, 'actions\\[0\\].speed')]:
            with self.subTest(field=field):
                previous = getattr(self.action, field)
                setattr(self.action, field, value)
                with self.assertRaisesRegex(RuntimeError, expected):
                    bpy.ops.wm.skein_action_preview(**self.props())
                setattr(self.action, field, previous)
        self.controller.actions.entries[1].value.id = self.action.id
        with self.assertRaisesRegex(RuntimeError, 'Duplicate action ID'):
            bpy.ops.wm.skein_action_preview(**self.props())
        self.controller.actions.entries[1].value.id = 'roll'
        props = self.props()
        self.controller.actions.entries.remove(0)
        with self.assertRaisesRegex(RuntimeError, 'changed|unavailable'):
            bpy.ops.wm.skein_action_preview(**props)
        with self.assertRaisesRegex(RuntimeError, 'unavailable'):
            bpy.ops.wm.skein_action_preview(target_object=self.obj.name, target_path='location', action_id='wave')
        with self.assertRaisesRegex(RuntimeError, 'unavailable'):
            bpy.ops.wm.skein_action_preview(target_object='Missing', target_path=props['target_path'], action_id='wave')
        self.assertEqual(self.requests, [])

    def test_offline_invalid_settings_and_connection_failure_are_actionable(self):
        with patch.object(self.preview, 'bpy', SimpleNamespace(app=SimpleNamespace(online_access=False), data=bpy.data)):
            with self.assertRaisesRegex(RuntimeError, 'Online Access'):
                bpy.ops.wm.skein_action_preview(**self.props())
        self.preferences.port = 'not a port'
        with self.assertRaisesRegex(RuntimeError, 'port|Port'):
            bpy.ops.wm.skein_stop_action_preview()
        self.preferences.port = '0'
        with self.assertRaisesRegex(RuntimeError, 'port|Port'):
            bpy.ops.wm.skein_stop_action_preview()
        self.preferences.port = str(self.server.server_port)
        self.preferences.host = 'ftp://127.0.0.1'
        with self.assertRaisesRegex(RuntimeError, 'host|Host'):
            bpy.ops.wm.skein_stop_action_preview()
        self.preferences.host = 'http://127.0.0.1'
        with socket.socket() as reserved:
            reserved.bind(('127.0.0.1', 0))
            self.preferences.port = str(reserved.getsockname()[1])
            with self.assertRaisesRegex(RuntimeError, 'Start ARForge|running'):
                bpy.ops.wm.skein_stop_action_preview()
        self.assertEqual(self.requests, [])

    def test_timeout_is_bounded_and_invalid_response_is_reported(self):
        type(self).delay = 3
        started = time.monotonic()
        with self.assertRaisesRegex(RuntimeError, 'timed out|timeout'):
            bpy.ops.wm.skein_action_preview(**self.props())
        elapsed = time.monotonic() - started
        self.assertGreaterEqual(elapsed, 1.8)
        self.assertLess(elapsed, 2.8)
        type(self).delay = 0
        type(self).response = {'jsonrpc': '2.0', 'id': 1, 'result': {'ok': False}}
        with self.assertRaisesRegex(RuntimeError, 'response'):
            bpy.ops.wm.skein_action_preview(**self.props())

    def test_http_errors_and_malformed_response_are_reported(self):
        type(self).status = 503
        with self.assertRaisesRegex(RuntimeError, '503'):
            bpy.ops.wm.skein_action_preview(**self.props())
        type(self).status = 200
        type(self).response = b'not JSON'
        with self.assertRaisesRegex(RuntimeError, 'Invalid ARForge response'):
            bpy.ops.wm.skein_action_preview(**self.props())

    def test_controller_panel_buttons_capture_rna_targets(self):
        from extension.skein_panel import render_two, draw_action_diagnostics
        operations = []
        class Layout(RecordingLayout):
            def operator(self, name, **kwargs):
                props = SimpleNamespace()
                operations.append((name, kwargs, props))
                return props
            def menu(self, *_args, **_kwargs):
                pass
            def context_pointer_set(self, *_args):
                pass
            def context_string_set(self, *_args):
                pass
        layout = Layout()
        render_two(layout, self.container, hash_over_64(CHARACTER_CONTROLLER), clip_object=self.obj)
        previews = [(kwargs, props) for name, kwargs, props in operations if name == 'wm.skein_action_preview']
        self.assertEqual(len(previews), 2)
        for index, (kwargs, props) in enumerate(previews):
            self.assertEqual(kwargs['text'], 'Preview in ARForge')
            action = self.controller.actions.entries[index].value
            self.assertEqual(props.target_object, self.obj.name)
            self.assertEqual(self.obj.path_resolve(props.target_path), action)
            self.assertEqual(bpy.ops.wm.skein_action_preview(target_object=props.target_object,
                             target_path=props.target_path, action_id=props.action_id), {'FINISHED'})
        draw_action_diagnostics(layout, self.obj)
        self.assertIn('wm.skein_stop_action_preview', [name for name, _, _ in operations])
        text = ' '.join(layout.labels)
        self.assertIn('Export', text)
        self.assertIn('reload', text)
        self.assertTrue(all(len(line) <= 55 for line in layout.labels))


if __name__ == '__main__':
    sys.argv = [__file__]
    result = unittest.main(exit=False, verbosity=2).result
    if not result.wasSuccessful():
        sys.exit(1)
