//! Optional editor hints. Metadata describes tools without coupling the addon to a game.
use std::collections::BTreeMap;

use bevy_ecs::resource::Resource;
use serde::Serialize;
use serde_json::Value;

/// Project-provided authoring metadata. Inserting this before `SkeinPlugin` is supported.
#[derive(Resource, Default, Clone, Debug, Serialize)]
pub struct AuthoringRegistry {
    /// Editors keyed by the defining reflected type and field name.
    pub fields: Vec<FieldEditor>,
    /// Optional project method validating the current export records.
    pub validation_method: Option<String>,
    /// Optional project preview operations for list entries.
    pub previews: Vec<PreviewOperation>,
}

/// Editor override for a single reflected field.
#[derive(Clone, Debug, Serialize)]
pub struct FieldEditor {
    /// Defining reflected type path, including for nested structs.
    pub type_path: String,
    /// Reflected field name.
    pub field: String,
    /// Widget understood by the generic addon.
    pub widget: FieldWidget,
}

/// Supported editor overrides.
#[derive(Clone, Copy, Debug, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum FieldWidget {
    /// A String selected from final exported animation names.
    AnimationClip,
}

/// A project preview method operating on one component list entry.
#[derive(Clone, Debug, Serialize)]
pub struct PreviewOperation {
    /// Owning reflected component type.
    pub component_type: String,
    /// List field on that component.
    pub list_field: String,
    /// Stable identity field on each entry.
    pub identity_field: String,
    /// Button label.
    pub label: String,
    /// Project BRP method for previewing an entry.
    pub method: String,
    /// Mapping from request parameter names to entry field names.
    pub request_fields: BTreeMap<String, String>,
    /// Explicit stop request for this operation.
    pub stop: PreviewStop,
}

/// Method and payload that stop a preview.
#[derive(Clone, Debug, Serialize)]
pub struct PreviewStop {
    /// Project BRP method.
    pub method: String,
    /// Method parameters.
    pub params: Value,
}

/// Generic authoring metadata method.
#[cfg(all(feature = "brp", not(target_family = "wasm")))]
pub const BRP_SKEIN_AUTHORING_METHOD: &str =
    "skein/authoring";

/// Return the project metadata in the versioned authoring envelope.
#[cfg(all(feature = "brp", not(target_family = "wasm")))]
pub fn export_authoring(
    bevy_ecs::system::In(_): bevy_ecs::system::In<
        Option<Value>,
    >,
    world: &bevy_ecs::world::World,
) -> bevy_remote::BrpResult {
    let mut value = serde_json::to_value(
        world.resource::<AuthoringRegistry>(),
    )
    .map_err(bevy_remote::BrpError::internal)?;
    value["schema"] =
        Value::String("skein.authoring/v1".into());
    Ok(value)
}
