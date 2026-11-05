"""Blender integration: ordered string-choice lists and explicit Sensor migration."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import bpy
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tools')]
import extension
from extension import authoring_sync, skein_panel as panel
from extension.component_values import append_component, read_component
from extension.op_registry_loading import process_registry

SENSOR = 'poc_design_editor::Sensor'
def ref(name): return {'type': {'$ref': '#/$defs/' + name}}
def schema(kind, name, **kwargs):
    return dict(kind=kind, typePath=name, shortPath=name.split('::')[-1], **kwargs)
REGISTRY = {
    'String': schema('Value', 'String', type='string'),
    'f32': schema('Value', 'f32', type='float'),
    'VecString': schema('List', 'VecString', items=ref('String')),
    'Box': schema('Struct', 'Box', properties={key: ref('f32') for key in ('width','height','depth')}),
    SENSOR: schema('Struct', SENSOR, reflectTypes=['Component'], properties={
        'shape': ref('Box'), 'enter_events':ref('VecString'), 'stay_events':ref('VecString'),
        'exit_events':ref('VecString'), 'stay_interval':ref('f32')}),
    'Other': schema('Struct', 'Other', reflectTypes=['Component'], properties={'name':ref('String')}),
}
DESCRIPTION = dict(schema='skein.authoring/v1',fields=[dict(type_path=SENSOR,field=name,
    widget='string_list_choice', choices=[dict(id='welcome',label='Welcome [message]'),
    dict(id='wave',label='Wave [animation]'),dict(id='clear',label='Clear [message]')],error=None)
    for name in ('enter_events','stay_events','exit_events')],previews=[],validation_method=None)
VALUE = dict(shape=dict(width=2.,height=3.,depth=4.),enter_events=['welcome','wave'],
             stay_events=[],exit_events=['clear'],stay_interval=1.)

def cache(name, value):
    text=bpy.data.texts.get(name) or bpy.data.texts.new(name)
    text.clear();text.write(json.dumps(value))

class Layout:
    def __init__(self): self.menus=[];self.properties=[];self.operations=[];self.pointers={};self.labels=[];self.enabled=True
    def box(self): return self
    def row(self,**kw): return self
    def label(self,**kw): self.labels.append(kw)
    def context_pointer_set(self,key,value): self.pointers[key]=value
    def context_string_set(self,key,value): pass
    def operator(self,name,**kw): self.operations.append(name);return SimpleNamespace()
    def prop(self,obj,key,**kw): self.properties.append(key)
    def menu(self,name,**kw): self.menus.append((name,kw))
    def separator(self,**kw): pass

class ChoiceTests(unittest.TestCase):
    def setUp(self):
        for owner in bpy.data.objects: owner.skein_two.clear()
        process_registry(bpy.context, REGISTRY)
        cache('skein-registry.json', REGISTRY);cache('skein-authoring.json',DESCRIPTION)
        authoring_sync._status.update(state='ready')
        self.obj=bpy.context.object
        append_component(self.obj, REGISTRY, SENSOR,VALUE)
        self.form=getattr(self.obj.skein_two[0], SENSOR)

    def test_real_list_picker_selects_stable_ids_and_reorders(self):
        events=self.form.enter_events
        with bpy.context.temp_override(skein_choice_parent=self.form, skein_choice_target=events.entries[0]):
            self.assertEqual(bpy.ops.skein.set_string_choice(field='enter_events',value='clear'),{'FINISHED'})
            self.assertEqual(bpy.ops.skein.set_string_choice(field='enter_events',value='missing'),{'CANCELLED'})
        with bpy.context.temp_override(skein_list=events):
            self.assertEqual(bpy.ops.wm.skein_list_move(index=0,direction='DOWN'),{'FINISHED'})
            self.assertEqual(bpy.ops.wm.skein_list_add(),{'FINISHED'})
            self.assertEqual(bpy.ops.wm.skein_list_remove(index=2),{'FINISHED'})
        actual=read_component(self.obj.skein_two[0],REGISTRY)
        self.assertEqual(actual['enter_events'],['wave','clear'])
        self.assertEqual(actual['exit_events'],['clear'])
        layout=Layout();panel.render_two(layout,self.obj.skein_two[0],SENSOR)
        self.assertEqual(len(layout.menus),3)
        self.assertNotIn('value',layout.properties)
        self.assertIn('wm.skein_list_add',layout.operations)

    def test_cached_choices_remain_editable_without_runtime_or_online_access(self):
        for state in ('offline', 'syncing', 'error', 'unverified'):
            with self.subTest(state=state):
                authoring_sync._status.update(state=state)
                layout=Layout()
                panel.draw_string_choice(layout,self.form.enter_events.entries[0],
                    'enter_events',DESCRIPTION['fields'][0],parent=self.form)
                self.assertTrue(layout.enabled)
                self.assertTrue(any('validation pending' in item.get('text','') for item in layout.labels))
                with bpy.context.temp_override(skein_choice_parent=self.form,
                        skein_choice_target=self.form.enter_events.entries[0]):
                    self.assertEqual(bpy.ops.skein.set_string_choice(field='enter_events',value='clear'),{'FINISHED'})
                    self.assertEqual(bpy.ops.skein.set_string_choice(field='enter_events',value='unknown'),{'CANCELLED'})
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'offline.blend'
            bpy.ops.wm.save_as_mainfile(filepath=str(path))
            bpy.ops.wm.open_mainfile(filepath=str(path))
            self.assertEqual(read_component(bpy.context.object.skein_two[0],REGISTRY)['enter_events'],['clear','wave'])

    def test_unavailable_catalog_still_blocks_choice_assignment(self):
        description=copy.deepcopy(DESCRIPTION)
        description['fields'][0].update(choices=None,error='Catalog invalid')
        cache('skein-authoring.json',description)
        authoring_sync._status.update(state='offline')
        with bpy.context.temp_override(skein_choice_parent=self.form,
                skein_choice_target=self.form.enter_events.entries[0]):
            self.assertEqual(bpy.ops.skein.set_string_choice(field='enter_events',value='clear'),{'CANCELLED'})
        self.assertEqual(self.form.enter_events.entries[0].value,'welcome')

    def test_missing_reference_remains_visible_and_saved(self):
        self.form.enter_events.entries[0].value='deleted'
        layout=Layout();panel.render_two(layout,self.obj.skein_two[0],SENSOR)
        self.assertTrue(any(kw['text']=='deleted' and kw['icon']=='ERROR' for _,kw in layout.menus))
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'choices.blend'
            bpy.ops.wm.save_as_mainfile(filepath=str(path))
            bpy.ops.wm.open_mainfile(filepath=str(path))
            actual=read_component(bpy.context.object.skein_two[0],REGISTRY)
            self.assertEqual(actual['enter_events'][0],'deleted')

class MigrationTests(unittest.TestCase):
    def setUp(self):
        for owner in bpy.data.objects: owner.skein_two.clear()
        self.old=copy.deepcopy(REGISTRY)
        self.old[SENSOR]['properties']={key:ref('String') for key in
            ('enter_text','exit_text','enter_event','stay_event','exit_event')}
        self.old[SENSOR]['properties'].update(shape=ref('Box'),stay_interval=ref('f32'))
        process_registry(bpy.context,self.old)
        cache('skein-registry.json',self.old)
        cache('skein-authoring.json',dict(schema='skein.authoring/v1',fields=[],previews=[],validation_method=None))
        self.obj=bpy.context.object
        self.before=dict(shape=VALUE['shape'],enter_text='Welcome',exit_text='',
            enter_event='wave',stay_event='',exit_event='',stay_interval=1.)
        append_component(self.obj,self.old,'Other',dict(name='retained'))
        append_component(self.obj,self.old,SENSOR,self.before)
        self.mapping=dict(schema='arforge.sensor-migration/v1',sensors=[dict(
            asset='models/sensor.glb',node_name=self.obj.name,before=self.before,after=VALUE)])

    def test_migration_backup_save_reopen_and_export_preserve_components(self):
        from extension.sensor_event_migration import migrate
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            source=root/'source.blend';backup=root/'before.blend';output=root/'after.blend'
            bpy.ops.wm.save_as_mainfile(filepath=str(source))
            original=source.read_bytes()
            mapping=root/'migration-report.json';mapping.write_text(json.dumps(self.mapping))
            registry=root/'registry.json';registry.write_text(json.dumps(REGISTRY))
            self.assertEqual(bpy.ops.wm.skein_migrate_sensor_events(
                mapping_path=str(mapping),registry_path=str(registry),backup_path=str(backup)),{'FINISHED'})
            self.assertEqual(migrate(bpy.context,REGISTRY,self.mapping,root/'unused.blend'),0)
            self.assertFalse((root/'unused.blend').exists())
            self.assertTrue(backup.is_file())
            self.assertEqual(source.read_bytes(),original)
            self.assertEqual(bpy.data.filepath,str(source))
            self.assertEqual(read_component(self.obj.skein_two[0],REGISTRY),dict(name='retained'))
            self.assertEqual(read_component(self.obj.skein_two[1],REGISTRY),VALUE)
            fields=json.loads(bpy.data.texts['skein-authoring.json'].as_string())['fields']
            self.assertEqual({field['field'] for field in fields if field['widget']=='string_list_choice'},
                             {'enter_events','stay_events','exit_events'})
            layout=Layout();panel.render_two(layout,self.obj.skein_two[1],SENSOR)
            self.assertEqual(len(layout.menus),3)
            self.assertNotIn('value',layout.properties)
            bpy.ops.wm.save_as_mainfile(filepath=str(output))
            bpy.ops.wm.open_mainfile(filepath=str(output))
            self.assertEqual(read_component(bpy.context.object.skein_two[1],REGISTRY),VALUE)
            settings=bpy.context.scene.skein_extension_properties
            settings.enabled,settings.extras,settings.extensions=True,True,False
            path=root/'after.glb'
            self.assertEqual(bpy.ops.export_scene.gltf(filepath=str(path),export_format='GLB',
                export_extras=True),{'FINISHED'})
            import struct
            data=path.read_bytes();size=struct.unpack_from('<I',data,12)[0]
            document=json.loads(data[20:20+size])
            components=[component[SENSOR] for node in document['nodes']
                for component in node.get('extras',{}).get('skein',[]) if SENSOR in component]
            self.assertEqual(components,[VALUE])
            bpy.ops.wm.open_mainfile(filepath=str(backup))
            self.assertEqual(read_component(bpy.context.object.skein_two[1],self.old),self.before)

    def test_bad_mapping_and_registry_keep_everything_before_backup(self):
        from extension.sensor_event_migration import migrate
        bad=copy.deepcopy(self.mapping);bad['sensors'][0]['before']['enter_text']='different'
        with tempfile.TemporaryDirectory() as temporary:
            backup=Path(temporary)/'before.blend'
            with self.assertRaises(ValueError):migrate(bpy.context,REGISTRY,bad,backup)
            self.assertFalse(backup.exists())
            self.assertEqual(read_component(self.obj.skein_two[1],self.old),self.before)
            changed=copy.deepcopy(REGISTRY);changed['Other']['properties']={}
            with self.assertRaises(ValueError):migrate(bpy.context,changed,self.mapping,backup)
            self.assertFalse(backup.exists())

    def test_ambiguous_asset_and_existing_backup_require_explicit_resolution(self):
        from extension.sensor_event_migration import migrate
        other=copy.deepcopy(self.mapping['sensors'][0]);other['asset']='models/other.glb'
        other['after']['enter_events']=['different']
        ambiguous=copy.deepcopy(self.mapping);ambiguous['sensors'].append(other)
        with tempfile.TemporaryDirectory() as temporary:
            backup=Path(temporary)/'before.blend'
            with self.assertRaisesRegex(ValueError,'found 2'):
                migrate(bpy.context,REGISTRY,ambiguous,backup)
            self.assertFalse(backup.exists())
            backup.write_bytes(b'keep')
            with self.assertRaises(ValueError):migrate(bpy.context,REGISTRY,self.mapping,backup)
            self.assertEqual(backup.read_bytes(),b'keep')
            self.assertEqual(migrate(bpy.context,REGISTRY,ambiguous,
                Path(temporary)/'actual.blend',asset_path='models/sensor.glb'),1)
            self.assertEqual(read_component(self.obj.skein_two[1],REGISTRY),VALUE)

    def test_asset_filter_resolves_one_ambiguous_object_and_keeps_other_unique_objects(self):
        from extension.sensor_event_migration import migrate
        other_obj=bpy.data.objects.new('SecondSensor',None)
        bpy.context.scene.collection.objects.link(other_obj)
        try:
            append_component(other_obj,self.old,SENSOR,self.before)
            report=copy.deepcopy(self.mapping)
            alternative=copy.deepcopy(report['sensors'][0])
            alternative['asset']='models/alternative.glb'
            alternative['after']['enter_events']=['clear']
            unique=copy.deepcopy(report['sensors'][0])
            unique.update(asset='models/second.glb',node_name=other_obj.name)
            unique['after']['enter_events']=['wave']
            report['sensors'].extend([alternative,unique])
            with tempfile.TemporaryDirectory() as temporary:
                backup=Path(temporary)/'before.blend'
                with self.assertRaisesRegex(ValueError,'found 2'):
                    migrate(bpy.context,REGISTRY,report,backup)
                self.assertFalse(backup.exists())
                self.assertEqual(migrate(bpy.context,REGISTRY,report,backup,
                    asset_path='models/sensor.glb'),2)
                self.assertEqual(read_component(self.obj.skein_two[1],REGISTRY),VALUE)
                self.assertEqual(read_component(other_obj.skein_two[0],REGISTRY),unique['after'])
        finally:
            bpy.data.objects.remove(other_obj,do_unlink=True)

    def test_failure_during_rebuild_rolls_back_registry_values_and_caches(self):
        from extension import sensor_event_migration as migration
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(migration,'replace_component',side_effect=ValueError('injected failure')):
                with self.assertRaisesRegex(ValueError,'injected failure'):
                    migration.migrate(bpy.context,REGISTRY,self.mapping,Path(temporary)/'backup.blend')
            self.assertEqual(json.loads(bpy.context.window_manager.skein.registry),self.old)
            self.assertEqual(read_component(self.obj.skein_two[1],self.old),self.before)
            self.assertEqual(read_component(self.obj.skein_two[0],self.old),dict(name='retained'))
            self.assertEqual(json.loads(bpy.data.texts['skein-registry.json'].as_string()),self.old)

if __name__=='__main__':
    extension.register()
    bpy.context.preferences.addons.new().module='extension'
    result=unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    if not result.wasSuccessful(): raise AssertionError('Sensor event tests failed')
