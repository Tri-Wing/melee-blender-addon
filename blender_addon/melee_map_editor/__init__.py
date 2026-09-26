bl_info = {
    'name': 'Melee Map Editor', 'author': 'Melee Map Editor contributors',
    'version': (0, 1, 0), 'blender': (4, 5, 0), 'location': 'View3D > Sidebar > Melee Map',
    'description': 'Import and edit Melee stages, including external model additions', 'category': 'Import-Export',
}

import uuid
import json
from pathlib import Path
import bmesh
import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty,
                       FloatVectorProperty, IntProperty, StringProperty)
from bpy_extras import view3d_utils
from bpy_extras.io_utils import ExportHelper, ImportHelper
from . import (animations, atmosphere, camera, collision, gameplay, inspector,
               jobjs, material_properties, materials, metadata,
               model_additions, modeling, scene, surface, topology)
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


class MME_OT_add_models(bpy.types.Operator):
    bl_idname = 'mme.add_models'
    bl_label = 'Add Selected Models to Stage'
    bl_options = {'REGISTER', 'UNDO'}

    target_jobj_id: EnumProperty(name='Placement Target', items=model_additions.target_items)

    def invoke(self, context, event):
        if not model_additions.target_items(self, context):
            self.report({'ERROR'}, 'This stage has no structurally eligible model-addition attachment.')
            return {'CANCELLED'}
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        def action():
            added = model_additions.register_selected(context, self.target_jobj_id)
            count = len({obj.parent.get('mme_addition_id') for obj in added if obj.parent})
            context.scene.mme_status = f'Converted and integrated {count} imported model(s).'
            self.report({'INFO'}, context.scene.mme_status)
        return execute_safely(self, context, action)


class MME_OT_normalize_collision(bpy.types.Operator):
    bl_idname = 'mme.normalize_collision_components'
    bl_label = 'Separate Disconnected Islands'
    bl_description = 'Rebuild collision component objects after native edge or vertex deletion'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        def action():
            if context.mode != 'OBJECT':
                raise StageError('Return to Object Mode before separating collision islands.')
            source = read(scene.session(context.scene) / 'collision/collision.json')
            converted = collision.ensure_component_representation(
                context.scene, source)
            changed = collision.normalize_components(context.scene, source)
            context.scene.mme_status = (
                f'Separated {changed} collision island(s).' if changed else
                ('Converted collision components.' if converted else
                 'Collision components are already normalized.'))
            self.report({'INFO'}, context.scene.mme_status)
        return execute_safely(self, context, action)


class MME_OT_edit_model(bpy.types.Operator):
    bl_idname = 'mme.edit_model'
    bl_label = 'Edit Selected Model'

    def execute(self, context):
        def action():
            active = context.active_object
            if active and active.get('mme_role') == model_additions.ROLE \
                    and active.get('mme_session_id') == context.scene.mme_session_id:
                if context.mode != 'OBJECT':
                    bpy.ops.object.mode_set(mode='OBJECT')
                bpy.ops.object.select_all(action='DESELECT')
                active.hide_set(False)
                active.select_set(True)
                context.view_layer.objects.active = active
                context.tool_settings.mesh_select_mode = (True, False, False)
                bpy.ops.object.mode_set(mode='EDIT')
                context.scene.mme_status = f'Editing {active.name}.'
                return
            infos = modeling.targets(context.scene)
            selected = next((info for info in infos if active and active.get('mme_id') == info['id']
                             and active.get('mme_session_id') == context.scene.mme_session_id), None)
            if active and active.get('mme_role') == 'pobj' and selected is None:
                raise StageError(active.get('mme_read_only_reason', 'This model is read-only in this session.'))
            info, obj = modeling.resolve(context.scene,
                                         info=selected or (infos[0] if infos else None),
                                         operation='vertexMovement')
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
            context.scene.mme_status = (f'Editing {obj.name}. Move vertices only; animated materials are preserved.'
                                        if not modeling.allows(info, 'topologyReplacement') else
                                        f'Editing {obj.name}. Vertex moves preserve appearance; new faces use the assigned material.')
        return execute_safely(self, context, action)


class MME_OT_edit_jobj(bpy.types.Operator):
    bl_idname = 'mme.edit_jobj'
    bl_label = 'Select Editable JOBJ'

    def execute(self, context):
        def action():
            infos = jobjs.targets(context.scene)
            info = jobjs.selected_info(context) or (infos[0] if infos else None)
            if info is None:
                raise StageError('This stage has no editable static JOBJs.')
            armature, bone = jobjs.target_bone(context.scene, info)
            if context.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
            bpy.ops.object.select_all(action='DESELECT')
            armature.hide_set(False)
            armature.select_set(True)
            context.view_layer.objects.active = armature
            bpy.ops.object.mode_set(mode='POSE')
            bpy.ops.pose.select_all(action='DESELECT')
            # Blender 5.2 moved pose selection from Bone to PoseBone.
            if hasattr(bone, 'select'):
                bone.select = True
            else:
                bone.bone.select = True
            armature.data.bones.active = bone.bone
            context.scene.mme_status = f'Selected {armature.name} / {bone.name}. Use Blender Move, Rotate, and Scale in Pose Mode.'
        return execute_safely(self, context, action)


class MME_OT_animation(bpy.types.Operator):
    bl_idname = 'mme.cycle_animation'
    bl_label = 'Cycle Stage Animation'
    bl_options = {'REGISTER'}
    direction: IntProperty(default=1)

    def execute(self, context):
        def action():
            armature = animations.cycle(context, self.direction)
            if armature is None:
                raise StageError('This stage has no imported stage animations.')
            current = animations.active_action(armature)
            context.scene.mme_status = f'Previewing {current.name}'
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
            if not obj or obj.get('mme_session_id') != context.scene.mme_session_id:
                raise StageError('Select an editable model first.')
            _, obj = modeling.resolve(context.scene, obj=obj,
                                      operation='materialAssignment')
            if self.material_id == 'GREY':
                material = bpy.data.materials.new('Melee Export Grey')
                material.diffuse_color = (0.45, 0.45, 0.45, 1)
            else:
                material = next((m for m in bpy.data.materials if surface.material_id(m) == self.material_id), None)
                if material is None:
                    raise StageError('Stage material missing. Re-import to restore the material catalog.')
            surface.assign(obj, material)
            surface.update_uv_editor(context)
            context.scene.mme_status = 'Material assigned to all faces. Use Blender UV tools for textured materials.'
        return execute_safely(self, context, action)


