bl_info = {
    'name': 'Melee Map Editor', 'author': 'Melee Map Editor contributors',
    'version': (0, 1, 0), 'blender': (4, 5, 0), 'location': 'View3D > Sidebar > Melee Map',
    'description': 'Import Melee stages and edit static collision', 'category': 'Import-Export',
}

import uuid
import json
from pathlib import Path
import bmesh
import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, StringProperty
from bpy_extras.io_utils import ExportHelper, ImportHelper
from . import collision, scene, topology, materials, inspector, modeling, surface
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


class MME_OT_edit_model(bpy.types.Operator):
    bl_idname = 'mme.edit_model'
    bl_label = 'Edit Selected Model'

    def execute(self, context):
        def action():
            active = context.active_object
            infos = modeling.targets(context.scene)
            selected = next((info for info in infos if active and active.get('mme_id') == info['id']
                             and active.get('mme_session_id') == context.scene.mme_session_id), None)
            if active and active.get('mme_role') == 'pobj' and selected is None:
                raise StageError(active.get('mme_read_only_reason', 'This model is read-only in this session.'))
            obj = modeling.target_object(context.scene, selected or (infos[0] if infos else None))
            if context.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
            for collection in bpy.data.collections:
                if collection.get('mme_session_id') == context.scene.mme_session_id:
                    collection.hide_viewport = False
            bpy.ops.object.select_all(action='DESELECT')
            obj.hide_set(False)
            obj.select_set(True)
            context.view_layer.objects.active = obj
            context.tool_settings.mesh_select_mode = (True, False, False)
            bpy.ops.object.mode_set(mode='EDIT')
            context.scene.mme_status = f'Editing {obj.name}. Vertex moves preserve appearance; new faces use the assigned material.'
        return execute_safely(self, context, action)


_material_items_cache = {}


def model_material_items(self, context):
    entries = json.loads(context.scene.get('mme_model_materials', '[]')) if context else []
    key = tuple((m['id'], m['name'], m['usesUv']) for m in entries)
    if key not in _material_items_cache:
        _material_items_cache[key] = [('GREY', 'Grey Export', 'Use the plain grey replacement material')] + [
            (m['id'], m['name'] + (' (UV)' if m['usesUv'] else ''), 'Assign to the entire model') for m in entries]
    return _material_items_cache[key]


class MME_OT_model_material(bpy.types.Operator):
    bl_idname = 'mme.model_material'
    bl_label = 'Assign Model Material'
    bl_options = {'REGISTER', 'UNDO'}
    material_id: EnumProperty(name='Stage material', items=model_material_items)

    def invoke(self, context, event):
        obj = context.active_object
        if obj and obj.type == 'MESH' and obj.data.materials:
            current = surface.material_id(obj.data.materials[0])
            if current and any(item[0] == current for item in model_material_items(self, context)):
                self.material_id = current
        return context.window_manager.invoke_props_dialog(self, width=400)

    def draw(self, context):
        self.layout.prop(self, 'material_id')
        self.layout.label(text='Applies to all faces of the selected model.')

    def execute(self, context):
        def action():
            obj = context.active_object
            if not obj or obj.get('mme_session_id') != context.scene.mme_session_id or obj.get('mme_id') not in modeling.target_ids(context.scene):
                raise StageError('Select an editable model first.')
            if self.material_id == 'GREY':
                material = bpy.data.materials.new('Melee Export Grey')
                material.diffuse_color = (0.45, 0.45, 0.45, 1)
            else:
                material = next((m for m in bpy.data.materials if surface.material_id(m) == self.material_id), None)
                if material is None:
                    raise StageError('Stage material missing. Re-import to restore the material catalog.')
            surface.assign(obj, material)
            context.scene.mme_status = 'Material assigned to all faces. Use Blender UV tools for textured materials.'
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
            if self.property == 'type':
                collision.serialize(obj, read(scene.session(context.scene) / 'collision/collision.json'))
                collision.assign_type(bm, selected, collision.CATEGORIES.index(context.scene.mme_collision_type))
            for e in selected:
                if self.property == 'material':
                    e[layer] = (e[layer] & 0xFF00) | context.scene.mme_collision_material
                elif self.property in ('drop', 'ledge'):
                    e[layer] ^= 0x100 if self.property == 'drop' else 0x200
            bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
            obj['mme_dirty'] = True
        return execute_safely(self, context, action)


