"""Preview-only World background created from the active stage fog color."""
import bpy


def linear_color(value):
    return tuple(component / 12.92 if component <= .04045
                 else ((component + .055) / 1.055) ** 2.4 for component in value[:3])


def create(stage, scene, created_worlds):
    source = stage.get('atmosphere') or {}
    color = linear_color(source.get('backgroundColor', [0, 0, 0, 1]))
    world = bpy.data.worlds.new('Melee Atmosphere')
    created_worlds.append(world)
    world.use_nodes = True
    world.color = color
    world['mme_role'] = 'preview-atmosphere'
    world['mme_preview_only'] = True
    world['mme_fog_id'] = source.get('previewFogId') or ''
    if source.get('warning'):
        world['mme_fog_warning'] = source['warning']
    background = world.node_tree.nodes.get('Background')
    background.inputs['Color'].default_value = (*color, 1)
    background.inputs['Strength'].default_value = 1
    scene.world = world

    # Material Preview otherwise keeps Blender's studio environment. Imported
    # stages should show their own clear color immediately in open 3D views.
    screen = bpy.context.screen
    if screen:
        for area in screen.areas:
            if area.type != 'VIEW_3D':
                continue
            shading = area.spaces.active.shading
            if hasattr(shading, 'use_scene_world'):
                shading.use_scene_world = True
            if hasattr(shading, 'use_scene_world_render'):
                shading.use_scene_world_render = True
    return world
