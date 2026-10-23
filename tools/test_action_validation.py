"""Pure tests: python3 tools/test_action_validation.py (no Blender required)."""
import importlib.util
import math
from pathlib import Path
import sys
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('action_validation', Path(__file__).parents[1] / 'extension/action_validation.py')
validation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validation)


def controller():
    return {
        'bindings': dict(forward='KeyW', backward='KeyS', left='KeyA', right='KeyD', jump='Space'),
        'animations': {'idle': 'Idle', 'move_clip': 'Run', 'jump': 'Jump'},
        'actions': [dict(id='roll', clip='Roll', binding='ShiftLeft', looped=False, speed=1.0)],
    }


class ActionValidationTests(unittest.TestCase):
    def test_valid_and_old_controller(self):
        data = controller()
        self.assertEqual(validation.validate_character_controller(data, ['Idle', 'Run', 'Jump', 'Roll']), [])
        del data['actions']
        self.assertEqual(validation.validate_character_controller(data, ['Idle', 'Run', 'Jump']), [])

    def test_optional_state_mapping_and_legacy_fallback(self):
        data = controller()
        data['actions'][0]['state'] = 'Idle'
        self.assertEqual(validation.validate_character_controller(data, ['Run', 'Jump', 'Roll']), [])
        data['actions'][0]['state'] = None
        self.assertIn('animations.idle', {d['field'] for d in validation.validate_character_controller(data, ['Run', 'Jump', 'Roll'])})
        data['actions'][0]['state'] = 'Fly'
        self.assertIn('actions[0].state', {d['field'] for d in validation.validate_character_controller(data)})
        data['actions'][0]['state'] = 'Idle'
        data['actions'].append(dict(id='rest', clip='Roll', binding=None, looped=True, speed=1, state='Idle'))
        self.assertIn('actions[1].state', {d['field'] for d in validation.validate_character_controller(data)})
        data['actions'][0]['clip'] = 'Missing'
        self.assertIn('animations.idle', {d['field'] for d in validation.validate_character_controller(data, ['Run', 'Jump', 'Roll'])})

    def test_overridden_legacy_clips_still_require_valid_names(self):
        for clip in ('', '  ', 'bad\tclip', 'x' * 257):
            data = controller()
            data['actions'][0]['state'] = 'Idle'
            data['animations']['idle'] = clip
            self.assertIn('animations.idle', {d['field'] for d in validation.validate_character_controller(data, ['Run', 'Jump', 'Roll'])})

    def test_field_diagnostics(self):
        data = controller()
        data['actions'][0].update(id='Idle', clip='Missing', binding='Space', speed=math.nan)
        fields = {d['field'] for d in validation.validate_character_controller(data, ['Idle', 'Run', 'Jump'])}
        self.assertEqual(fields, {'actions[0].id', 'actions[0].clip', 'actions[0].binding', 'actions[0].speed'})

    def test_id_and_binding_duplicates_and_reserved(self):
        data = controller()
        data['actions'] *= 2
        diagnostics = validation.validate_character_controller(data)
        self.assertIn('actions[1].id', {d['field'] for d in diagnostics})
        self.assertIn('actions[1].binding', {d['field'] for d in diagnostics})
        for key in ('Escape', 'F5', 'F6', 'F7', 'F8', 'F9', 'Unidentified'):
            data = controller()
            data['actions'][0]['binding'] = key
            self.assertTrue(validation.validate_character_controller(data))
        for action_id in ('idle', 'move', 'jump', 'a' * 65, '', 'two words'):
            data = controller()
            data['actions'][0]['id'] = action_id
            self.assertTrue(validation.validate_character_controller(data))

    def test_clip_names_and_speed_bounds(self):
        for clip in ('', '  ', '\tIdle', '\u0085Idle', 'x' * 257, '模' * 86):
            data = controller()
            data['actions'][0]['clip'] = clip
            self.assertTrue(validation.validate_character_controller(data), clip)
        for speed in (0, -1, 10.1, math.inf, math.nan, True, '1'):
            data = controller()
            data['actions'][0]['speed'] = speed
            self.assertTrue(validation.validate_character_controller(data), speed)
        data = controller()
        data['actions'][0].update(speed=10, clip=' Roll ')
        self.assertEqual(validation.validate_character_controller(data, ['Idle', 'Run', 'Jump', ' Roll ']), [])

    def test_limit_optional_binding_and_legacy_alias(self):
        data = controller()
        data['actions'][0]['binding'] = None
        data['animations']['move'] = data['animations'].pop('move_clip')
        self.assertEqual(validation.validate_character_controller(data), [])
        data['actions'] = [dict(id=f'action_{i}', clip='Roll', binding=None, looped=True, speed=1) for i in range(65)]
        self.assertIn('actions', {d['field'] for d in validation.validate_character_controller(data)})

    def test_required_action_fields(self):
        for field in ('id', 'clip', 'binding', 'looped', 'speed'):
            data = controller()
            del data['actions'][0][field]
            self.assertIn(f'actions[0].{field}', {d['field'] for d in validation.validate_character_controller(data)})

    def test_duplicate_export_names_and_legacy_missing(self):
        diagnostics = validation.validate_character_controller(controller(), ['Idle', 'Run', 'Roll', 'Roll'])
        self.assertEqual({d['field'] for d in diagnostics}, {'actions[0].clip', 'animations.jump'})


