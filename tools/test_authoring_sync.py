"""Real Blender cache/sync checks, isolated from user preferences and clipboard."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import extension
from extension import authoring_sync as sync
from extension.form_to_object import get_data_from_active_editor
from extension.object_to_form import object_to_form
from extension.op_insert_component import touch_all_fields
from extension.op_registry_loading import process_registry
from extension.property_groups import hash_over_64

TYPE = 'example::Actor'
REGISTRY = {
    'String': dict(kind='Value', typePath='String', shortPath='String', type='string'),
    'f32': dict(kind='Value', typePath='f32', shortPath='f32', type='float'),
    TYPE: dict(kind='Struct', typePath=TYPE, shortPath='Actor', reflectTypes=['Component'],
        properties={'clip': {'type': {'$ref': '#/$defs/String'}}, 'speed': {'type': {'$ref': '#/$defs/f32'}}}),
}
DESCRIPTION = dict(schema='skein.authoring/v1', fields=[dict(type_path=TYPE, field='clip', widget='animation_clip')],
                   validation_method=None, previews=[])
PRESETS = {TYPE: {'default': dict(clip='Idle', speed=3.0)}}


class AuthoringSyncTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        extension.register()

    def setUp(self):
        sync.unregister()
        for text in list(bpy.data.texts):
            if text.name.startswith('skein-'):
                bpy.data.texts.remove(text)
        for obj in bpy.data.objects:
            if hasattr(obj, 'skein_two'):
                obj.skein_two.clear()
        process_registry(bpy.context, copy.deepcopy(REGISTRY))
        self.original = copy.deepcopy(REGISTRY)
        self.write('skein-registry.json', REGISTRY)
        self.write('skein-presets.json', PRESETS)
        self.write('skein-authoring.json', DESCRIPTION)
        self.write('skein-cache-info.json', dict(schema='skein.cache/v1', endpoint='http://cached:1', fetched_at='old'))
        self.obj = bpy.context.object
        component = self.obj.skein_two.add()
        component.name = 'Custom Actor'
        component.selected_type_path = TYPE
        touch_all_fields(component, hash_over_64(TYPE))
        object_to_form(component, hash_over_64(TYPE), dict(clip='Authored', speed=8.0))
        self.addCleanup(sync.unregister)
        self.addCleanup(patch.stopall)
        patch('extension.connection.endpoint', return_value='http://test:1').start()
        patch.object(sync, '_online', return_value=True).start()

    def write(self, name, value):
        text = bpy.data.texts.get(name) or bpy.data.texts.new(name)
        text.clear()
        text.write(json.dumps(value))

    def read(self, name):
        return json.loads(bpy.data.texts[name].as_string())

    def value(self):
        component = self.obj.skein_two[0]
        return get_data_from_active_editor(component, hash_over_64(TYPE))

    def pump(self):
        deadline = time.monotonic() + 3
        while sync.status()['state'] == 'syncing' and time.monotonic() < deadline:
            sync.poll()
            time.sleep(.005)
        self.assertNotEqual(sync.status()['state'], 'syncing', sync.status())

    def post(self, url, method, params):
        self.assertNotEqual(threading.get_ident(), self.main_thread)
        return copy.deepcopy({'registry.schema': self.original, 'skein/presets': PRESETS, 'skein/authoring': DESCRIPTION}[method])

    def test_sync_is_nonblocking_and_applies_only_on_main_thread(self):
        self.main_thread = threading.get_ident()
        started, release = threading.Event(), threading.Event()
        def delayed(*args):
            started.set()
            release.wait(2)
            return self.post(*args)
        with patch('extension.connection.post', side_effect=delayed):
            before = time.monotonic()
            sync.request_sync()
            self.assertLess(time.monotonic() - before, .2)
            self.assertTrue(started.wait(1))
            self.assertEqual(sync.status()['state'], 'syncing')
            release.set()
            self.pump()
        self.assertEqual(sync.status()['state'], 'ready', sync.status())
        self.assertEqual(self.value(), dict(clip='Authored', speed=8.0))
        self.assertEqual(self.read('skein-authoring.json'), DESCRIPTION)
        self.assertNotEqual(self.read('skein-cache-info.json')['fetched_at'], 'old')
        self.assertFalse(any(text.name.endswith('.001') for text in bpy.data.texts))

    def test_identical_polls_leave_saved_file_and_cached_provenance_unchanged(self):
        url = 'http://test:1'
        optional = {'skein/presets': PRESETS, 'skein/authoring': DESCRIPTION}
        sync._apply(url, self.original, optional, [])
        cached = {text.name: text.as_string() for text in bpy.data.texts}
        fetched_at = sync.status()['fetched_at']
        contacted_at = sync.status().get('last_contact_at')
        with tempfile.TemporaryDirectory(prefix='skein-clean-cache-') as directory:
            bpy.ops.wm.save_as_mainfile(filepath=str(Path(directory) / 'saved.blend'), check_existing=False)
            self.assertFalse(bpy.data.is_dirty)
            with patch.object(sync, '_write', wraps=sync._write) as write, patch.object(sync, '_rebuild', wraps=sync._rebuild) as rebuild:
                for _ in range(3):
                    sync._results.put((sync._generation, url, self.original, optional, [], None))
                    sync.poll()
                print('Saved file dirty after identical sync polls:', bpy.data.is_dirty)
                write.assert_not_called()
                rebuild.assert_not_called()
            self.assertFalse(bpy.data.is_dirty)
        self.assertEqual({text.name: text.as_string() for text in bpy.data.texts}, cached)
        self.assertEqual(sync.status()['fetched_at'], fetched_at)
        self.assertNotEqual(sync.status().get('last_contact_at'), contacted_at)
        self.assertNotIn('last_contact_at', self.read('skein-cache-info.json'))
        self.assertEqual(self.value(), dict(clip='Authored', speed=8.0))

    def test_changed_default_writes_only_changed_cache_and_provenance(self):
        url = 'http://test:1'
        optional = {'skein/presets': PRESETS, 'skein/authoring': DESCRIPTION}
        sync._apply(url, self.original, optional, [])
        fetched_at = sync.status()['fetched_at']
        changed = {'skein/presets': {TYPE: {'default': dict(clip='New default', speed=4.0)}},
                   'skein/authoring': DESCRIPTION}
        with patch.object(sync, '_write', wraps=sync._write) as write, patch.object(sync, '_rebuild', wraps=sync._rebuild) as rebuild:
            sync._apply(url, self.original, changed, [])
            self.assertEqual([call.args[0] for call in write.call_args_list], ['skein-presets.json', 'skein-cache-info.json'])
            rebuild.assert_not_called()
        self.assertNotEqual(sync.status()['fetched_at'], fetched_at)
        self.assertEqual(self.read('skein-presets.json'), changed['skein/presets'])
        self.assertEqual(self.value(), dict(clip='Authored', speed=8.0))

    def test_identical_dataset_from_new_endpoint_updates_only_provenance(self):
        optional = {'skein/presets': PRESETS, 'skein/authoring': DESCRIPTION}
        sync._apply('http://test:1', self.original, optional, [])
        fetched_at = sync.status()['fetched_at']
        with patch.object(sync, '_write', wraps=sync._write) as write, patch.object(sync, '_rebuild', wraps=sync._rebuild) as rebuild:
            sync._apply('http://other:2', self.original, optional, [])
            self.assertEqual([call.args[0] for call in write.call_args_list], ['skein-cache-info.json'])
            rebuild.assert_not_called()
        self.assertNotEqual(sync.status()['fetched_at'], fetched_at)
        self.assertEqual(self.read('skein-cache-info.json')['endpoint'], 'http://other:2')

    def test_offline_bootstrap_restores_cached_forms_and_metadata(self):
        with patch.object(sync, '_online', return_value=False), patch('extension.connection.post') as post:
            sync.bootstrap()
        post.assert_not_called()
        self.assertEqual(sync.status()['state'], 'offline')
        self.assertEqual(sync.status()['endpoint'], 'http://cached:1')
        self.assertEqual(sync.description(), DESCRIPTION)
        self.assertEqual(self.value(), dict(clip='Authored', speed=8.0))
        self.assertEqual(self.read('skein-presets.json'), PRESETS)

    def test_incompatible_used_schema_rejected_before_cache_or_value_changes(self):
        self.main_thread = threading.get_ident()
        self.original['f32']['type'] = 'string'
        before = {text.name: text.as_string() for text in bpy.data.texts}
        with patch('extension.connection.post', side_effect=self.post):
            sync.request_sync()
            self.pump()
        self.assertEqual(sync.status()['state'], 'error')
        self.assertIn('f32', sync.status()['message'])
        self.assertEqual({text.name: text.as_string() for text in bpy.data.texts}, before)
        self.assertEqual(self.value(), dict(clip='Authored', speed=8.0))

    def test_new_unused_type_refresh_preserves_existing_values(self):
        self.main_thread = threading.get_ident()
        self.original['extra::Type'] = dict(kind='Struct', typePath='extra::Type', shortPath='Type',
                                          reflectTypes=['Component'], properties={})
        with patch('extension.connection.post', side_effect=self.post):
            sync.request_sync()
            self.pump()
        self.assertEqual(sync.status()['state'], 'ready', sync.status())
        self.assertIn('extra::Type', self.read('skein-registry.json'))
        self.assertEqual(self.value(), dict(clip='Authored', speed=8.0))

    def test_generation_discards_worker_result_after_new_file_bootstrap(self):
        self.main_thread = threading.get_ident()
        started, release = threading.Event(), threading.Event()
        def delayed(*args):
            started.set()
            release.wait(2)
            return self.post(*args)
        with patch('extension.connection.post', side_effect=delayed):
            sync.request_sync()
            self.assertTrue(started.wait(1))
            with patch.object(sync, '_online', return_value=False):
                sync.bootstrap()
            before = self.read('skein-cache-info.json')
            release.set()
            time.sleep(.05)
            sync.poll()
        self.assertEqual(self.read('skein-cache-info.json'), before)
        self.assertEqual(sync.status()['state'], 'offline')

    def test_optional_methods_missing_retains_only_compatible_cache(self):
        from extension.connection import RpcError
        self.main_thread = threading.get_ident()
        def missing(url, method, params):
            if method == 'registry.schema':
                return self.post(url, method, params)
            raise RpcError(-32601, 'Method not found')
        with patch('extension.connection.post', side_effect=missing):
            sync.request_sync()
            self.pump()
        self.assertEqual(sync.status()['state'], 'unverified')
        self.assertEqual(sync.description(), DESCRIPTION)
        self.assertEqual(self.read('skein-presets.json'), PRESETS)
        self.assertIn('unverified', sync.status()['message'].lower())

    def test_stale_invalid_description_is_not_applied_to_new_registry(self):
        from extension.connection import RpcError
        self.obj.skein_two.clear()
        self.main_thread = threading.get_ident()
        self.original[TYPE]['properties'].pop('clip')
        def missing(url, method, params):
            if method == 'registry.schema':
                return self.post(url, method, params)
            raise RpcError(-32601, 'Method not found')
        with patch('extension.connection.post', side_effect=missing):
            sync.request_sync()
            self.pump()
        self.assertEqual(sync.description()['fields'], [])
        self.assertEqual(self.read('skein-authoring.json')['fields'], [])
        self.assertEqual(sync.status()['state'], 'unverified')

    def test_cached_authoring_survives_save_and_reopen_offline(self):
        name = self.obj.name
        with tempfile.TemporaryDirectory(prefix='skein-sync-') as directory:
            path = str(Path(directory) / 'cached.blend')
            bpy.ops.wm.save_as_mainfile(filepath=path, check_existing=False)
            object_to_form(self.obj.skein_two[0], hash_over_64(TYPE), dict(clip='Temporary', speed=1.0))
            with patch.object(sync, '_online', return_value=False), patch('extension.connection.post') as post:
                bpy.ops.wm.open_mainfile(filepath=path)
            post.assert_not_called()
        self.obj = bpy.data.objects[name]
        self.assertEqual(self.value(), dict(clip='Authored', speed=8.0))
        self.assertEqual(sync.description(), DESCRIPTION)
        self.assertEqual(self.read('skein-presets.json'), PRESETS)
        self.assertFalse(any(text.name.endswith('.001') for text in bpy.data.texts))

    def test_unknown_existing_component_blocks_refresh_without_touching_raw_data(self):
        self.main_thread = threading.get_ident()
        unknown = self.obj.skein_two.add()
        unknown.selected_type_path = 'unknown::Opaque'
        unknown['raw_original'] = {'keep': [1, 2, 3]}
        raw = sync._raw(unknown)
        with patch('extension.connection.post', side_effect=self.post):
            sync.request_sync()
            self.pump()
        self.assertEqual(sync.status()['state'], 'error')
        self.assertEqual(sync._raw(self.obj.skein_two[1]), raw)
        self.assertEqual(self.value(), dict(clip='Authored', speed=8.0))

    def test_network_failure_keeps_cached_forms_and_provenance(self):
        from extension.connection import Unavailable
        with patch('extension.connection.post', side_effect=Unavailable('No runtime')):
            sync.request_sync()
            self.pump()
        self.assertEqual(sync.status()['state'], 'offline')
        self.assertEqual(self.value(), dict(clip='Authored', speed=8.0))
        self.assertEqual(self.read('skein-cache-info.json')['fetched_at'], 'old')

    def test_bad_cached_description_is_preserved_as_raw_text(self):
        invalid = dict(DESCRIPTION, fields=[dict(type_path=TYPE, field='absent', widget='animation_clip')])
        self.write('skein-authoring.json', invalid)
        raw = bpy.data.texts['skein-authoring.json'].as_string()
        with patch.object(sync, '_online', return_value=False):
            sync.bootstrap()
        self.assertEqual(bpy.data.texts['skein-authoring.json'].as_string(), raw)
        self.assertEqual(sync.description()['fields'], [])
        self.assertIn('incompatible', sync.status()['message'])

    def test_reflect_types_order_does_not_reject_compatible_refresh(self):
        current = copy.deepcopy(REGISTRY)
        current[TYPE]['reflectTypes'] = ['Component', 'Default']
        process_registry(bpy.context, current)
        self.original[TYPE]['reflectTypes'] = ['Default', 'Component']
        self.main_thread = threading.get_ident()
        with patch('extension.connection.post', side_effect=self.post):
            sync.request_sync()
            self.pump()
        self.assertEqual(sync.status()['state'], 'ready', sync.status())
        self.assertEqual(self.value(), dict(clip='Authored', speed=8.0))

    def test_new_list_field_initializes_empty_and_preserves_old_fields(self):
        self.main_thread = threading.get_ident()
        self.original['VecString'] = dict(kind='List',typePath='VecString',shortPath='VecString',type='array',items={'type':{'$ref':'#/$defs/String'}})
        self.original[TYPE]['properties']['actions'] = {'type':{'$ref':'#/$defs/VecString'}}
        self.original[TYPE]['required'] = ['actions']
        with patch('extension.connection.post', side_effect=self.post):
            sync.request_sync()
            self.pump()
        self.assertEqual(sync.status()['state'], 'ready', sync.status())
        self.assertEqual(self.value(), dict(clip='Authored', speed=8.0, actions=[]))

    def test_new_option_field_initializes_none_and_preserves_old_fields(self):
        self.main_thread = threading.get_ident()
        option='core::option::Option<String>'
        self.original[option] = dict(kind='Enum',typePath=option,shortPath='Option<String>',type='object',modulePath='core::option',oneOf=[dict(shortPath='None',typePath=option+'::None'),dict(kind='Tuple',typePath=option+'::Some',shortPath='Some',type='array',prefixItems=[{'type':{'$ref':'#/$defs/String'}}])])
        self.original[TYPE]['properties']['optional'] = {'type':{'$ref':'#/$defs/'+option}}
        with patch('extension.connection.post', side_effect=self.post):
            sync.request_sync()
            self.pump()
        self.assertEqual(sync.status()['state'], 'ready', sync.status())
        self.assertEqual(self.value(), dict(clip='Authored', speed=8.0, optional=None))

    def test_runtime_component_metadata_is_not_an_authored_schema_change(self):
        self.main_thread = threading.get_ident()
        self.original[TYPE]['componentInfo'] = dict(isSendAndSync=True, mutable=True, storageType='Table')
        before = self.value()
        with patch('extension.connection.post', side_effect=self.post):
            sync.request_sync()
            self.pump()
        self.assertEqual(sync.status()['state'], 'ready', sync.status())
        self.assertEqual(self.value(), before)
        self.assertEqual(self.read('skein-registry.json')[TYPE]['componentInfo'], self.original[TYPE]['componentInfo'])

    def test_required_field_order_is_not_an_authored_schema_change(self):
        before = copy.deepcopy(REGISTRY)
        before[TYPE]['required'] = ['clip', 'speed']
        process_registry(bpy.context, before)
        self.write('skein-registry.json', before)
        self.original[TYPE]['required'] = ['speed', 'clip']
        self.main_thread = threading.get_ident()
        with patch('extension.connection.post', side_effect=self.post):
            sync.request_sync()
            self.pump()
        self.assertEqual(sync.status()['state'], 'ready', sync.status())
        self.assertEqual(self.value(), dict(clip='Authored', speed=8.0))

    def test_real_fields_named_like_metadata_still_require_migration(self):
        from extension.op_registry_loading import validate_registry_refresh
        for name in ('description', 'shortPath', 'componentInfo'):
            changed = copy.deepcopy(REGISTRY)
            changed[TYPE]['properties'][name] = {'type': {'$ref': '#/$defs/String'}}
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_registry_refresh(bpy.context, changed)
        self.assertEqual(self.value(), dict(clip='Authored', speed=8.0))

    def test_malformed_registry_is_rejected_without_touching_cache(self):
        from extension.op_registry_loading import validate_registry
        for mutation in (
            lambda value: value[TYPE].update(kind='Anything'),
            lambda value: value[TYPE].update(properties=[]),
            lambda value: value[TYPE]['properties'].update(clip={'type': {'$ref': 12}}),
            lambda value: value[TYPE].update(kind='List', items=None),
            lambda value: value[TYPE].update(kind='Enum', type='object', oneOf=['bad']),
            lambda value: value[TYPE].update(kind='TupleStruct', prefixItems=None),
        ):
            bad = copy.deepcopy(REGISTRY)
            mutation(bad)
            with self.assertRaises(ValueError):
                validate_registry(bad)
        self.main_thread = threading.get_ident()
        self.original[TYPE]['properties'] = []
        before = {text.name: text.as_string() for text in bpy.data.texts}
        with patch('extension.connection.post', side_effect=self.post):
            sync.request_sync()
            self.pump()
        self.assertEqual(sync.status()['state'], 'error')
        self.assertEqual({text.name: text.as_string() for text in bpy.data.texts}, before)
        self.assertEqual(self.value(), dict(clip='Authored', speed=8.0))

    def test_failed_rebuild_rolls_back_raw_and_supported_values(self):
        self.main_thread = threading.get_ident()
        self.original['extra::Type'] = dict(kind='Struct', typePath='extra::Type', shortPath='Type', properties={})
        self.obj.skein_two[0]['opaque_extra'] = {'keep': [1, 2, 3]}
        before = sync._raw(self.obj.skein_two[0])
        def damaging_rebuild(context, registry):
            process_registry(context, registry)
            if 'extra::Type' in registry:
                object_to_form(self.obj.skein_two[0], hash_over_64(TYPE), dict(clip='Lost', speed=0.0))
                self.obj.skein_two[0]['opaque_extra'] = {'keep': [99]}
        with patch('extension.connection.post', side_effect=self.post), patch.object(sync, 'process_registry', side_effect=damaging_rebuild):
            sync.request_sync()
            self.pump()
        self.assertEqual(sync.status()['state'], 'error')
        self.assertEqual(sync._raw(self.obj.skein_two[0]), before)
        self.assertEqual(self.value(), dict(clip='Authored', speed=8.0))
        self.assertEqual(self.read('skein-registry.json'), REGISTRY)
        self.assertEqual(json.loads(bpy.context.window_manager.skein.registry), REGISTRY)

    def test_nonfinite_response_preserves_forms_and_all_caches(self):
        self.main_thread = threading.get_ident()
        self.original['extra::Type'] = dict(kind='Value', typePath='extra::Type', shortPath='Type', type='float', minimum=float('inf'))
        before = {text.name: text.as_string() for text in bpy.data.texts}
        with patch('extension.connection.post', side_effect=self.post):
            sync.request_sync()
            self.pump()
        self.assertEqual(sync.status()['state'], 'error')
        self.assertEqual({text.name: text.as_string() for text in bpy.data.texts}, before)
        self.assertEqual(json.loads(bpy.context.window_manager.skein.registry), REGISTRY)
        self.assertEqual(self.value(), dict(clip='Authored', speed=8.0))

    def test_switch_endpoint_or_disable_online_ignores_pending_response(self):
        for online, endpoint in [(False, 'http://127.0.0.1:15702'), (True, 'http://different:15702')]:
            with self.subTest(online=online, endpoint=endpoint):
                before = {text.name: text.as_string() for text in bpy.data.texts}
                sync._pending = sync._generation
                sync._status['state'] = 'syncing'
                sync._results.put((sync._generation, 'http://127.0.0.1:15702', REGISTRY,
                                   {'skein/authoring': DESCRIPTION, 'skein/presets': PRESETS}, [], None))
                with patch.object(sync, '_online', return_value=online), patch('extension.connection.endpoint', return_value=endpoint):
                    sync.poll()
                self.assertEqual(sync.status()['state'], 'offline')
                self.assertEqual({text.name: text.as_string() for text in bpy.data.texts}, before)

    def test_worker_parse_exception_releases_pending_slot_and_preserves_cache(self):
        before = {text.name: text.as_string() for text in bpy.data.texts}
        with patch('extension.connection.post', side_effect=RecursionError('excessive nesting')):
            sync.request_sync()
            self.pump()
        self.assertIsNone(sync._pending)
        self.assertEqual({text.name: text.as_string() for text in bpy.data.texts}, before)
        self.assertIn('excessive nesting', sync.status()['message'])

    def test_real_registry_allows_unit_tuple_and_unregistered_opaque_references(self):
        from extension.op_registry_loading import validate_registry
        registry = copy.deepcopy(REGISTRY)
        registry['Unit'] = dict(kind='TupleStruct',typePath='Unit',shortPath='Unit',type='array')
        registry['Opaque'] = dict(kind='Struct',typePath='Opaque',shortPath='Opaque',properties={'hidden':{'type':{'$ref':'#/$defs/core::any::TypeId'}}})
        validate_registry(registry)

    def test_unrepresentable_default_disables_only_its_reset_and_keeps_sync_usable(self):
        self.main_thread = threading.get_ident()
        self.original['BadLimit'] = dict(kind='TupleStruct',typePath='BadLimit',shortPath='BadLimit',reflectTypes=['Component'],prefixItems=[{'type':{'$ref':'#/$defs/f32'}}])
        def request(url, method, params):
            if method == 'skein/presets':
                return dict(PRESETS, BadLimit={'default':None})
            return self.post(url, method, params)
        with patch('extension.connection.post', side_effect=request):
            sync.request_sync()
            self.pump()
        self.assertEqual(sync.status()['state'], 'ready', sync.status())
        self.assertEqual(self.read('skein-presets.json')[TYPE], PRESETS[TYPE])
        self.assertNotIn('BadLimit', self.read('skein-presets.json'))
        from extension.component_values import default_reason
        self.assertIn('cannot be represented losslessly', default_reason('BadLimit'))
        before = {text.name: text.as_string() for text in bpy.data.texts}
        with patch.object(sync, '_write', wraps=sync._write) as write:
            sync._apply('http://test:1', self.original,
                        {'skein/presets': dict(PRESETS, BadLimit={'default': None}), 'skein/authoring': DESCRIPTION}, [])
            write.assert_not_called()
        self.assertEqual({text.name: text.as_string() for text in bpy.data.texts}, before)
        self.assertIn('cannot be represented losslessly', default_reason('BadLimit'))
        self.assertEqual(self.value(), dict(clip='Authored', speed=8.0))

    def test_unregister_invalidates_pending_results(self):
        self.main_thread = threading.get_ident()
        started, release = threading.Event(), threading.Event()
        def delayed(*args):
            started.set()
            release.wait(2)
            return self.post(*args)
        with patch('extension.connection.post', side_effect=delayed):
            sync.request_sync()
            self.assertTrue(started.wait(1))
            sync.unregister()
            release.set()
            time.sleep(.05)
            sync.poll()
        self.assertEqual(self.read('skein-cache-info.json')['fetched_at'], 'old')


if __name__ == '__main__':
    sys.argv = [__file__] + (sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
    result = unittest.main(exit=False).result
    if not result.wasSuccessful():
        raise SystemExit(1)
