"""Temporary Blender playback of bindings reported by the actual glTF exporter."""
import ast
import math
import time

import bpy
from bpy.app.handlers import persistent

_session = None


def active(owner=None):
    return _session is not None and (owner is None or _session['owner'] == owner)


def _playing(context):
    return bool(context.screen and context.screen.is_animation_playing)


def _animated_values(bindings):
    values, seen = [], set()
    for binding in bindings:
        target = binding['target']
        sources = [(binding['action'], binding['slot'])] if binding['mode'] == 'ACTION' else [
            (strip.action, strip.action_slot) for track in binding['tracks'] for strip in track.strips if strip.action]
        for action, slot in sources:
            for layer in action.layers:
                for strip in layer.strips:
                    for bag in strip.channelbags:
                        if slot and bag.slot_handle != slot.handle:
                            continue
                        for curve in bag.fcurves:
                            path = curve.data_path
                            key = (target.as_pointer(), path)
                            if key in seen:
                                continue
                            seen.add(key)
                            try:
                                if path.endswith(']'):
                                    prefix, _, suffix = path.rpartition('[')
                                    parent = target.path_resolve(prefix) if prefix else target
                                    field, attribute = ast.literal_eval(suffix[:-1]), False
                                    value = parent[field]
                                else:
                                    prefix, _, field = path.rpartition('.')
                                    parent = target.path_resolve(prefix) if prefix else target
                                    attribute, value = True, getattr(parent, field)
                                if not isinstance(value, (str, int, float, bool)):
                                    value = tuple(value)
                                values.append((parent, field, attribute, value))
                            except (ValueError, TypeError, AttributeError, KeyError):
                                raise ValueError(f'Cannot safely restore animated property {path!r}.')
    return values


def _snapshot(context, bindings):
    from .clip_picker import capture_state, _alive
    scene, view_layer, window, screen = context.scene, context.view_layer, context.window, context.screen
    restore_editor = capture_state(context)
    properties = _animated_values(bindings)
    playing = _playing(context)
    if playing:
        bpy.ops.screen.animation_cancel(restore_frame=False)
    created = [binding['target'] for binding in bindings if binding['target'].animation_data is None]
    def restore():
        restore_editor()
        for parent, field, attribute, value in properties:
            if _alive(parent):
                if attribute:
                    setattr(parent, field, value)
                else:
                    parent[field] = value
        for target in created:
            if _alive(target):
                target.animation_data_clear()
        if (playing and _alive(window) and _alive(screen) and _alive(scene)
                and window.scene == scene and window.screen == screen
                and not screen.is_animation_playing):
            with bpy.context.temp_override(window=window, screen=screen, scene=scene, view_layer=view_layer):
                bpy.ops.screen.animation_play()
    return restore


def stop():
    global _session
    current, _session = _session, None
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    if current:
        current['restore']()
        _redraw()


def _redraw():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            area.tag_redraw()


def sample_elapsed(elapsed):
    """Sample a real scene deterministically; timers supply elapsed wall-clock time."""
    if _session is None:
        return
    state = _session
    from .clip_picker import _alive
    window = state['window']
    if (not _alive(state['scene']) or not _alive(state['view_layer'])
            or not _alive(state['owner'])
            or any(not _alive(item['target']) for item in state['bindings'])
            or (window is not None and (not _alive(window) or window.scene != state['scene']
                                        or window.view_layer != state['view_layer']))):
        stop()
        return
    duration = state['end'] - state['start']
    distance = max(0.0, elapsed) * state['fps'] * state['speed']
    if not state['looped'] and distance >= duration:
        stop()
        return
    frame = state['start'] + (distance % duration if state['looped'] else distance)
    integer = math.floor(frame)
    state['scene'].frame_set(integer, subframe=frame - integer)
    state['view_layer'].update()
    _redraw()


def _tick():
    try:
        if _session is None:
            return None
        sample_elapsed(time.monotonic() - _session['started'])
        return 1 / 60 if _session is not None else None
    except (ReferenceError, RuntimeError, ValueError):
        stop()
        return None


