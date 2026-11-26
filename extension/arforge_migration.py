"""Explicit source migration from the legacy ARForge designer Preview components."""
import json

import bpy

from .component_values import ERRORS, append_component, read_component, validate_value

PREFIX = 'poc_design_editor::'
LEGACY_CONTROLLER = PREFIX + 'CharacterController'
LEGACY_PLAYER = PREFIX + 'PlayerControlled'
NEW_TYPES = {PREFIX + name for name in ('Character', 'Controller2D', 'Controller3D', 'Animator')}


def legacy_objects(objects):
    return sorted(
        (obj for obj in objects if any(
            component.selected_type_path in {LEGACY_CONTROLLER, LEGACY_PLAYER}
            for component in obj.skein_two
        )),
        key=lambda obj: obj.name_full,
    )


def migration_plan(objects, registry):
    plan = []
    for obj in legacy_objects(objects):
        try:
            if not obj.is_editable:
                raise ValueError('Linked object must be migrated in its source file')
            indices = {}
            for index, component in enumerate(obj.skein_two):
                path = component.selected_type_path
                if path in NEW_TYPES:
                    raise ValueError('Already contains a replacement component')
                if path in {LEGACY_CONTROLLER, LEGACY_PLAYER}:
                    if path in indices:
                        raise ValueError(f'Duplicate {path}')
                    indices[path] = index
            if LEGACY_CONTROLLER not in indices:
                raise ValueError('PlayerControlled has no CharacterController on the same object')
            if not {PREFIX + 'Character', PREFIX + 'Controller3D', PREFIX + 'Animator'} <= registry.keys():
                raise ValueError('Sync the type registry from an updated ARForge Preview first')
            controller = read_component(obj.skein_two[indices[LEGACY_CONTROLLER]], registry)
            player = LEGACY_PLAYER in indices and read_component(
                obj.skein_two[indices[LEGACY_PLAYER]], registry
            )['enabled']
            replacements = [
                (PREFIX + 'Character', {'body': controller['body']}),
                (PREFIX + 'Controller3D', {
                    'player_controlled': player,
                    'movement': controller['movement'],
                    'bindings': controller['bindings'],
                }),
            ]
            if controller['animations'] is not None or controller.get('actions'):
                replacements.append((PREFIX + 'Animator', {
                    'animations': controller['animations'],
                    'actions': controller.get('actions', []),
                }))
            for path, value in replacements:
                validate_value(registry, path, value)
        except ERRORS as error:
            raise ValueError(f'{obj.name_full}: {error}') from error
        plan.append((obj, sorted(indices.values(), reverse=True), replacements))
    return plan


def migrate(objects, registry):
    """Stage all replacements before removing any legacy components."""
    plan = migration_plan(objects, registry)
    staged = []
    try:
        for obj, _, replacements in plan:
            staged.append((obj, len(obj.skein_two), obj.active_component_index))
            for path, value in replacements:
                append_component(obj, registry, path, value)
    except ERRORS:
        for obj, count, active in reversed(staged):
            while len(obj.skein_two) > count:
                obj.skein_two.remove(len(obj.skein_two) - 1)
            obj.active_component_index = active
        raise
    for obj, indices, _ in plan:
        active = obj.active_component_index
        for index in indices:
            obj.skein_two.remove(index)
        obj.active_component_index = min(
            active - sum(index < active for index in indices), len(obj.skein_two) - 1
        )
    return len(plan)


class MigrateArforgeCharacters(bpy.types.Operator):
    """Convert legacy ARForge character components in this scene"""
    bl_idname = 'wm.skein_migrate_arforge_characters'
    bl_label = 'Migrate ARForge Character Components'
    bl_options = {'REGISTER', 'UNDO'}

    def invoke(self, context, event):
        self.objects = [obj.name_full for obj in legacy_objects(context.scene.objects)]
        if not self.objects:
            self.report({'INFO'}, 'No legacy ARForge character components in this scene')
            return {'CANCELLED'}
        return context.window_manager.invoke_props_dialog(self, width=420)

    def draw(self, context):
        self.layout.label(text='Migrate these Objects to Character, Controller3D and optional Animator:')
        for name in self.objects:
            self.layout.label(text=name, icon='OBJECT_DATA')
        self.layout.label(text='Save the .blend and export the GLB after migration.')

    def execute(self, context):
        try:
            if hasattr(self, 'objects') and self.objects != [
                obj.name_full for obj in legacy_objects(context.scene.objects)
            ]:
                raise ValueError('Scene changed. Review the migration list again.')
            registry = json.loads(context.window_manager.skein.registry)
            count = migrate(context.scene.objects, registry)
        except ERRORS as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        self.report({'INFO'}, f'Migrated {count} ARForge character Objects')
        return {'FINISHED'}