class MME_OT_assign(bpy.types.Operator):
    bl_idname = 'mme.assign_collision'
    bl_label = 'Assign Collision Property'
    bl_options = {'REGISTER', 'UNDO'}
    property: EnumProperty(items=[(x, x.title(), '') for x in ('type', 'material', 'drop', 'ledge')])

    def execute(self, context):
        def action():
            objects = scene.editing_collision_objects(context)
            if not read(scene.session(context.scene) / 'stage.json')['capabilities']['collisionEdit']:
                raise StageError('Collision is read-only for this stage.')
            selections = []
            for obj in objects:
                bm = bmesh.from_edit_mesh(obj.data)
                selected = [edge for edge in bm.edges
                            if edge.select and not edge.hide]
                if selected:
                    selections.append((obj, bm, selected))
            if not selections:
                raise StageError('Select collision edges first.')
            key = 'mme_category' if self.property == 'type' else 'mme_low'
            if self.property == 'type':
                collision.serialize_components(
                    scene.collision_objects(context.scene),
                    read(scene.session(context.scene) / 'collision/collision.json'),
                    context.scene)
            for obj, bm, selected in selections:
                layer = bm.edges.layers.int.get(key)
                if layer is None:
                    raise StageError('Collision attributes are missing. Undo the edit or re-import.')
                if self.property == 'type':
                    collision.assign_type(
                        bm, selected,
                        collision.CATEGORIES.index(context.scene.mme_collision_type))
                for edge in selected:
                    if self.property == 'material':
                        edge[layer] = ((edge[layer] & 0xFF00)
                                       | context.scene.mme_collision_material)
                    elif self.property in ('drop', 'ledge'):
                        edge[layer] ^= 0x100 if self.property == 'drop' else 0x200
                bmesh.update_edit_mesh(obj.data, loop_triangles=False,
                                       destructive=False)
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
            objects = scene.editing_collision_objects(context)
            directory = scene.session(context.scene)
            if not read(directory / 'stage.json')['capabilities']['collisionEdit']:
                raise StageError('Collision is read-only for this stage.')
            source = read(directory / 'collision/collision.json')
            all_objects = scene.collision_objects(context.scene)
            collision.serialize_components(all_objects, source, context.scene)
            category = collision.CATEGORIES.index(
                context.scene.mme_collision_type)
            if self.operation == 'connect':
                target = topology.connect_components(
                    context, objects, source, category,
                    context.scene.mme_collision_material)
                target['mme_dirty'] = True
            elif self.operation == 'extend':
                selected = [obj for obj in objects
                            if topology.selected_counts(obj)[0]]
                if len(selected) != 1:
                    raise StageError('Select exactly one open endpoint.')
                topology.edit(selected[0], source, self.operation,
                              self.offset_x, self.offset_z, category,
                              context.scene.mme_collision_material, self.joint,
                              all_objects)
                selected[0]['mme_dirty'] = True
            else:
                selected = [obj for obj in objects
                            if topology.selected_counts(obj)[1]]
                if not selected:
                    raise StageError('Select collision edges first.')
                for obj in selected:
                    topology.edit(obj, source, self.operation,
                                  self.offset_x, self.offset_z, category,
                                  context.scene.mme_collision_material,
                                  self.joint, all_objects)
                    obj['mme_dirty'] = True
            context.scene.mme_status = 'Collision topology updated. Validate before exporting.'
        return execute_safely(self, context, action)


class MME_OT_place_collision_vertex(bpy.types.Operator):
    bl_idname = 'mme.place_collision_vertex'
    bl_label = 'Place Collision Vertex'
    bl_description = 'Click in the viewport to place an isolated vertex on the collision plane'
    bl_options = {'REGISTER', 'UNDO'}

    def invoke(self, context, event):
        try:
            objects = scene.editing_collision_objects(context)
            obj = context.edit_object
            if obj not in objects:
                raise StageError('Make a collision component active first.')
            if context.area is None or context.area.type != 'VIEW_3D':
                raise StageError('Place collision vertices from a 3D View.')
            directory = scene.session(context.scene)
            if not read(directory / 'stage.json')['capabilities']['collisionEdit']:
                raise StageError('Collision is read-only for this stage.')
            self._window_region = next((region for region in context.area.regions
                                        if region.type == 'WINDOW'), None)
            if self._window_region is None or context.space_data.region_3d is None:
                raise StageError('The 3D viewport is unavailable for placement.')
        except (StageError, OSError, ValueError, KeyError, RuntimeError) as exc:
            context.scene.mme_status = str(exc)
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        context.window.cursor_modal_set('CROSSHAIR')
        context.window_manager.modal_handler_add(self)
        context.scene.mme_status = 'Click in the 3D viewport to place a collision vertex; Esc or right-click cancels.'
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type in {'ESC', 'RIGHTMOUSE'}:
            return self._finish(context, cancelled=True)
        if event.type in {'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE',
                          'WHEELINMOUSE', 'WHEELOUTMOUSE'}:
            return {'PASS_THROUGH'}
        if event.type != 'LEFTMOUSE' or event.value != 'PRESS':
            return {'RUNNING_MODAL'}
        region = self._window_region
        if not (region.x <= event.mouse_x < region.x + region.width
                and region.y <= event.mouse_y < region.y + region.height):
            context.scene.mme_status = 'Click inside the 3D viewport, or press Esc to cancel.'
            return {'RUNNING_MODAL'}
        try:
            objects = scene.editing_collision_objects(context)
            obj = context.edit_object
            if obj not in objects:
                raise StageError('Collision Edit Mode ended before placement.')
            coordinate = (event.mouse_x - region.x, event.mouse_y - region.y)
            origin = view3d_utils.region_2d_to_origin_3d(
                region, context.space_data.region_3d, coordinate)
            direction = view3d_utils.region_2d_to_vector_3d(
                region, context.space_data.region_3d, coordinate)
        except (StageError, OSError, ValueError, KeyError, RuntimeError) as exc:
            context.scene.mme_status = str(exc)
            self.report({'ERROR'}, str(exc))
            return self._finish(context, cancelled=True)
        try:
            local = topology.project_to_collision_plane(obj, origin, direction)
        except StageError as exc:
            context.scene.mme_status = f'{exc} Adjust the view and click again, or press Esc to cancel.'
            self.report({'WARNING'}, str(exc))
            return {'RUNNING_MODAL'}
        try:
            source = read(scene.session(context.scene) / 'collision/collision.json')
            collision.serialize_components(
                scene.collision_objects(context.scene), source, context.scene)
            topology.add_isolated_vertex(
                obj, source, local, scene.collision_objects(context.scene))
            context.tool_settings.mesh_select_mode = (True, False, False)
            obj['mme_dirty'] = True
            context.scene.mme_status = ('Placed an isolated collision vertex. Connect it to another '
                                        'vertex before export.')
            self.report({'INFO'}, context.scene.mme_status)
            return self._finish(context)
        except (StageError, OSError, ValueError, KeyError, RuntimeError) as exc:
            context.scene.mme_status = str(exc)
            self.report({'ERROR'}, str(exc))
            return self._finish(context, cancelled=True)

    def _finish(self, context, cancelled=False):
        context.window.cursor_modal_restore()
        if context.area:
            context.area.tag_redraw()
        return {'CANCELLED'} if cancelled else {'FINISHED'}


