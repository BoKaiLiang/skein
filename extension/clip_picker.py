"""Clip candidates come from the actual glTF exporter, never the action datablock list."""
from pathlib import Path
import tempfile
import textwrap
import time
import uuid

import bpy
from bpy.app.handlers import persistent

_cache = {}
_pending = None
_last_checks = {}
_discovery = None


def is_discovery(settings):
    return _discovery is not None and settings.get('gltf_export_id') == _discovery['id']


def record_discovery(name, names, bindings=None):
    _discovery['clips'][name] = names
    _discovery.setdefault('bindings', {})[name] = bindings or {}


def cached_clip_names(obj):
    entry = _cache.get(obj.as_pointer())
    return entry['names'] if entry and not entry.get('error') and not entry.get('stale') else None


def cached_clip_error(obj):
    entry = _cache.get(obj.as_pointer())
    return entry.get('error') if entry else None


def cached_clip_bindings(obj, clip):
    entry = _cache.get(obj.as_pointer(), {})
    return entry.get('bindings', {}).get(clip, [])


def _animation_signature(animation):
    if animation is None:
        return None
    active = (animation.action.name if animation.action else None,
              animation.action_slot.identifier if animation.action_slot else None,
              animation.use_nla)
    tracks = tuple((track.name, track.mute, track.is_solo,
                    tuple((strip.name, strip.action.name if strip.action else None,
                           strip.action_slot.identifier if strip.action_slot else None,
                           strip.mute, strip.frame_start, strip.frame_end,
                           strip.action_frame_start, strip.action_frame_end,
                           strip.scale, strip.repeat, strip.blend_type, strip.influence)
                          for strip in track.strips)) for track in animation.nla_tracks)
    return active, tracks


def clip_cache_signature(context, obj):
    """Ignore current frame/evaluated transforms: exporter sampling must not invalidate itself."""
    settings = context.scene.skein_extension_properties
    objects = []
    for item in context.scene.objects:
        shape_keys = getattr(item.data, 'shape_keys', None)
        objects.append((item.as_pointer(), item.name, item.parent.as_pointer() if item.parent else None,
                        item.hide_viewport, item.hide_render, _animation_signature(item.animation_data),
                        _animation_signature(shape_keys.animation_data if shape_keys else None)))
    actions = []
    for action in bpy.data.actions:
        curves = []
        for layer in action.layers:
            for strip in layer.strips:
                for bag in strip.channelbags:
                    curves.extend((bag.slot_handle, curve.data_path, curve.array_index, curve.mute,
                                   len(curve.keyframe_points))
                                  for curve in bag.fcurves)
        actions.append((action.as_pointer(), action.name,
                        tuple((slot.identifier, slot.target_id_type) for slot in action.slots),
                        tuple(action.frame_range), tuple(curves)))
    scene = context.scene
    layers = []
    def layer_signature(layer):
        layers.append((layer.collection.as_pointer(), layer.exclude, layer.hide_viewport,
                       layer.collection.hide_viewport, layer.collection.hide_render))
        for child in layer.children:
            layer_signature(child)
    layer_signature(context.view_layer.layer_collection)
    return (obj.as_pointer(), scene.as_pointer(), settings.clip_export_mode, settings.clip_export_animations,
            scene.frame_start, scene.frame_end, scene.render.fps, scene.render.fps_base,
            tuple(objects), tuple(actions), tuple(layers))


def _alive(value):
    try:
        return value is not None and bool(value.as_pointer()) and bool(value.id_data.as_pointer())
    except ReferenceError:
        return False


