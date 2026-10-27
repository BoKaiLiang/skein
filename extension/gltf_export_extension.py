import bpy

# glTF extensions are named following a convention with known prefixes.
# See: https://github.com/KhronosGroup/glTF/tree/main/extensions#about-gltf-extensions
# also: https://github.com/KhronosGroup/glTF/blob/main/extensions/Prefixes.md
glTF_extension_name = "BEVY_skein"

# is this extension required to view the glTF?
extension_is_required = False
LIGHTS = {
    "POINT": "point",
    "SUN": "directional",
    "SPOT": "spot"
}

# gltf exporter extension
#
# The extension takes the data from the format we need to use inside of Blender
# which can include arbitrary "active_index" selections for lists, etc and
# rewrites the skein data into a format bevy can parse and reflect directly.
#
# exported gltf extras output will look like this:
#
# ```json
# "extras":{
#   "skein":[{
#     "test_project::Rotate":{
#       "speed":1.0
#     }
#   }],
# },
# ```
class SkeinExtensionProperties(bpy.types.PropertyGroup):
    enabled: bpy.props.BoolProperty(
        name="skein",
        description='Rewrite Skein data into a directly Bevy reflectable format',
        default=True
        ) # type: ignore
    extras: bpy.props.BoolProperty(
        name="extras",
        description='Export using glTF Extras',
        default=True
        ) # type: ignore
    clip_export_mode: bpy.props.EnumProperty(
        name="Picker export mode", default='NLA_TRACKS',
        items=[('NLA_TRACKS', 'NLA Tracks', 'Use exported NLA track names'),
               ('ACTIONS', 'Actions', 'Use the glTF Actions export mode'),
               ('ACTIVE_ACTIONS', 'Active Actions', 'Use active actions'),
               ('SCENE', 'Scene', 'Bake scene animation')],
    ) # type: ignore
    clip_export_animations: bpy.props.BoolProperty(name="Picker animations", default=True) # type: ignore
    extensions: bpy.props.BoolProperty(
        name="extensions",
        description='Export data into glTF Extensions',
        default=False
        ) # type: ignore
# Draw the Skein settings options in the glTF export panel
def draw_export(context, layout):

    # Note: If you are using Collection Exporter, you may want to restrict UI for some collections
    # You can access the collection like this: context.collection
    # So you can check if you want to show the UI for this collection or not, using
    # if context.collection.name != "Coll":
    #     return

    header, body = layout.panel("GLTF_addon_skein_exporter", default_closed=False)

    # TODO: True or False here (and in panels)? Affects visual layout
    header.use_property_split = False

    props = bpy.context.scene.skein_extension_properties

    header.prop(props, 'enabled', text="")
    header.label(text="Skein")
    if body != None:
        body.prop(props, 'extras')
        body.prop(props, 'extensions')

    # if body != None:
    #     body.prop(props, 'float_property', text="Some float value")