class MME_OT_place_item_spawn(bpy.types.Operator):
    bl_idname = 'mme.place_item_spawn'
    bl_label = 'Add Item Spawn'
    bl_description = 'Click in the viewport to add an item spawn on the gameplay plane'
    bl_options = {'REGISTER', 'UNDO'}

    def invoke(self, context, event):
        try:
            if context.mode != 'OBJECT':
                raise StageError('Switch to Object Mode before adding an item spawn.')
            if context.area is None or context.area.type != 'VIEW_3D':
                raise StageError('Add item spawns from a 3D View.')
            stage = read(scene.session(context.scene) / 'stage.json')
            self._set_index = gameplay.set_index(context.scene, stage,
                                                  context.active_object)
            self._type_id = gameplay.item_spawn_slot(context.scene,
                                                      self._set_index)
            if self._type_id is None:
                raise StageError('This general-point set already uses all 21 item-spawn slots.')
            source_set = next(entry for entry in stage['gameplay']['sets']
                              if entry['index'] == self._set_index)
            if not source_set.get('itemSpawnTopologyEditable'):
                raise StageError(source_set.get('itemSpawnTopologyReadOnlyReason')
                                 or 'Item-spawn topology editing is unavailable.')
            self._plane_y = gameplay.plane_y(context.scene, self._set_index)
            self._window_region = next((region for region in context.area.regions
                                        if region.type == 'WINDOW'), None)
            if self._window_region is None or context.space_data.region_3d is None:
                raise StageError('The 3D viewport is unavailable for placement.')
        except (StageError, OSError, ValueError, KeyError, RuntimeError,
                StopIteration) as exc:
            context.scene.mme_status = str(exc)
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        context.window.cursor_modal_set('CROSSHAIR')
        context.window_manager.modal_handler_add(self)
        context.scene.mme_status = ('Click in the 3D viewport to add item spawn '
                                    f'{self._type_id - gameplay.FIRST_ITEM_SPAWN + 1}; '
                                    'Esc or right-click cancels.')
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type in {'ESC', 'RIGHTMOUSE'}:
            return self._finish(context, cancelled=True)
        if event.type in {'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE',
                          'WHEELINMOUSE', 'WHEELOUTMOUSE'}:
            return {'PASS_THROUGH'}
        if event.type != 'LEFTMOUSE' or event.value != 'PRESS':
            return {'RUNNING_MODAL'}
        region = self._window_region
        if not (region.x <= event.mouse_x < region.x + region.width
                and region.y <= event.mouse_y < region.y + region.height):
            context.scene.mme_status = 'Click inside the 3D viewport, or press Esc to cancel.'
            return {'RUNNING_MODAL'}
        try:
            coordinate = (event.mouse_x - region.x, event.mouse_y - region.y)
            origin = view3d_utils.region_2d_to_origin_3d(
                region, context.space_data.region_3d, coordinate)
            direction = view3d_utils.region_2d_to_vector_3d(
                region, context.space_data.region_3d, coordinate)
            location = gameplay.project_to_plane(origin, direction, self._plane_y)
            stage = read(scene.session(context.scene) / 'stage.json')
            obj = gameplay.add_item_spawn(context.scene, stage, self._set_index,
                                           self._type_id, location)
            context.scene.mme_gameplay_set_index = self._set_index
            context.scene.mme_status = (f'Added {obj.name}. Validate before exporting.')
            self.report({'INFO'}, context.scene.mme_status)
            return self._finish(context)
        except (StageError, OSError, ValueError, KeyError, RuntimeError) as exc:
            context.scene.mme_status = str(exc)
            self.report({'ERROR'}, str(exc))
            return self._finish(context, cancelled=True)

    def _finish(self, context, cancelled=False):
        context.window.cursor_modal_restore()
        if context.area:
            context.area.tag_redraw()
        return {'CANCELLED'} if cancelled else {'FINISHED'}


class MME_OT_open_export(bpy.types.Operator):
    bl_idname = 'mme.open_export_directory'
    bl_label = 'Open Export Directory'

    def execute(self, context):
        if not context.scene.mme_export_directory:
            return {'CANCELLED'}
        bpy.ops.wm.path_open(filepath=context.scene.mme_export_directory)
        return {'FINISHED'}


def _stage_info(s):
    try:
        return json.loads(s.get('mme_stage_info', '{}'))
    except (TypeError, ValueError):
        return {}


def _addition_targets(s):
    try:
        return model_additions.stage_targets(read(scene.session(s) / 'stage.json'))
    except (StageError, OSError, ValueError, KeyError):
        return []


def _wrapped_labels(layout, text, width=38):
    import textwrap
    for line in textwrap.wrap(text or '', width=width):
        layout.label(text=line)


