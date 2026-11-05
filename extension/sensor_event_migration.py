"""Explicit backed-up conversion of legacy Sensor source data using a migration report."""
import json
from pathlib import Path

import bpy

from . import authoring, authoring_sync
from .component_values import (
    ERRORS, _comparison_value, canonical, read_component, replace_component, validate_value,
)
from .op_registry_loading import component_owners, process_registry, validate_registry, validate_registry_refresh

SENSOR = 'poc_design_editor::Sensor'
FIELDS = {'shape', 'enter_events', 'stay_events', 'exit_events', 'stay_interval'}
DEFAULTS = dict(enter_text='', exit_text='', enter_event='', stay_event='', exit_event='', stay_interval=1.)


def _old_key(registry, value):
    fields = registry[SENSOR]['properties']
    normalized = {name: value[name] if name in value else DEFAULTS[name] for name in fields}
    validate_value(registry, SENSOR, normalized)
    return canonical(_comparison_value(registry, SENSOR, normalized))


def _choice_fields(report):
    choices = report.get('event_choices')
    return [dict(type_path=SENSOR, field=field, widget='string_list_choice',
        choices=choices, error=None if choices is not None else 'Sync current Preview to load event choices')
        for field in ('enter_events', 'stay_events', 'exit_events')]


def migration_plan(context, registry, report, asset_path=''):
    """Validate every mapping and used schema before creating a backup or changing RNA."""
    previous = json.loads(context.window_manager.skein.registry)
    validate_registry(registry)
    if registry.get(SENSOR, {}).get('properties', {}).keys() != FIELDS:
        raise ValueError('Select a registry with the new Sensor event arrays')
    # The migration alone authorizes replacing Sensor. Ordinary Sync keeps its
    # existing schema guard, including all other used types and Sensor shape.
    compatible = dict(registry)
    if SENSOR not in previous:
        raise ValueError('The current cached registry has no Sensor schema')
    compatible[SENSOR] = previous[SENSOR]
    validate_registry_refresh(context, compatible)
    if not isinstance(report, dict) or report.get('schema') != 'arforge.sensor-migration/v1' or not isinstance(report.get('sensors'), list):
        raise ValueError('Select an arforge.sensor-migration/v1 report')
    authoring.validate_description(dict(authoring.empty_description(), fields=_choice_fields(report)), registry)
    if 'enter_text' not in previous[SENSOR].get('properties', {}):
        return previous, []
    mappings = []
    for item in report['sensors']:
        if not isinstance(item, dict) or not isinstance(item.get('node_name'), str) or not isinstance(item.get('before'), dict) or not isinstance(item.get('after'), dict):
            raise ValueError('Invalid Sensor migration mapping')
        validate_value(registry, SENSOR, item['after'])
        mappings.append((item.get('asset'), item['node_name'], _old_key(previous, item['before']), item['after']))
    plan = []
    for owner in component_owners():
        for index, component in enumerate(getattr(owner, 'skein_two', ())):
            if component.selected_type_path != SENSOR:
                continue
            if not isinstance(owner, bpy.types.Object) or not owner.is_editable or owner.library or owner.override_library:
                raise ValueError(f'{owner.name}: migrate this Sensor in its editable source Object')
            before = read_component(component, previous)
            key = _old_key(previous, before)
            matches = [(asset, after) for asset, name, old, after in mappings
                       if name == owner.name and old == key]
            candidates = {canonical(after): after for _, after in matches}
            # An asset choice resolves only an ambiguous Object. Other Objects
            # with one exact mapping still participate in the same schema swap.
            if len(candidates) > 1 and asset_path:
                candidates = {canonical(after): after for asset, after in matches if asset == asset_path}
            if len(candidates) != 1:
                repair = ('regenerate the mapping from this source Object and its current Sensor values'
                          if not candidates else 'select the matching source asset explicitly')
                raise ValueError(f'{owner.name}: expected one exact Sensor migration mapping, found {len(candidates)}; {repair}')
            plan.append((owner, index, next(iter(candidates.values()))))
    return previous, plan


def _restore(previous, snapshots):
    process_registry(bpy.context, previous)
    for owner, raw, _, active in snapshots:
        owner.skein_two.clear()
        for item in raw:
            component = owner.skein_two.add()
            for key, value in item.items():
                component[key] = value
        owner.active_component_index = active


