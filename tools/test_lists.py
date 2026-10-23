"""Run: Blender --background --factory-startup --python tools/test_lists.py."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from extension import register
from extension.form_to_object import get_data_from_active_editor
from extension.object_to_form import object_to_form
from extension.op_registry_loading import process_registry
from extension.skein_panel import render_two


class RecordingLayout:
    """Exercise recursive panel construction against real registered bpy properties."""
    def __init__(self):
        self.labels = []
        self.operators = []

    def box(self):
        return self

    def row(self, **kwargs):
        return self

    def label(self, **kwargs):
        self.labels.append(kwargs["text"])

    def context_pointer_set(self, key, value):
        assert key == "skein_list" and value._skein_is_list

    def operator(self, name, **kwargs):
        from types import SimpleNamespace
        self.operators.append(name)
        return SimpleNamespace()

    def prop(self, value, key, **kwargs):
        assert hasattr(value, key), key

    def separator(self, **kwargs):
        pass


def ref(name):
    return {"type": {"$ref": "#/$defs/" + name}}


def schema(kind, name, **kwargs):
    return dict(kind=kind, typePath=name, shortPath=name.split("::")[-1], **kwargs)


REGISTRY = {
    "String": schema("Value", "String", type="string"),
    "f32": schema("Value", "f32", type="float"),
    "bool": schema("Value", "bool", type="boolean"),
    # Real Bevy KeyCode is an object enum because Unidentified carries data.
    "KeyCode": schema("Enum", "KeyCode", type="object", oneOf=[
        dict(schema("Tuple", "KeyCode::Unidentified", type="array", prefixItems=[ref("String")]), shortPath="Unidentified"),
        {"shortPath": "ShiftLeft", "typePath": "KeyCode::ShiftLeft"},
        {"shortPath": "Space", "typePath": "KeyCode::Space"},
    ]),
    "core::option::Option<KeyCode>": schema(
        "Enum", "core::option::Option<KeyCode>", type="object", modulePath="core::option",
        oneOf=[{"shortPath": "None", "typePath": "OptionKey::None"},
               dict(schema("Tuple", "OptionKey::Some", type="array", prefixItems=[ref("KeyCode")]), shortPath="Some")]),
    "Settings": schema("Struct", "Settings", properties={"looping": ref("bool"), "speed": ref("f32")}),
    "Action": schema("Struct", "Action", properties={"id": ref("String"), "clip": ref("String"),
        "settings": ref("Settings"), "binding": ref("core::option::Option<KeyCode>")}),
    "VecAction": schema("List", "VecAction", type="array", items=ref("Action")),
    "VecString": schema("List", "VecString", type="array", items=ref("String")),
    "VecVecString": schema("List", "VecVecString", type="array", items=ref("VecString")),
    "UnsupportedTuple": schema("Tuple", "UnsupportedTuple", prefixItems=[ref("String"), ref("String")]),
    "VecUnsupported": schema("List", "VecUnsupported", type="array", items=ref("UnsupportedTuple")),
    "Map": schema("Map", "Map"),
    "Set": schema("Set", "Set"),
    "Defaults": schema("Struct", "Defaults", reflectTypes=["Component"], properties={"map": ref("Map"), "set": ref("Set"), "unsupported": ref("VecUnsupported")}),
    "Controller": schema("Struct", "Controller", reflectTypes=["Component"], properties={
        "actions": ref("VecAction"), "tags": ref("VecString"), "groups": ref("VecVecString")}),
}
DATA = {"actions": [
    {"id": "idle", "clip": "Idle", "settings": {"looping": True, "speed": 1.25}, "binding": None},
    {"id": "roll", "clip": "Roll", "settings": {"looping": False, "speed": 0.5}, "binding": "ShiftLeft"},
], "tags": ["player", "hero"], "groups": [["one", "two"], [], ["three"]]}
REGISTRY["poc_design_editor::CharacterController"] = schema(
    "Struct", "poc_design_editor::CharacterController", reflectTypes=["Component"],
    properties=REGISTRY["Controller"]["properties"])


class ListTests(unittest.TestCase):
    def setUp(self):
        self.obj = bpy.context.active_object
        self.obj.skein_two.clear()
        bpy.context.window_manager.selected_component = "Controller"
        bpy.ops.object.insert_component()
        self.container = self.obj.skein_two[0]

    def test_unsupported_elements_and_map_set_keep_existing_defaults(self):
        bpy.context.window_manager.selected_component = "Defaults"
        bpy.ops.object.insert_component()
        defaults = self.obj.skein_two[-1]
        self.assertEqual(defaults.Defaults.unsupported.force_default, "list")
        self.assertEqual(get_data_from_active_editor(defaults, "Defaults"), {"map": {}, "set": [], "unsupported": []})

    def test_old_saved_component_gets_empty_new_list(self):
        old_registry = json.loads(json.dumps(REGISTRY))
        del old_registry["Controller"]["properties"]["actions"]
        self.obj.skein_two.clear()
        process_registry(bpy.context, old_registry)
        bpy.context.window_manager.selected_component = "Controller"
        bpy.ops.object.insert_component()
        old_data = {"tags": ["old"], "groups": [["kept"]]}
        object_to_form(self.obj.skein_two[0], "Controller", old_data)
        embedded = bpy.data.texts["skein-registry.json"]
        embedded.clear()
        embedded.write(json.dumps(old_registry))
        name = self.obj.name
        with tempfile.TemporaryDirectory(prefix="skein-old-lists-") as temp:
            path = str(Path(temp) / "old.blend")
            bpy.ops.wm.save_as_mainfile(filepath=path)
            bpy.ops.wm.open_mainfile(filepath=path)
            embedded = bpy.data.texts["skein-registry.json"]
            embedded.clear()
            embedded.write(json.dumps(REGISTRY))
            process_registry(bpy.context, REGISTRY)
            self.assertEqual(get_data_from_active_editor(bpy.data.objects[name].skein_two[0], "Controller"),
                             dict(old_data, actions=[]))

    def test_old_action_rows_get_none_for_new_option_state(self):
        old_data = json.loads(json.dumps(DATA))
        object_to_form(self.container, "Controller", old_data)
        embedded = bpy.data.texts["skein-registry.json"]
        embedded.clear()
        embedded.write(json.dumps(REGISTRY))
        name = self.obj.name
        with tempfile.TemporaryDirectory(prefix="skein-old-action-state-") as temp:
            path = str(Path(temp) / "old-actions.blend")
            bpy.ops.wm.save_as_mainfile(filepath=path)
            bpy.ops.wm.open_mainfile(filepath=path)
        self.obj = bpy.data.objects[name]
        updated = json.loads(json.dumps(REGISTRY))
        updated["ActionState"] = schema("Enum", "ActionState", type="string", oneOf=["Idle", "Move", "Jump"])
        updated["core::option::Option<ActionState>"] = schema(
            "Enum", "core::option::Option<ActionState>", type="object", modulePath="core::option",
            oneOf=[{"shortPath": "None", "typePath": "State::None"},
                   dict(schema("Tuple", "State::Some", type="array", prefixItems=[ref("ActionState")]), shortPath="Some")])
        updated["Action"]["properties"]["state"] = ref("core::option::Option<ActionState>")
        process_registry(bpy.context, updated)
        actual = get_data_from_active_editor(self.obj.skein_two[0], "Controller")
        expected = dict(old_data, actions=[dict(action, state=None) for action in old_data["actions"]])
        self.assertEqual(actual, expected)
        actual["actions"][0]["state"] = "Idle"
        object_to_form(self.obj.skein_two[0], "Controller", actual)
        self.assertEqual(get_data_from_active_editor(self.obj.skein_two[0], "Controller"), actual)
        process_registry(bpy.context, REGISTRY)

    def test_roundtrip_nested_and_scalar_lists(self):
        object_to_form(self.container, "Controller", DATA)
        self.assertEqual(get_data_from_active_editor(self.container, "Controller"), DATA)
        replacement = {"actions": [DATA["actions"][1]], "tags": [], "groups": [["replacement"]]}
        object_to_form(self.container, "Controller", replacement)
        self.assertEqual(get_data_from_active_editor(self.container, "Controller"), replacement)

    def test_option_complex_enum_with_unit_variant(self):
        data = dict(DATA, actions=[DATA["actions"][1]])
        object_to_form(self.container, "Controller", data)
        self.assertEqual(get_data_from_active_editor(self.container, "Controller"), data)
        binding = self.container.Controller.actions.entries[0].value.binding
        self.assertEqual(binding.skein_enum_index, "Some")
        self.assertEqual(binding.Some.skein_enum_index, "ShiftLeft")

    def test_list_panel_renders_new_schema_fields_and_items(self):
        controller = self.container.Controller
        del controller["actions"]
        layout = RecordingLayout()
        render_two(layout, controller, "actions")
        self.assertIn("wm.skein_list_add", layout.operators)
        object_to_form(self.container, "Controller", DATA)
        layout = RecordingLayout()
        render_two(layout, controller, "actions")
        self.assertEqual(layout.operators.count("wm.skein_list_remove"), 2)

    def test_character_panel_wraps_diagnostics_and_marks_bindings_data_only(self):
        from extension.skein_panel import draw_action_diagnostics
        bpy.context.window_manager.selected_component = "poc_design_editor::CharacterController"
        bpy.ops.object.insert_component()
        object_to_form(self.obj.skein_two[-1], "poc_design_editor::CharacterController", DATA)
        layout = RecordingLayout()
        layout.prop = lambda *args, **kwargs: None
        draw_action_diagnostics(layout, self.obj)
        text = " ".join(layout.labels)
        self.assertIn("saved/exported", text)
        self.assertIn("actions[0].id", text)
        self.assertTrue(all(len(line) <= 55 for line in layout.labels))

    def test_list_operators(self):
        from extension import op_list
        # Parent addon registers this module in normal installations.
        if not op_list.SkeinListAdd.is_registered:
            op_list.register()
        actions = self.container.Controller.actions
        with bpy.context.temp_override(skein_list=actions):
            self.assertEqual(bpy.ops.wm.skein_list_add(), {"FINISHED"})
            self.assertEqual(bpy.ops.wm.skein_list_add(), {"FINISHED"})
            actions.entries[1].value.id = "kept"
            self.assertEqual(bpy.ops.wm.skein_list_remove(index=0), {"FINISHED"})
            self.assertEqual(actions.entries[0].value.id, "kept")
            self.assertEqual(bpy.ops.wm.skein_list_remove(index=9), {"CANCELLED"})
        data = get_data_from_active_editor(self.container, "Controller")
        self.assertEqual(data["actions"][0]["id"], "kept")
        self.assertIn("settings", data["actions"][0])
        self.assertIsNone(data["actions"][0]["binding"])
        with bpy.context.temp_override(skein_list=self.container.Controller.tags):
            self.assertEqual(bpy.ops.wm.skein_list_add(), {"FINISHED"})
        self.assertEqual(get_data_from_active_editor(self.container, "Controller")["tags"], [""])
        with bpy.context.temp_override(skein_list=self.container.Controller.groups):
            self.assertEqual(bpy.ops.wm.skein_list_add(), {"FINISHED"})
        self.assertEqual(get_data_from_active_editor(self.container, "Controller")["groups"], [[]])
        self.assertEqual(bpy.ops.wm.skein_list_add(), {"CANCELLED"})

    def test_save_reopen_and_registry_reload(self):
        object_to_form(self.container, "Controller", DATA)
        name = self.obj.name
        with tempfile.TemporaryDirectory(prefix="skein-lists-") as temp:
            path = str(Path(temp) / "lists.blend")
            bpy.ops.wm.save_as_mainfile(filepath=path)
            bpy.ops.wm.open_mainfile(filepath=path)
            container = bpy.data.objects[name].skein_two[0]
            self.assertEqual(get_data_from_active_editor(container, "Controller"), DATA)
            # Mirrors manual Reload Local Skein Registry after loading a file.
            process_registry(bpy.context, REGISTRY)
            self.assertEqual(get_data_from_active_editor(bpy.data.objects[name].skein_two[0], "Controller"), DATA)


if __name__ == "__main__":
    register()
    bpy.data.texts.new("skein-registry.json").write(json.dumps(REGISTRY))
    process_registry(bpy.context, REGISTRY)
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ListTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        sys.exit(1)