class MME_PT_sidebar:
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Melee Map'


class MME_PT_stage(MME_PT_sidebar, bpy.types.Panel):
    bl_label = 'Stage'
    bl_idname = 'MME_PT_stage'
    bl_order = 0

    def draw(self, context):
        layout = self.layout
        s = context.scene
        layout.operator('mme.import_stage', icon='IMPORT')
        if not s.mme_session:
            layout.label(text='Choose the CLI in add-on preferences.')
            return
        info = _stage_info(s)
        if info:
            layout.label(text=info.get('filename', 'Imported stage'), icon='SCENE_DATA')
            layout.label(text=f"{info.get('groups', 0)} model groups · {info.get('lines', 0)} collision lines")


class MME_PT_viewport(MME_PT_sidebar, bpy.types.Panel):
    bl_label = 'Viewport'
    bl_idname = 'MME_PT_viewport'
    bl_order = 1
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return bool(context.scene.mme_session)

    def draw(self, context):
        layout = self.layout
        layout.prop(context.scene, 'mme_dithered_transparency')
        layout.operator('mme.toggle_models')


class MME_PT_selection(MME_PT_sidebar, bpy.types.Panel):
    bl_label = 'Selected Stage Object'
    bl_idname = 'MME_PT_selection'
    bl_order = 2

    @classmethod
    def poll(cls, context):
        return bool(context.scene.mme_session and metadata.selected(context))

    def draw(self, context):
        layout = self.layout
        item = metadata.selected(context)
        if isinstance(item, bpy.types.PoseBone):
            layout.label(text=f'Name: {item.name}')
        else:
            layout.prop(item, 'name', text='Name')
        for key, value in metadata.describe(context.scene, item):
            if key in {'Reason', 'Stable ID'}:
                box = layout.box()
                box.label(text=key)
                _wrapped_labels(box, value)
            else:
                layout.label(text=f'{key}: {value}')


class MME_PT_models(MME_PT_sidebar, bpy.types.Panel):
    bl_label = 'Models'
    bl_idname = 'MME_PT_models'
    bl_order = 3

    @classmethod
    def poll(cls, context):
        return bool(context.scene.mme_session)

    def draw(self, context):
        layout = self.layout
        s = context.scene
        editable = modeling.targets(s)
        if not editable:
            layout.label(text='No editable rigid models', icon='LOCKED')
            return
        available = sum(bool(modeling.target_matches(s, info)) for info in editable)
        if not available:
            layout.label(text='No editable rigid models remain', icon='INFO')
            return
        ids = {info['id'] for info in editable}
        active = context.active_object
        selected = (active and active.get('mme_session_id') == s.mme_session_id
                    and active.get('mme_id') in ids)
        layout.label(text=f'{available} editable rigid models')
        row = layout.row()
        row.enabled = bool(selected) or not (active and active.get('mme_role') == 'pobj')
        row.operator('mme.edit_model', icon='EDITMODE_HLT')
        can_assign_material = False
        if selected:
            target = next(info for info in editable if info['id'] == active.get('mme_id'))
            can_assign_material = modeling.allows(target, 'materialAssignment')
            state = 'edited' if active.get('mme_dirty') else 'unchanged'
            layout.label(text=f'Selected: {state}')
            if not modeling.allows(target, 'topologyReplacement'):
                _wrapped_labels(layout, modeling.capability(target, 'topologyReplacement').get(
                    'reason', 'Topology replacement is unavailable.'))
        elif active and active.get('mme_role') == 'pobj':
            layout.label(text='Selected model is read-only', icon='LOCKED')
            _wrapped_labels(layout, active.get('mme_read_only_reason', 'Read-only in this session.'))
        row = layout.row()
        row.enabled = bool(selected) and can_assign_material
        row.operator('mme.model_material', icon='MATERIAL')
        if not s.get('mme_model_materials'):
            layout.label(text='Re-import to load stage materials', icon='INFO')


class MME_PT_import_models(MME_PT_sidebar, bpy.types.Panel):
    bl_label = 'Import Models'
    bl_idname = 'MME_PT_import_models'
    bl_order = 4
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return bool(context.scene.mme_session)

    def draw(self, context):
        layout = self.layout
        s = context.scene
        if not _addition_targets(s):
            layout.label(text='Unavailable for this stage', icon='LOCKED')
            return
        pending = model_additions.objects(s)
        addition_ids = {obj.parent.get('mme_addition_id') for obj in pending if obj.parent}
        addition_ids.discard(None)
        if addition_ids:
            layout.label(text=f'{len(addition_ids)} added model(s) · {len(pending)} mesh part(s)')
        else:
            layout.label(text='No added models')
        if context.active_object in pending:
            layout.operator('mme.edit_model', text='Edit Selected Model', icon='EDITMODE_HLT')
        layout.operator('mme.add_models', icon='ADD')


class MME_PT_import_report(MME_PT_sidebar, bpy.types.Panel):
    bl_label = 'Conversion Report'
    bl_idname = 'MME_PT_import_report'
    bl_parent_id = 'MME_PT_import_models'
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return bool(context.scene.mme_session and model_additions.objects(context.scene)
                    and context.scene.get('mme_addition_report'))

    def draw(self, context):
        layout = self.layout
        try:
            report = json.loads(context.scene['mme_addition_report'])
        except (TypeError, ValueError, KeyError):
            layout.label(text='Conversion report is unavailable', icon='ERROR')
            return
        layout.label(text=f"{report.get('triangles', 0)} triangles · {report.get('chunks', 0)} GX chunks")
        layout.label(text=f"{report.get('parts', 0)} parts · {report.get('images', 0)} images")
        layout.label(text=f"{report.get('textureBytes', 0)} RGBA texture bytes")
        warnings = report.get('warnings', [])
        if warnings:
            box = layout.box()
            box.label(text=f'{len(warnings)} conversion warning(s)', icon='INFO')
            for warning in warnings:
                _wrapped_labels(box, warning)


