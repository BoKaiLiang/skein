"""Blender-local preview tests against actual exporter source mappings."""
from pathlib import Path
import sys
import tempfile
import json
from types import SimpleNamespace
import unittest
import bpy
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import extension
from extension import local_preview, clip_picker


class LocalPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        extension.register()
        if not local_preview.PreviewClip.is_registered:
            local_preview.register()
        bpy.context.preferences.addons.new().module = 'extension'

    def setUp(self):
        local_preview.stop()
        bpy.ops.object.select_all(action='SELECT')
        bpy.ops.object.delete(use_global=False)
        for action in list(bpy.data.actions): bpy.data.actions.remove(action)
        clip_picker.clear_cache()
        bpy.ops.object.empty_add()
        self.owner = bpy.context.object
        self.owner.name = 'PreviewRoot'
        bpy.ops.mesh.primitive_cube_add()
        self.mesh = bpy.context.object
        self.mesh.parent = self.owner
        self.mesh.location.x = 0
        self.mesh.keyframe_insert(data_path='location', frame=1)
        self.mesh.location.x = 10
        self.mesh.keyframe_insert(data_path='location', frame=11)
        self.action = self.mesh.animation_data.action
        self.action.name = 'Source different from exported name'
        self.slot = self.mesh.animation_data.action_slot
        for layer in self.action.layers:
            for strip in layer.strips:
                for bag in strip.channelbags:
                    for curve in bag.fcurves:
                        for key in curve.keyframe_points: key.interpolation = 'LINEAR'
        track = self.mesh.animation_data.nla_tracks.new()
        track.name = 'Exported Clip'
        strip = track.strips.new('Motion', 1, self.action)
        strip.action_slot = self.slot
        self.mesh.animation_data.action = None
        bpy.context.scene.skein_extension_properties.clip_export_mode = 'NLA_TRACKS'
        bpy.context.scene.render.fps = 10
        bpy.context.scene.frame_set(4, subframe=0.25)
        bpy.context.view_layer.objects.active = self.owner
        self.names = clip_picker.refresh_clip_cache(bpy.context, self.owner)
        self.assertIn('Exported Clip', self.names)

    def tearDown(self):
        local_preview.stop()

    def test_nla_source_name_loop_speed_and_stop_restore(self):
        frame = (bpy.context.scene.frame_current, bpy.context.scene.frame_subframe)
        original = self.mesh.location.copy()
        signature = clip_picker.clip_cache_signature(bpy.context, self.owner)
        local_preview.start(bpy.context, self.owner, 'Exported Clip', looped=True, speed=2.0)
        local_preview.sample_elapsed(0.2)
        self.assertAlmostEqual(self.mesh.location.x, 4.0, places=3)
        local_preview.sample_elapsed(0.7)
        self.assertAlmostEqual(self.mesh.location.x, 4.0, places=3)
        self.assertTrue(local_preview.active(self.owner))
        local_preview.stop()
        self.assertEqual((bpy.context.scene.frame_current, bpy.context.scene.frame_subframe), frame)
        self.assertLess((self.mesh.location - original).length, 1e-6)
        self.assertEqual(clip_picker.clip_cache_signature(bpy.context, self.owner), signature)

    def test_action_slots_are_derived_from_actual_export(self):
        bpy.context.scene.skein_extension_properties.clip_export_mode = 'ACTIONS'
        self.mesh.animation_data.action = self.action
        self.mesh.animation_data.action_slot = self.slot
        names = clip_picker.refresh_clip_cache(bpy.context, self.owner)
        self.assertIn(self.action.name, names)
        local_preview.start(bpy.context, self.owner, self.action.name, looped=False, speed=0.5)
        local_preview.sample_elapsed(0.4)
        self.assertAlmostEqual(self.mesh.location.x, 2.0, places=3)
        local_preview.sample_elapsed(3)
        self.assertFalse(local_preview.active())
        self.assertEqual(self.mesh.animation_data.action, self.action)
        self.assertEqual(self.mesh.animation_data.action_slot, self.slot)

    def test_shape_key_tracks_and_frame_restore(self):
        self.mesh.shape_key_add(name='Basis')
        key = self.mesh.shape_key_add(name='Smile')
        key.value = 0
        key.keyframe_insert(data_path='value', frame=1)
        key.value = 1
        key.keyframe_insert(data_path='value', frame=11)
        data = self.mesh.data.shape_keys.animation_data
        action, slot = data.action, data.action_slot
        track = data.nla_tracks.new()
        track.name = 'Face Clip'
        strip = track.strips.new('Face', 1, action)
        strip.action_slot = slot
        data.action = None
        bpy.context.scene.frame_set(3)
        original = key.value
        clip_picker.refresh_clip_cache(bpy.context, self.owner)
        local_preview.start(bpy.context, self.owner, 'Face Clip', looped=True)
        local_preview.sample_elapsed(0.5)
        self.assertGreater(key.value, 0.4)
        self.assertLess(key.value, 0.6)
        local_preview.stop()
        self.assertAlmostEqual(key.value, original)
        self.assertIsNone(data.action)

    def test_skeletal_pose_samples_and_unkeyed_pose_restore(self):
        bpy.ops.object.armature_add()
        rig = bpy.context.object
        rig.parent = self.owner
        bone = rig.pose.bones[0]
        bone.rotation_mode = 'XYZ'
        bone.rotation_euler.z = 0
        bone.keyframe_insert(data_path='rotation_euler', frame=1)
        bone.rotation_euler.z = 1
        bone.keyframe_insert(data_path='rotation_euler', frame=11)
        data = rig.animation_data
        action, slot = data.action, data.action_slot
        track = data.nla_tracks.new()
        track.name = 'Skeleton Clip'
        strip = track.strips.new('Pose', 1, action)
        strip.action_slot = slot
        data.action = None
        bpy.context.scene.frame_set(4)
        bone.location.y = 0.37
        original_matrix = bone.matrix_basis.copy()
        bpy.context.view_layer.objects.active = self.owner
        clip_picker.refresh_clip_cache(bpy.context, self.owner)
        local_preview.start(bpy.context, self.owner, 'Skeleton Clip', looped=True)
        local_preview.sample_elapsed(0.5)
        self.assertGreater(bone.rotation_euler.z, 0.4)
        self.assertLess(bone.rotation_euler.z, 0.6)
        self.assertAlmostEqual(bone.location.y, 0)
        local_preview.stop()
        self.assertTrue(all(abs(original_matrix[i][j] - bone.matrix_basis[i][j]) < 1e-6 for i in range(4) for j in range(4)))
        self.assertIsNone(data.action)

    def test_exporter_track_group_blending_and_custom_value_restore(self):
        self.mesh.animation_data.action = None
        self.mesh['progress'] = 0.15
        self.mesh.keyframe_insert(data_path='["progress"]', frame=1)
        self.mesh['progress'] = 0.85
        self.mesh.keyframe_insert(data_path='["progress"]', frame=11)
        action = self.mesh.animation_data.action
        slot = self.mesh.animation_data.action_slot
        track = self.mesh.animation_data.nla_tracks.new()
        track.name = 'Additive Layer'
        strip = track.strips.new('Custom', 1, action)
        strip.action_slot = slot
        strip.blend_type = 'ADD'
        self.mesh.animation_data.action = None
        bpy.context.scene.frame_set(4)
        self.mesh['progress'] = 0.23
        original = self.mesh['progress']
        clip_picker.refresh_clip_cache(bpy.context, self.owner)
        bindings = clip_picker.cached_clip_bindings(self.owner, 'Exported Clip')
        self.assertEqual(len(bindings[0]['tracks']), 2)
        local_preview.start(bpy.context, self.owner, 'Exported Clip', looped=True)
        local_preview.sample_elapsed(0.5)
        self.assertAlmostEqual(self.mesh.location.x, 5.0, places=3)
        self.assertNotAlmostEqual(self.mesh['progress'], original)
        local_preview.stop()
        self.assertEqual(self.mesh['progress'], original)

    def test_real_save_boundary_stops_preview_before_file_is_written(self):
        original = (bpy.context.scene.frame_current, bpy.context.scene.frame_subframe)
        local_preview.start(bpy.context, self.owner, 'Exported Clip', looped=True)
        local_preview.sample_elapsed(0.6)
        with tempfile.TemporaryDirectory(prefix='skein-local-save-') as directory:
            bpy.ops.wm.save_as_mainfile(filepath=str(Path(directory) / 'preview.blend'))
            self.assertFalse(local_preview.active())
            self.assertEqual((bpy.context.scene.frame_current, bpy.context.scene.frame_subframe), original)

    def test_real_export_restores_preview_before_serializing_the_model(self):
        with tempfile.TemporaryDirectory(prefix='skein-local-export-') as directory:
            first = Path(directory) / 'before.glb'
            second = Path(directory) / 'after.glb'
            settings = dict(export_format='GLB', export_animation_mode='NLA_TRACKS', will_save_settings=False)
            bpy.ops.export_scene.gltf(filepath=str(first), **settings)
            local_preview.start(bpy.context, self.owner, 'Exported Clip', looped=True, speed=2.0)
            local_preview.sample_elapsed(0.3)
            self.assertAlmostEqual(self.mesh.location.x, 6.0, places=3)
            bpy.ops.export_scene.gltf(filepath=str(second), **settings)
            self.assertFalse(local_preview.active())
            self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_scene_switch_restores_original_scene_without_touching_new_scene(self):
        original_scene = bpy.context.scene
        original_frame = (original_scene.frame_current, original_scene.frame_subframe)
        for boundary in ('stop', 'timer'):
            other_scene = bpy.data.scenes.new('Unrelated Scene')
            other_scene.frame_set(57, subframe=0.5)
            try:
                local_preview.start(bpy.context, self.owner, 'Exported Clip', looped=True)
                local_preview.sample_elapsed(0.6)
                bpy.context.window.scene = other_scene
                if boundary == 'stop':
                    local_preview.stop()
                else:
                    local_preview.sample_elapsed(0.8)
                self.assertFalse(local_preview.active())
                self.assertEqual((original_scene.frame_current, original_scene.frame_subframe), original_frame)
                self.assertEqual((other_scene.frame_current, other_scene.frame_subframe), (57, 0.5))
                self.assertEqual(bpy.context.scene, other_scene)
            finally:
                bpy.context.window.scene = original_scene
                local_preview.stop()
                bpy.data.scenes.remove(other_scene)

    def test_deleted_binding_does_not_prevent_restoring_surviving_targets(self):
        bpy.ops.mesh.primitive_cube_add()
        survivor = bpy.context.object
        survivor.parent = self.owner
        survivor.location.y = 0
        survivor.keyframe_insert(data_path='location', frame=1)
        survivor.location.y = 10
        survivor.keyframe_insert(data_path='location', frame=11)
        data = survivor.animation_data
        track = data.nla_tracks.new()
        track.name = 'Exported Clip'
        strip = track.strips.new('Partner', 1, data.action)
        strip.action_slot = data.action_slot
        data.action = None
        bpy.context.scene.frame_set(4, subframe=0.25)
        bpy.context.view_layer.objects.active = self.owner
        clip_picker.refresh_clip_cache(bpy.context, self.owner)
        original = survivor.location.copy()
        local_preview.start(bpy.context, self.owner, 'Exported Clip', looped=True)
        local_preview.sample_elapsed(0.6)
        self.assertGreater((survivor.location - original).length, 0.1)
        bpy.data.objects.remove(self.mesh, do_unlink=True)
        local_preview.stop()
        self.assertFalse(local_preview.active())
        self.assertLess((survivor.location - original).length, 1e-6)
        self.assertEqual((bpy.context.scene.frame_current, bpy.context.scene.frame_subframe), (4, 0.25))

    def test_preview_does_not_discover_again_and_save_export_undo_stop(self):
        clip_picker._pending = (self.owner, None, 0)
        local_preview.start(bpy.context, self.owner, 'Exported Clip', looped=True)
        local_preview.sample_elapsed(0.3)
        clip_picker.request_clip_refresh(bpy.context, self.owner)
        self.assertEqual(clip_picker._refresh_pending(), 0.5)
        self.assertTrue(local_preview.active())
        clip_picker._pending = None
        local_preview.on_restore_boundary(None)
        self.assertFalse(local_preview.active())
        local_preview.start(bpy.context, self.owner, 'Exported Clip', looped=True)
        from extension.gltf_export_extension import glTF2ExportUserExtension
        glTF2ExportUserExtension()
        self.assertFalse(local_preview.active(), 'Export constructor must stop before reading animation state')

    def test_play_operator_reads_current_generic_metadata_loop_and_speed(self):
        from tools.test_action_validation import REGISTRY, DESCRIPTION, ref
        from extension.op_registry_loading import process_registry
        from extension.op_insert_component import insert_component_data
        from extension.object_to_form import object_to_form
        registry = json.loads(json.dumps(REGISTRY))
        registry['demo::Motion']['properties']['repeat'] = ref('bool')
        description = json.loads(json.dumps(DESCRIPTION))
        description['fields'][0]['playback'] = {'loop_field': 'repeat', 'speed_field': 'rate'}
        for name, value in (('skein-registry.json', registry), ('skein-authoring.json', description)):
            text = bpy.data.texts.get(name) or bpy.data.texts.new(name)
            text.clear()
            text.write(json.dumps(value))
        process_registry(bpy.context, registry)
        bpy.context.window_manager.selected_component = 'demo::Avatar'
        insert_component_data(bpy.context, self.owner)
        container = self.owner.skein_two[0]
        object_to_form(container, 'demo::Avatar', {'motions': [dict(
            tag='arbitrary', clip_ref='Exported Clip', rate=2.0, repeat=True)]})
        form = getattr(container, 'demo::Avatar').motions.entries[0].value
        target = SimpleNamespace()
        local_preview.capture_target(target, self.owner, form, 'clip_ref')
        self.assertEqual(bpy.ops.wm.skein_preview_clip(**vars(target)), {'FINISHED'})
        local_preview.sample_elapsed(0.7)
        self.assertAlmostEqual(self.mesh.location.x, 4.0, places=3)
        self.assertTrue(local_preview.active())
        self.assertEqual(bpy.ops.wm.skein_stop_clip_preview(), {'FINISHED'})
        self.assertFalse(local_preview.active())
        text = bpy.data.texts['skein-authoring.json']
        for playback, elapsed, expected in (({'loop_field': 'repeat'}, 1.2, 2.0),
                                            ({'speed_field': 'rate'}, 0.2, 4.0)):
            description['fields'][0]['playback'] = playback
            text.clear()
            text.write(json.dumps(description))
            self.assertEqual(bpy.ops.wm.skein_preview_clip(**vars(target)), {'FINISHED'})
            local_preview.sample_elapsed(elapsed)
            self.assertAlmostEqual(self.mesh.location.x, expected, places=3)
            if 'loop_field' not in playback:
                local_preview.sample_elapsed(0.7)
                self.assertFalse(local_preview.active())
            else:
                self.assertTrue(local_preview.active())
            local_preview.stop()
        form.clip_ref = 'Changed after button capture'
        self.assertEqual(bpy.ops.wm.skein_preview_clip(**vars(target)), {'CANCELLED'})
        self.assertFalse(local_preview.active())


if __name__ == '__main__':
    sys.argv = [__file__]
    result = unittest.main(exit=False, verbosity=2).result
    if not result.wasSuccessful(): raise SystemExit(1)
