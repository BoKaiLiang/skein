"""Run project-advertised list-item previews against the configured runtime."""
import json

import bpy

from . import authoring, connection
from .component_values import canonical, read_component
from .form_to_object import get_data_from_active_editor
from .property_groups import hash_over_64

ERRORS = (ValueError, TypeError, KeyError, AttributeError, ReferenceError, RuntimeError)


def _operation(component_type, list_field):
    registry = json.loads(bpy.context.window_manager.skein.registry)
    description = authoring.cached_description()
    operation = authoring.preview_operation(description, component_type, list_field)
    if operation is None:
        raise ValueError('This preview is no longer advertised. Sync the runtime and reopen the component.')
    return registry, operation


def capture_target(operator, owner, component, list_field, entry_value):
    """Capture the clicked owner and row before transient UI context disappears."""
    operation = authoring.preview_operation(authoring.cached_description(), component.selected_type_path, list_field)
    if operation is None:
        raise ValueError('This preview is no longer advertised.')
    form = getattr(component, hash_over_64(component.selected_type_path))
    entries = getattr(form, list_field).entries
    entry = next((entry for entry in entries if entry.value == entry_value), None)
    if entry is None or component.id_data != owner:
        raise ValueError('The preview item is unavailable on this component.')
    value = get_data_from_active_editor(entry, 'value')
    operator.target_object = owner.name
    operator.component_path = component.path_from_id()
    operator.component_type = component.selected_type_path
    operator.list_field = list_field
    operator.target_path = entry_value.path_from_id()
    operator.component_pointer = str(component.as_pointer())
    operator.target_pointer = str(entry_value.as_pointer())
    try:
        operator.identity_json = canonical(value[operation['identity_field']])
    except (KeyError, ValueError):
        # Invalid values must remain editable; execution reports the schema error.
        operator.identity_json = ''


def current_preview(operator):
    registry, operation = _operation(operator.component_type, operator.list_field)
    owner = bpy.data.objects.get(operator.target_object)
    try:
        component = owner.path_resolve(operator.component_path) if owner else None
        target = owner.path_resolve(operator.target_path) if owner else None
    except (ValueError, TypeError):
        component = target = None
    if (not isinstance(component, bpy.types.PropertyGroup) or not isinstance(target, bpy.types.PropertyGroup)
            or str(component.as_pointer()) != operator.component_pointer
            or getattr(component, 'selected_type_path', None) != operator.component_type
            or not any(item == component for item in owner.skein_two)
            or str(target.as_pointer()) != operator.target_pointer):
        raise ValueError('The preview component or item changed or is unavailable. Reopen the component and try again.')
    form = getattr(component, hash_over_64(operator.component_type))
    sequence = getattr(form, operator.list_field)
    index = next((index for index, entry in enumerate(sequence.entries) if entry.value == target), None)
    if index is None:
        raise ValueError('The preview item is unavailable on this component.')
    values = read_component(component, registry)[operator.list_field]
    value = values[index]
    identity = canonical(value[operation['identity_field']])
    if identity != operator.identity_json:
        raise ValueError('The preview item identity changed. Use the current item row and try again.')
    if sum(canonical(item[operation['identity_field']]) == identity for item in values) != 1:
        raise ValueError('Preview item identities must be unique in this list.')
    return operation, authoring.preview_params(operation, value)


class SkeinActionPreview(bpy.types.Operator):
    """Run the project preview for this current list item"""
    bl_idname = 'wm.skein_action_preview'
    bl_label = 'Preview item'
    target_object: bpy.props.StringProperty(options={'HIDDEN'}) # type: ignore
    component_path: bpy.props.StringProperty(options={'HIDDEN'}) # type: ignore
    component_type: bpy.props.StringProperty(options={'HIDDEN'}) # type: ignore
    list_field: bpy.props.StringProperty(options={'HIDDEN'}) # type: ignore
    target_path: bpy.props.StringProperty(options={'HIDDEN'}) # type: ignore
    component_pointer: bpy.props.StringProperty(options={'HIDDEN'}) # type: ignore
    target_pointer: bpy.props.StringProperty(options={'HIDDEN'}) # type: ignore
    identity_json: bpy.props.StringProperty(options={'HIDDEN'}) # type: ignore

    def execute(self, context):
        try:
            operation, params = current_preview(self)
            connection.post(connection.endpoint(context), operation['method'], params)
        except ERRORS as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        self.report({'INFO'}, 'Preview request sent to the runtime.')
        return {'FINISHED'}


class SkeinStopActionPreview(bpy.types.Operator):
    """Run the stop operation advertised by the project"""
    bl_idname = 'wm.skein_stop_action_preview'
    bl_label = 'Stop preview'
    component_type: bpy.props.StringProperty(options={'HIDDEN'}) # type: ignore
    list_field: bpy.props.StringProperty(options={'HIDDEN'}) # type: ignore

    def execute(self, context):
        try:
            _, operation = _operation(self.component_type, self.list_field)
            stop = operation.get('stop')
            if stop is None:
                raise ValueError('No stop operation is advertised for this preview.')
            connection.post(connection.endpoint(context), stop['method'], stop['params'])
        except ERRORS as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        self.report({'INFO'}, 'Stop preview request sent to the runtime.')
        return {'FINISHED'}


register, unregister = bpy.utils.register_classes_factory((SkeinActionPreview, SkeinStopActionPreview))
