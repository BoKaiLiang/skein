"""Run isolated Blender --background --factory-startup --python tools/test_panel.py."""
import json
import copy
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import bpy
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tools')]
import extension
from test_lists import REGISTRY, DATA, schema
from extension import skein_panel as panel, validation, component_values
from extension.op_registry_loading import process_registry
from extension.op_insert_component import touch_all_fields
from extension.object_to_form import object_to_form

REGISTRY['StaticShape'] = schema('Struct', 'StaticShape', reflectTypes=['Component'], properties={})
DESCRIPTION = dict(schema='skein.authoring/v1', fields=[dict(type_path='Action', field='clip', widget='animation_clip')], validation_method=None, previews=[dict(component_type='Controller', list_field='actions', identity_field='id', label='Remote preview', method='example/preview', request_fields={'id': 'id'}, stop={'method': 'example/preview', 'params': {'stop': True}})])

class Layout:
    def __init__(self, open_panels=()):
        self.labels, self.operations, self.properties, self.panels = [], [], [], []
        self.open_panels = set(open_panels)
        self.alert = False
        self.kind = 'root'
    def child(self, kind):
        result = copy.copy(self)
        result.kind = kind
        return result
    def row(self, **kwargs): return self.child('row')
    def column(self, **kwargs): return self.child('column')
    def box(self): return self.child('box')
    def label(self, **kwargs): self.labels.append(dict(kwargs, alert=self.alert))
    def operator(self, name, **kwargs):
        self.operations.append((name, kwargs))
        return SimpleNamespace()
    def prop(self, data, field, **kwargs):
        self.properties.append(field)
        assert hasattr(data, field), field
    def prop_search(self, *args, **kwargs): pass
    def template_list(self, *args, **kwargs): pass
    def popover(self, *args, **kwargs): pass
    def separator(self, **kwargs): pass
    def context_pointer_set(self, *args): pass
    def panel(self, key, default_closed=False):
        assert self.kind == 'root', 'Blender panels require full region width'
        self.panels.append((key, default_closed))
        return self, self if key in self.open_panels else None

class PanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        extension.register()
        process_registry(bpy.context, REGISTRY)
        for name, value in [('skein-registry.json', REGISTRY), ('skein-authoring.json', DESCRIPTION)]:
            text = bpy.data.texts.get(name) or bpy.data.texts.new(name)
            text.clear(); text.write(json.dumps(value))
    def setUp(self):
        self.owner = bpy.context.object
        self.owner.skein_two.clear()
        for name, value in [('Controller', DATA), ('StaticShape', {})]:
            item = self.owner.skein_two.add()
            item.name = item.selected_type_path = name
            touch_all_fields(item, name)
            object_to_form(item, name, value)
        self.owner.active_component_index = 0
    def draw(self, layout=None, diagnostics=()):
        layout = layout or Layout()
        with patch.object(panel.authoring_sync, 'status', return_value=dict(state='ready', message='Technical full explanation', endpoint='http://runtime:1234', fetched_at='2026-10-27T12:00:00Z')), patch.object(validation, 'get_owner_diagnostics', return_value=list(diagnostics)), patch.object(panel.clip_picker, 'request_clip_refresh') as refresh, patch.object(panel.clip_picker, 'draw_clip_field') as field:
            panel.draw_generic_panel(bpy.context, self.owner, layout, 'object', 'OBJECT_PT_skein_preset_panel')
            return layout, refresh.call_count, field.call_args_list
    def test_animation_controls_only_for_selected_animation_component(self):
        self.owner.active_component_index = 1
        layout, refreshes, fields = self.draw()
        self.assertEqual(refreshes, 0)
        self.assertFalse(fields)
        self.assertNotIn('clip_export_mode', layout.properties)
        self.assertNotIn('wm.skein_refresh_clips', [name for name, _ in layout.operations])
        self.owner.active_component_index = 0
        layout, refreshes, fields = self.draw()
        self.assertEqual(refreshes, 1)
        self.assertEqual(len(fields), 2)
        self.assertTrue(all(call.kwargs.get('type_path') == 'Action' for call in fields))
        expanded, _, _ = self.draw(Layout(['skein_animation_advanced']))
        self.assertIn('clip_export_mode', expanded.properties)
        self.assertIn('clip_export_animations', expanded.properties)
        self.assertNotIn('clip_export_mode', layout.properties)
        self.assertTrue(all(name not in {'wm.skein_action_preview', 'wm.skein_stop_action_preview'} for name, _ in layout.operations))
    def test_ready_one_line_and_connection_details_collapsed(self):
        layout, _, _ = self.draw()
        text = ' '.join(label['text'] for label in layout.labels)
        self.assertIn('Ready', text)
        self.assertNotIn('2026-10-27', text)
        self.assertNotIn('http://runtime', text)
        self.assertNotIn('Technical full explanation', text)
        self.assertIn(('skein_connection_details', True), layout.panels)
        expanded, _, _ = self.draw(Layout(['skein_connection_details']))
        self.assertIn('http://runtime:1234', ' '.join(label['text'] for label in expanded.labels))
    def test_messages_group_by_severity_and_hide_paths_until_details(self):
        messages = [dict(severity=severity, object='Asset', field='example::VeryLongController.actions[0].clip', message=message) for severity, message in [('WARNING', 'Choose a clip'), ('ERROR', 'Clip is missing'), ('INFO', 'Cached data')]]
        layout = Layout()
        self.assertTrue(hasattr(panel, 'draw_diagnostics'))
        panel.draw_diagnostics(layout, messages)
        text = ' '.join(label['text'] for label in layout.labels)
        self.assertLess(text.index('Errors'), text.index('Warnings'))
        self.assertIn('Info', text)
        self.assertNotIn('example::', text)
        self.assertTrue(any(label['alert'] and label['icon'] == 'ERROR' for label in layout.labels))
        self.assertTrue(validation.format_diagnostic(messages[1]).startswith('Error:'))
        self.assertNotIn('example::', validation.format_diagnostic(messages[1]))
        self.assertIn('example::', validation.format_diagnostic(messages[1], details=True))
        self.assertIn('actions[0].clip', validation.format_diagnostic(dict(messages[1], field='actions[0].clip')))
        expanded = Layout(['skein_diagnostic_details'])
        panel.draw_diagnostics(expanded, messages)
        self.assertIn('example::', ' '.join(label['text'] for label in expanded.labels))
    def test_sync_error_is_visibly_distinct_from_ready(self):
        layout = Layout()
        panel.draw_sync_status(layout, dict(state='error', message='Long technical error', endpoint='', fetched_at=''))
        self.assertTrue(any(label['alert'] and label.get('icon') == 'ERROR' for label in layout.labels))
        self.assertNotIn('Long technical error', ' '.join(label['text'] for label in layout.labels))

    def test_reset_labels_use_artist_language(self):
        self.assertNotIn('Rust', component_values.ResetComponent.bl_label)
        self.assertIn('Default', component_values.ResetComponent.bl_label)

if __name__ == '__main__':
    sys.argv = [__file__]
    result = unittest.main(exit=False, verbosity=2).result
    if not result.wasSuccessful(): sys.exit(1)