try:
    import bpy
except ImportError:
    bpy = None


@unittest.skipIf(bpy is None, 'Run in Blender for actual GLB export checks')
class ExportValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(Path(__file__).parents[1]))
        import extension
        from extension import gltf_export_extension
        extension.register()
        bpy.context.preferences.addons.new().module = 'extension'
        cls.data = {}
        cls.original_reader = gltf_export_extension.get_object_controller_data
        gltf_export_extension.get_object_controller_data = lambda obj: cls.data.get(obj.name)
        cls.gltf_module = gltf_export_extension

    @classmethod
    def tearDownClass(cls):
        cls.gltf_module.get_object_controller_data = cls.original_reader

    def setUp(self):
        self.data.clear()
        bpy.ops.object.select_all(action='SELECT')
        bpy.ops.object.delete(use_global=False)
        for action in list(bpy.data.actions):
            bpy.data.actions.remove(action)
        bpy.ops.mesh.primitive_cube_add()
        self.cube = bpy.context.object
        self.cube.name = 'Character'
        self.cube.location.x = 0
        self.cube.keyframe_insert(data_path='location', frame=1)
        self.cube.location.x = 1
        self.cube.keyframe_insert(data_path='location', frame=10)
        self.cube.animation_data.action.name = 'Roll'
        data = controller()
        data['animations'] = None
        self.data[self.cube.name] = data
        self.directory = tempfile.TemporaryDirectory(prefix='skein-validation-')
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name) / 'character.glb'

    def export(self, **settings):
        return bpy.ops.export_scene.gltf(filepath=str(self.output), export_format='GLB',
                                         export_animation_mode='ACTIONS', **settings)

    def test_valid_export_and_missing_clip_aborts_before_write(self):
        self.assertEqual(self.export(), {'FINISHED'})
        self.assertTrue(self.output.is_file())
        original = self.output.read_bytes()
        self.data[self.cube.name]['actions'][0]['clip'] = 'Missing'
        bpy.context.scene.frame_set(7)
        with self.assertRaises(RuntimeError):
            self.export()
        self.assertEqual(self.output.read_bytes(), original, 'A failed validation must preserve an existing GLB')
        self.assertEqual(bpy.context.scene.frame_current, 7)
        self.output.unlink()
        with self.assertRaises(RuntimeError):
            self.export()
        self.assertFalse(self.output.exists(), 'A failed validation must not produce a broken GLB')

    def test_export_settings_excluding_animations_aborts(self):
        with self.assertRaises(RuntimeError):
            self.export(export_animations=False)
        self.assertFalse(self.output.exists())

    def test_exporters_without_critical_hook_support_still_abort(self):
        self.data[self.cube.name]['actions'][0]['clip'] = 'Missing'
        extension_type = self.gltf_module.glTF2ExportUserExtension
        extension_type.is_critical = False
        try:
            with self.assertRaises(RuntimeError):
                self.export()
            self.assertFalse(self.output.exists())
        finally:
            extension_type.is_critical = True

    def test_invalid_excluded_object_does_not_block_selected_export(self):
        bpy.ops.mesh.primitive_cube_add()
        excluded = bpy.context.object
        self.data[excluded.name] = {'actions': [dict(id='invalid')]}
        excluded.select_set(False)
        self.cube.select_set(True)
        bpy.context.view_layer.objects.active = self.cube
        self.assertEqual(self.export(use_selection=True), {'FINISHED'})
        self.assertTrue(self.output.exists())

    def test_clip_targeting_other_character_aborts(self):
        bpy.ops.mesh.primitive_cube_add()
        other = bpy.context.object
        self.data[other.name] = self.data.pop(self.cube.name)
        with self.assertRaises(RuntimeError):
            self.export()
        self.assertFalse(self.output.exists())


if __name__ == '__main__':
    if bpy is not None:
        sys.argv = [__file__] + (sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
    result = unittest.main(exit=False).result
    if not result.wasSuccessful():
        raise SystemExit(1)