class MME_PT_jobj_animation(MME_PT_sidebar, bpy.types.Panel):
    bl_label = 'JOBJ & Animation'
    bl_idname = 'MME_PT_jobj_animation'
    bl_order = 5
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return bool(context.scene.mme_session)

    def draw(self, context):
        layout = self.layout
        s = context.scene
        editable_jobjs = jobjs.targets(s)
        if editable_jobjs:
            layout.label(text=f'{len(editable_jobjs)} editable static JOBJs')
            layout.operator('mme.edit_jobj', icon='EMPTY_ARROWS')
            active_bone = context.active_pose_bone
            if active_bone and active_bone.get('mme_id') in {info['id'] for info in editable_jobjs}:
                state = 'edited' if active_bone.get('mme_dirty') else 'unchanged'
                layout.label(text=f'Selected JOBJ: {state}')
            elif active_bone and active_bone.get('mme_role'):
                layout.label(text='Selected JOBJ is read-only', icon='LOCKED')
        elif 'mme_editable_jobjs' in s:
            layout.label(text='No editable static JOBJs', icon='LOCKED')
        animated_armatures = [obj for obj in s.objects if obj.type == 'ARMATURE'
                              and animations.actions(obj)]
        if not animated_armatures:
            return
        if editable_jobjs or 'mme_editable_jobjs' in s:
            layout.separator()
        actions = [action for obj in animated_armatures for action in animations.actions(obj)]
        layout.label(text=f'{len(actions)} stage animation actions')
        active_armature = (context.active_object if context.active_object in animated_armatures
                           else animated_armatures[0])
        active_action = animations.active_action(active_armature)
        if active_action:
            layout.label(text=active_action.name, icon='ACTION')
        row = layout.row(align=True)
        row.operator('mme.cycle_animation', text='Previous').direction = -1
        row.operator('mme.cycle_animation', text='Next').direction = 1
        editable_actions = sum(bool(json.loads(action.get('mme_fcurve_jobj_ids', '[]')))
                               for action in actions)
        if editable_actions:
            layout.label(text=f'{editable_actions} actions have editable JOBJ curves')


class MME_PT_gameplay(MME_PT_sidebar, bpy.types.Panel):
    bl_label = 'Gameplay Tools'
    bl_idname = 'MME_PT_gameplay'
    bl_order = 6
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return bool(context.scene.mme_session)

    def draw(self, context):
        layout = self.layout
        s = context.scene
        try:
            stage = read(scene.session(s) / 'stage.json')
            sets = (stage.get('gameplay') or {}).get('sets', [])
            if not sets:
                layout.label(text='No general-point sets', icon='LOCKED')
                return
            indexes = {entry['index'] for entry in sets}
            if len(sets) > 1:
                layout.prop(s, 'mme_gameplay_set_index', text='Target Set')
            try:
                index = gameplay.set_index(s, stage, context.active_object)
            except StageError:
                index = sets[0]['index']
            source_set = next(entry for entry in sets if entry['index'] == index)
            count = len(gameplay.item_objects(s, index))
            layout.label(text=f'Item Spawns · Set {index}: {count} / 21')
            row = layout.row()
            row.enabled = bool(source_set.get('itemSpawnTopologyEditable')) \
                and count < 21 and context.mode == 'OBJECT'
            row.operator('mme.place_item_spawn', icon='ADD')
            if context.mode != 'OBJECT':
                layout.label(text='Switch to Object Mode to edit item spawns', icon='INFO')
            elif not source_set.get('itemSpawnTopologyEditable'):
                _wrapped_labels(layout,
                    source_set.get('itemSpawnTopologyReadOnlyReason')
                    or 'Item-spawn topology editing is unavailable.')
            elif count >= 21:
                layout.label(text='All item-spawn slots are in use', icon='INFO')
        except (StageError, OSError, ValueError, KeyError, StopIteration) as exc:
            _wrapped_labels(layout, str(exc))


class MME_PT_collision(MME_PT_sidebar, bpy.types.Panel):
    bl_label = 'Collision'
    bl_idname = 'MME_PT_collision'
    bl_order = 7
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return bool(context.scene.mme_session)

    def draw(self, context):
        layout = self.layout
        s = context.scene
        editable = _stage_info(s).get('editable', False)
        try:
            objects = scene.collision_objects(s)
            if not objects:
                raise StageError('All collision components were deleted.')
            layout.label(text='Edited' if any(obj.get('mme_dirty') for obj in objects)
                         else 'Unchanged')
        except StageError:
            layout.label(text='Collision components missing', icon='ERROR')
            return
        editing = any(obj.mode == 'EDIT' for obj in objects)
        if not editable:
            layout.label(text='Read-only for this stage', icon='LOCKED')
            reason = _stage_info(s).get('collisionReadOnlyReason')
            if reason:
                _wrapped_labels(layout, reason)
            return
        if not editing:
            layout.label(text='Select components and press Tab to edit.', icon='INFO')
            layout.operator('mme.normalize_collision_components',
                            icon='MESH_DATA')
        if _stage_info(s).get('movingCollisionPreview'):
            layout.label(text='Attached geometry edits in JOBJ-local space', icon='INFO')
        tools = layout.column()
        tools.enabled = editing
        tools.operator('mme.place_collision_vertex', icon='ADD')
        for left, right in ((('split', 'Split Edge'), ('extend', 'Extend Collision')),
                            (('connect', 'Connect Vertices'), ('reverse', 'Reverse Direction'))):
            row = tools.row(align=True)
            for action, label in (left, right):
                row.operator('mme.collision_topology', text=label).operation = action
        row = tools.row(align=True)
        row.prop(s, 'mme_collision_type', text='')
        row.operator('mme.assign_collision', text='Assign Type').property = 'type'
        row = tools.row(align=True)
        materials.draw_surface(row, s)
        row.operator('mme.assign_collision', text='Assign').property = 'material'
        row = tools.row(align=True)
        row.operator('mme.assign_collision', text='Toggle Drop-through').property = 'drop'
        row.operator('mme.assign_collision', text='Toggle Ledge-grab').property = 'ledge'


