"""Actual Blender operators with the full ARForge registry. Pass registry path after --."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import extension
from extension.action_validation import CHARACTER_CONTROLLER as TYPE
from extension.form_to_object import get_data_from_active_editor
from extension.object_to_form import object_to_form
from extension.op_registry_loading import process_registry
from extension.property_groups import hash_over_64

if '--' not in sys.argv or len(sys.argv) != sys.argv.index('--') + 2:
    raise SystemExit('Usage: Blender --factory-startup --python tools/test_controller_clipboard.py -- /path/to/skein-registry.json')
REGISTRY_PATH = Path(sys.argv[sys.argv.index('--') + 1])
DATA = dict(movement=dict(speed=5.5, jump_height=2.0, gravity=9.5),
    body=dict(height=1.75, radius=0.5),
    bindings=dict(forward='KeyW', backward='KeyS', left='KeyA', right='KeyD', jump='Space'),
    animations=dict(idle='Idle', move_clip='Run', jump='Jump', default='Idle', transition_seconds=0.25),
    actions=[dict(id='roll', clip='Roll', binding='ShiftLeft', looped=False, speed=1.5, state=None),
             dict(id='sprint', clip='Fast Run', binding=None, looped=True, speed=2.0, state='Move')])

class ControllerClipboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        extension.register()
        bpy.context.preferences.addons.new().module = 'extension'
        cls.registry = json.loads(REGISTRY_PATH.read_text())
        bpy.data.texts.new('skein-registry.json').write(json.dumps(cls.registry))
        process_registry(bpy.context, cls.registry)
        cls.clipboard = bpy.context.window_manager.clipboard

    @classmethod
    def tearDownClass(cls):
        bpy.context.window_manager.clipboard = cls.clipboard

    def setUp(self):
        if bpy.context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        extension.clip_picker.clear_cache()
        bpy.ops.object.select_all(action='SELECT')
        bpy.ops.object.delete(use_global=False)
        bpy.ops.object.empty_add()
        self.source = bpy.context.object
        self.source.name = 'Source'
        bpy.context.window_manager.selected_component = TYPE
        bpy.ops.object.insert_component()
        object_to_form(self.source.skein_two[0], hash_over_64(TYPE), DATA)
        bpy.ops.object.empty_add()
        self.target = bpy.context.object
        self.target.name = 'Target'
        self.target.location = (3, 4, 5)

    def read(self, obj=None, index=0):
        return get_data_from_active_editor((obj or self.target).skein_two[index], hash_over_64(TYPE))

    def copy_source(self):
        bpy.context.view_layer.objects.active = self.source
        self.assertTrue(hasattr(extension, 'controller_clipboard'), 'Controller clipboard operators are missing')
        self.assertEqual(bpy.ops.object.skein_copy_controller(), {'FINISHED'})
        bpy.context.view_layer.objects.active = self.target
        return bpy.context.window_manager.clipboard

    def paste(self):
        return bpy.ops.object.skein_paste_controller()

    def reject(self, text):
        wm = bpy.context.window_manager
        before = [(c.name, c.selected_type_path, c.as_pointer()) for c in self.target.skein_two]
        active = self.target.active_component_index
        wm.clipboard = text
        try:
            result = self.paste()
        except RuntimeError:
            result = {'CANCELLED'}
        self.assertEqual(result, {'CANCELLED'})
        self.assertEqual([(c.name, c.selected_type_path, c.as_pointer()) for c in self.target.skein_two], before)
        self.assertEqual(self.target.active_component_index, active)
        self.assertEqual(wm.clipboard, text)

    def test_copy_add_replace_preserves_order_and_independence(self):
        text = self.copy_source()
        envelope = json.loads(text)
        self.assertEqual(envelope, dict(schema='arforge.character-controller/v1', type_path=TYPE, data=DATA))
        self.assertEqual(self.paste(), {'FINISHED'})
        self.assertEqual(self.read(), DATA)
        self.assertEqual(tuple(self.target.location), (3, 4, 5))
        self.assertEqual(bpy.context.window_manager.clipboard, text)
        before = self.target.skein_two.add()
        before.name = 'Other before'
        before.selected_type_path = 'unrelated::Before'
        self.target.skein_two.move(1, 0)
        after = self.target.skein_two.add()
        after.name = 'Other after'
        after.selected_type_path = 'unrelated::After'
        self.target.skein_two[1].name = 'My controller'
        self.target.active_component_index = 2
        for _ in range(3):
            self.assertEqual(self.paste(), {'FINISHED'})
        self.assertEqual([c.name for c in self.target.skein_two], ['Other before', 'My controller', 'Other after'])
        self.assertEqual(self.target.active_component_index, 2)
        self.assertEqual(self.read(index=1), DATA)
        form = getattr(self.target.skein_two[1], hash_over_64(TYPE))
        form.movement.speed = 8
        form.actions.entries[0].value.clip = 'Different'
        self.assertEqual(self.read(self.source), DATA)
        self.assertEqual(bpy.context.window_manager.clipboard, text)

    def test_missing_context_disables_operators(self):
        from types import SimpleNamespace
        for context in (SimpleNamespace(mode='OBJECT'), SimpleNamespace(object=None, mode='OBJECT')):
            self.assertFalse(extension.controller_clipboard.CopyControllerSettings.poll(context))
            self.assertFalse(extension.controller_clipboard.PasteControllerSettings.poll(context))

    def test_cached_missing_clips_warn_without_rewriting(self):
        from types import SimpleNamespace
        self.copy_source()
        self.paste()
        self.assertEqual(extension.clip_picker.refresh_clip_cache(bpy.context, self.target), [])
        reports = []
        reporter = SimpleNamespace(report=lambda level, message: reports.append((level, message)))
        self.assertEqual(extension.controller_clipboard.PasteControllerSettings.execute(reporter, bpy.context), {'FINISHED'})
        self.assertEqual(self.read(), DATA)
        self.assertTrue(reports, 'Blender operator report capture failed')
        self.assertTrue(any('absent from this export' in message for level, message in reports))
        self.assertTrue(any('final export validation' in message for level, message in reports))

    def test_empty_lists_and_none_options_replace_existing_values(self):
        text = self.copy_source()
        self.paste()
        data = dict(DATA, actions=[], animations=None)
        envelope = json.loads(text)
        envelope['data'] = data
        bpy.context.window_manager.clipboard = json.dumps(envelope)
        for _ in range(2):
            self.assertEqual(self.paste(), {'FINISHED'})
            self.assertEqual(self.read(), data)
            self.assertEqual(len(self.target.skein_two), 1)

    def test_actual_operator_undo_redo_add_and_replace(self):
        text = self.copy_source()
        name = self.target.name
        bpy.ops.ed.undo_push(message='Before controller paste')
        self.paste()
        bpy.ops.ed.undo_push(message='After controller paste')
        bpy.ops.ed.undo()
        self.target = bpy.data.objects[name]
        self.assertEqual(len(self.target.skein_two), 0)
        self.assertEqual(bpy.context.window_manager.clipboard, text)
        bpy.ops.ed.redo()
        self.target = bpy.data.objects[name]
        self.assertEqual(self.read(), DATA)
        replacement = copy.deepcopy(DATA)
        replacement['movement']['speed'] = 8.0
        envelope = json.loads(text)
        envelope['data'] = replacement
        bpy.context.window_manager.clipboard = json.dumps(envelope)
        self.paste()
        bpy.ops.ed.undo_push(message='After controller replacement')
        bpy.ops.ed.undo()
        self.target = bpy.data.objects[name]
        self.assertEqual(self.read(), DATA)
        bpy.ops.ed.redo()
        self.target = bpy.data.objects[name]
        self.assertEqual(self.read(), replacement)

    def test_invalid_envelopes_shapes_and_values_leave_target_untouched(self):
        text = self.copy_source()
        self.paste()
        envelope = json.loads(text)
        invalid = ['', 'not json', '[]', text[:-1], text.replace('v1', 'v2'), text.replace(TYPE, 'Other'),
                   text.replace('"schema":', '"schema":"duplicate", "schema":'),
                   ' ' * (256 * 1024 + 1), json.dumps(dict(envelope, extra=1))]
        for field, value in [('movement', {}), ('body', None), ('actions', {}), ('animations', 'Idle')]:
            invalid.append(json.dumps(dict(envelope, data=dict(DATA, **{field: value}))))
        for mutate in [
            lambda d: d['movement'].update(extra=1),
            lambda d: d['movement'].pop('speed'),
            lambda d: d['movement'].update(speed=True),
            lambda d: d['movement'].update(speed='5'),
            lambda d: d['movement'].update(speed=1e100),
            lambda d: d['movement'].update(speed=float('nan')),
            lambda d: d['movement'].update(speed=float('inf')),
            lambda d: d['body'].update(radius=0.10000000000000001),
            lambda d: d['bindings'].update(jump='InvalidKey'),
            lambda d: d['actions'][0].update(looped=1),
            lambda d: d['actions'][0].update(state='InvalidState'),
            lambda d: d['actions'][0].update(binding={'Some': 'Space'}),
            lambda d: d['actions'][0].update(unknown='lost'),
            lambda d: d['actions'][0].update(speed=0.0),
            lambda d: d['actions'][0].update(id='idle'),
            lambda d: d['actions'].append(copy.deepcopy(d['actions'][0])),
        ]:
            data = copy.deepcopy(DATA)
            mutate(data)
            invalid.append(json.dumps(dict(envelope, data=data)))
        for bad in invalid:
            with self.subTest(value=bad[:100]):
                self.reject(bad)
                self.assertEqual(self.read(), DATA)

    def test_missing_registry_duplicate_and_readonly_are_rejected(self):
        text = self.copy_source()
        wm = bpy.context.window_manager
        saved = wm.skein.registry
        for missing in (TYPE, 'f32'):
            registry = copy.deepcopy(self.registry)
            del registry[missing]
            wm.skein.registry = json.dumps(registry)
            try:
                self.reject(text)
            finally:
                wm.skein.registry = saved
        self.paste()
        duplicate = self.target.skein_two.add()
        duplicate.selected_type_path = TYPE
        self.reject(text)
        bpy.context.view_layer.objects.active = self.target
        with self.assertRaises(RuntimeError):
            bpy.ops.object.skein_copy_controller()
        self.target.skein_two.remove(1)
        # A linked object is genuinely read only, unlike a hidden or unselected object.
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / 'linked.blend')
            bpy.data.libraries.write(path, {self.target})
            with bpy.data.libraries.load(path, link=True) as (source, target):
                target.objects = [self.target.name]
            linked = target.objects[0]
            bpy.context.scene.collection.objects.link(linked)
            bpy.context.view_layer.objects.active = linked
            self.assertFalse(bpy.ops.object.skein_paste_controller.poll())
        bpy.context.view_layer.objects.active = self.target

    def test_z_edit_mode_rejected_and_missing_clips_survive_save_reopen(self):
        bpy.ops.mesh.primitive_cube_add()
        bpy.ops.object.mode_set(mode='EDIT')
        self.assertFalse(bpy.ops.object.skein_paste_controller.poll())
        bpy.ops.object.mode_set(mode='OBJECT')
        text = self.copy_source()
        self.paste()
        self.assertIsNotNone(extension.clip_picker._pending)
        self.assertEqual(self.read(), DATA)
        name = self.target.name
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / 'clipboard.blend')
            bpy.ops.wm.save_as_mainfile(filepath=path)
            bpy.ops.wm.open_mainfile(filepath=path)
        self.target = bpy.data.objects[name]
        self.assertEqual(self.read(), DATA)
        self.assertEqual(bpy.context.window_manager.clipboard, text)

if __name__ == '__main__':
    sys.argv = [__file__]
    if bpy.app.background:
        raise RuntimeError('Run without --background: macOS Blender needs a GUI window for the system clipboard')
    try:
        result = unittest.main(exit=False, verbosity=2).result
        if not result.wasSuccessful():
            sys.exit(1)
    finally:
        bpy.ops.wm.quit_blender()
