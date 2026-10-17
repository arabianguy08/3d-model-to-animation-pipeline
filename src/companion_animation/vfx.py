"""Small Blender presentation helpers. Imported only inside Blender.

These author ordinary Blender objects/actions. Game flight and collision remain
separate from the character's baked body motion.
"""
import math


def charge_orb(location, start=1, impact=31, end=61):
    """Create an original emissive orb and orbiting sparks on a fixed timeline."""
    import bpy
    material = bpy.data.materials.new("Charge emission")
    material.use_nodes = True
    nodes = material.node_tree.nodes
    nodes.clear()
    emission = nodes.new("ShaderNodeEmission")
    emission.inputs["Color"].default_value = (.1, .8, 1, 1)
    emission.inputs["Strength"].default_value = 4
    output = nodes.new("ShaderNodeOutputMaterial")
    material.node_tree.links.new(emission.outputs[0], output.inputs["Surface"])
    objects = []
    for index in range(9):
        bpy.ops.mesh.primitive_uv_sphere_add(segments=16, ring_count=8, location=location)
        obj = bpy.context.object
        obj.name = "Charge orb" if index == 0 else f"Charge spark {index}"
        obj.data.materials.append(material)
        radius = .12 if index == 0 else .023
        for frame in [start, start + (impact - start) / 2, impact, (impact + end) / 2, end]:
            progress = (frame - start) / (end - start)
            pulse = math.sin(math.pi * progress) ** 2
            obj.scale = (max(1e-6, radius * pulse),) * 3
            angle = math.tau * (index / 8 + progress)
            obj.location = location if index == 0 else (
                location[0] + .24 * math.cos(angle), location[1] + .24 * math.sin(angle),
                location[2] + .06 * math.sin(angle * 2))
            obj.keyframe_insert("scale", frame=frame)
            obj.keyframe_insert("location", frame=frame)
        objects.append(obj)
    return objects