def start(context, owner, clip, looped=False, speed=1.0):
    global _session
    from . import clip_picker
    from .authoring import selectable_clips
    stop()
    if context.mode != 'OBJECT':
        raise ValueError('Return to Object Mode to preview an animation.')
    if context.scene.is_nla_tweakmode:
        raise ValueError('Exit animation track editing before previewing a clip.')
    if type(looped) is not bool or type(speed) not in (int, float) or not math.isfinite(speed) or speed <= 0:
        raise ValueError('Playback speed must be positive and finite.')
    entry = clip_picker._cache.get(owner.as_pointer(), {})
    if entry.get('signature') != clip_picker.clip_cache_signature(context, owner):
        clip_picker.refresh_clip_cache(context, owner)
    names = clip_picker.cached_clip_names(owner)
    if names is None or clip not in selectable_clips(names):
        raise ValueError('Choose a unique exported animation clip before previewing.')
    bindings = clip_picker.cached_clip_bindings(owner, clip)
    if not bindings:
        raise ValueError('This exported clip has no safe local playback binding. Use Animation Tracks or Actions and refresh clips.')
    targets = set()
    for binding in bindings:
        target = binding['target']
        if target.as_pointer() in targets or not target.is_editable or target.library or target.override_library:
            raise ValueError('This clip has overlapping or read-only playback bindings and cannot be previewed safely.')
        targets.add(target.as_pointer())
    starts = {round(float(item['start']), 6) for item in bindings}
    if len(starts) != 1:
        raise ValueError('This clip combines different source timelines and cannot be previewed safely.')
    first, last = min(starts), max(float(item['end']) for item in bindings)
    if not math.isfinite(first) or not math.isfinite(last) or last <= first:
        raise ValueError('This clip has no playable frame range.')
    restore = _snapshot(context, bindings)
    try:
        for binding in bindings:
            source = binding['target']
            if isinstance(source, bpy.types.Object) and source.pose:
                from mathutils import Matrix
                for bone in source.pose.bones:
                    bone.matrix_basis = Matrix.Identity(4)
            elif isinstance(source, bpy.types.Key):
                for key in source.key_blocks:
                    key.value = 0
            data = binding['target'].animation_data_create()
            for track in data.nla_tracks:
                track.is_solo = False
                track.mute = True
            data.action = None
            if binding['mode'] == 'NLA':
                data.use_nla = True
                for track in binding['tracks']:
                    track.mute = False
            else:
                data.use_nla = False
                data.action = binding['action']
                data.action_slot = binding['slot']
        scene = context.scene
        _session = dict(scene=context.scene, view_layer=context.view_layer, window=context.window,
                        owner=owner, bindings=bindings, restore=restore, start=first, end=last,
                        fps=scene.render.fps / scene.render.fps_base, looped=looped,
                        speed=speed, started=time.monotonic())
        sample_elapsed(0)
        bpy.app.timers.register(_tick, first_interval=1 / 60)
    except Exception:
        _session = None
        restore()
        raise


def capture_target(operator, owner, form, field):
    operator.owner_name = owner.name
    operator.owner_pointer = str(owner.as_pointer())
    operator.form_path = form.path_from_id()
    operator.form_pointer = str(form.as_pointer())
    operator.field = field
    operator.original_clip = getattr(form, field)


class PreviewClip(bpy.types.Operator):
    bl_idname = 'wm.skein_preview_clip'
    bl_label = 'Preview animation in Blender'
    owner_name: bpy.props.StringProperty(options={'HIDDEN'}) # type: ignore
    owner_pointer: bpy.props.StringProperty(options={'HIDDEN'}) # type: ignore
    form_path: bpy.props.StringProperty(options={'HIDDEN'}) # type: ignore
    form_pointer: bpy.props.StringProperty(options={'HIDDEN'}) # type: ignore
    field: bpy.props.StringProperty(options={'HIDDEN'}) # type: ignore
    original_clip: bpy.props.StringProperty(options={'HIDDEN'}) # type: ignore

    def execute(self, context):
        try:
            from .authoring import cached_description, field_editor
            owner = bpy.data.objects.get(self.owner_name)
            if owner is None or str(owner.as_pointer()) != self.owner_pointer:
                raise ValueError('The model changed. Choose the clip again.')
            form = owner.path_resolve(self.form_path)
            if str(form.as_pointer()) != self.form_pointer or getattr(form, self.field) != self.original_clip:
                raise ValueError('The animation entry changed. Choose the clip again.')
            descriptor = field_editor(cached_description(), getattr(form, '_skein_type_path', ''), self.field)
            if not descriptor or descriptor.get('widget') != 'animation_clip':
                raise ValueError('Sync this model’s animation settings before previewing.')
            playback = descriptor.get('playback') or {}
            looped = getattr(form, playback['loop_field']) if playback.get('loop_field') else False
            speed = getattr(form, playback['speed_field']) if playback.get('speed_field') else 1.0
            start(context, owner, self.original_clip, looped, speed)
        except (ValueError, TypeError, AttributeError, ReferenceError, RuntimeError) as error:
            self.report({'WARNING'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


class StopClipPreview(bpy.types.Operator):
    bl_idname = 'wm.skein_stop_clip_preview'
    bl_label = 'Stop animation preview'

    def execute(self, context):
        stop()
        return {'FINISHED'}


@persistent
def on_restore_boundary(*_args):
    stop()


_handlers = ('save_pre', 'load_pre', 'undo_pre', 'redo_pre')
_classes = (PreviewClip, StopClipPreview)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    for name in _handlers:
        getattr(bpy.app.handlers, name).append(on_restore_boundary)


def unregister():
    stop()
    for name in _handlers:
        getattr(bpy.app.handlers, name).remove(on_restore_boundary)
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
