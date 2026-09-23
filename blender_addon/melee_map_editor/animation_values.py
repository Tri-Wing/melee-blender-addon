"""Transport animated numeric shader values through per-object attributes."""
import json
import uuid

import bpy


TAG = 'mme_animation_attribute'


def numeric(value):
    return value if isinstance(value, (int, float)) else tuple(value)


def controlled(socket):
    """An attribute link still represents an animation-controlled default."""
    return not socket.is_linked or any(link.from_node.get(TAG) for link in socket.links)


def bindings(material):
    nodes = material.node_tree.nodes
    result = {}
    namespace = material.get('mme_animation_attribute_id')
    # Material copies must not address the same object properties if both are
    # assigned to one mesh and later select different animation values.
    if namespace and any(other != material and other.get('mme_animation_attribute_id') == namespace
                         for other in bpy.data.materials):
        replacement = uuid.uuid4().hex
        material['mme_animation_attribute_id'] = replacement
        for node in nodes:
            if node.get(TAG):
                node.attribute_name = node.attribute_name.replace(namespace, replacement, 1)
    for node in nodes:
        if not node.get(TAG):
            continue
        name, output, index = json.loads(node[TAG])
        source = nodes.get(name)
        if source is not None:
            sockets = source.outputs if output else source.inputs
            if index < len(sockets):
                result[sockets[index].as_pointer()] = node
    return result


def write(node, owners, value):
    name = node.attribute_name
    for obj in owners:
        previous = obj.get(name)
        if previous is not None and numeric(previous) == value:
            continue
        obj[name] = value
        obj.update_tag(refresh={'OBJECT'})


def create(socket, owners, value):
    tree = socket.id_data
    material = next(material for material in bpy.data.materials if material.node_tree == tree)
    namespace = material.get('mme_animation_attribute_id')
    if not namespace:
        namespace = uuid.uuid4().hex
        material['mme_animation_attribute_id'] = namespace
    node = tree.nodes.new('ShaderNodeAttribute')
    node.name = 'Stage Animated Value'
    node.label = 'Stage animation value'
    node.attribute_type = 'OBJECT'
    sockets = socket.node.outputs if socket.is_output else socket.node.inputs
    index = next(i for i, candidate in enumerate(sockets) if candidate == socket)
    node[TAG] = json.dumps([socket.node.name, socket.is_output, index])
    node.attribute_name = f'mme_anim_{namespace}_{sum(bool(n.get(TAG)) for n in tree.nodes)}'
    node.location = (socket.node.location.x - 200, socket.node.location.y - 150)
    write(node, owners, value)
    output = node.outputs['Fac' if isinstance(value, (int, float)) else
                          'Color' if len(value) == 4 else 'Vector']
    if socket.is_output:
        for target in [link.to_socket for link in socket.links]:
            tree.links.new(output, target)
    else:
        tree.links.new(output, socket)
    return node


def remove(material):
    """Restore numeric controls before rebuilding any part of a preview graph."""
    if not material.node_tree:
        return
    tree = material.node_tree
    for node in bindings(material).values():
        name, output, index = json.loads(node[TAG])
        source = tree.nodes[name]
        socket = (source.outputs if output else source.inputs)[index]
        if output:
            for target in [link.to_socket for result in node.outputs for link in result.links]:
                tree.links.new(socket, target)
        property_name = node.attribute_name
        tree.nodes.remove(node)
        for obj in bpy.data.objects:
            if property_name in obj:
                del obj[property_name]
                obj.update_tag(refresh={'OBJECT'})


def value(socket, obj):
    """Read a preview control, including its per-object animated value."""
    for node in socket.id_data.nodes:
        if node.get(TAG):
            name, output, index = json.loads(node[TAG])
            sockets = socket.node.outputs if socket.is_output else socket.node.inputs
            if name == socket.node.name and output == socket.is_output and sockets[index] == socket:
                return numeric(obj[node.attribute_name])
    return numeric(socket.default_value)
