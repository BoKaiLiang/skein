import bpy

from .op_insert_component import touch_all_fields


def active_list(context):
    value = getattr(context, "skein_list", None)
    return value if getattr(value, "_skein_is_list", False) else None


class SkeinListAdd(bpy.types.Operator):
    """Append an item to this reflected list"""
    bl_idname = "wm.skein_list_add"
    bl_label = "Add List Item"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        target = active_list(context)
        if target is None:
            return {'CANCELLED'}
        entry = target.entries.add()
        touch_all_fields(entry, "value")
        return {'FINISHED'}


class SkeinListRemove(bpy.types.Operator):
    """Remove an item from this reflected list"""
    bl_idname = "wm.skein_list_remove"
    bl_label = "Remove List Item"
    bl_options = {'REGISTER', 'UNDO'}

    index: bpy.props.IntProperty(min=0)

    def execute(self, context):
        target = active_list(context)
        if target is None or not 0 <= self.index < len(target.entries):
            return {'CANCELLED'}
        target.entries.remove(self.index)
        return {'FINISHED'}


register, unregister = bpy.utils.register_classes_factory((SkeinListAdd, SkeinListRemove))
