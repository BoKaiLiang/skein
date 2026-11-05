"""Blender source-copy proof: -- SOURCE.blend REGISTRY.json REPORT.json NEW_OUTPUT_DIR."""
import hashlib
import json
from pathlib import Path
import shutil
import struct
import sys

import bpy
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import extension
from extension import authoring_sync
from extension.component_values import read_component
from extension.op_registry_loading import component_owners
from extension.sensor_event_migration import SENSOR, migration_plan


def main(source, registry_path, report_path, output):
    source = source.resolve()
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    output.mkdir(parents=True, exist_ok=False)
    copied = output / 'source.blend'
    shutil.copy2(source, copied)
    extension.register()
    bpy.context.preferences.addons.new().module = 'extension'
    bpy.ops.wm.open_mainfile(filepath=str(copied))
    registry = json.loads(registry_path.read_text())
    registry = registry.get('result', registry)
    report = json.loads(report_path.read_text())
    previous, plan = migration_plan(bpy.context, registry, report)
    expected = {owner.name: after for owner, _, after in plan}
    untouched = {(owner.bl_rna.identifier, owner.name): [authoring_sync._raw(component) for component in owner.skein_two
                           if component.selected_type_path != SENSOR]
                 for owner in component_owners() if getattr(owner, 'skein_two', ())}
    backup = output / 'before-sensor-events.blend'
    assert bpy.ops.wm.skein_migrate_sensor_events(mapping_path=str(report_path),
        registry_path=str(registry_path), backup_path=str(backup)) == {'FINISHED'}
    migrated = output / 'level.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(migrated))
    bpy.ops.wm.open_mainfile(filepath=str(migrated))
    for name, after in expected.items():
        actual = [read_component(component, registry) for component in bpy.data.objects[name].skein_two
                  if component.selected_type_path == SENSOR]
        assert actual == [after], (name, actual, after)
    for owner in component_owners():
        if (owner.bl_rna.identifier, owner.name) in untouched:
            actual = [authoring_sync._raw(component) for component in getattr(owner, 'skein_two', ())
                      if component.selected_type_path != SENSOR]
            assert actual == untouched[(owner.bl_rna.identifier, owner.name)], owner.name
    settings = bpy.context.scene.skein_extension_properties
    settings.enabled, settings.extras, settings.extensions = True, True, False
    exported = output / 'level.glb'
    assert bpy.ops.export_scene.gltf(filepath=str(exported), export_format='GLB',
        export_extras=True, export_animations=True, export_animation_mode='NLA_TRACKS',
        export_cameras=True, export_lights=True, export_yup=True) == {'FINISHED'}
    data = exported.read_bytes()
    size = struct.unpack_from('<I', data, 12)[0]
    document = json.loads(data[20:20 + size])
    actual = {node.get('name'): component[SENSOR] for node in document['nodes']
              for component in node.get('extras', {}).get('skein', []) if SENSOR in component}
    assert actual == expected, (actual, expected)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash
    result = dict(ok=True, source=str(source), source_sha256=source_hash,
                  backup=str(backup), migrated=str(migrated), exported=str(exported), sensors=actual)
    (output / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print('SENSOR_MIGRATION_OK ' + json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    args = sys.argv[sys.argv.index('--') + 1:]
    if len(args) != 4:
        raise ValueError('Expected SOURCE.blend REGISTRY.json REPORT.json NEW_OUTPUT_DIR')
    main(*(Path(value).absolute() for value in args))
