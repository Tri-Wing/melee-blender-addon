"""Blender preview controls for HSD LOBJ light sets."""
import json
import math
import bpy
from mathutils import Vector
from .protocol import SESSION_PROTOCOL, StageError


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
            'Preview Light Set' if light_set['id'] == selected else 'Light Set',
            parent, f"lights-{light_set['id']}", 'light-set', group_index)
        child.hide_render = True
        child.hide_viewport = light_set['id'] != selected
        for index, source in enumerate(light_set.get('lights', [])):
            kind = source['type']
            data = None
            if kind != 'ambient':
                blender_kind = {'infinite': 'SUN', 'point': 'POINT', 'spot': 'SPOT'}[kind]
                data = bpy.data.lights.new(f'{kind.title()} Stage Light', blender_kind)
                created_data.append(data)
                data.color = linear_color(source['color'])
                data.energy = 1
                if kind == 'spot':
                    position = game_vector(source['position'])
                    interest = game_vector(source['interest'])
                    cutoff = source.get('parameters', {}).get('cutoff') or 45
                    data.spot_size = math.radians(max(1, min(179, cutoff * 2)))
            obj = bpy.data.objects.new(f'{kind.title()} Stage Light', data)
            created_objects.append(obj)
            child.objects.link(obj)
            tag(obj, 'light', source['id'], group_index)
            obj['mme_light_set'] = light_set['id']
            obj['mme_light_type'] = kind
            obj['mme_light_flags'] = source['flags']
            obj['mme_light_diffuse'] = source['diffuse']
            obj['mme_light_specular'] = source['specular']
            obj['mme_light_animated'] = source['animated']
            obj['mme_light_attenuation'] = json.dumps(source['attenuation'], sort_keys=True)
            obj['mme_light_parameters'] = json.dumps(source.get('parameters', {}), sort_keys=True)
            obj['mme_light_enabled'] = not source.get('hidden', False)
            obj.id_properties_ui('mme_light_enabled').update(
                description='Enable this light in the Blender Melee preview and DAT export')
            obj.hide_render = True
            if kind == 'ambient':
                color = linear_color(source['color'])
                obj['mme_light_color'] = list(color)
                obj.id_properties_ui('mme_light_color').update(
                    description='Ambient color used by the Melee preview and DAT export',
                    subtype='COLOR', min=0, max=1)
                obj['mme_light_intensity'] = 1.0
                obj.id_properties_ui('mme_light_intensity').update(
                    description='Ambient preview and export intensity', min=0, soft_max=4)
                obj.empty_display_type = 'SPHERE'
                obj.empty_display_size = 3
                obj.color = (*color, 1)
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