def migrate(context, registry, report, backup_path, asset_path=''):
    """Keep source .blend bytes intact; write a unique backup before the schema swap."""
    previous, plan = migration_plan(context, registry, report, asset_path)
    if not plan:
        return 0
    backup = Path(backup_path).expanduser().absolute()
    if backup.suffix.lower() != '.blend' or backup.exists() or not backup.parent.is_dir():
        raise ValueError('Backup must be a new .blend file in an existing directory')
    if bpy.data.filepath and backup.resolve() == Path(bpy.data.filepath).resolve():
        raise ValueError('Backup must differ from the current source .blend')
    names = ('skein-registry.json', 'skein-presets.json', 'skein-authoring.json')
    caches = {name: bpy.data.texts[name].as_string() if name in bpy.data.texts else None for name in names}
    if any(name in bpy.data.texts and not bpy.data.texts[name].is_editable for name in names):
        raise ValueError('Skein cache is read-only; source data was kept')
    snapshots = authoring_sync._snapshot(previous)
    targets = {(owner.as_pointer(), index) for owner, index, _ in plan}
    # copy=True includes unsaved source edits while retaining the active filepath.
    if bpy.ops.wm.save_as_mainfile(filepath=str(backup), copy=True) != {'FINISHED'}:
        raise ValueError('Could not create the required source backup')
    try:
        process_registry(context, registry)
        for owner, index, after in plan:
            replace_component(owner, index, registry, after)
        for owner, raw, values, _ in snapshots:
            for index, original in enumerate(raw):
                if (owner.as_pointer(), index) not in targets and authoring_sync._raw(owner.skein_two[index]) != original:
                    raise ValueError(f'{owner.name}: migration changed an unrelated component')
            for index, before in values:
                if (owner.as_pointer(), index) not in targets and not authoring_sync._preserves_values(before, read_component(owner.skein_two[index], registry)):
                    raise ValueError(f'{owner.name}: migration changed unrelated component values')
        authoring_sync._write('skein-registry.json', registry)
        if caches['skein-presets.json'] is not None:
            presets = json.loads(caches['skein-presets.json'])
            presets.pop(SENSOR, None)
            authoring_sync._write('skein-presets.json', presets)
        description = (json.loads(caches['skein-authoring.json'])
                       if caches['skein-authoring.json'] is not None else authoring.empty_description())
        description['fields'] = [field for field in description.get('fields', []) if field.get('type_path') != SENSOR]
        description['fields'].extend(_choice_fields(report))
        authoring.validate_description(description, registry)
        authoring_sync._write('skein-authoring.json', description)
        authoring_sync.restore_cache(context)
    except Exception:
        _restore(previous, snapshots)
        for name, content in caches.items():
            text = bpy.data.texts.get(name)
            if content is None:
                if text is not None:
                    bpy.data.texts.remove(text)
            else:
                text = text or bpy.data.texts.new(name)
                text.clear()
                text.write(content)
        authoring_sync.restore_cache(context)
        raise
    return len(plan)


class MigrateSensorEvents(bpy.types.Operator):
    """Migrate Sensor source fields using an explicit report and back up this .blend first"""
    bl_idname = 'wm.skein_migrate_sensor_events'
    bl_label = 'Migrate Sensor Events'
    bl_options = {'REGISTER'}

    mapping_path: bpy.props.StringProperty(name='Migration report', subtype='FILE_PATH')
    registry_path: bpy.props.StringProperty(name='New registry', subtype='FILE_PATH')
    backup_path: bpy.props.StringProperty(name='New backup .blend', subtype='FILE_PATH')
    asset_path: bpy.props.StringProperty(name='Source asset choice', description='Optional workspace-relative GLB path for ambiguous Objects; unique mappings remain included')

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=600)

    def draw(self, context):
        for field in ('mapping_path', 'registry_path', 'backup_path', 'asset_path'):
            self.layout.prop(self, field)
        self.layout.label(text='Backs up current edits. Save and re-export explicitly after migration.')

    def execute(self, context):
        try:
            paths = [Path(bpy.path.abspath(path)) for path in (self.mapping_path, self.registry_path)]
            if any(not path.is_file() or path.stat().st_size > 16 * 1024 * 1024 for path in paths):
                raise ValueError('Select regular migration-report and registry JSON files below 16 MiB')
            report, registry = [json.loads(path.read_text()) for path in paths]
            registry = registry.get('result', registry)
            count = migrate(context, registry, report, bpy.path.abspath(self.backup_path), self.asset_path)
        except ERRORS + (OSError,) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        self.report({'INFO'}, f'Migrated {count} Sensor components; Sync Skein Authoring, then save and export explicitly')
        return {'FINISHED'}