def capture_state(context):
    """Keep fixed scene/view-layer references even if the user changes scenes."""
    scene, view_layer, window = context.scene, context.view_layer, context.window
    frame = (scene.frame_current, scene.frame_subframe)
    active = view_layer.objects.active
    mode = active.mode if active else 'OBJECT'
    selected = {item.as_pointer() for item in context.selected_objects}
    animations, transforms, properties, visibility = [], [], [], []
    def copy_value(value):
        if hasattr(value, 'to_dict'):
            return value.to_dict()
        if hasattr(value, 'to_list'):
            return value.to_list()
        return value
    for item in bpy.data.objects:
        visibility.append((item, item.hide_viewport, item.hide_render))
        for target in [item] + (list(item.pose.bones) if item.pose else []):
            transforms.append((target, {name: tuple(getattr(target, name)) for name in
                ('location', 'rotation_euler', 'rotation_quaternion', 'rotation_axis_angle', 'scale')}))
            properties.append((target, {name: copy_value(value) for name, value in target.items()}))
        shape_keys = getattr(item.data, 'shape_keys', None)
        if shape_keys:
            properties.append((shape_keys, {name: copy_value(value) for name, value in shape_keys.items()}))
        for source in (item, shape_keys):
            data = source.animation_data if source else None
            if data:
                animations.append((data, data.action, data.action_slot, data.use_nla,
                    [(track, track.mute, track.is_solo) for track in data.nla_tracks]))
    shape_values = [(key, key.value) for item in bpy.data.objects
                    for key in (getattr(item.data, 'shape_keys', None).key_blocks
                                if getattr(item.data, 'shape_keys', None) else ())]
    def set_mode(value):
        if (_alive(window) and _alive(scene) and _alive(view_layer)
                and window.scene == scene and window.view_layer == view_layer):
            current = view_layer.objects.active
            if current and current.mode != value:
                with bpy.context.temp_override(window=window, scene=scene, view_layer=view_layer):
                    bpy.ops.object.mode_set(mode=value)
    def restore():
        set_mode('OBJECT')
        for data, action, slot, use_nla, tracks in animations:
            if not _alive(data):
                continue
            data.action = action if _alive(action) else None
            if _alive(slot) and _alive(action):
                data.action_slot = slot
            data.use_nla = use_nla
            for track, mute, solo in tracks:
                if _alive(track):
                    track.mute, track.is_solo = mute, solo
        if _alive(scene):
            scene.frame_set(frame[0], subframe=frame[1])
        if _alive(view_layer):
            view_layer.update()
        for target, values in transforms:
            if _alive(target):
                for name, value in values.items():
                    setattr(target, name, value)
        for key, value in shape_values:
            if _alive(key):
                key.value = value
        for target, values in properties:
            if _alive(target):
                for name, value in values.items():
                    target[name] = value
        for item, viewport, render in visibility:
            if _alive(item):
                item.hide_viewport, item.hide_render = viewport, render
        if _alive(view_layer):
            for item in view_layer.objects:
                item.select_set(item.as_pointer() in selected, view_layer=view_layer)
            if _alive(active) and view_layer.objects.get(active.name) == active:
                view_layer.objects.active = active
                set_mode(mode)
        if _alive(window):
            window.cursor_set('DEFAULT')
    return restore


def refresh_clip_cache(context, obj):
    """Discover with bounded preview settings; the real export always validates again."""
    global _discovery
    from . import local_preview
    local_preview.stop()
    if context.mode != 'OBJECT':
        raise RuntimeError('Clip discovery requires Object Mode; return to Object Mode to refresh exported clips.')
    if _discovery is not None:
        return cached_clip_names(obj)
    restore = capture_state(context)
    settings = context.scene.skein_extension_properties
    token = str(uuid.uuid4())
    _discovery = {'id': token, 'clips': {}}
    try:
        with tempfile.TemporaryDirectory(prefix='skein-clips-') as temp:
            result = bpy.ops.export_scene.gltf(
                filepath=str(Path(temp) / 'discovery.glb'), export_format='GLB',
                gltf_export_id=token, will_save_settings=False,
                use_active_scene=True, use_selection=False,
                export_animation_mode=settings.clip_export_mode,
                export_animations=settings.clip_export_animations,
            )
            if result != {'FINISHED'}:
                raise RuntimeError('Clip discovery export did not finish.')
        if obj.name not in _discovery['clips']:
            raise RuntimeError('Skein export hook did not discover this Object. Enable Skein in Blender Preferences and ensure the Object is exported.')
        names = _discovery['clips'][obj.name]
        _cache[obj.as_pointer()] = {'names': names, 'error': None,
            'bindings': _discovery.get('bindings', {}).get(obj.name, {})}
        return names
    except Exception as error:
        _cache[obj.as_pointer()] = {'names': [], 'error': str(error)}
        raise
    finally:
        _discovery = None
        restore()
        _cache[obj.as_pointer()]['signature'] = clip_cache_signature(context, obj)
        _last_checks[obj.as_pointer()] = time.monotonic()
        while len(_cache) > 64:
            expired = next(iter(_cache))
            del _cache[expired]
            _last_checks.pop(expired, None)


def _refresh_pending():
    global _pending
    from . import local_preview
    if _pending is None:
        return None
    if local_preview.active():
        return 0.5
    obj, signature, due = _pending
    if time.monotonic() < due:
        return max(0.1, due - time.monotonic())
    if bpy.context.mode != 'OBJECT':
        return 0.5
    _pending = None
    refreshed = False
    try:
        if bpy.context.object == obj:
            signature = clip_cache_signature(bpy.context, obj)
            _last_checks[obj.as_pointer()] = time.monotonic()
            cached = _cache.get(obj.as_pointer())
            if cached is None or cached.get('signature') != signature:
                if cached:
                    cached['stale'] = True
                refresh_clip_cache(bpy.context, obj)
                refreshed = True
    except (RuntimeError, ReferenceError) as error:
        print(f'Skein clip discovery: {error}')
        refreshed = True
    if refreshed:
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                area.tag_redraw()
    return None


def request_clip_refresh(context, obj):
    global _pending
    from . import local_preview
    if _discovery is not None or local_preview.active():
        return
    now = time.monotonic()
    previous_check = _last_checks.get(obj.as_pointer(), 0)
    if context.mode != 'OBJECT':
        if _pending is None or _pending[0] != obj:
            _pending = (obj, None, now + 0.4)
    elif now - previous_check < 0.5:
        # Schedule a trailing check even when the triggering redraw is the last one.
        if _pending is None or _pending[0] != obj:
            _pending = (obj, None, previous_check + 0.5)
    else:
        signature = clip_cache_signature(context, obj)
        _last_checks[obj.as_pointer()] = now
        cached = _cache.get(obj.as_pointer())
        if cached and cached.get('signature') == signature:
            return
        if cached:
            cached['stale'] = True
        if _pending is None or _pending[:2] != (obj, signature):
            _pending = (obj, signature, now + 0.4)
    while len(_last_checks) > 64:
        _last_checks.pop(next(iter(_last_checks)))
    if not bpy.app.timers.is_registered(_refresh_pending):
        bpy.app.timers.register(_refresh_pending, first_interval=0.4)


