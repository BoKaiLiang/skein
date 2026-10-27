"""Generic validation contract; run with Python or Blender for real export checks."""
import importlib
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:
    import bpy
except ImportError:
    bpy = None
if bpy is None:
    package = types.ModuleType('generic_skein_tests')
    package.__path__ = [str(ROOT / 'extension')]
    sys.modules[package.__name__] = package
    validation = importlib.import_module('generic_skein_tests.validation')
else:
    from extension import validation


def ref(name): return {'type': {'$ref': '#/$defs/' + name}}
def schema(kind, name, **fields):
    return dict(kind=kind, typePath=name, shortPath=name.split('::')[-1], **fields)

REGISTRY = {
    'String': schema('Value', 'String', type='string'),
    'bool': schema('Value', 'bool', type='boolean'),
    'f32': schema('Value', 'f32', type='float'),
    'demo::Motion': schema('Struct', 'demo::Motion', properties={
        'tag': ref('String'), 'clip_ref': ref('String'), 'rate': ref('f32')}),
    'VecMotion': schema('List', 'VecMotion', type='array', items=ref('demo::Motion')),
    'demo::Avatar': schema('Struct', 'demo::Avatar', reflectTypes=['Component'], properties={'motions': ref('VecMotion')}),
    'demo::Label': schema('Struct', 'demo::Label', reflectTypes=['Component'], properties={'text': ref('String')}),
}
DESCRIPTION = dict(schema='skein.authoring/v1', fields=[dict(type_path='demo::Motion', field='clip_ref', widget='animation_clip')], validation_method='demo/validate', previews=[])
DATA = {'motions': [dict(tag='first', clip_ref='Roll', rate=1.0)]}


def records():
    return [dict(name='Asset', kind='Object', components=[{'demo::Avatar': json.loads(json.dumps(DATA))}])]


class GenericValidationTests(unittest.TestCase):
    def test_renamed_types_and_fields_still_validate_clips(self):
        self.assertEqual(validation.validate_export(records(), REGISTRY, DESCRIPTION, {'Asset': ['Roll']}, lambda *_: {'diagnostics': []}), [])
        for names in ([], ['Roll', 'Roll']):
            result = validation.validate_export(records(), REGISTRY, DESCRIPTION, {'Asset': names}, lambda *_: {'diagnostics': []})
            self.assertEqual(result[0]['field'], 'demo::Avatar.motions[0].clip_ref')
            self.assertEqual(result[0]['severity'], 'ERROR')

    def test_current_records_pass_to_advertised_method_without_game_policy(self):
        observed = []
        values = records()
        values[0]['components'][0]['demo::Avatar']['motions'][0]['tag'] = 'current edited value'
        def request(method, params):
            observed.append((method, params))
            return {'diagnostics': [dict(severity='ERROR', object='Asset', field='arbitrary', message='Project rejected current values')]}
        result = validation.validate_export(values, REGISTRY, DESCRIPTION, {'Asset': ['Roll']}, request)
        self.assertEqual(observed, [('demo/validate', {'objects': values})])
        self.assertEqual(result[0]['message'], 'Project rejected current values')

    def test_declared_clip_fields_are_checked_without_implicit_project_skipping(self):
        registry = json.loads(json.dumps(REGISTRY))
        registry['demo::Avatar']['properties']['enabled'] = ref('bool')
        values = records()
        values[0]['components'][0]['demo::Avatar']['enabled'] = False
        result = validation.validate_export(values, registry, dict(DESCRIPTION, validation_method=None), {'Asset': []})
        self.assertEqual(result[0]['severity'], 'ERROR')
        self.assertEqual(result[0]['field'], 'demo::Avatar.motions[0].clip_ref')

    def test_unavailable_warns_but_malformed_response_blocks(self):
        for reply in (None, {}, {'diagnostics': [{}]}, {'diagnostics': [{'severity': 'INFO'}]}):
            result = validation.validate_export(records(), REGISTRY, DESCRIPTION, {'Asset': []}, lambda *_: reply)
            self.assertEqual([d['severity'] for d in result], ['ERROR', 'ERROR'])
        def offline(*_): raise OSError('offline')
        result = validation.validate_export(records(), REGISTRY, DESCRIPTION, {'Asset': ['Roll']}, offline)
        self.assertEqual(result[0]['severity'], 'WARNING')
        self.assertIn('unverified', result[0]['message'])

    def test_missing_method_warns_but_other_rpc_errors_block(self):
        for code, severity in ((-32601, 'WARNING'), (-32602, 'ERROR'), (-32603, 'ERROR')):
            def failed(*_): raise validation.RpcError(code, 'runtime error')
            result = validation.validate_export(records(), REGISTRY, DESCRIPTION, {'Asset': ['Roll']}, failed)
            self.assertEqual(result[0]['severity'], severity)

    def test_no_project_method_means_no_network_policy(self):
        description = dict(DESCRIPTION, validation_method=None)
        self.assertEqual(validation.validate_export([], REGISTRY, description, {}), [])
        self.assertEqual(validation.validate_export([dict(name='Mesh', kind='Mesh', components=[{'demo::Label': {'text': 'any value'}}])], REGISTRY, description, {}), [])


