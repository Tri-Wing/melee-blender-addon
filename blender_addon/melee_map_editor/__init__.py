bl_info = {
    'name': 'Melee Map Editor', 'author': 'Melee Map Editor contributors',
    'version': (0, 1, 0), 'blender': (4, 5, 0), 'location': 'View3D > Sidebar > Melee Map',
    'description': 'Import Melee stages and edit static collision', 'category': 'Import-Export',
}

import uuid
from pathlib import Path
import bmesh
import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty
from bpy_extras.io_utils import ExportHelper, ImportHelper
from . import collision, scene
from .protocol import StageError, read, run


class MME_Preferences(bpy.types.AddonPreferences):
    bl_idname = __package__
    cli_path: StringProperty(name='MeleeMap CLI', subtype='FILE_PATH',
        description='Path to meleemap executable or MeleeMap.Cli build meleemap.dll')
    dotnet_path: StringProperty(name='dotnet executable', default='dotnet',
        description='Only needed when using a development .dll build')

    def draw(self, context):
        self.layout.prop(self, 'cli_path')
        self.layout.prop(self, 'dotnet_path')
        self.layout.label(text='Developed and tested with Blender 4.5.0 on Linux.')


def backend(context):
    prefs = context.preferences.addons[__package__].preferences
    return bpy.path.abspath(prefs.cli_path), prefs.dotnet_path


def execute_safely(operator, context, action):
    try:
        action()
        return {'FINISHED'}
    except (StageError, OSError, ValueError, KeyError, RuntimeError) as exc:
        context.scene.mme_status = str(exc)
        operator.report({'ERROR'}, str(exc))
        return {'CANCELLED'}


class MME_OT_import(bpy.types.Operator, ImportHelper):
    bl_idname = 'mme.import_stage'
    bl_label = 'Import Stage DAT'
    filename_ext = '.dat'
    filter_glob: StringProperty(default='*.dat', options={'HIDDEN'})

    def execute(self, context):
        def action():
            if context.scene.mme_session:
                raise StageError('Use a new Blender scene to import another stage.')
            if context.mode != 'OBJECT':
                raise StageError('Switch to Object Mode before importing a stage.')
            root = Path(bpy.utils.user_resource('DATAFILES', path='melee_map_editor/sessions', create=True))
            directory = root / uuid.uuid4().hex
            run(*backend(context), 'extract', bpy.path.abspath(self.filepath), '--session', directory)
            obj = scene.import_session(context, directory)
            bpy.ops.object.select_all(action='DESELECT')
            obj.select_set(True)
            context.view_layer.objects.active = obj
            self.report({'INFO'}, context.scene.mme_status)
        return execute_safely(self, context, action)


class MME_OT_export(bpy.types.Operator, ExportHelper):
    bl_idname = 'mme.export_stage'
    bl_label = 'Export Stage DAT'
    filename_ext = '.dat'
    filter_glob: StringProperty(default='*.dat', options={'HIDDEN'})
    check_existing: BoolProperty(default=True, options={'HIDDEN'})

    def invoke(self, context, event):
        try:
            stage = read(scene.session(context.scene) / 'stage.json')
            filename = Path(stage['source']['filename']).name
        except (StageError, OSError):
            filename = 'stage.dat'
        self.filepath = str(Path(self.filepath).with_name(filename)) if self.filepath else filename
        return ExportHelper.invoke(self, context, event)

    def execute(self, context):
        def action():
            path = Path(bpy.path.abspath(self.filepath)).resolve()
            scene.apply(context.scene, *backend(context), path)
            context.scene.mme_export_directory = str(path.parent)
            context.scene.mme_status = f'Exported and structurally validated: {path.name}'
            self.report({'INFO'}, context.scene.mme_status)
        return execute_safely(self, context, action)


class MME_OT_validate(bpy.types.Operator):
    bl_idname = 'mme.validate_stage'
    bl_label = 'Validate Stage'

    def execute(self, context):
        def action():
            scene.validate(context.scene, *backend(context))
            context.scene.mme_status = 'Structural validation passed. In-game testing is still required.'
            self.report({'INFO'}, context.scene.mme_status)
        return execute_safely(self, context, action)


class MME_OT_groups(bpy.types.Operator):
    bl_idname = 'mme.toggle_models'
    bl_label = 'Show / Hide Model Groups'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        def action():
            matches = [c for c in bpy.data.collections if c.get('mme_session_id') == context.scene.mme_session_id
                       and c.get('mme_role') == 'models']
            if len(matches) != 1:
                raise StageError('The protected Models collection is missing or duplicated.')
            matches[0].hide_viewport = not matches[0].hide_viewport
        return execute_safely(self, context, action)