class MME_OT_topology(bpy.types.Operator):
    bl_idname = 'mme.collision_topology'
    bl_label = 'Edit Collision Topology'
    bl_options = {'REGISTER', 'UNDO'}
    operation: EnumProperty(items=[(x, label, '') for x, label in (
        ('split', 'Split Edge'), ('extend', 'Extend Collision'),
        ('connect', 'Connect Vertices'), ('reverse', 'Reverse Direction'))])
    offset_x: FloatProperty(name='Offset X', default=10)
    offset_z: FloatProperty(name='Offset Z', default=0)
    joint: IntProperty(name='Joint for isolated vertices', default=1, min=1)

    def invoke(self, context, event):
        if self.operation in ('extend', 'connect'):
            return context.window_manager.invoke_props_dialog(self)
        return self.execute(context)

    def draw(self, context):
        if self.operation == 'extend':
            self.layout.prop(self, 'offset_x')
            self.layout.prop(self, 'offset_z')
            self.layout.label(text='Inherits type, material and flags from the end edge.')
        elif self.operation == 'connect':
            self.layout.prop(context.scene, 'mme_collision_type')
            materials.draw_surface(self.layout, context.scene)
            self.layout.prop(self, 'joint')
            self.layout.label(text='Connected endpoints retain their existing joint.')

    def execute(self, context):
        def action():
            obj = scene.collision_object(context.scene)
            if context.edit_object != obj:
                raise StageError('Enter Collision Editing first.')
            directory = scene.session(context.scene)
            if not read(directory / 'stage.json')['capabilities']['collisionEdit']:
                raise StageError('Collision is read-only for this stage.')
            source = read(directory / 'collision/collision.json')
            topology.edit(obj, source, self.operation, self.offset_x, self.offset_z,
                          collision.CATEGORIES.index(context.scene.mme_collision_type),
                          context.scene.mme_collision_material, self.joint)
            obj['mme_dirty'] = True
            context.scene.mme_status = 'Collision topology updated. Validate before exporting.'
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
        editable = modeling.targets(s)
        if editable:
            ids = {info['id'] for info in editable}
            layout.label(text=f'{len(editable)} editable rigid models')
            active = context.active_object
            selected = active and active.get('mme_session_id') == s.mme_session_id and active.get('mme_id') in ids
            row = layout.row()
            row.enabled = bool(selected) or not (active and active.get('mme_role') == 'pobj')
            row.operator('mme.edit_model', icon='EDITMODE_HLT')
            if selected:
                layout.label(text='Selected: edited' if active.get('mme_dirty') else 'Selected: editable — unchanged')
            elif active and active.get('mme_role') == 'pobj':
                layout.label(text='Selected model: read-only', icon='LOCKED')
                layout.label(text=active.get('mme_read_only_reason', 'Read-only in this session.'))
            layout.label(text='Vertex moves preserve original appearance.')
            layout.label(text='New faces use the assigned stage material.')
            row = layout.row()
            row.enabled = bool(selected)
            row.operator('mme.model_material', icon='MATERIAL')
            layout.label(text='One material per model; UVs use the active map.')
            if not s.get('mme_model_materials'):
                layout.label(text='Re-import to load stage materials.')
            layout.label(text='Object transforms and hierarchy are read-only.')
        else:
            layout.label(text='No supported rigid models in this scene.')
        if 'mme_editable_meshes' not in s:
            layout.label(text='Re-import to enable multiple model targets.')
        layout.operator('mme.toggle_models')
        layout.operator('mme.edit_collision')
        layout.label(text='Move vertices on X/Z; keep Blender Y = 0.')
        layout.label(text='Use these tools to create or reconnect edges.')
        for left, right in ((('split', 'Split Edge'), ('extend', 'Extend Collision')),
                            (('connect', 'Connect Vertices'), ('reverse', 'Reverse Direction'))):
            row = layout.row(align=True)
            for action, label in (left, right):
                row.operator('mme.collision_topology', text=label).operation = action
        layout.label(text='Native vertex/edge deletion is supported.')
        layout.label(text='Arrows show edge direction.')
        row = layout.row(align=True)
        row.prop(s, 'mme_collision_type', text='')
        row.operator('mme.assign_collision', text='Assign Type').property = 'type'
        row = layout.row(align=True)
        materials.draw_surface(row, s)
        row.operator('mme.assign_collision', text='Assign').property = 'material'
        row = layout.row(align=True)
        row.operator('mme.assign_collision', text='Toggle Drop-through').property = 'drop'
        row.operator('mme.assign_collision', text='Toggle Ledge-grab').property = 'ledge'
        layout.label(text='Solid floor: dark green')
        layout.label(text='Drop-through floor: bright green')
        layout.label(text='Ceiling: red')
        layout.label(text='Right wall: blue / Left wall: amber')
        layout.label(text='Dynamic: purple (read-only)')
        layout.label(text='White mark: ledge-grab flag')
        layout.operator('mme.validate_stage', icon='CHECKMARK')
        layout.operator('mme.export_stage', icon='EXPORT')
        if s.mme_export_directory:
            layout.operator('mme.open_export_directory', icon='FILE_FOLDER')
        box = layout.box()
        import textwrap
        for line in textwrap.wrap(s.mme_status, width=38):
            box.label(text=line)


