import bpy
from .arforge_migration import MigrateArforgeCharacters, legacy_objects
from .op_trigger_collection_exporters import TriggerCollectionExporters

class SKEIN_PT_sidebar(bpy.types.Panel):
    """Display test button"""
    bl_label = "Skein"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "skein"

    def draw(self, context):
        col = self.layout.column(align=True)
        col.operator(TriggerCollectionExporters.bl_idname)
        if legacy_objects(context.scene.objects):
            col.operator(MigrateArforgeCharacters.bl_idname)

classes = (
    SKEIN_PT_sidebar,
)

register, unregister = bpy.utils.register_classes_factory(classes)