class MME_PT_collision_legend(MME_PT_sidebar, bpy.types.Panel):
    bl_label = 'Display Legend'
    bl_idname = 'MME_PT_collision_legend'
    bl_parent_id = 'MME_PT_collision'
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return bool(context.scene.mme_session)

    def draw(self, context):
        layout = self.layout
        layout.label(text='Solid floor: dark green')
        layout.label(text='Drop-through floor: bright green')
        layout.label(text='Ceiling: red')
        layout.label(text='Right wall: blue · Left wall: amber')
        layout.label(text='Dynamic range: purple')
        layout.label(text='White mark: ledge-grab flag')
        layout.label(text='Arrows show edge direction')


class MME_PT_export(MME_PT_sidebar, bpy.types.Panel):
    bl_label = 'Validate & Export'
    bl_idname = 'MME_PT_export'
    bl_order = 8

    @classmethod
    def poll(cls, context):
        return bool(context.scene.mme_session)

    def draw(self, context):
        layout = self.layout
        s = context.scene
        layout.operator('mme.validate_stage', icon='CHECKMARK')
        layout.operator('mme.export_stage', icon='EXPORT')
        if s.mme_export_directory:
            layout.operator('mme.open_export_directory', icon='FILE_FOLDER')
        box = layout.box()
        _wrapped_labels(box, s.mme_status)


class MME_PT_diagnostics(MME_PT_sidebar, bpy.types.Panel):
    bl_label = 'Diagnostics'
    bl_idname = 'MME_PT_diagnostics'
    bl_order = 9
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return bool(context.scene.mme_session)

    def draw(self, context):
        layout = self.layout
        s = context.scene
        info = _stage_info(s)
        layout.label(text='Source hash checked on validation/export')
        if info.get('deferred'):
            layout.label(text=f"{info['deferred']} unsupported meshes omitted", icon='ERROR')
        else:
            layout.label(text='No unsupported meshes omitted')
        if not info.get('editable', False):
            layout.label(text='Collision editing unavailable', icon='LOCKED')
        if info.get('movingCollisionPreview'):
            layout.label(text='Serialized moving collision preview enabled')
        unresolved = info.get('unresolvedCollisionBindings', 0)
        if unresolved:
            layout.label(text=f'{unresolved} collision binding(s) unresolved',
                         icon='INFO')
        layout.label(text=f'{len(_addition_targets(s))} model placement target(s)')


class MME_PT_edge(MME_PT_sidebar, bpy.types.Panel):
    bl_label = 'Selected Edge Metadata'
    bl_idname = 'MME_PT_edge'
    bl_parent_id = 'MME_PT_collision'
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return bool(context.scene.mme_session)

    @staticmethod
    def details(context):
        obj = context.edit_object
        if obj not in scene.collision_objects(context.scene):
            raise StageError('Enter Edit Mode on a collision component and select an edge.')
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


class MME_PT_edge_raw(MME_PT_sidebar, bpy.types.Panel):
    bl_label = 'Raw Flags and Source Metadata'
    bl_idname = 'MME_PT_edge_raw'
    bl_parent_id = 'MME_PT_edge'
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
_gameplay_draw_handle = None


def draw_collision():
    # Query the current scene every draw, so undo and loading .blend files are safe.
    import gpu
    from gpu_extras.batch import batch_for_shader
    context = bpy.context
    if not context.scene.mme_session or not context.space_data.overlay.show_overlays:
        return
    try:
        batches = {}
        markers = []
        selected_lines = []

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

        def selection_cross(obj, coordinate):
            from mathutils import Vector
            point = obj.matrix_world @ coordinate
            axes = [obj.matrix_world.to_3x3() @ Vector((0.8, 0, 0)),
                    obj.matrix_world.to_3x3() @ Vector((0, 0, 0.8))]
            for axis in axes:
                selected_lines.extend((point - axis, point + axis))

        for obj in scene.collision_objects(context.scene):
            if not obj.visible_get() or obj.hide_get():
                continue
            if obj.mode == 'EDIT':
                bm = bmesh.from_edit_mesh(obj.data)
                layer = bm.edges.layers.int.get('mme_category')
                low = bm.edges.layers.int.get('mme_low')
                vertex = bm.verts.layers.int.get('mme_vertex')
                start = bm.edges.layers.int.get('mme_start')
                if layer is None or low is None or vertex is None or start is None:
                    continue
                for item in bm.verts:
                    if item.select and not item.hide and not item.link_edges:
                        selection_cross(obj, item.co)
                for edge in bm.edges:
                    if edge.hide:
                        continue
                    category = edge[layer]
                    if 0 <= category < len(collision.CATEGORIES):
                        points = [obj.matrix_world @ value.co
                                  for value in edge.verts]
                        if edge.verts[0][vertex] != edge[start]:
                            points.reverse()
                        color = collision.overlay_color(category, edge[low])
                        directed = directed_line(points)
                        batches.setdefault(color, []).extend(directed)
                        if edge.select:
                            selected_lines.extend(directed)
                        if edge[low] & 0x200:
                            markers.append((points[0] + points[1]) * 0.5)
            else:
                attr = obj.data.attributes.get('mme_category')
                low = obj.data.attributes.get('mme_low')
                vertex = obj.data.attributes.get('mme_vertex')
                start = obj.data.attributes.get('mme_start')
                if attr is None or low is None or vertex is None or start is None:
                    continue
                if obj.select_get() and len(obj.data.edges) == 0:
                    for item in obj.data.vertices:
                        selection_cross(obj, item.co)
                for edge, value, flags in zip(obj.data.edges, attr.data, low.data):
                    if 0 <= value.value < len(collision.CATEGORIES):
                        points = collision.display_edge_points(
                            context.scene, obj, edge)
                        color = collision.overlay_color(value.value, flags.value)
                        directed = directed_line(points)
                        batches.setdefault(color, []).extend(directed)
                        if obj.select_get():
                            selected_lines.extend(directed)
                        if flags.value & 0x200:
                            markers.append((points[0] + points[1]) * 0.5)
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        depth = gpu.state.depth_test_get()
        width = gpu.state.line_width_get()
        try:
            gpu.state.depth_test_set('NONE')
            shader.bind()
            # The category overlay otherwise covers Blender's native orange
            # selection wire. Draw a wider orange line first so the category
            # color remains readable inside a clear selection halo.
            if selected_lines:
                gpu.state.line_width_set(6)
                shader.uniform_float('color', collision.SELECTION_COLOR)
                batch_for_shader(shader, 'LINES',
                                 {'pos': selected_lines}).draw(shader)
            gpu.state.line_width_set(2)
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
        animations.apply(scene_arg)
        collision.update_component_transforms(scene_arg)
        modeling.update_dirty(scene_arg, depsgraph)
        jobjs.update_dirty(scene_arg, depsgraph)
        objects = scene.collision_objects(scene_arg)
        # Small collision meshes make this cheap; model geometry is checked on export.
        dirty = collision.fingerprint_components(objects, scene_arg) != scene_arg.get(
            'mme_collision_fingerprint')
        for obj in objects:
            if bool(obj.get('mme_dirty')) != dirty:
                obj['mme_dirty'] = dirty
    except (StageError, ReferenceError):
        pass