class MME_PT_edge(bpy.types.Panel):
    bl_label = 'Selected Edge Metadata'
    bl_idname = 'MME_PT_edge'
    bl_parent_id = 'MME_PT_stage'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Melee Map'
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return bool(context.scene.mme_session)

    @staticmethod
    def details(context):
        obj = scene.collision_object(context.scene)
        if context.edit_object != obj:
            raise StageError('Enter Collision Editing and select an edge.')
        return inspector.describe(obj, inspector.source(scene.session(context.scene)))

    def draw(self, context):
        layout = self.layout
        try:
            info = self.details(context)
        except (StageError, OSError, ValueError, KeyError) as exc:
            import textwrap
            for line in textwrap.wrap(str(exc), 38):
                layout.label(text=line)
            return
        layout.label(text=f"Edge {info['handle']}" + (f" (active of {info['selected']})" if info['selected'] > 1 else ''))
        layout.label(text='Type: ' + info['category'].replace('-', ' ').title())
        layout.label(text=f"Surface: {info['surfaceName']} ({info['surface']})")
        drop = 'On' if info['drop'] else 'Off'
        layout.label(text='Drop-through: ' + drop + (' (floor only)' if info['category'] != 'floor' else ''))
        layout.label(text='Ledge-grab flag: ' + ('On' if info['ledge'] else 'Off'))
        layout.label(text='Disabled / empty: ' + ('Yes' if info['disabled'] else 'No'))
        layout.label(text=f"Collision joint: {info['joint']}")
        layout.label(text=f"Direction: vertex {info['start']} → {info['end']}")
        layout.label(text=f"Length: {info['length']:.4f} game units")
        layout.label(text='Endpoints (game X, Y):')
        for label, key in (('Start', 'startPosition'), ('End', 'endPosition')):
            x, depth, y = info[key]
            layout.label(text=f'{label}: ({x:.4f}, {y:.4f})')
            if abs(depth) > 0.00001:
                layout.label(text=f'Off collision plane: Blender Y = {depth:.4f}', icon='ERROR')
        for label, key in (('At start', 'start'), ('At end', 'end')):
            neighbors = ', '.join(map(str, info['adjacent'][key])) or 'none'
            layout.label(text=f'{label}: adjacent edges {neighbors}')
        baseline = info['baseline']
        layout.label(text=f"Original line index: {baseline['sourceIndex']} (zero-based)" if baseline else 'New edge (no original line index)')
        layout.label(text='Read-only display of actual edge values.')


