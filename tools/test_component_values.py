"""Run isolated Blender --factory-startup --python this_file (clipboard needs a window)."""
import copy
import tempfile
import json
from pathlib import Path
import sys
import unittest
import bpy
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))
import extension
from test_lists import REGISTRY, DATA, ref, schema

REGISTRY.update({
    "f64": schema("Value", "f64", type="float"),
    "F32Root": schema("TupleStruct", "F32Root", reflectTypes=["Component"], prefixItems=[ref("f32")]),
    "F64Root": schema("TupleStruct", "F64Root", reflectTypes=["Component"], prefixItems=[ref("f64")]),
    "glam::Vec2": schema("Struct", "glam::Vec2", properties={"x": ref("f32"), "y": ref("f32")}),
    "VecF32": schema("List", "VecF32", type="array", items=ref("f32")),
    "F32Choice": schema("Enum", "F32Choice", type="object", modulePath="example", oneOf=[dict(schema("Tuple", "F32Choice::Payload", type="array", prefixItems=[ref("f32")]), shortPath="Payload")]),
    "core::option::Option<f32>": schema("Enum", "core::option::Option<f32>", type="object", modulePath="core::option", oneOf=[{"shortPath": "None", "typePath": "OptionF32::None"}, dict(schema("Tuple", "OptionF32::Some", type="array", prefixItems=[ref("f32")]), shortPath="Some")]),
    "F32Composite": schema("Struct", "F32Composite", reflectTypes=["Component"], properties={"list": ref("VecF32"), "choice": ref("F32Choice"), "option": ref("core::option::Option<f32>"), "position": ref("glam::Vec2")}),
    "ScalarComponent": schema("TupleStruct", "ScalarComponent", reflectTypes=["Component"], prefixItems=[ref("String")]),
    "ScalarEnumComponent": schema("Enum", "ScalarEnumComponent", reflectTypes=["Component"], type="string", oneOf=["Idle", "Move"]),
    "ScalarValueComponent": schema("Value", "ScalarValueComponent", reflectTypes=["Component"], type="string"),
    "PayloadChoice": schema("Enum", "PayloadChoice", type="object", modulePath="example", oneOf=[
        dict(schema("Tuple", "PayloadChoice::Payload", type="array", prefixItems=[ref("String")]), shortPath="Payload"),
        {"shortPath": "Empty", "typePath": "PayloadChoice::Empty"}]),
    "PayloadComponent": schema("Struct", "PayloadComponent", reflectTypes=["Component"], properties={"choice": ref("PayloadChoice")}),
})
from extension.op_registry_loading import process_registry
from extension.object_to_form import object_to_form
from extension.property_groups import hash_over_64

class ComponentValueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        extension.register()
        bpy.data.texts.new('skein-registry.json').write(json.dumps(REGISTRY))
        process_registry(bpy.context, REGISTRY)
        cls.clipboard = bpy.context.window_manager.clipboard
        cls.values = getattr(extension, 'component_values', None)
        if cls.values is None:
            try:
                from extension import component_values
                cls.values = component_values
            except ImportError:
                pass
        if cls.values and not cls.values.SKEIN_UL_components.is_registered:
            cls.values.register()

    @classmethod
    def tearDownClass(cls):
        bpy.context.window_manager.clipboard = cls.clipboard

    def setUp(self):
        self.assertIsNotNone(self.values, 'Generic component values module is missing')
        self.obj = bpy.context.object
        self.obj.skein_two.clear()
        for name in ['Before', 'Controller', 'After']:
            c = self.obj.skein_two.add()
            c.name = name
            c.selected_type_path = 'Controller'
            from extension.op_insert_component import touch_all_fields
            touch_all_fields(c, 'Controller')
            object_to_form(c, 'Controller', DATA)
        self.obj.active_component_index = 2

    def invoke(self, operation, index=1):
        c = self.obj.skein_two[index]
        return getattr(bpy.ops.wm, 'skein_component_' + operation)(**self.values.target_token(c))

    def test_copy_paste_clicked_existing_component_preserves_neighbors(self):
        self.assertEqual(self.invoke('copy'), {'FINISHED'})
        text = bpy.context.window_manager.clipboard
        self.assertEqual(json.loads(text), dict(schema='skein.component/v1', type_path='Controller', data=DATA))
        self.obj.skein_two[1].Controller.tags.entries.clear()
        pointers = [c.as_pointer() for c in self.obj.skein_two]
        self.assertEqual(self.invoke('paste'), {'FINISHED'})
        self.assertEqual(self.values.read_component(self.obj.skein_two[1], REGISTRY), DATA)
        self.assertEqual([c.name for c in self.obj.skein_two], ['Before', 'Controller', 'After'])
        self.assertEqual(self.obj.active_component_index, 2)
        self.assertEqual(self.obj.skein_two[0].as_pointer(), pointers[0])
        self.assertEqual(self.obj.skein_two[2].as_pointer(), pointers[2])

    def test_rejects_mismatch_stale_lossy_and_malformed_atomically(self):
        self.invoke('copy')
        original = bpy.context.window_manager.clipboard
        token = self.values.target_token(self.obj.skein_two[1])
        invalid_shapes = []
        for field, value in [('actions', {}), ('tags', None), ('groups', 'bad')]:
            invalid_shapes.append(json.dumps(dict(json.loads(original), data=dict(DATA, **{field: value}))))
        for mutate in [lambda d: d.update(extra=1), lambda d: d.pop('tags'), lambda d: d['actions'][0].update(extra=1), lambda d: d['actions'][0].pop('settings'), lambda d: d['actions'][0].update(binding='Unknown')]:
            data = copy.deepcopy(DATA)
            mutate(data)
            invalid_shapes.append(json.dumps(dict(json.loads(original), data=data)))
        for text in invalid_shapes + ['[]', original.replace('skein.component/v1', 'skein.component/v2'), original.replace('Controller', 'Other'), original.replace('1.25', 'true'), original.replace('1.25', 'NaN'), original.replace('1.25', 'Infinity'), original.replace('\"schema\":', '\"schema\":\"duplicate\",\"schema\":'), original.replace('1.25', '1e100'), ' ' * (256*1024+1)]:
            bpy.context.window_manager.clipboard = text
            before = [c.as_pointer() for c in self.obj.skein_two]
            try:
                result = self.invoke('paste')
            except RuntimeError:
                result = {'CANCELLED'}
            self.assertEqual(result, {'CANCELLED'})
            self.assertEqual(before, [c.as_pointer() for c in self.obj.skein_two])
            self.assertEqual([c.name for c in self.obj.skein_two], ['Before', 'Controller', 'After'])
            self.assertEqual(self.obj.active_component_index, 2)
            self.assertEqual([self.values.read_component(c, REGISTRY) for c in self.obj.skein_two], [DATA]*3)
        bpy.context.window_manager.clipboard = original
        self.obj.skein_two.remove(1)
        try:
            result = bpy.ops.wm.skein_component_paste(**token)
        except RuntimeError:
            result = {'CANCELLED'}
        self.assertEqual(result, {'CANCELLED'})
        for name in ['Map', 'Set', 'UnsupportedTuple', 'VecUnsupported', 'Defaults']:
            with self.assertRaises(ValueError):
                self.values.ensure_supported(REGISTRY, name)

    def test_z_linked_target_rejected_and_save_reopen_preserves_values(self):
        self.invoke('copy')
        name = self.obj.name
        with tempfile.TemporaryDirectory() as temp:
            library = str(Path(temp) / 'library.blend')
            bpy.data.libraries.write(library, {self.obj})
            with bpy.data.libraries.load(library, link=True) as (source, target):
                target.objects = [name]
            linked = target.objects[0]
            token = self.values.target_token(linked.skein_two[1])
            with self.assertRaises(RuntimeError):
                bpy.ops.wm.skein_component_paste(**token)
            path = str(Path(temp) / 'saved.blend')
            bpy.ops.wm.save_as_mainfile(filepath=path)
            bpy.ops.wm.open_mainfile(filepath=path)
            self.obj = bpy.data.objects[name]
        self.assertEqual([self.values.read_component(c, REGISTRY) for c in self.obj.skein_two], [DATA]*3)

    def test_root_scalar_and_enum_payload_copy_paste_reset(self):
        cases = [('ScalarComponent', 'Original', 'Replacement', 'Default'),
                 ('ScalarValueComponent', 'Original', 'Replacement', 'Default'),
                 ('ScalarEnumComponent', 'Idle', 'Move', 'Idle'),
                 ('PayloadComponent', {'choice': {'Payload': 'Original'}},
                  {'choice': {'Payload': 'Replacement'}}, {'choice': {'Payload': 'Default'}})]
        for path, original, replacement, default in cases:
            with self.subTest(path=path):
                component = self.obj.skein_two.add()
                component.name, component.selected_type_path = path, path
                from extension.op_insert_component import touch_all_fields
                touch_all_fields(component, hash_over_64(path))
                # Initialize through the real property directly so RED can test read first.
                if path == 'PayloadComponent':
                    component.PayloadComponent.choice.skein_enum_index = 'Payload'
                    component.PayloadComponent.choice.Payload = 'Original'
                else:
                    setattr(component, hash_over_64(path), original)
                index = len(self.obj.skein_two) - 1
                self.assertEqual(self.values.read_component(component, REGISTRY), original)
                self.assertEqual(self.invoke('copy', index), {'FINISHED'})
                copied = json.loads(bpy.context.window_manager.clipboard)
                self.assertEqual(copied['data'], original)
                bpy.context.window_manager.clipboard = json.dumps(dict(copied, data=replacement))
                self.assertEqual(self.invoke('paste', index), {'FINISHED'})
                self.assertEqual(self.values.read_component(self.obj.skein_two[index], REGISTRY), replacement)
                presets = bpy.data.texts.get('skein-presets.json') or bpy.data.texts.new('skein-presets.json')
                presets.clear()
                presets.write(json.dumps({path: {'default': default}}))
                self.assertEqual(self.invoke('reset', index), {'FINISHED'})
                self.assertEqual(self.values.read_component(self.obj.skein_two[index], REGISTRY), default)

    def test_f32_defaults_use_float32_semantics_and_f64_stays_strict(self):
        import struct
        rounded = struct.unpack('!f', struct.pack('!f', 1.1))[0]
        cases = [('F32Root', 1, 1.0), ('F64Root', 1, 1.0), ('F32Root', 1.1, rounded),
                 ('F32Composite', {'list': [1.1], 'choice': {'Payload': 1.1}, 'option': 1.1, 'position': [1.1, 1.1]},
                  {'list': [rounded], 'choice': {'Payload': rounded}, 'option': rounded, 'position': [rounded, rounded]})]
        for path, default, expected in cases:
            with self.subTest(path=path):
                component = self.obj.skein_two.add()
                component.name, component.selected_type_path = path, path
                from extension.op_insert_component import touch_all_fields
                touch_all_fields(component, hash_over_64(path))
                index = len(self.obj.skein_two) - 1
                presets = bpy.data.texts.get('skein-presets.json') or bpy.data.texts.new('skein-presets.json')
                presets.clear()
                presets.write(json.dumps({path: {'default': default}}))
                self.assertEqual(self.invoke('reset', index), {'FINISHED'})
                self.assertEqual(self.values.read_component(self.obj.skein_two[index], REGISTRY), expected)
        component = self.obj.skein_two.add()
        component.name, component.selected_type_path = 'F64', 'F64Root'
        component.F64Root = 1.25
        index = len(self.obj.skein_two) - 1
        before = [(c.name, c.selected_type_path, self.values.read_component(c, REGISTRY)) for c in self.obj.skein_two]
        active = self.obj.active_component_index
        with self.assertRaises(ValueError):
            self.values.replace_component(self.obj, index, REGISTRY, 1.1)
        self.assertEqual([(c.name, c.selected_type_path, self.values.read_component(c, REGISTRY)) for c in self.obj.skein_two], before)
        self.assertEqual(self.obj.active_component_index, active)
        self.assertEqual(self.values.read_component(self.obj.skein_two[index], REGISTRY), 1.25)
        self.values.replace_component(self.obj, index, REGISTRY, 1.5)
        self.assertEqual(self.values.read_component(self.obj.skein_two[index], REGISTRY), 1.5)

    def test_camera_owner_and_entity_named_component_are_supported(self):
        camera = bpy.data.cameras.new('TestCamera')
        from extension.op_insert_component import touch_all_fields
        component = camera.skein_two.add()
        component.name = 'Camera settings'
        component.selected_type_path = 'Controller'
        touch_all_fields(component, 'Controller')
        object_to_form(component, 'Controller', DATA)
        self.assertEqual(bpy.ops.wm.skein_component_copy(**self.values.target_token(component)), {'FINISHED'})
        self.assertEqual(json.loads(bpy.context.window_manager.clipboard)['data'], DATA)
        self.values.ensure_supported({'game::EntityLabel': dict(REGISTRY['Settings'], typePath='game::EntityLabel'), **REGISTRY}, 'game::EntityLabel')

    def test_undo_redo(self):
        self.invoke('copy')
        self.obj.skein_two[1].Controller.tags.entries.clear()
        name = self.obj.name
        bpy.ops.ed.undo_push(message='Before component paste')
        self.invoke('paste')
        bpy.ops.ed.undo_push(message='After component paste')
        bpy.ops.ed.undo()
        self.obj = bpy.data.objects[name]
        self.assertEqual(self.values.read_component(self.obj.skein_two[1], REGISTRY)['tags'], [])
        bpy.ops.ed.redo()
        self.obj = bpy.data.objects[name]
        self.assertEqual(self.values.read_component(self.obj.skein_two[1], REGISTRY), DATA)

    def test_source_and_target_keep_unchosen_components(self):
        source = bpy.data.objects.new('ClipboardSource', None)
        from extension.op_insert_component import touch_all_fields
        for name in ['Unchosen', 'Chosen']:
            c = source.skein_two.add()
            c.name, c.selected_type_path = name, 'Controller'
            touch_all_fields(c, 'Controller')
            object_to_form(c, 'Controller', dict(DATA, tags=[name]))
        self.assertEqual(bpy.ops.wm.skein_component_copy(**self.values.target_token(source.skein_two[1])), {'FINISHED'})
        self.assertEqual(self.invoke('paste'), {'FINISHED'})
        self.assertEqual([self.values.read_component(c, REGISTRY)['tags'] for c in source.skein_two], [['Unchosen'], ['Chosen']])
        self.assertEqual([self.values.read_component(c, REGISTRY)['tags'] for c in self.obj.skein_two], [DATA['tags'], ['Chosen'], DATA['tags']])
        self.obj.skein_two[1].Controller.tags.entries[0].value = 'independent'
        self.assertEqual(self.values.read_component(source.skein_two[1], REGISTRY)['tags'], ['Chosen'])
        with self.assertRaises(RuntimeError):
            bpy.ops.wm.skein_component_paste()
        wrong = self.values.target_token(self.obj.skein_two[1])
        wrong['type_path'] = 'Other'
        with self.assertRaises(RuntimeError):
            bpy.ops.wm.skein_component_paste(**wrong)

    def test_unrelated_lists_do_not_get_component_menu(self):
        from types import SimpleNamespace
        class NoLayout:
            @property
            def layout(self):
                raise AssertionError('Unrelated list menu was modified')
        self.values.draw_component_menu(NoLayout(), SimpleNamespace(ui_list=SimpleNamespace(bl_idname='OTHER_UL_list')))

    def test_reset_uses_cached_default_and_missing_reason(self):
        presets = bpy.data.texts.get('skein-presets.json') or bpy.data.texts.new('skein-presets.json')
        presets.clear()
        presets.write(json.dumps({'Controller': {'default': dict(DATA, tags=['default'])}}))
        self.assertEqual(self.invoke('reset'), {'FINISHED'})
        self.assertEqual(self.values.read_component(self.obj.skein_two[1], REGISTRY)['tags'], ['default'])
        presets.clear()
        presets.write('{}')
        self.assertIn('Default', self.values.default_reason('Controller'))
        before = self.values.read_component(self.obj.skein_two[1], REGISTRY)
        with self.assertRaises(RuntimeError):
            self.invoke('reset')
        self.assertEqual(self.values.read_component(self.obj.skein_two[1], REGISTRY), before)

if __name__ == '__main__':
    sys.argv = [__file__]
    if bpy.app.background:
        raise RuntimeError('Run without --background: macOS clipboard requires a GUI window')
    result = unittest.main(exit=False, verbosity=2).result
    if not result.wasSuccessful():
        sys.exit(1)
    bpy.ops.wm.quit_blender()
