"""Run with Blender --background --factory-startup --python-exit-code 1 --python FILE -- REGISTRY SOURCE OUTPUT."""
import json
from pathlib import Path
import struct
import sys

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import extension
from extension.arforge_migration import LEGACY_CONTROLLER, LEGACY_PLAYER, legacy_objects, migration_plan, migrate
from extension.component_values import append_component, read_component
from extension.op_registry_loading import process_registry


def glb_components(path):
    data = path.read_bytes()
    length, chunk_type = struct.unpack_from('<I4s', data, 12)
    assert chunk_type == b'JSON'
    document = json.loads(data[20:20 + length])
    return {
        node['name']: {
            key: value
            for component in node.get('extras', {}).get('skein', [])
            for key, value in component.items()
        }
        for node in document['nodes']
    }


def main(registry_path, source, output):
    output.mkdir()
    registry = json.loads(registry_path.read_text())
    registry = registry.get('result', registry)
    bpy.ops.preferences.addon_enable(module='extension')
    bpy.ops.wm.open_mainfile(filepath=str(source))
    text = bpy.data.texts.get('skein-registry.json') or bpy.data.texts.new('skein-registry.json')
    text.clear()
    text.write(json.dumps(registry))
    process_registry(bpy.context, registry)

    objects = bpy.context.scene.objects
    plan = migration_plan(objects, registry)
    assert plan, 'Fixture must have legacy components'
    expected = {obj.name: dict(replacements) for obj, _, replacements in plan}
    before = {
        obj.name: {c.selected_type_path: c.as_pointer() for c in obj.skein_two}
        for obj, _, _ in plan
    }

    # A conflict on a later object must leave all original source components intact.
    bad = bpy.data.objects.new('Conflict', None)
    bpy.context.scene.collection.objects.link(bad)
    append_component(bad, registry, LEGACY_PLAYER, {'enabled': False})
    try:
        migrate(objects, registry)
    except ValueError as error:
        assert 'Conflict' in str(error)
    else:
        raise AssertionError('An orphan PlayerControlled marker was accepted')
    for obj, _, _ in plan:
        assert {c.selected_type_path: c.as_pointer() for c in obj.skein_two} == before[obj.name]
    bpy.data.objects.remove(bad, do_unlink=True)

    assert bpy.ops.wm.skein_migrate_arforge_characters() == {'FINISHED'}
    assert not legacy_objects(objects)
    assert migrate(objects, registry) == 0
    for obj, _, _ in plan:
        by_type = {c.selected_type_path: c for c in obj.skein_two}
        for path, pointer in before[obj.name].items():
            if path not in {LEGACY_CONTROLLER, LEGACY_PLAYER}:
                assert by_type[path].as_pointer() == pointer

    blend = output / 'migrated.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(blend))
    bpy.ops.wm.open_mainfile(filepath=str(blend))
    for name, replacements in expected.items():
        obj = bpy.data.objects[name]
        actual = {
            c.selected_type_path: read_component(c, registry)
            for c in obj.skein_two if c.selected_type_path in replacements
        }
        assert actual == replacements, (name, actual, replacements)

    camera = bpy.context.scene.skein_extension_properties
    camera.enabled, camera.extras, camera.extensions = True, True, False
    glb = output / 'scene.glb'
    assert bpy.ops.export_scene.gltf(
        filepath=str(glb), export_format='GLB', export_extras=True,
        export_animations=True, export_animation_mode='NLA_TRACKS',
        export_cameras=True, export_lights=True, export_yup=True,
    ) == {'FINISHED'}
    exported = glb_components(glb)
    for name, replacements in expected.items():
        assert {key: exported[name][key] for key in replacements} == replacements
        assert LEGACY_CONTROLLER not in exported[name] and LEGACY_PLAYER not in exported[name]
    (output / 'scene.ron').write_text('(nodes:[(id:"level",asset:"scene.glb")])\n')
    print(f'ARFORGE_MIGRATION_OK {list(expected)}')


main(*(Path(value).resolve() for value in sys.argv[sys.argv.index('--') + 1:]))
