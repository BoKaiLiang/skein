"""Real Blender exporter tests: Blender --background --factory-startup --python tools/test_clip_picker.py."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import extension
from extension import gltf_export_extension
from extension.action_validation import CHARACTER_CONTROLLER


class ClipPickerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        extension.register()
        bpy.context.preferences.addons.new().module = 'extension'
        cls.data = {}
        cls.reader = gltf_export_extension.get_object_controller_data
        gltf_export_extension.get_object_controller_data = lambda obj: cls.data.get(obj.name)

    @classmethod
    def tearDownClass(cls):
        gltf_export_extension.get_object_controller_data = cls.reader

    def setUp(self):
        self.assertTrue(hasattr(extension, 'clip_picker'), 'Addon needs exported clip picker')
        self.picker = extension.clip_picker
        self.picker.clear_cache()
        self.data.clear()
        bpy.ops.object.select_all(action='SELECT')
        bpy.ops.object.delete(use_global=False)
        for action in list(bpy.data.actions):
            bpy.data.actions.remove(action)
        bpy.ops.object.empty_add()
        self.root = bpy.context.object
        self.root.name = 'Controller'
        self.data[self.root.name] = {'actions': [dict(id='roll', clip='Missing', binding=None, looped=False, speed=1)]}
        self.mesh = self.animated('Hero', 'SourceAction', 'Exported Roll', self.root)
        self.other = self.animated('Other', 'WrongCharacter', 'Other Clip', None)
        bpy.context.view_layer.objects.active = self.root
        bpy.context.scene.skein_extension_properties.clip_export_mode = 'NLA_TRACKS'
        bpy.context.scene.skein_extension_properties.clip_export_animations = True

    def animated(self, name, action_name, track_name, parent):
        bpy.ops.mesh.primitive_cube_add()
        obj = bpy.context.object
        obj.name = name
        obj.parent = parent
        obj.location.x = 0
        obj.keyframe_insert(data_path='location', frame=1)
        obj.location.x = 1
        obj.keyframe_insert(data_path='location', frame=10)
        action = obj.animation_data.action
        action.name = action_name
        track = obj.animation_data.nla_tracks.new()
        track.name = track_name
        strip = track.strips.new(action_name, 1, action)
        strip.action_slot = obj.animation_data.action_slot
        obj.animation_data.action = None
        return obj

    def test_unlisted_export_hook_reports_error_instead_of_empty_candidates(self):
        addon_entry = bpy.context.preferences.addons['extension']
        bpy.context.preferences.addons.remove(addon_entry)
        try:
            with self.assertRaisesRegex(RuntimeError, 'Skein export hook'):
                self.picker.refresh_clip_cache(bpy.context, self.root)
            self.assertIn('Skein export hook', self.picker.cached_clip_error(self.root))
        finally:
            bpy.context.preferences.addons.new().module = 'extension'
        self.assertEqual(self.picker.refresh_clip_cache(bpy.context, self.root), ['Exported Roll'])

    def test_actual_nla_names_filtered_to_exported_character(self):
        names = self.picker.refresh_clip_cache(bpy.context, self.root)
        self.assertEqual(names, ['Exported Roll'])
        self.assertEqual(self.picker.cached_clip_names(self.root), names)
        self.assertNotIn('SourceAction', names)
        self.assertNotIn('Other Clip', names)
        # Discovery must not disable validation on a later ordinary export.
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(RuntimeError):
                bpy.ops.export_scene.gltf(filepath=str(Path(temp) / 'invalid.glb'), export_animation_mode='NLA_TRACKS')

    def test_cache_signature_tracks_animation_model_and_mode_changes(self):
        signature = self.picker.clip_cache_signature(bpy.context, self.root)
        self.picker.refresh_clip_cache(bpy.context, self.root)
        self.mesh.animation_data.nla_tracks[0].name = 'Renamed'
        self.assertNotEqual(signature, self.picker.clip_cache_signature(bpy.context, self.root))
        self.assertEqual(self.picker.refresh_clip_cache(bpy.context, self.root), ['Renamed'])
        signature = self.picker.clip_cache_signature(bpy.context, self.root)
        bpy.context.scene.skein_extension_properties.clip_export_mode = 'ACTIONS'
        self.assertNotEqual(signature, self.picker.clip_cache_signature(bpy.context, self.root))
        signature = self.picker.clip_cache_signature(bpy.context, self.root)
        self.mesh.parent = None
        self.assertNotEqual(signature, self.picker.clip_cache_signature(bpy.context, self.root))
        self.assertEqual(self.picker.refresh_clip_cache(bpy.context, self.root), [])

    def test_edit_mode_discovery_defers_without_switching_mode(self):
        self.data[self.mesh.name] = self.data[self.root.name]
        bpy.ops.object.select_all(action='DESELECT')
        self.mesh.select_set(True)
        bpy.context.view_layer.objects.active = self.mesh
        bpy.ops.object.mode_set(mode='EDIT')
        try:
            with self.assertRaisesRegex(RuntimeError, 'Object Mode'):
                self.picker.refresh_clip_cache(bpy.context, self.mesh)
            self.assertEqual(bpy.context.mode, 'EDIT_MESH')
            self.picker.request_clip_refresh(bpy.context, self.mesh)
            self.picker._pending = (self.mesh, None, 0)
            self.assertEqual(self.picker._refresh_pending(), 0.5)
            self.assertEqual(bpy.context.mode, 'EDIT_MESH')
            self.assertIsNone(self.picker.cached_clip_names(self.mesh))
        finally:
            bpy.ops.object.mode_set(mode='OBJECT')
        self.assertIsNone(self.picker._refresh_pending())
        self.assertEqual(self.picker.cached_clip_names(self.mesh), ['Exported Roll'])
        self.assertEqual(bpy.context.mode, 'OBJECT')
        if bpy.app.timers.is_registered(self.picker._refresh_pending):
            bpy.app.timers.unregister(self.picker._refresh_pending)

    def test_pose_mode_entered_after_scheduling_defers_without_switching_mode(self):
        bpy.ops.object.armature_add()
        armature = bpy.context.object
        self.data[armature.name] = {'actions': []}
        self.picker.request_clip_refresh(bpy.context, armature)
        self.picker._pending = (armature, None, 0)
        bpy.ops.object.mode_set(mode='POSE')
        try:
            with self.assertRaisesRegex(RuntimeError, 'Object Mode'):
                self.picker.refresh_clip_cache(bpy.context, armature)
            self.assertEqual(bpy.context.mode, 'POSE')
            self.assertEqual(self.picker._refresh_pending(), 0.5)
            self.assertEqual(bpy.context.mode, 'POSE')
            self.assertIsNone(self.picker.cached_clip_names(armature))
        finally:
            bpy.ops.object.mode_set(mode='OBJECT')
        self.assertIsNone(self.picker._refresh_pending())
        self.assertEqual(self.picker.cached_clip_names(armature), [])
        self.assertEqual(bpy.context.mode, 'OBJECT')
        if bpy.app.timers.is_registered(self.picker._refresh_pending):
            bpy.app.timers.unregister(self.picker._refresh_pending)

    def test_morph_nla_track_rename_invalidates_actual_exported_weights_clip(self):
        self.mesh.shape_key_add(name='Basis')
        shape = self.mesh.shape_key_add(name='Smile')
        shape.data[0].co.z += 1
        shape.value = 0
        shape.keyframe_insert(data_path='value', frame=1)
        shape.value = 1
        shape.keyframe_insert(data_path='value', frame=10)
        source = self.mesh.data.shape_keys.animation_data
        action = source.action
        action.name = 'Morph Source'
        track = source.nla_tracks.new()
        track.name = 'Exported Smile'
        strip = track.strips.new(action.name, 1, action)
        strip.action_slot = source.action_slot
        source.action = None
        names = self.picker.refresh_clip_cache(bpy.context, self.root)
        self.assertIn('Exported Smile', names)
        signature = self.picker.clip_cache_signature(bpy.context, self.root)
        track.name = 'Renamed Smile'
        self.assertNotEqual(signature, self.picker.clip_cache_signature(bpy.context, self.root))
        names = self.picker.refresh_clip_cache(bpy.context, self.root)
        self.assertIn('Renamed Smile', names)
        self.assertNotIn('Exported Smile', names)

    def test_signature_uses_curve_eligibility_not_sample_values(self):
        action = self.mesh.animation_data.nla_tracks[0].strips[0].action
        curve = action.layers[0].strips[0].channelbags[0].fcurves[0]
        signature = self.picker.clip_cache_signature(bpy.context, self.root)
        curve.keyframe_points[0].co.y += 10
        self.assertEqual(signature, self.picker.clip_cache_signature(bpy.context, self.root))
        curve.keyframe_points.insert(5, 0.5)
        self.assertNotEqual(signature, self.picker.clip_cache_signature(bpy.context, self.root))

    def test_disabled_animations_are_not_candidates_and_mute_matches_exporter(self):
        self.mesh.animation_data.nla_tracks[0].mute = True
        # NLA_TRACKS intentionally exports muted tracks too, using exporter names.
        self.assertEqual(self.picker.refresh_clip_cache(bpy.context, self.root), ['Exported Roll'])
        self.mesh.animation_data.nla_tracks[0].mute = False
        bpy.context.scene.skein_extension_properties.clip_export_animations = False
        self.assertEqual(self.picker.refresh_clip_cache(bpy.context, self.root), [])

    def test_debounce_refreshes_once_until_model_changes(self):
        self.picker.request_clip_refresh(bpy.context, self.root)
        pending = self.picker._pending
        self.picker.request_clip_refresh(bpy.context, self.root)
        self.assertEqual(self.picker._pending, pending)
        self.assertTrue(bpy.app.timers.is_registered(self.picker._refresh_pending))
        self.picker._pending = (self.root, pending[1], 0)
        self.assertIsNone(self.picker._refresh_pending())
        self.assertEqual(self.picker.cached_clip_names(self.root), ['Exported Roll'])
        self.picker._last_checks.clear()
        self.picker.request_clip_refresh(bpy.context, self.root)
        self.assertIsNone(self.picker._pending, 'Exporter sampling must not trigger another export')
        self.mesh.animation_data.nla_tracks[0].strips[0].action.name = 'New Action Name'
        self.picker._last_checks.clear()
        self.picker.request_clip_refresh(bpy.context, self.root)
        self.assertIsNotNone(self.picker._pending)
        self.assertIsNone(self.picker.cached_clip_names(self.root), "Stale choices must be hidden")
        # Avoid leaving a pending background refresh while the next test replaces the scene.
        bpy.app.timers.unregister(self.picker._refresh_pending)
        self.picker.clear_cache()

    def test_discovery_preserves_frame_selection_actions_and_export_preferences(self):
        bpy.context.scene.frame_set(7, subframe=0.25)
        action = self.mesh.animation_data.nla_tracks[0].strips[0].action
        slot = self.mesh.animation_data.nla_tracks[0].strips[0].action_slot
        self.mesh.animation_data.action = action
        self.mesh.animation_data.action_slot = slot
        before_selected = tuple(bpy.context.selected_objects)
        before_prefs = bpy.context.scene.get('glTF2ExportSettings')
        before_signature = self.picker.clip_cache_signature(bpy.context, self.root)
        self.picker.refresh_clip_cache(bpy.context, self.root)
        self.assertEqual(bpy.context.scene.frame_current, 7)
        self.assertEqual(bpy.context.scene.frame_subframe, 0.25)
        self.assertEqual(tuple(bpy.context.selected_objects), before_selected)
        self.assertEqual(bpy.context.view_layer.objects.active, self.root)
        self.assertEqual(self.mesh.animation_data.action, action)
        self.assertEqual(self.mesh.animation_data.action_slot, slot)
        self.assertEqual(bpy.context.scene.get('glTF2ExportSettings'), before_prefs)
        self.assertEqual(self.picker.clip_cache_signature(bpy.context, self.root), before_signature)
        original = self.picker.record_discovery
        def fail(*_args):
            raise RuntimeError('discovery failure regression')
        self.picker.record_discovery = fail
        try:
            with self.assertRaises(RuntimeError):
                self.picker.refresh_clip_cache(bpy.context, self.root)
        finally:
            self.picker.record_discovery = original
        self.assertIsNone(self.picker._discovery)
        self.assertIn('discovery failure regression', self.picker.cached_clip_error(self.root))
        from tools.test_lists import RecordingLayout
        from types import SimpleNamespace
        layout = RecordingLayout()
        self.picker.SkeinExportedClips.draw(SimpleNamespace(layout=layout), SimpleNamespace(skein_clip_object=self.root))
        self.assertIn('discovery failure regression', ' '.join(layout.labels))
        self.assertIn('wm.skein_refresh_clips', layout.operators)
        self.assertNotIn('Discovering exported clips…', layout.labels)
        self.assertTrue(all(len(line) <= 55 for line in layout.labels))
        self.picker._last_checks.clear()
        self.picker.request_clip_refresh(bpy.context, self.root)
        self.assertIsNone(self.picker._pending, 'Failed discovery must wait for an explicit retry or source change')
        self.assertEqual(bpy.ops.wm.skein_refresh_clips(), {'FINISHED'})
        self.assertIsNone(self.picker.cached_clip_error(self.root))
        self.assertEqual(self.picker.cached_clip_names(self.root), ['Exported Roll'])
        self.assertEqual(self.data[self.root.name]['actions'][0]['clip'], 'Missing')
        self.assertEqual(bpy.context.scene.frame_current, 7)
        self.assertEqual(bpy.context.scene.frame_subframe, 0.25)
        self.assertEqual(self.mesh.animation_data.action, action)
        self.assertEqual(self.mesh.animation_data.action_slot, slot)
        self.assertEqual(self.picker.clip_cache_signature(bpy.context, self.root), before_signature)

    def test_string_choice_and_missing_value_survive_reopen(self):
        from tools.test_lists import REGISTRY, DATA
        from extension.op_registry_loading import process_registry
        from extension.object_to_form import object_to_form
        from extension.form_to_object import get_data_from_active_editor
        registry = json.loads(json.dumps(REGISTRY))
        bpy.data.texts.new('skein-registry.json').write(json.dumps(registry))
        process_registry(bpy.context, registry)
        bpy.context.view_layer.objects.active = self.root
        bpy.context.window_manager.selected_component = CHARACTER_CONTROLLER
        bpy.ops.object.insert_component()
        container = self.root.skein_two[0]
        object_to_form(container, CHARACTER_CONTROLLER, DATA)
        form = getattr(container, CHARACTER_CONTROLLER).actions.entries[0].value
        with bpy.context.temp_override(skein_clip_target=form):
            self.assertEqual(bpy.ops.wm.skein_choose_clip(field='clip', clip='Exported Roll',
                target_object=self.root.name, target_path=form.path_from_id()), {'FINISHED'})
        self.assertEqual(form.clip, 'Exported Roll')
        # Popup draw and execution have distinct context lifetimes.
        from types import SimpleNamespace
        self.picker.refresh_clip_cache(bpy.context, self.root)
        choices = []
        class PopupLayout:
            def operator(self, name, **kwargs):
                choice = SimpleNamespace()
                choices.append(choice)
                return choice
        menu_context = SimpleNamespace(skein_clip_object=self.root, skein_clip_target=form, skein_clip_field='clip')
        self.picker.SkeinExportedClips.draw(SimpleNamespace(layout=PopupLayout()), menu_context)
        choice = choices[0]
        self.assertTrue(hasattr(choice, 'target_path'), 'Popup must capture a target that survives menu closure')
        self.assertEqual(self.root.path_resolve(choice.target_path), form)
        form.clip = 'Before Popup Choice'
        self.assertEqual(bpy.ops.wm.skein_choose_clip(field=choice.field, clip=choice.clip,
            target_object=choice.target_object, target_path=choice.target_path), {'FINISHED'})
        self.assertEqual(form.clip, 'Exported Roll')
        self.assertEqual(bpy.ops.wm.skein_choose_clip(field='clip', clip='Wrong',
            target_object=self.root.name, target_path='missing_form'), {'CANCELLED'})
        self.assertEqual(form.clip, 'Exported Roll')
        form.clip = 'Missing Saved Clip' 
        names = self.picker.refresh_clip_cache(bpy.context, self.root)
        self.assertEqual(form.clip, 'Missing Saved Clip')
        self.assertNotIn(form.clip, names)
        from tools.test_lists import RecordingLayout
        from extension.skein_panel import render_two
        class ClipLayout(RecordingLayout):
            def context_pointer_set(self, key, value):
                self.assert_pointer = value
            def context_string_set(self, key, value):
                assert key == 'skein_clip_field'
                assert 'context_string_set' in bpy.types.UILayout.bl_rna.functions
            def menu(self, name, **kwargs):
                self.operators.append(name)
        layout = ClipLayout()
        render_two(layout, container, CHARACTER_CONTROLLER, clip_object=self.root)
        self.assertEqual(layout.operators.count('SKEIN_MT_exported_clips'), 2)
        self.assertIn('Missing exported clip: Missing Saved Clip', layout.labels)
        self.assertEqual(form.clip, 'Missing Saved Clip')
        root_name = self.root.name
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / 'picker.blend')
            bpy.ops.wm.save_as_mainfile(filepath=path)
            bpy.ops.wm.open_mainfile(filepath=path)
            data = get_data_from_active_editor(bpy.data.objects[root_name].skein_two[0], CHARACTER_CONTROLLER)
            self.assertEqual(data['actions'][0]['clip'], 'Missing Saved Clip')


if __name__ == '__main__':
    sys.argv = [__file__]
    result = unittest.main(exit=False, verbosity=2).result
    if not result.wasSuccessful():
        sys.exit(1)