def edits(scene, stage):
    """Serialize supported Blender light controls as static LOBJ edits."""
    sources = {source['id']: source for light_set in (stage.get('lighting') or {}).get('lightSets', [])
               for source in light_set.get('lights', [])}
    if not sources:
        return None
    objects = [obj for obj in bpy.data.objects if obj.get('mme_session_id') == scene.mme_session_id
               and obj.get('mme_role') == 'light']
    by_id = {}
    for obj in objects:
        key = obj.get('mme_id')
        if key in by_id:
            raise StageError('An imported light object is duplicated.')
        by_id[key] = obj
    if set(by_id) != set(sources):
        raise StageError('The imported light inventory changed. Restore missing light objects.')

    def game(value):
        return {'x': value.x, 'y': value.z, 'z': -value.y}

    def different(a, b):
        return any(abs(a[key] - b[key]) > 1e-5 * max(1, abs(a[key]), abs(b[key]))
                   for key in ('x', 'y', 'z'))

    def color_byte(value):
        if not math.isfinite(value) or value < 0 or value > 1:
            raise StageError('Light color multiplied by energy must stay between 0 and 1 for DAT export.')
        encoded = 12.92 * value if value <= .0031308 else 1.055 * value ** (1 / 2.4) - .055
        return max(0, min(255, round(encoded * 255)))

    direct = {}
    for key, source in sources.items():
        obj = by_id[key]
        kind = source['type']
        if kind == 'ambient':
            intensity = obj.get('mme_light_intensity')
            rgb = obj.get('mme_light_color')
            if not hasattr(rgb, '__len__') or isinstance(rgb, str) or len(rgb) != 3:
                raise StageError(f'{obj.name}: ambient light color must contain three components.')
        else:
            expected = {'infinite': 'SUN', 'point': 'POINT', 'spot': 'SPOT'}[kind]
            if obj.type != 'LIGHT' or obj.data is None or obj.data.type != expected:
                raise StageError(f'{obj.name}: imported light type changed.')
            intensity = obj.data.energy
            rgb = obj.data.color
        if not isinstance(intensity, (int, float)) or not math.isfinite(intensity) or intensity < 0:
            raise StageError(f'{obj.name}: light energy must be a finite nonnegative value.')
        color = [color_byte(component * intensity) for component in rgb]
        source_color = [round(component * 255) for component in source['color'][:3]]
        enabled = obj.get('mme_light_enabled')
        if not isinstance(enabled, (bool, int)):
            raise StageError(f'{obj.name}: mme_light_enabled must be a boolean.')
        edit = {'id': key}
        if color != source_color:
            edit['color'] = color
        if bool(enabled) == source.get('hidden', False):
            edit['enabled'] = bool(enabled)
        if kind != 'ambient':
            if kind == 'infinite':
                ray = obj.rotation_euler.to_matrix() @ Vector((0, 0, -1))
                magnitude = game_vector(source['position']).length
                if ray.length == 0 or magnitude == 0:
                    raise StageError(f'{obj.name}: infinite light direction has zero length.')
                position = game(ray.normalized() * magnitude)
            else:
                position = game(obj.location)
            if different(position, source['position']):
                edit['position'] = position
            if kind == 'spot':
                ray = obj.rotation_euler.to_matrix() @ Vector((0, 0, -1))
                source_position = game_vector(source['position'])
                distance = (game_vector(source['interest']) - source_position).length
                if ray.length == 0 or distance == 0:
                    raise StageError(f'{obj.name}: spot light direction has zero length.')
                interest = game(obj.location + ray.normalized() * distance)
                if different(interest, source['interest']):
                    edit['interest'] = interest
        if len(edit) == 1:
            continue
        direct[key] = edit

    # Stage code chooses the active model-group light set outside the DAT. Many
    # stages carry byte-for-byte equivalent descriptor sets for several model
    # groups, so an edit to the preview representative must reach every member
    # of that family to affect whichever copy the game selects at runtime.
    selected = preview_set(stage)
    if selected:
        ignored = {'id', 'animated', 'sourceOffset'}

        def signature(light_set):
            return json.dumps([{key: value for key, value in source.items() if key not in ignored}
                               for source in light_set.get('lights', [])], sort_keys=True)

        family = [light_set for light_set in (stage.get('lighting') or {}).get('lightSets', [])
                  if light_set.get('source') == 'model-group'
                  and signature(light_set) == signature(selected)]
        for index, source in enumerate(selected.get('lights', [])):
            template = direct.get(source['id'])
            if not template:
                continue
            for light_set in family:
                sibling = light_set['lights'][index]
                replicated = {'id': sibling['id'],
                              **{field: value for field, value in template.items() if field != 'id'}}
                existing = direct.get(sibling['id'])
                if existing and existing != replicated:
                    raise StageError('Equivalent model-group lights have conflicting edits.')
                direct[sibling['id']] = replicated

    compiled = {}
    for key, edit in direct.items():
        source = sources[key]
        offset = source['sourceOffset']
        if offset not in compiled:
            compiled[offset] = edit
            continue
        current = compiled[offset]
        for field, value in edit.items():
            if field == 'id':
                continue
            if field in current and current[field] != value:
                raise StageError('Copies of one shared LOBJ have conflicting edits.')
            current[field] = value
    values = list(compiled.values())
    return {'protocolVersion': SESSION_PROTOCOL, 'lights': values} if values else None
