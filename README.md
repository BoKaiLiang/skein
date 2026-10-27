# Skein

A Bevy Plugin and a Blender extension for improving the efficiency of Bevy/Blender workflows: Store reflected component data in glTF extras using software like Blender, and insert components based on those extras.

tldr:

1. Reflect component data in Bevy
2. Apply Bevy Components to objects, meshes, or materials in Blender
3. Export to glTF
4. Components are instantiated when spawning in Bevy

## Local fork authoring tools

This branch adds generic List editing, project-described animation dropdowns,
whole-component Copy/Paste/Reset, automatic registry/default caching, and export
validation. It targets Bevy 0.19.0, Rust `bevy_skein` 0.6.0, and Blender 5.2+.
The addon version is 0.1.23. The upstream compatibility table below describes
upstream releases, not this fork's tested support.

Remove the official addon, install this fork's ZIP with Blender's **Install from
Disk**, and enable it. Start a Bevy application with Skein BRP enabled. With
Blender Online Access allowed, registry, Rust defaults and optional authoring
metadata synchronize automatically. A saved `.blend` restores its caches offline.
No Text Editor or manual JSON import is needed. Sync failures keep authored data.
Runtime component metadata and required-field ordering do not force migration.
Compatible refreshes can add supported empty List/None Option fields while
verifying every existing value; other field removals or type changes still reject.

Right-click a Skein component row to copy its whole value, paste into an existing
component of the exact same type, or reset to its cached Rust Default. Paste and
Reset each create one Undo step. Missing defaults disable Reset with a reason.
The first version refuses unsupported or lossy values, including Map/Set/Array,
multi-field tuples, opaque values and references needing remapping. It preserves
existing values on rejection. Bone whole-component clipboard operations are not
supported yet.

Projects insert `authoring::AuthoringRegistry` before adding `SkeinPlugin` to
advertise String animation fields and optional validation/preview methods.
Editors are keyed by the defining reflected type and field, including nested
Struct/Option/List values. The addon has no game-specific component names or
animation policies. `skein/authoring` returns `skein.authoring/v1`.
Animation choices use actual final glTF names within the owning Object hierarchy.
Missing/ambiguous references retain their values and block export. Every declared
Clip field is checked, even if project logic currently overrides it.

The project validation method receives current final-export records as
`{"objects":[{"name":"...","kind":"Object","components":[{"project::Type":{}}]}]}`.
It returns `{"diagnostics":[{"severity":"ERROR","object":"...","field":"...","message":"..."}]}`.
Local schema and Clip checks run offline. An unavailable project check warns
that it is unverified; returned errors and malformed responses abort before a
last-good GLB is replaced. Previews use advertised methods and request-field
mappings, keeping runtime playback policy in the project.

Authoring regression commands, using a test-owned Blender process:

```sh
python3 tools/test_authoring.py
python3 tools/test_connection.py
Blender --background --factory-startup --online-mode --python-exit-code 1 --python tools/test_authoring_sync.py
Blender --background --factory-startup --offline-mode --python-exit-code 1 --python tools/test_lists.py
Blender --background --factory-startup --online-mode --python-exit-code 1 --python tools/test_action_preview.py
Blender --background --factory-startup --offline-mode --python-exit-code 1 --python tools/test_action_validation.py
Blender --background --factory-startup --offline-mode --python-exit-code 1 --python tools/test_clip_picker.py
Blender --factory-startup --offline-mode --python-exit-code 1 --python tools/test_component_values.py
```

The final command needs a GUI system clipboard on macOS and restores it before
quitting. Do not run it alongside another clipboard test or an artist's Blender
session.

## Quickstart

Add the plugin and reflect component data (`reflect(Component)` is important!)

```rust no_run
use bevy::prelude::*;
use bevy_skein::SkeinPlugin;

fn main() {
    App::new()
        .add_plugins((
            DefaultPlugins,
            SkeinPlugin::default(),
        ))
        .run();
}

#[derive(Component, Reflect, Debug)]
#[reflect(Component)]
struct Player {
    name: String,
    power: f32,
    test: i32,
}
```

- Install the Blender Extension
  - Drag and drop the installation link from the website, as detailed in the [installation](https://bevyskein.dev/docs/installation) docs.
- Fetch the Bevy registry using the Blender Operator
- Select and apply a component
- Export to glTF
- Spawn a Scene from the glTF file, which will have components instantiated

```rust ignore
commands.spawn(SceneRoot(asset_server.load(
    GltfAssetLabel::Scene(0).from_asset("my_export.gltf"),
)));
```

## Use Cases

- Apply Bevy Components in Blender
- Replace materials from Blender with materials defined in Bevy ([example](examples/replace_material.rs))
- Use Blender Drivers to power Bevy Component values

## Compatible Bevy versions

The Bevy plugin and the Blender addon have separate release cycles and versions. The contact points are the BRP Registry API format for ingesting Bevy data into Blender and the glTF format we store data in to get data back into Bevy. These clearly-defined API points mean the Bevy Plugin and the Blender addon can evolve independently.

All versions of `bevy_skein` are currently compatible with all versions of the `skein` Blender addon. However, the extension format export support was added in addon verison `0.1.14`, and is only supported by `Bevy 0.18+`.

| Bevy version | `bevy_skein` version |
| :----------- | :------------------- |
| `0.19`       | `0.6`                |
| `0.18`       | `0.5`                |
| `0.17`       | `0.3-4`              |
| `0.16`       | `0.2`                |
| `0.15`       | `0.1`                |

The skein addon current release and main branch support as far back as 4.2. Upgrading Blender to new versions requires updating the addon to at least the minimum version here as well.

| Blender version  | `skein` minimum addon version | notes                                                                         |
| :--------------- | :---------------------------- | ----------------------------------------------------------------------------- |
| `>=5.2`          | `0.1.16`                      | upstream glTF exporter has critical bug fixes for `extensions` export feature |
| `>=5.0`          | `0.1.13`                      |                                                                               |
| `>=4.2 && < 5.0` | `0.1.0`                       |                                                                               |
| `>=4.2`          | branch `main`                 |                                                                               |

## Why is it named Skein?

Its a tool that aims to improve the efficiency of Bevy (group of birds) and Blender workflows.

> A flock of wild geese or swans in flight, typically in a V-shaped formation.
>
> - [Oxford Dictionary](https://web.archive.org/web/20190107072506/https://en.oxforddictionaries.com/definition/skein)

> A V formation is a symmetric V- or chevron-shaped flight formation. In nature, it occurs among geese, swans, ducks, and other migratory birds, improving their energy efficiency
>
> - [Wikipedia](https://en.wikipedia.org/wiki/V_formation)

Its also thread/yarn related which is cool too.