def draw_clip_field(layout, form, field, obj, type_path=None):
    """Keep the real StringProperty and write chosen names directly into it."""
    row = layout.row(align=True)
    row.prop(form, field)
    row.context_pointer_set('skein_clip_target', form)
    row.context_pointer_set('skein_clip_object', obj)
    row.context_string_set('skein_clip_field', field)
    names = cached_clip_names(obj)
    current = getattr(form, field)
    row.menu('SKEIN_MT_exported_clips', text='', icon='DOWNARROW_HLT')
    from . import local_preview
    play = row.operator('wm.skein_preview_clip', text='', icon='PLAY')
    local_preview.capture_target(play, obj, form, field)
    if local_preview.active(obj):
        row.operator('wm.skein_stop_clip_preview', text='', icon='PAUSE')
    from .authoring import selectable_clips
    if names is not None and current and current not in selectable_clips(names):
        for line in textwrap.wrap(f'Missing or ambiguous exported clip: {current}', width=55):
            layout.label(text=line, icon='ERROR')


class SkeinChooseClip(bpy.types.Operator):
    bl_idname = 'wm.skein_choose_clip'
    bl_label = 'Choose exported animation clip'
    bl_options = {'UNDO', 'INTERNAL'}
    field: bpy.props.StringProperty() # type: ignore
    clip: bpy.props.StringProperty() # type: ignore
    target_object: bpy.props.StringProperty(options={'HIDDEN'}) # type: ignore
    target_path: bpy.props.StringProperty(options={'HIDDEN'}) # type: ignore
    target_pointer: bpy.props.StringProperty(options={'HIDDEN'}) # type: ignore

    def execute(self, context):
        from . import local_preview
        local_preview.stop()
        owner = bpy.data.objects.get(self.target_object)
        try:
            form = owner.path_resolve(self.target_path) if owner else None
        except (ValueError, TypeError):
            form = None
        if (form is None or str(form.as_pointer()) != self.target_pointer
                or self.field not in getattr(form, '__annotations__', {})):
            self.report({'WARNING'}, 'The animation clip field is unavailable; reopen the clip menu and choose again.')
            return {'CANCELLED'}
        from .authoring import selectable_clips
        entry = _cache.get(owner.as_pointer(), {})
        names = cached_clip_names(owner)
        if (entry.get('signature') != clip_cache_signature(context, owner)
                or names is None or self.clip not in selectable_clips(names)):
            self.report({'WARNING'}, 'The exported clip choices changed; refresh and choose again.')
            return {'CANCELLED'}
        setattr(form, self.field, self.clip)
        return {'FINISHED'}


class SkeinExportedClips(bpy.types.Menu):
    bl_idname = 'SKEIN_MT_exported_clips'
    bl_label = 'Exported animation clips'

    def draw(self, context):
        obj = context.skein_clip_object
        names = cached_clip_names(obj)
        error = cached_clip_error(obj)
        if error:
            for line in textwrap.wrap('Clip discovery failed: ' + error, width=55):
                self.layout.label(text=line, icon='ERROR')
            self.layout.operator('wm.skein_refresh_clips', text='Refresh exported clips', icon='FILE_REFRESH')
        elif names is None:
            self.layout.label(text='Discovering exported clips…')
        elif not names:
            self.layout.label(text='No exported clips in this Object hierarchy')
        else:
            from .authoring import selectable_clips
            choices = selectable_clips(names)
            if not choices:
                self.layout.label(text='No unambiguous exported clip names', icon='ERROR')
            for name in choices:
                operation = self.layout.operator('wm.skein_choose_clip', text=name)
                operation.field = context.skein_clip_field
                operation.clip = name
                operation.target_object = obj.name
                operation.target_path = context.skein_clip_target.path_from_id()
                operation.target_pointer = str(context.skein_clip_target.as_pointer())


class SkeinRefreshClips(bpy.types.Operator):
    bl_idname = 'wm.skein_refresh_clips'
    bl_label = 'Refresh exported clips'

    def execute(self, context):
        try:
            refresh_clip_cache(context, context.object)
        except RuntimeError as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


@persistent
def clear_cache(*_args):
    global _pending
    _cache.clear()
    _last_checks.clear()
    _pending = None


classes = (SkeinChooseClip, SkeinExportedClips, SkeinRefreshClips)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.app.handlers.load_post.append(clear_cache)


def unregister():
    if bpy.app.timers.is_registered(_refresh_pending):
        bpy.app.timers.unregister(_refresh_pending)
    bpy.app.handlers.load_post.remove(clear_cache)
    clear_cache()
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
