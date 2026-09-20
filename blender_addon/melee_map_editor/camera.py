"""Preview-only Blender camera created from grGroundParam."""
import math
import bpy
from mathutils import Vector


def game_vector(value):
    return Vector((value['x'], -value['z'], value['y']))


def create(parent, stage, tag, created_objects, created_cameras, scene):
    source = stage.get('camera')
    if not source:
        return None

    name = 'Melee Camera'
    data = bpy.data.cameras.new(name)
    created_cameras.append(data)
    data.type = 'PERSP'
    data.lens_unit = 'FOV'
    data.sensor_fit = 'VERTICAL'
    data.angle = math.radians(source['fieldOfViewDegrees'])
    data.clip_start = source.get('nearClip', 0.1)
    data.clip_end = source.get('farClip', 16384.0)
    data.display_size = 15

    obj = bpy.data.objects.new(name, data)
    created_objects.append(obj)
    parent.objects.link(obj)
    tag(obj, 'preview-camera', 'preview-camera')
    obj['mme_preview_only'] = True
    obj['mme_fixed_camera'] = source['fixedCamera']
    obj['mme_runtime_tracks_subjects'] = source['runtimeTracksSubjects']
    obj['mme_game_position'] = [source['position'][axis] for axis in ('x', 'y', 'z')]
    obj['mme_game_interest'] = [source['interest'][axis] for axis in ('x', 'y', 'z')]
    obj['mme_field_of_view'] = source['fieldOfViewDegrees']
    obj['mme_vertical_angle'] = source['verticalAngleDegrees']
    obj['mme_horizontal_angle'] = source['horizontalAngleDegrees']

    position = game_vector(source['position'])
    interest = game_vector(source['interest'])
    direction = interest - position
    obj.location = position
    if direction.length:
        obj.rotation_euler = direction.to_track_quat('-Z', 'Y').to_euler()

    # Melee's game viewport is 640x480. Keeping the imported scene at that
    # ratio makes the camera framing useful immediately in Camera View.
    scene.render.resolution_x = 640
    scene.render.resolution_y = 480
    scene.render.resolution_percentage = 100
    scene.camera = obj
    return obj