# Blender discovers this exact class name.
class glTF2ExportUserExtension:
    is_critical = True

    def __init__(self):
        from io_scene_gltf2.io.com.gltf2_io_extensions import Extension
        from .clip_picker import capture_state
        self.Extension = Extension
        self.properties = bpy.context.scene.skein_extension_properties
        self.nodes = {}
        self.sources = {}
        self.source_errors = {}
        self.restore_state = capture_state(bpy.context)

    def passthrough_extension_data(self, passthrough_extensions, gltf2_plan, export_settings):
        passthrough_extensions.append(glTF_extension_name)

    def gather_node_hook(self, node, source, settings):
        from .clip_picker import is_discovery
        self.nodes[id(node)] = (node, source.name)
        if not self.properties.enabled or is_discovery(settings):
            return
        if getattr(source.data, 'type', None) in LIGHTS and 'KHR_lights_punctual' in (node.extensions or {}):
            self.gather_skein_two(source.data, node.extensions['KHR_lights_punctual'].extension['light'].extension)
        self.gather_skein_two(source, node)

    def gather_mesh_hook(self, sink, source, blender_object, vertex_groups, modifiers, materials, settings):
        self.gather_if_enabled(source, sink, settings)

    def gather_material_hook(self, sink, source, settings):
        self.gather_if_enabled(source, sink, settings)

    def gather_camera_hook(self, sink, source, settings):
        self.gather_if_enabled(source, sink, settings)

    def gather_joint_hook(self, sink, source, settings):
        self.gather_if_enabled(source.bone, sink, settings)

    def gather_scene_hook(self, sink, source, settings):
        self.gather_if_enabled(source, sink, settings)

    def gather_if_enabled(self, source, sink, settings):
        from .clip_picker import is_discovery
        if self.properties.enabled and not is_discovery(settings):
            self.gather_skein_two(source, sink)

    def gather_gltf_hook(self, active_scene_idx, scenes, animations, settings):
        from .clip_picker import is_discovery, record_discovery
        from .validation import current_registry, validate_export, export_description, format_diagnostic
        reachable = set()
        def visit(value):
            if id(value) in reachable:
                return
            reachable.add(id(value))
            if isinstance(value, dict):
                children = value.values()
            elif isinstance(value, (list, tuple)):
                children = value
            else:
                children = vars(value).values() if hasattr(value, '__dict__') else ()
            for child in children:
                visit(child)
        visit(scenes)
        clips = {}
        def descendants(node, found):
            if id(node) in found:
                return
            found.add(id(node))
            for child in node.children or ():
                descendants(child, found)
        for node_id, (node, name) in self.nodes.items():
            if node_id not in reachable:
                continue
            hierarchy = set()
            descendants(node, hierarchy)
            own = {animation.name for animation in animations if any(
                id(channel.target.node) in hierarchy for channel in animation.channels or ())}
            # Duplicate names anywhere in the final GLB remain ambiguous.
            clips[name] = [animation.name for animation in animations if animation.name in own]
            if is_discovery(settings):
                record_discovery(name, clips[name])
        if is_discovery(settings) or not self.properties.enabled:
            return
        local_failures = [message for (sink_id, _source_id), message in self.source_errors.items()
                          if sink_id in reachable]
        if local_failures:
            self.abort(settings, 'Skein export validation failed:\n' + '\n'.join(local_failures))
        records = [record for sink_id, record in self.sources.values() if sink_id in reachable]
        try:
            registry = current_registry()
            description, diagnostics = export_description(registry)
            if not any(item['severity'] == 'ERROR' for item in diagnostics):
                diagnostics.extend(validate_export(records, registry, description, clips))
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError) as error:
            self.abort(settings, f'Skein export metadata validation failed: {error}')
        failures = []
        for item in diagnostics:
            message = format_diagnostic(item)
            if item['severity'] == 'ERROR':
                failures.append(message)
            else:
                print('Skein WARNING: ' + message)
                settings['log'].warning('Skein: ' + message, popup=True)
        if failures:
            self.abort(settings, 'Skein export validation failed:\n' + '\n'.join(failures))

    def abort(self, settings, message):
        def abort_export(_settings):
            self.restore_state()
            raise RuntimeError(message)
        # Older exporters swallow hook exceptions, but callbacks run before writing.
        settings['post_export_callbacks'].append(abort_export)
        abort_export(settings)

    def gather_skein_two(self, source, sink):
        from .validation import current_registry
        from .component_values import read_component, ERRORS
        if not (self.properties.extras or self.properties.extensions):
            return
        components = []
        try:
            registry = current_registry()
            for component in getattr(source, 'skein_two', ()):
                components.append({component.selected_type_path: read_component(component, registry)})
        except ERRORS as error:
            # Gathering can visit data later filtered from the final scenes.
            self.source_errors[(id(sink), source.as_pointer())] = f'{source.name}: invalid reflected component: {error}'
            return
        if not components:
            return
        kind = source.bl_rna.identifier
        if isinstance(source, bpy.types.Light):
            kind = 'Light'
        record = dict(name=source.name, kind=kind, components=components)
        self.sources[(id(sink), source.as_pointer())] = (id(sink), record)
        if self.properties.extras:
            if isinstance(sink, dict):
                sink.setdefault('extras', {})
                if sink['extras'] is None:
                    sink['extras'] = {}
                sink['extras']['skein'] = components
            else:
                if sink.extras is None:
                    sink.extras = {}
                sink.extras['skein'] = components
        if self.properties.extensions:
            if isinstance(sink, dict):
                extensions = sink.setdefault('extensions', {})
            else:
                if sink.extensions is None:
                    sink.extensions = {}
                extensions = sink.extensions
            extensions[glTF_extension_name] = self.Extension(
                name=glTF_extension_name, extension={'components': components}, required=extension_is_required)


def glTF2_pre_export_callback(export_settings):
    pass


def glTF2_post_export_callback(export_settings):
    pass


def pre_export_hook(export_settings):
    pass
