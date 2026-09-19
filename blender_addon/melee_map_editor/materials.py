"""Terrain labels from melee/src/melee/mp/forward.h, enum mp_Terrain.

Keep uncertain entries explicit; older editor names disagree with the decomp.
The byte indexes a stage-specific response table, not a Blender render material.
"""
NAMES = ('Basic', 'Rock', 'Grass', 'Dirt', 'Wood', 'Light Metal', 'Heavy Metal',
         'Paper', 'Goop', 'Birdo', 'Water', 'Unknown (11)', 'UFO', 'Turtle',
         'Snow', 'Ice', 'Game & Watch', 'Unknown (17)', 'Checkered', 'Unknown (19)')
ITEMS = [(f'SURFACE_{i}', name, f'Collision surface {i}: {name}', i)
         for i, name in enumerate(NAMES)] + [
    ('CUSTOM', 'Custom / Unknown ID', 'Keep or specify a raw surface ID outside the named range', 256)]


def get_surface(scene):
    value = scene.mme_collision_material
    return value if value < len(NAMES) else 256


def set_surface(scene, value):
    if value < len(NAMES):
        scene.mme_collision_material = value
    elif scene.mme_collision_material < len(NAMES):
        scene.mme_collision_material = len(NAMES)


def draw_surface(layout, scene):
    layout.prop(scene, 'mme_collision_surface')
    if scene.mme_collision_material >= len(NAMES):
        layout.prop(scene, 'mme_collision_material', text='Custom ID')