@unittest.skipIf(bpy is None, 'Run in Blender for actual GLB checks')
class GenericExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import extension
        from extension.op_registry_loading import process_registry
        from extension import connection
        extension.register()
        bpy.context.preferences.addons.new().module = 'extension'
        text = bpy.data.texts.get('skein-registry.json') or bpy.data.texts.new('skein-registry.json')
        text.write(json.dumps(REGISTRY))
        process_registry(bpy.context, REGISTRY)
        text = bpy.data.texts.new('skein-authoring.json')
        text.write(json.dumps(DESCRIPTION))
        cls.connection = connection
        cls.old_post, cls.old_endpoint = connection.post, connection.endpoint
        connection.endpoint = lambda *_: 'test://runtime'

    @classmethod
    def tearDownClass(cls):
        cls.connection.post, cls.connection.endpoint = cls.old_post, cls.old_endpoint

    def setUp(self):
        bpy.ops.object.select_all(action='SELECT')
        bpy.ops.object.delete(use_global=False)
        for action in list(bpy.data.actions): bpy.data.actions.remove(action)
        self.requests = []
        def post(url, method, params):
            self.requests.append((method, params))
            return {'diagnostics': []}
        self.connection.post = post
        bpy.ops.mesh.primitive_cube_add()
        self.root = bpy.context.object
        self.root.name = 'Asset'
        self.root.keyframe_insert(data_path='location', frame=1)
        self.root.location.x = 1
        self.root.keyframe_insert(data_path='location', frame=10)
        self.root.animation_data.action.name = 'Roll'
        self.component(self.root, 'demo::Avatar', DATA)
        self.directory = tempfile.TemporaryDirectory(prefix='skein-generic-export-')
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name) / 'asset.glb'

    def component(self, owner, path, value):
        from extension.op_insert_component import insert_component_data
        from extension.object_to_form import object_to_form
        from extension.property_groups import hash_over_64
        bpy.context.window_manager.selected_component = path
        insert_component_data(bpy.context, owner)
        component = owner.skein_two[-1]
        object_to_form(component, hash_over_64(path), value)
        return component

    def export(self, **settings):
        return bpy.ops.export_scene.gltf(filepath=str(self.output), export_format='GLB', export_animation_mode='ACTIONS', **settings)

    def test_local_clip_failure_preserves_last_good_file_and_editor_state(self):
        self.assertEqual(self.export(), {'FINISHED'})
        good = self.output.read_bytes()
        from extension.object_to_form import object_to_form
        object_to_form(self.root.skein_two[0], 'demo::Avatar', {'motions': [dict(tag='first', clip_ref='Missing', rate=1.0)]})
        bpy.context.scene.frame_set(7, subframe=0.25)
        with self.assertRaises(RuntimeError): self.export()
        self.assertEqual(self.output.read_bytes(), good)
        self.assertEqual((bpy.context.scene.frame_current, bpy.context.scene.frame_subframe), (7, 0.25))
        self.output.unlink()
        with self.assertRaises(RuntimeError): self.export()
        self.assertFalse(self.output.exists())

    def test_project_checks_current_values_and_actual_reachable_owners(self):
        material = bpy.data.materials.new('UsedMaterial')
        self.root.data.materials.append(material)
        self.component(material, 'demo::Label', {'text': 'used material'})
        unused = bpy.data.materials.new('UnusedMaterial')
        self.component(unused, 'demo::Label', {'text': 'excluded'})
        bpy.ops.mesh.primitive_cube_add()
        excluded = bpy.context.object
        self.component(excluded, 'demo::Label', {'text': 'excluded Object'})
        excluded.select_set(False)
        self.root.select_set(True)
        bpy.context.view_layer.objects.active = self.root
        self.export(use_selection=True)
        values = self.requests[-1][1]['objects']
        self.assertEqual({(v['name'], v['kind']) for v in values}, {('Asset', 'Object'), ('UsedMaterial', 'Material')})
        good = self.output.read_bytes()
        def reject(url, method, params):
            self.requests.append((method, params))
            return {'diagnostics': [dict(severity='ERROR', object='Asset', field='custom', message='current values rejected')]}
        self.connection.post = reject
        from extension.object_to_form import object_to_form
        changed = {'motions': [dict(tag='edited after export', clip_ref='Roll', rate=2.0)]}
        object_to_form(self.root.skein_two[0], 'demo::Avatar', changed)
        with self.assertRaisesRegex(RuntimeError, 'current values rejected'): self.export(use_selection=True)
        asset = next(v for v in self.requests[-1][1]['objects'] if v['name'] == 'Asset')
        self.assertEqual(asset['components'], [{'demo::Avatar': changed}])
        self.assertEqual(self.output.read_bytes(), good)

    def test_invalid_reflected_data_is_checked_only_when_exported(self):
        bpy.ops.mesh.primitive_cube_add()
        other = bpy.context.object
        malformed = other.skein_two.add()
        malformed.selected_type_path = 'missing::Component'
        other.select_set(False)
        self.root.select_set(True)
        bpy.context.view_layer.objects.active = self.root
        self.assertEqual(self.export(use_selection=True), {'FINISHED'})
        good = self.output.read_bytes()
        with self.assertRaisesRegex(RuntimeError, 'missing::Component'):
            self.export(use_selection=False)
        self.assertEqual(self.output.read_bytes(), good)

    def test_abort_restores_edit_mode(self):
        bpy.context.view_layer.objects.active = self.root
        bpy.ops.object.mode_set(mode='EDIT')
        try:
            with self.assertRaises(RuntimeError): self.export(export_animations=False)
            self.assertEqual(bpy.context.mode, 'EDIT_MESH')
        finally:
            bpy.ops.object.mode_set(mode='OBJECT')

    def test_malformed_remote_response_and_actual_rpc_errors_preserve_last_good(self):
        self.export()
        good = self.output.read_bytes()
        for result in ({}, {'diagnostics': [{}]}):
            self.connection.post = lambda *_: result
            with self.assertRaises(RuntimeError): self.export()
            self.assertEqual(self.output.read_bytes(), good)
        def failed(*_): raise self.connection.RpcError(-32602, 'invalid current request')
        self.connection.post = failed
        with self.assertRaises(RuntimeError): self.export()
        self.assertEqual(self.output.read_bytes(), good)

    def test_malformed_metadata_blocks_but_absent_metadata_warns(self):
        self.export()
        good = self.output.read_bytes()
        text = bpy.data.texts['skein-authoring.json']
        original = text.as_string()
        try:
            text.clear()
            text.write('{"schema":"skein.authoring/v1","fields":false}')
            with self.assertRaisesRegex(RuntimeError, 'metadata is invalid'): self.export()
            self.assertEqual(self.output.read_bytes(), good)
            bpy.data.texts.remove(text)
            self.assertEqual(self.export(), {'FINISHED'})
        finally:
            text = bpy.data.texts.get('skein-authoring.json') or bpy.data.texts.new('skein-authoring.json')
            text.clear()
            text.write(original)

    def test_unavailable_project_validator_warns_and_allows_valid_local_export(self):
        def offline(*_): raise self.connection.Unavailable('test offline')
        self.connection.post = offline
        self.assertEqual(self.export(), {'FINISHED'})
        self.assertTrue(self.output.exists())

    def test_older_exporters_that_swallow_hook_errors_still_do_not_write(self):
        from extension.gltf_export_extension import glTF2ExportUserExtension
        glTF2ExportUserExtension.is_critical = False
        try:
            with self.assertRaises(RuntimeError): self.export(export_animations=False)
            self.assertFalse(self.output.exists())
        finally:
            glTF2ExportUserExtension.is_critical = True


if __name__ == '__main__':
    sys.argv = [__file__]
    result = unittest.main(exit=False, verbosity=2).result
    if not result.wasSuccessful(): raise SystemExit(1)