@bpy.app.handlers.persistent
def update_animation(scene_arg, depsgraph=None):
    try:
        animations.apply(scene_arg)
        collision.update_component_transforms(scene_arg)
    except (StageError, ReferenceError, FileNotFoundError, ValueError, KeyError):
        pass


@bpy.app.handlers.persistent
def load_animation(_):
    animations.clear_cache()
    try:
        animations.apply(bpy.context.scene)
        collision.update_component_transforms(bpy.context.scene)
    except (StageError, ReferenceError, FileNotFoundError, ValueError, KeyError):
        pass


class MME_TextureLayerProperties(bpy.types.PropertyGroup):
    def update_blend(self, context):
        material_properties.update(self.id_data, context)

    blend: FloatProperty(name='Blend', min=0, max=1, default=1,
                         update=update_blend, options=set())


class MME_PT_material(bpy.types.Panel):
    bl_label = 'Melee Material'
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = 'material'

    @classmethod
    def poll(cls, context):
        material = context.material
        return material is not None and material.get('mme_model_material_source') is not None

    def draw(self, context):
        material = context.material
        info = material_properties.definition(material)
        layout = self.layout
        if not info:
            layout.label(text=material.get('mme_material_read_only_reason',
                         'This material uses unsupported source data.'), icon='LOCKED')
            return
        if info['canToggleVertexColor']:
            layout.prop(material, 'mme_use_vertex_color')
        layout.prop(material, 'mme_alpha_source')
        alpha_source = material_properties.ALPHA_SOURCES[material.mme_alpha_source]
        vertex_alpha = material.mme_use_vertex_color if alpha_source == 0 else alpha_source in (2, 3)
        if material.mme_use_vertex_color or vertex_alpha:
            if material.mme_use_vertex_color and vertex_alpha:
                layout.label(text='This material uses vertex colors and vertex alpha.')
            elif material.mme_use_vertex_color:
                layout.label(text='This material uses vertex colors.')
            else:
                layout.label(text='This material uses vertex alpha.')
            layout.label(text='Edit Stage Color 0 with Blender Vertex Paint.')
        layout.prop(material, 'mme_transparency')
        if info['canEditDiffuse'] or not material.mme_use_vertex_color:
            layout.prop(material, 'mme_diffuse')
        if material.mme_diffuse_lighting:
            layout.prop(material, 'mme_ambient')
        if material.mme_specular_lighting:
            layout.prop(material, 'mme_specular')
            layout.prop(material, 'mme_shininess')
        uses_material_alpha = alpha_source in (1, 3) or (alpha_source == 0 and not material.mme_use_vertex_color)
        if info['canEditAlpha'] or uses_material_alpha:
            layout.prop(material, 'mme_alpha')
        texture_definitions = info.get('textures') or []
        if len(texture_definitions) == 1 and info['canEditBlend']:
            layout.prop(material, 'mme_texture_blend')
        elif len(texture_definitions) > 1:
            roles = ((0x10, 'Diffuse'), (0x20, 'Specular'), (0x40, 'Ambient'),
                     (0x80, 'Extension'), (0x100, 'Shadow'))
            operations = ('None', 'Alpha Mask', 'RGB Mask', 'Blend', 'Modulate',
                          'Replace', 'Pass', 'Add', 'Subtract')
            box = layout.box()
            box.label(text='Texture Layers')
            for index, (source, layer) in enumerate(zip(texture_definitions, material.mme_texture_layers)):
                role = ' + '.join(label for bit, label in roles if source.get('lightmapFlags', 0) & bit) or 'Unassigned'
                coordinate = ('Reflection' if source.get('coordinateType') == 1
                              else f"UV {source.get('texCoord', 0)}")
                operation = source.get('colorOperation', 0)
                name = operations[operation] if 0 <= operation < len(operations) else f'Operation {operation}'
                column = box.column(align=True)
                column.label(text=f'Layer {index + 1}: {role} · {coordinate} · {name}')
                row = column.row()
                row.enabled = source.get('canEditBlend', False)
                row.prop(layer, 'blend')
        if info.get('editableRenderFlagsMask'):
            box = layout.box()
            box.label(text='Render Flags')
            box.prop(material, 'mme_diffuse_lighting')
            box.prop(material, 'mme_specular_lighting')
            box.prop(material, 'mme_toon_shading')
            box.prop(material, 'mme_depth_offset')
            box.prop(material, 'mme_depth_always')
            box.prop(material, 'mme_no_depth_write')
            box.prop(material, 'mme_shadow')
            box.prop(material, 'mme_all_textures')
            box.prop(material, 'mme_effect')
            box.prop(material, 'mme_user_render_flag')
        layout.label(text='Exports to models using this Blender material.')
        if material.get('mme_material_error'):
            layout.label(text=material['mme_material_error'], icon='ERROR')


CLASSES = (MME_Preferences, MME_OT_import, MME_OT_export, MME_OT_validate, MME_OT_groups,
           MME_OT_add_models,
           MME_OT_normalize_collision,
           MME_OT_edit_model, MME_OT_edit_jobj, MME_OT_animation,
           MME_OT_model_material,
           MME_OT_assign, MME_OT_topology, MME_OT_place_collision_vertex,
           MME_OT_place_item_spawn, MME_OT_open_export,
           MME_PT_stage, MME_PT_viewport, MME_PT_selection, MME_PT_models, MME_PT_import_models,
           MME_PT_import_report, MME_PT_jobj_animation, MME_PT_gameplay,
           MME_PT_collision,
           MME_PT_collision_legend, MME_PT_edge, MME_PT_edge_raw, MME_PT_export,
           MME_PT_diagnostics, MME_TextureLayerProperties, MME_PT_material)