class MME_PT_edge_raw(bpy.types.Panel):
    bl_label = 'Raw Flags and Source Metadata'
    bl_idname = 'MME_PT_edge_raw'
    bl_parent_id = 'MME_PT_edge'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Melee Map'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        try:
            info = MME_PT_edge.details(context)
        except (StageError, OSError, ValueError, KeyError):
            layout.label(text='Select a valid collision edge.')
            return
        layout.label(text=f"Stored high flags: 0x{info['high']:04X}")
        layout.label(text=f"Low flags: 0x{info['low']:04X}")
        if info['exportHigh'] is not None:
            layout.label(text=f"Export high flags: 0x{info['exportHigh']:04X}")
        layout.label(text=f"Unknown high bits: 0x{info['high'] & ~0x8F:04X}")
        layout.label(text=f"Unknown property bits: 0x{info['low'] & 0xFC00:04X}")
        for label, key in (('Edge UUID', 'id'), ('Joint UUID', 'jointId'),
                           ('Start vertex UUID', 'startId'), ('End vertex UUID', 'endId')):
            layout.label(text=label + ':')
            layout.label(text=info[key])
        for label, key in (('Start Blender XYZ', 'startPosition'), ('End Blender XYZ', 'endPosition')):
            layout.label(text=label + ':')
            layout.label(text=', '.join(f'{v:.4f}' for v in info[key]))
        if info['baseline']:
            record = info['baseline']['source']
            layout.separator()
            layout.label(text='Original source records (before edits):')
            layout.label(text=f"Flags: 0x{record['highFlags']:04X} / 0x{record['lowFlags']:04X}")
            layout.label(text=f"Vertex indices: {record['vertex0']} → {record['vertex1']}")
            for label, key in (('Previous', 'previous0'), ('Next', 'next0'),
                               ('Alternate previous', 'previous1'), ('Alternate next', 'next1')):
                layout.label(text=f'{label}: {record[key]}')
            layout.label(text='Source indices are zero-based; -1 = none.')
            layout.label(text='Export rebuilds links from edited topology.')


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
        batches = {}
        markers = []

        def directed_line(points):
            from mathutils import Vector
            a, b = points
            delta = b - a
            if delta.length < 0.00001:
                return points
            direction = delta.normalized()
            side = Vector((-direction.z, 0, direction.x))
            size = min(1.2, delta.length * 0.2)
            tip = a + delta * 0.65
            base = tip - direction * size
            return [a, b, tip, base + side * size * 0.5, tip, base - side * size * 0.5]

        if obj.mode == 'EDIT':
            bm = bmesh.from_edit_mesh(obj.data)
            layer = bm.edges.layers.int.get('mme_category')
            low = bm.edges.layers.int.get('mme_low')
            vertex = bm.verts.layers.int.get('mme_vertex')
            start = bm.edges.layers.int.get('mme_start')
            if layer is None or low is None or vertex is None or start is None:
                return
            for edge in bm.edges:
                if edge.hide:
                    continue
                category = edge[layer]
                if 0 <= category < len(collision.CATEGORIES):
                    points = [obj.matrix_world @ v.co for v in edge.verts]
                    if edge.verts[0][vertex] != edge[start]:
                        points.reverse()
                    color = collision.overlay_color(category, edge[low])
                    batches.setdefault(color, []).extend(directed_line(points))
                    if edge[low] & 0x200:
                        markers.append((points[0] + points[1]) * 0.5)
        else:
            attr = obj.data.attributes.get('mme_category')
            low = obj.data.attributes.get('mme_low')
            vertex = obj.data.attributes.get('mme_vertex')
            start = obj.data.attributes.get('mme_start')
            if attr is None or low is None or vertex is None or start is None:
                return
            for edge, value, flags in zip(obj.data.edges, attr.data, low.data):
                if 0 <= value.value < len(collision.CATEGORIES):
                    points = [obj.matrix_world @ obj.data.vertices[i].co for i in edge.vertices]
                    if vertex.data[edge.vertices[0]].value != start.data[edge.index].value:
                        points.reverse()
                    color = collision.overlay_color(value.value, flags.value)
                    batches.setdefault(color, []).extend(directed_line(points))
                    if flags.value & 0x200:
                        markers.append((points[0] + points[1]) * 0.5)
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        depth = gpu.state.depth_test_get()
        width = gpu.state.line_width_get()
        try:
            gpu.state.depth_test_set('NONE')
            gpu.state.line_width_set(2)
            shader.bind()
            for color, coords in batches.items():
                if coords:
                    shader.uniform_float('color', color)
                    batch_for_shader(shader, 'LINES', {'pos': coords}).draw(shader)
            # White crosses indicate ledge-grab flags; floor color shows drop-through.
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
        modeling.update_dirty(scene_arg, depsgraph)
        obj = scene.collision_object(scene_arg)
        # Small collision meshes make this cheap; model geometry is checked on export.
        dirty = collision.fingerprint(obj) != scene_arg.get('mme_collision_fingerprint')
        if bool(obj.get('mme_dirty')) != dirty:
            obj['mme_dirty'] = dirty
    except (StageError, ReferenceError):
        pass


CLASSES = (MME_Preferences, MME_OT_import, MME_OT_export, MME_OT_validate, MME_OT_groups,
           MME_OT_edit_collision, MME_OT_edit_model, MME_OT_model_material, MME_OT_assign, MME_OT_topology, MME_OT_open_export, MME_PT_stage, MME_PT_edge, MME_PT_edge_raw)
SCENE_PROPS = ('mme_session', 'mme_session_id', 'mme_status', 'mme_export_directory',
               'mme_collision_type', 'mme_collision_material', 'mme_collision_surface')


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
    bpy.types.Scene.mme_collision_material = IntProperty(name='Surface ID', min=0, max=255)
    bpy.types.Scene.mme_collision_surface = EnumProperty(name='Surface',
        description='Collision surface response (friction and contact effects)',
        items=materials.ITEMS, get=materials.get_surface, set=materials.set_surface)
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
