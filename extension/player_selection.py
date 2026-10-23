"""Export-local checks for the ARForge player marker."""
CHARACTER_CONTROLLER = 'poc_design_editor::CharacterController'
PLAYER_CONTROLLED = 'poc_design_editor::PlayerControlled'


def validate_player_selection(records):
    """Check (name, same-object controller, marker values) for one export."""
    diagnostics = []
    enabled = []
    controllers = []
    for name, has_controller, markers in records:
        if has_controller:
            controllers.append(name)
        if not markers:
            continue
        def error(message):
            diagnostics.append(dict(severity='ERROR', field=PLAYER_CONTROLLED, message=f'{name}: {message}'))
        if len(markers) > 1:
            error('Duplicate PlayerControlled components; keep one marker on this Object.')
        if not has_controller:
            error('PlayerControlled must be on the same Object as CharacterController, not mesh, material, scene or bone data.')
        for marker in markers:
            if not isinstance(marker, dict) or set(marker) != {'enabled'} or type(marker['enabled']) is not bool:
                error('PlayerControlled must contain exactly enabled: true or false; reload a compatible registry.')
            elif marker['enabled'] and has_controller:
                enabled.append(name)
    if len(enabled) > 1:
        diagnostics.append(dict(severity='ERROR', field=PLAYER_CONTROLLED,
            message='Multiple enabled player markers in this export: ' + ', '.join(enabled) + '. Enable only one.'))
    elif not enabled and controllers:
        diagnostics.append(dict(severity='WARNING', field=PLAYER_CONTROLLED,
            message='No enabled player marker in this export (' + ', '.join(controllers) + '). '
            'ARForge checks all loaded models; only one unmarked controller supports the legacy fallback.'))
    return diagnostics


def get_source_selection_record(source):
    """Read marker data from the same reflected form as the glTF exporter."""
    import bpy
    from .form_to_object import get_data_from_active_editor
    from .property_groups import hash_over_64
    components = getattr(source, 'skein_two', ())
    markers = []
    for component in components:
        if component.selected_type_path == PLAYER_CONTROLLED:
            try:
                markers.append(get_data_from_active_editor(component, hash_over_64(PLAYER_CONTROLLED)))
            except (KeyError, AttributeError, TypeError, ValueError):
                markers.append(None)
    has_controller = isinstance(source, bpy.types.Object) and any(
        component.selected_type_path == CHARACTER_CONTROLLER for component in components)
    return (source.name, has_controller, markers)