SCENE_PROPS = ('mme_session', 'mme_session_id', 'mme_status', 'mme_export_directory',
               'mme_collision_type', 'mme_collision_material', 'mme_collision_surface',
               'mme_dithered_transparency', 'mme_gameplay_set_index')


def register():
    global _draw_handle, _gameplay_draw_handle
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Material.mme_diffuse = FloatVectorProperty(name='Diffuse Color', subtype='COLOR', size=3, min=0, max=1,
        default=(1, 1, 1), update=material_properties.update, options=set())
    bpy.types.Material.mme_ambient = FloatVectorProperty(name='Ambient Color', subtype='COLOR', size=3, min=0, max=1,
        default=(0, 0, 0), update=material_properties.update, options=set())
    bpy.types.Material.mme_specular = FloatVectorProperty(name='Specular Color', subtype='COLOR', size=3, min=0, max=1,
        default=(1, 1, 1), update=material_properties.update, options=set())
    bpy.types.Material.mme_shininess = FloatProperty(name='Shininess', min=0, max=128, default=50,
        update=material_properties.update, options=set())
    bpy.types.Material.mme_alpha = FloatProperty(name='Material Alpha', min=0, max=1, default=1,
        update=material_properties.update, options=set())
    bpy.types.Material.mme_texture_blend = FloatProperty(name='Texture Blend', min=0, max=1, default=1,
        update=material_properties.update, options=set())
    bpy.types.Material.mme_texture_layers = CollectionProperty(type=MME_TextureLayerProperties)
    bpy.types.Material.mme_use_vertex_color = BoolProperty(name='Use Vertex Colors', default=False,
        update=material_properties.update, options=set())
    bpy.types.Material.mme_transparency = EnumProperty(name='Transparency', items=(
        ('OPAQUE', 'Opaque', ''), ('ALPHA', 'Alpha Blend', ''),
        ('ADDITIVE', 'Additive', ''), ('SUBTRACT', 'Subtractive', ''),
        ('CUSTOM', 'Custom (Preserve)', 'Preserve an uncommon source blend configuration')),
        update=material_properties.update, options=set())
    bpy.types.Material.mme_alpha_source = EnumProperty(name='Alpha Source', items=(
        ('COMPATIBILITY', 'Compatibility', 'Follow the material color source'),
        ('MATERIAL', 'Material Alpha', ''), ('VERTEX', 'Vertex Alpha', ''),
        ('MULTIPLY', 'Material × Vertex', 'Multiply material alpha by vertex alpha')),
        update=material_properties.update, options=set())
    flag_properties = (
        ('mme_diffuse_lighting', 'Diffuse Lighting'), ('mme_specular_lighting', 'Specular Highlight'),
        ('mme_toon_shading', 'Toon Shading'), ('mme_depth_offset', 'Depth Offset'),
        ('mme_effect', 'Effect'), ('mme_shadow', 'Shadow'), ('mme_depth_always', 'Depth Test Always'),
        ('mme_all_textures', 'Enable All Textures'), ('mme_no_depth_write', 'Disable Depth Write'),
        ('mme_user_render_flag', 'User Flag'))
    for name, label in flag_properties:
        setattr(bpy.types.Material, name, BoolProperty(name=label, update=material_properties.update, options=set()))
    bpy.types.Scene.mme_session = StringProperty(subtype='DIR_PATH')
    bpy.types.Scene.mme_dithered_transparency = BoolProperty(
        name='Dithered Transparency', default=False, options=set(),
        description='Use faster, potentially noisier transparency for stage previews only; does not affect DAT export',
        update=surface.update_preview_dithering)
    bpy.types.Scene.mme_session_id = StringProperty()
    bpy.types.Scene.mme_status = StringProperty(default='No stage imported')
    bpy.types.Scene.mme_export_directory = StringProperty(subtype='DIR_PATH')
    bpy.types.Scene.mme_collision_type = EnumProperty(name='Type', items=[
        (x, x.replace('-', ' ').title(), '') for x in collision.CATEGORIES[:-1]])
    bpy.types.Scene.mme_collision_material = IntProperty(name='Surface ID', min=0, max=255)
    bpy.types.Scene.mme_gameplay_set_index = IntProperty(
        name='General-point set', min=0, max=255, default=0,
        description='Target general-point set when no gameplay guide is selected')
    bpy.types.Scene.mme_collision_surface = EnumProperty(name='Surface',
        description='Collision surface response (friction and contact effects)',
        items=materials.ITEMS, get=materials.get_surface, set=materials.set_surface)
    bpy.app.handlers.depsgraph_update_post.append(update_dirty)
    bpy.app.handlers.frame_change_post.append(update_animation)
    bpy.app.handlers.load_post.append(load_animation)
    if not bpy.app.background:
        _draw_handle = bpy.types.SpaceView3D.draw_handler_add(draw_collision, (), 'WINDOW', 'POST_VIEW')
        _gameplay_draw_handle = bpy.types.SpaceView3D.draw_handler_add(
            gameplay.draw, (), 'WINDOW', 'POST_VIEW')


def unregister():
    global _draw_handle, _gameplay_draw_handle
    if _draw_handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_draw_handle, 'WINDOW')
        _draw_handle = None
    if _gameplay_draw_handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_gameplay_draw_handle, 'WINDOW')
        _gameplay_draw_handle = None
    if update_dirty in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(update_dirty)
    if update_animation in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.remove(update_animation)
    if load_animation in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(load_animation)
    animations.clear_cache()
    for prop in ('mme_diffuse', 'mme_ambient', 'mme_specular', 'mme_shininess', 'mme_alpha',
                 'mme_texture_blend', 'mme_texture_layers', 'mme_use_vertex_color', 'mme_transparency', 'mme_alpha_source',
                 *(name for name, _ in material_properties.RENDER_FLAGS)):
        delattr(bpy.types.Material, prop)
    for prop in SCENE_PROPS:
        delattr(bpy.types.Scene, prop)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
