"""Player marker checks: python3 tools/test_player_selection.py; Blender for real exports."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('player_selection', ROOT / 'extension/player_selection.py')
selection = importlib.util.module_from_spec(spec)
spec.loader.exec_module(selection)


class SelectionTests(unittest.TestCase):
    def test_one_enabled_and_passive_assets(self):
        self.assertEqual(selection.validate_player_selection([
            ('Hero', True, [{'enabled': True}]), ('NPC', True, [])]), [])
        self.assertEqual(selection.validate_player_selection([]), [])
        for records in ([('Hero', True, [])], [('NPC', True, [{'enabled': False}])],
                        [('A', True, []), ('B', True, [])]):
            self.assertEqual({d['severity'] for d in selection.validate_player_selection(records)}, {'WARNING'})

    def test_invalid_markers_and_duplicate_controllers(self):
        for markers in ([{}], [{'enabled': 1}], [None], [{'enabled': True, 'extra': 1}],
                        [{'enabled': True}, {'enabled': False}]):
            self.assertTrue(any(d['severity'] == 'ERROR' for d in selection.validate_player_selection([
                ('Hero', True, markers)])))
        for enabled in (True, False):
            self.assertTrue(any(d['severity'] == 'ERROR' for d in selection.validate_player_selection([
                ('Mesh', False, [{'enabled': enabled}])])))
        diagnostics = selection.validate_player_selection([
            ('Hero', True, [{'enabled': True}]), ('Other', True, [{'enabled': True}])])
        self.assertIn('Hero', diagnostics[0]['message'])
        self.assertIn('Other', diagnostics[0]['message'])


try:
    import bpy
except ImportError:
    bpy = None


@unittest.skipIf(bpy is None, 'Run in Blender for actual GLB export checks')
class ExportSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(ROOT))
        import extension
        from extension.op_registry_loading import process_registry
        extension.register()
        bpy.context.preferences.addons.new().module = 'extension'
        def schema(kind, name, **fields):
            return dict(kind=kind, typePath=name, shortPath=name.split('::')[-1], **fields)
        def ref(name):
            return {'type': {'$ref': '#/$defs/' + name}}
        registry = {
            'bool': schema('Value', 'bool', type='boolean'),
            'String': schema('Value', 'String', type='string'),
            'VecString': schema('List', 'VecString', type='array', items=ref('String')),
            selection.CHARACTER_CONTROLLER: schema('Struct', selection.CHARACTER_CONTROLLER,
                reflectTypes=['Component'], properties={'actions': ref('VecString')}),
            selection.PLAYER_CONTROLLED: schema('Struct', selection.PLAYER_CONTROLLED,
                reflectTypes=['Component'], properties={'enabled': ref('bool')}),
        }
        bpy.data.texts.new('skein-registry.json').write(json.dumps(registry))
        process_registry(bpy.context, registry)
        cls.registry = registry

    def setUp(self):
        bpy.ops.object.select_all(action='SELECT')
        bpy.ops.object.delete(use_global=False)
        self.directory = tempfile.TemporaryDirectory(prefix='skein-player-selection-')
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name) / 'scene.glb'

    def component(self, owner, type_path, data=None):
        from extension.op_insert_component import insert_component_data
        from extension.object_to_form import object_to_form
        from extension.property_groups import hash_over_64
        bpy.context.window_manager.selected_component = type_path
        insert_component_data(bpy.context, owner)
        component = owner.skein_two[-1]
        if data is not None:
            object_to_form(component, hash_over_64(type_path), data)
        return component

    def character(self, name, marker=None):
        bpy.ops.mesh.primitive_cube_add()
        obj = bpy.context.object
        obj.name = name
        self.component(obj, selection.CHARACTER_CONTROLLER, {'actions': []})
        if marker is not None:
            self.component(obj, selection.PLAYER_CONTROLLED, {'enabled': marker})
        return obj

    def export(self, **settings):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = bpy.ops.export_scene.gltf(filepath=str(self.output), export_format='GLB', **settings)
        self.assertEqual(result, {'FINISHED'})
        return output.getvalue()

    def test_insert_marker_defaults_true(self):
        from extension.form_to_object import get_data_from_active_editor
        from extension.property_groups import hash_over_64
        hero = self.character('Hero')
        marker = self.component(hero, selection.PLAYER_CONTROLLED)
        self.assertEqual(get_data_from_active_editor(marker, hash_over_64(selection.PLAYER_CONTROLLED)), {'enabled': True})

    def test_panel_explains_marker_and_local_status(self):
        from extension.skein_panel import draw_player_selection
        from tools.test_lists import RecordingLayout
        hero = self.character('Hero', True)
        layout = RecordingLayout()
        draw_player_selection(layout, hero)
        labels = ' '.join(layout.labels)
        self.assertIn('Insert Component', labels)
        self.assertIn('same Object', labels)
        self.assertIn('false opts out', labels)
        self.assertIn('player candidate', labels)
        self.assertEqual(layout.operators, [])
        for marker in (None, False):
            other = self.character('Other', marker)
            layout = RecordingLayout()
            draw_player_selection(layout, other)
            labels = ' '.join(layout.labels)
            self.assertIn('not marked for player control', labels)
            self.assertNotIn('in this export', labels)

    def test_two_controllers_one_marker_exports(self):
        self.character('Hero', True)
        self.character('NPC')
        self.export()
        self.assertTrue(self.output.is_file())
        blob = self.output.read_bytes()
        document = json.loads(blob[20:20 + int.from_bytes(blob[12:16], 'little')])
        components = [component for node in document['nodes'] for component in node.get('extras', {}).get('skein', [])]
        self.assertEqual(sum(selection.CHARACTER_CONTROLLER in component for component in components), 2)
        self.assertEqual([component[selection.PLAYER_CONTROLLED] for component in components if selection.PLAYER_CONTROLLED in component], [{'enabled': True}])

    def test_two_markers_abort_and_preserve_good_glb(self):
        self.character('Hero', True)
        other = self.character('NPC')
        self.export()
        good = self.output.read_bytes()
        self.component(other, selection.PLAYER_CONTROLLED, {'enabled': True})
        with self.assertRaisesRegex(RuntimeError, 'Hero.*NPC|NPC.*Hero'):
            self.export()
        self.assertEqual(self.output.read_bytes(), good)

    def test_malformed_marker_from_incompatible_registry_aborts(self):
        from extension.op_registry_loading import process_registry
        malformed = json.loads(json.dumps(self.registry))
        malformed[selection.PLAYER_CONTROLLED]['properties']['enabled'] = {'type': {'$ref': '#/$defs/String'}}
        process_registry(bpy.context, malformed)
        try:
            hero = self.character('Hero')
            from extension.object_to_form import object_to_form
            from extension.op_insert_component import touch_all_fields
            from extension.property_groups import hash_over_64
            marker = hero.skein_two.add()
            marker.selected_type_path = selection.PLAYER_CONTROLLED
            key = hash_over_64(selection.PLAYER_CONTROLLED)
            touch_all_fields(marker, key)
            object_to_form(marker, key, {'enabled': 'true'})
            with self.assertRaises(RuntimeError):
                self.export()
            self.assertFalse(self.output.exists())
        finally:
            process_registry(bpy.context, self.registry)

    def test_duplicate_marker_aborts(self):
        hero = self.character('Hero', True)
        self.component(hero, selection.PLAYER_CONTROLLED, {'enabled': False})
        with self.assertRaises(RuntimeError):
            self.export()
        self.assertFalse(self.output.exists())

    def test_marker_requires_same_object_controller_even_when_false(self):
        hero = self.character('Hero', True)
        for owner in (hero.data, bpy.context.scene):
            with self.subTest(owner=owner.name):
                self.component(owner, selection.PLAYER_CONTROLLED, {'enabled': False})
                with self.assertRaises(RuntimeError):
                    self.export()
                owner.skein_two.clear()
        bpy.ops.object.empty_add()
        self.component(bpy.context.object, selection.PLAYER_CONTROLLED, {'enabled': False})
        with self.assertRaises(RuntimeError):
            self.export()

    def test_no_enabled_marker_warns_but_exports(self):
        self.character('Legacy')
        self.assertIn('WARNING', self.export())
        self.character('NPC', False)
        self.assertIn('WARNING', self.export())

    def test_camera_only_and_static_assets_export(self):
        bpy.ops.object.camera_add()
        self.export()
        bpy.ops.mesh.primitive_cube_add()
        self.export()

    def test_excluded_markers_do_not_affect_selection_or_next_export(self):
        hero = self.character('Hero', True)
        other = self.character('Other', True)
        other.select_set(False)
        hero.select_set(True)
        bpy.context.view_layer.objects.active = hero
        self.export(use_selection=True)
        with self.assertRaises(RuntimeError):
            self.export()
        self.export(use_selection=True)

    def test_other_scene_is_checked_only_when_exported(self):
        self.character('Hero', True)
        active = bpy.context.scene
        other = bpy.data.scenes.new('Other scene')
        self.addCleanup(lambda: bpy.data.scenes.remove(other))
        stray = bpy.data.objects.new('Stray marker', None)
        other.collection.objects.link(stray)
        self.component(stray, selection.PLAYER_CONTROLLED, {'enabled': False})
        self.export(use_active_scene=True)
        with self.assertRaises(RuntimeError):
            self.export(use_active_scene=False)
        self.assertIs(bpy.context.scene, active)
        self.export(use_active_scene=True)

    def test_nonselected_controllers_still_validate_actions(self):
        self.character('Hero', True)
        npc = self.character('NPC')
        from extension.object_to_form import object_to_form
        from extension.property_groups import hash_over_64
        object_to_form(npc.skein_two[0], hash_over_64(selection.CHARACTER_CONTROLLER), {'actions': ['invalid']})
        with self.assertRaisesRegex(RuntimeError, 'NPC'):
            self.export()


if __name__ == '__main__':
    if bpy is not None:
        sys.argv = [__file__] + (sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
    result = unittest.main(exit=False).result
    if not result.wasSuccessful():
        raise SystemExit(1)
