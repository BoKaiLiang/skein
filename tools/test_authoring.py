"""Pure contract tests for project-described editors (no Blender needed)."""
import copy
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('authoring', ROOT / 'extension/authoring.py')
authoring = importlib.util.module_from_spec(spec)
spec.loader.exec_module(authoring)


def reference(name):
    return {'type': {'$ref': '#/$defs/' + name}}


def fixture():
    registry = {
        'String': {'kind': 'Value', 'type': 'string'},
        'bool': {'kind': 'Value', 'type': 'boolean'},
        'f32': {'kind': 'Value', 'type': 'float'},
        'demo::Motion': {'kind': 'Struct', 'properties': {
            'name': reference('String'), 'animation': reference('String'),
            'repeat': reference('bool'), 'rate': reference('f32')}},
        'VecMotion': {'kind': 'List', 'items': reference('demo::Motion')},
        'demo::Unit': {'kind': 'Struct', 'reflectTypes': ['Component'],
                       'properties': {'motions': reference('VecMotion')}},
    }
    description = {'schema': 'skein.authoring/v1', 'fields': [
        {'type_path': 'demo::Motion', 'field': 'animation', 'widget': 'animation_clip'}],
        'validation_method': 'demo/validate', 'previews': [{
            'component_type': 'demo::Unit', 'list_field': 'motions', 'identity_field': 'name',
            'label': 'Preview', 'method': 'demo/preview',
            'request_fields': {'key': 'name', 'clip_name': 'animation', 'loop': 'repeat', 'speed': 'rate'},
            'stop': {'method': 'demo/stop', 'params': {}}}]}
    return registry, description


class AuthoringTests(unittest.TestCase):
    def test_single_field_wrappers_preserve_clip_traversal_in_nested_lists_and_options(self):
        registry, description = fixture()
        registry['Wrapped'] = dict(kind='TupleStruct', prefixItems=[reference('demo::Motion')])
        registry['VecWrapped'] = dict(kind='List', items=reference('Wrapped'))
        registry['Outer'] = dict(kind='Struct', properties={'items':reference('VecWrapped')})
        value = dict(name='a', animation='Missing', repeat=True, rate=1.0)
        self.assertEqual(list(authoring.clip_fields(registry, 'Wrapped', value, description)), [('animation','Missing')])
        self.assertEqual(list(authoring.clip_fields(registry, 'Outer', {'items':[value]}, description)), [('items[0].animation','Missing')])
        registry['core::option::Option<Wrapped>'] = dict(kind='Enum', modulePath='core::option', type='object', oneOf=[dict(shortPath='None'),dict(shortPath='Some', prefixItems=[reference('Wrapped')])])
        self.assertEqual(list(authoring.clip_fields(registry, 'core::option::Option<Wrapped>', value, description)), [('animation','Missing')])

    def test_arbitrary_type_and_field_names_drive_editors_and_preview(self):
        registry, description = fixture()
        result = authoring.validate_description(description, registry)
        self.assertEqual(authoring.field_widget(result, 'demo::Motion', 'animation'), 'animation_clip')
        self.assertIsNone(authoring.field_widget(result, 'demo::Motion', 'name'))
        operation = authoring.preview_operation(result, 'demo::Unit', 'motions')
        self.assertEqual(authoring.preview_params(operation, {
            'name': 'walk', 'animation': 'Exported Walk', 'repeat': True, 'rate': 1.5}),
            {'key': 'walk', 'clip_name': 'Exported Walk', 'loop': True, 'speed': 1.5})

    def test_invalid_description_keeps_schema_and_field_checks_at_boundary(self):
        registry, description = fixture()
        for change in ('schema', 'field', 'widget', 'duplicate', 'preview'):
            bad = copy.deepcopy(description)
            if change == 'schema': bad['schema'] = 'unknown/v2'
            if change == 'field': bad['fields'][0]['field'] = 'rate'
            if change == 'widget': bad['fields'][0]['widget'] = 'python_expression'
            if change == 'duplicate': bad['fields'] *= 2
            if change == 'preview': bad['previews'][0]['request_fields']['extra'] = 'missing'
            with self.subTest(change=change), self.assertRaises(ValueError):
                authoring.validate_description(bad, registry)

    def test_clip_walk_follows_nested_list_elements_without_field_name_assumptions(self):
        registry, description = fixture()
        value = {'motions': [dict(name='walk', animation='Exported Walk', repeat=True, rate=1)]}
        self.assertEqual(list(authoring.clip_fields(registry, 'demo::Unit', value, description)),
                         [('motions[0].animation', 'Exported Walk')])
        self.assertEqual(value['motions'][0]['name'], 'walk')

    def test_optional_clip_struct_and_none(self):
        registry, description = fixture()
        registry['OptionalMotion'] = {'kind': 'Enum', 'modulePath': 'core::option', 'oneOf': [
            {'shortPath': 'None', 'kind': 'Unit'},
            {'shortPath': 'Some', 'kind': 'Tuple', 'prefixItems': [reference('demo::Motion')]}]}
        registry['demo::Unit']['properties']['optional'] = reference('OptionalMotion')
        value = {'motions': [], 'optional': None}
        self.assertEqual(list(authoring.clip_fields(registry, 'demo::Unit', value, description)), [])
        value['optional'] = dict(name='idle', animation='Idle', repeat=True, rate=1)
        self.assertEqual(list(authoring.clip_fields(registry, 'demo::Unit', value, description)),
                         [('optional.animation', 'Idle')])

    def test_clip_errors_preserve_value_and_only_unique_named_values_are_selectable(self):
        for value, names in [('Old', ['New']), ('Walk', ['Walk', 'Walk']), ('', [None, ''])]:
            diagnostics = authoring.clip_diagnostics(value, names, 'motions[0].animation')
            self.assertEqual(diagnostics[0]['severity'], 'ERROR')
            self.assertEqual(diagnostics[0]['field'], 'motions[0].animation')
        self.assertEqual(authoring.clip_diagnostics('Walk', ['Walk', 'Run'], 'animation'), [])
        self.assertEqual(authoring.selectable_clips(['Walk', None, '', 'Walk', 'Idle']), ['Idle'])


if __name__ == '__main__':
    unittest.main()