class MME_OT_edit_collision(bpy.types.Operator):
    bl_idname = 'mme.edit_collision'
    bl_label = 'Enter Collision Editing'

    def execute(self, context):
        def action():
            stage = read(scene.session(context.scene) / 'stage.json')
            if not stage['capabilities']['collisionEdit']:
                raise StageError('Collision is read-only for this stage.')
            obj = scene.collision_object(context.scene)
            if context.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
            bpy.ops.object.select_all(action='DESELECT')
            obj.hide_set(False)
            obj.select_set(True)
            context.view_layer.objects.active = obj
            context.tool_settings.mesh_select_mode = (True, False, False)
            bpy.ops.object.mode_set(mode='EDIT')
        return execute_safely(self, context, action)


class MME_OT_assign(bpy.types.Operator):
    bl_idname = 'mme.assign_collision'
    bl_label = 'Assign Collision Property'
    bl_options = {'REGISTER', 'UNDO'}
    property: EnumProperty(items=[(x, x.title(), '') for x in ('type', 'material', 'drop', 'ledge')])

    def execute(self, context):
        def action():
            obj = scene.collision_object(context.scene)
            if obj != context.edit_object:
                raise StageError('Enter Collision Editing and select edges first.')
            if not read(scene.session(context.scene) / 'stage.json')['capabilities']['collisionEdit']:
                raise StageError('Collision is read-only for this stage.')
            bm = bmesh.from_edit_mesh(obj.data)
            selected = [e for e in bm.edges if e.select]
            if not selected:
                raise StageError('Select collision edges first.')
            key = 'mme_category' if self.property == 'type' else 'mme_low'
            layer = bm.edges.layers.int.get(key)
            if layer is None:
                raise StageError('Collision attributes are missing. Undo the edit or re-import.')
            for e in selected:
                if self.property == 'type':
                    e[layer] = collision.CATEGORIES.index(context.scene.mme_collision_type)
                elif self.property == 'material':
                    e[layer] = (e[layer] & 0xFF00) | context.scene.mme_collision_material
                else:
                    e[layer] ^= 0x100 if self.property == 'drop' else 0x200
            bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
            obj['mme_dirty'] = True
        return execute_safely(self, context, action)


class MME_OT_open_export(bpy.types.Operator):
    bl_idname = 'mme.open_export_directory'
    bl_label = 'Open Export Directory'

    def execute(self, context):
        if not context.scene.mme_export_directory:
            return {'CANCELLED'}
        bpy.ops.wm.path_open(filepath=context.scene.mme_export_directory)
        return {'FINISHED'}


class MME_PT_stage(bpy.types.Panel):
    bl_label = 'Melee Map'
    bl_idname = 'MME_PT_stage'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Melee Map'

    def draw(self, context):
        layout = self.layout
        s = context.scene
        layout.operator('mme.import_stage', icon='IMPORT')
        if not s.mme_session:
            layout.label(text='Choose the CLI in add-on preferences.')
            return
        info = s.get('mme_stage_info')
        if info:
            import json
            info = json.loads(info)
            layout.label(text=info['filename'])
            layout.label(text=f"{info['groups']} groups / {info['lines']} collision lines")
            layout.label(text='Source hash checked on validation/export')
            if info.get('deferred'):
                layout.label(text=f"{info['deferred']} unsupported meshes omitted", icon='ERROR')
            if not info['editable']:
                layout.label(text='Collision is read-only for this stage.', icon='LOCKED')
        try:
            obj = scene.collision_object(s)
            layout.label(text='Collision: edited' if obj.get('mme_dirty') else 'Collision: unchanged')
        except StageError:
            layout.label(text='Collision object missing', icon='ERROR')
        layout.label(text='Grey models: read-only static pose')
        layout.operator('mme.toggle_models')
        layout.operator('mme.edit_collision')
        layout.label(text='Move vertices on X/Z; keep Blender Y = 0.')
        layout.label(text='Topology changes are not available yet.')
        row = layout.row(align=True)
        row.prop(s, 'mme_collision_type', text='')
        row.operator('mme.assign_collision', text='Assign Type').property = 'type'
        row = layout.row(align=True)
        row.prop(s, 'mme_collision_material')
        row.operator('mme.assign_collision', text='Assign').property = 'material'
        row = layout.row(align=True)
        row.operator('mme.assign_collision', text='Toggle Drop-through').property = 'drop'
        row.operator('mme.assign_collision', text='Toggle Ledge-grab').property = 'ledge'
        layout.label(text='Floor: green / Ceiling: red')
        layout.label(text='Right wall: blue / Left wall: amber')
        layout.label(text='Dynamic: purple (read-only)')
        layout.label(text='White mark: drop-through / ledge flag')
        layout.operator('mme.validate_stage', icon='CHECKMARK')
        layout.operator('mme.export_stage', icon='EXPORT')
        if s.mme_export_directory:
            layout.operator('mme.open_export_directory', icon='FILE_FOLDER')
        box = layout.box()
        import textwrap
        for line in textwrap.wrap(s.mme_status, width=38):
            box.label(text=line)


