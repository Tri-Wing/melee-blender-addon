"""Blender preview controls for HSD LOBJ light sets."""
import json
import math
import bpy
from mathutils import Vector


def preview_set(stage):
    lighting = stage.get('lighting') or {}
    key = lighting.get('previewSetId')
    return next((entry for entry in lighting.get('lightSets', []) if entry['id'] == key), None)


def preview_lights(stage):
    entry = preview_set(stage)
    return entry.get('lights', []) if entry else []


def game_vector(value):
    return Vector((value['x'], -value['z'], value['y']))


def linear_color(value):
    return tuple(c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4
                 for c in value[:3])


def object_map(stage, source_hash=None):
    """Find the objects that control the selected light set after a node rebuild."""
    selected = preview_set(stage)
    if not selected:
        return {}
    ids = {source['id'] for source in selected.get('lights', [])}
    return {obj.get('mme_id'): obj for obj in bpy.data.objects
            if obj.get('mme_role') == 'light' and obj.get('mme_id') in ids
            and (source_hash is None or obj.get('mme_source_hash') == source_hash)}


def create(parent, stage, tag, created_objects, created_data, collection):
    """Import every static LOBJ descriptor and return the preview controls."""
    settings = stage.get('lighting') or {}
    selected = settings.get('previewSetId')
    preview_objects = {}
    for light_set in settings.get('lightSets', []):
        group_index = light_set.get('groupIndex')
        group_index = group_index if group_index is not None else -1
        child = collection(
            ('Preview ' if light_set['id'] == selected else '') + f"Light Set - {light_set['id']}",
            parent, f"lights-{light_set['id']}", 'light-set', group_index)
        child.hide_render = True
        child.hide_viewport = light_set['id'] != selected
        for index, source in enumerate(light_set.get('lights', [])):
            kind = source['type']
            data = None
            if kind != 'ambient':
                blender_kind = {'infinite': 'SUN', 'point': 'POINT', 'spot': 'SPOT'}[kind]
                data = bpy.data.lights.new(f"{kind.title()} LOBJ {index:03d}", blender_kind)
                created_data.append(data)
                data.color = linear_color(source['color'])
                data.energy = 1
                if kind == 'spot':
                    position = game_vector(source['position'])
                    interest = game_vector(source['interest'])
                    cutoff = source.get('parameters', {}).get('cutoff') or 45
                    data.spot_size = math.radians(max(1, min(179, cutoff * 2)))
            obj = bpy.data.objects.new(
                f"{kind.title()} LOBJ {index:03d} ({light_set['id']})", data)
            created_objects.append(obj)
            child.objects.link(obj)
            tag(obj, 'light', source['id'], group_index)
            obj['mme_light_set'] = light_set['id']
            obj['mme_light_type'] = kind
            obj['mme_light_flags'] = source['flags']
            obj['mme_light_diffuse'] = source['diffuse']
            obj['mme_light_specular'] = source['specular']
            obj['mme_light_animated'] = source['animated']
            obj['mme_light_color'] = json.dumps(source['color'])
            obj['mme_light_attenuation'] = json.dumps(source['attenuation'], sort_keys=True)
            obj['mme_light_parameters'] = json.dumps(source.get('parameters', {}), sort_keys=True)
            obj['mme_light_enabled'] = not source.get('hidden', False)
            obj.id_properties_ui('mme_light_enabled').update(
                description='Enable this light in the Blender Melee preview; not exported')
            obj.hide_render = True
            if kind == 'ambient':
                obj['mme_light_intensity'] = 1.0
                obj.id_properties_ui('mme_light_intensity').update(
                    description='Ambient preview intensity; not exported', min=0, soft_max=4)
                obj.empty_display_type = 'SPHERE'
                obj.empty_display_size = 3
                obj.color = (*linear_color(source['color']), 1)
            else:
                position = game_vector(source['position'])
                if kind == 'infinite':
                    direction = position.normalized() if position.length else Vector((0, 0, 1))
                    obj.location = direction * 20
                    # An infinite LOBJ vector follows the rays. Blender Suns use
                    # local -Z for that same direction; the shader uses local +Z
                    # as the vector from the surface back toward the light.
                    obj.rotation_euler = direction.to_track_quat('-Z', 'Y').to_euler()
                else:
                    obj.location = position
                    if kind == 'spot':
                        direction = game_vector(source['interest']) - position
                        if direction.length:
                            obj.rotation_euler = direction.to_track_quat('-Z', 'Y').to_euler()
            if light_set['id'] == selected:
                preview_objects[source['id']] = obj
    return preview_objects