_draw_handle = None


def draw_collision():
    # Query the current scene every draw, so undo and loading .blend files are safe.
    import gpu
    from gpu_extras.batch import batch_for_shader
    context = bpy.context
    if not context.scene.mme_session or not context.space_data.overlay.show_overlays:
        return
    try:
        obj = scene.collision_object(context.scene)
        if not obj.visible_get():
            return
        batches = [[] for _ in collision.CATEGORIES]
        markers = []
        if obj.mode == 'EDIT':
            bm = bmesh.from_edit_mesh(obj.data)
            layer = bm.edges.layers.int.get('mme_category')
            low = bm.edges.layers.int.get('mme_low')
            if layer is None or low is None:
                return
            for edge in bm.edges:
                if edge.hide:
                    continue
                category = edge[layer]
                if 0 <= category < len(batches):
                    points = [obj.matrix_world @ v.co for v in edge.verts]
                    batches[category].extend(points)
                    if edge[low] & 0x300:
                        markers.append((points[0] + points[1]) * 0.5)
        else:
            attr = obj.data.attributes.get('mme_category')
            low = obj.data.attributes.get('mme_low')
            if attr is None or low is None:
                return
            for edge, value, flags in zip(obj.data.edges, attr.data, low.data):
                if 0 <= value.value < len(batches):
                    points = [obj.matrix_world @ obj.data.vertices[i].co for i in edge.vertices]
                    batches[value.value].extend(points)
                    if flags.value & 0x300:
                        markers.append((points[0] + points[1]) * 0.5)
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        depth = gpu.state.depth_test_get()
        width = gpu.state.line_width_get()
        try:
            gpu.state.depth_test_set('NONE')
            gpu.state.line_width_set(2)
            shader.bind()
            for coords, color in zip(batches, collision.COLORS):
                if coords:
                    shader.uniform_float('color', color)
                    batch_for_shader(shader, 'LINES', {'pos': coords}).draw(shader)
            # White crosses indicate lines with drop-through or ledge flags.
            if markers:
                from mathutils import Vector
                coords = []
                for p in markers:
                    for delta in (Vector((0.6, 0, 0)), Vector((0, 0, 0.6))):
                        coords.extend((p-delta, p+delta))
                shader.uniform_float('color', (1, 1, 1, 1))
                batch_for_shader(shader, 'LINES', {'pos': coords}).draw(shader)
        finally:
            gpu.state.depth_test_set(depth)
            gpu.state.line_width_set(width)
    except (StageError, ReferenceError):
        pass


@bpy.app.handlers.persistent
def update_dirty(scene_arg, depsgraph):
    if not scene_arg.mme_session:
        return
    try:
        obj = scene.collision_object(scene_arg)
        # Small collision meshes make this cheap; model geometry is checked on export.
        dirty = collision.fingerprint(obj) != scene_arg.get('mme_collision_fingerprint')
        if bool(obj.get('mme_dirty')) != dirty:
            obj['mme_dirty'] = dirty
    except (StageError, ReferenceError):
        pass


CLASSES = (MME_Preferences, MME_OT_import, MME_OT_export, MME_OT_validate, MME_OT_groups,
           MME_OT_edit_collision, MME_OT_assign, MME_OT_open_export, MME_PT_stage)
SCENE_PROPS = ('mme_session', 'mme_session_id', 'mme_status', 'mme_export_directory',
               'mme_collision_type', 'mme_collision_material')


def register():
    global _draw_handle
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.mme_session = StringProperty(subtype='DIR_PATH')
    bpy.types.Scene.mme_session_id = StringProperty()
    bpy.types.Scene.mme_status = StringProperty(default='No stage imported')
    bpy.types.Scene.mme_export_directory = StringProperty(subtype='DIR_PATH')
    bpy.types.Scene.mme_collision_type = EnumProperty(name='Type', items=[
        (x, x.replace('-', ' ').title(), '') for x in collision.CATEGORIES[:-1]])
    bpy.types.Scene.mme_collision_material = IntProperty(name='Material ID', min=0, max=255)
    bpy.app.handlers.depsgraph_update_post.append(update_dirty)
    if not bpy.app.background:
        _draw_handle = bpy.types.SpaceView3D.draw_handler_add(draw_collision, (), 'WINDOW', 'POST_VIEW')


def unregister():
    global _draw_handle
    if _draw_handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_draw_handle, 'WINDOW')
        _draw_handle = None
    if update_dirty in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(update_dirty)
    for prop in SCENE_PROPS:
        delattr(bpy.types.Scene, prop)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
