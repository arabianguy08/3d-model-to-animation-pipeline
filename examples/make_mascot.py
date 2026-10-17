"""Generate Pip: original synthetic geometry, semantic bones and two real actions.

blender --background --factory-startup --python examples/make_mascot.py -- OUTPUT
"""
import json
import math
from pathlib import Path
import sys

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from companion_animation.pipeline import run_pipeline


def make(directory):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    if (directory / "source.blend").exists():
        raise RuntimeError("Refusing to overwrite an existing mascot")
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    scene = bpy.context.scene
    scene.render.fps = 30
    scene.frame_start, scene.frame_end = 1, 61
    armature = bpy.data.armatures.new("PipSkeleton")
    rig = bpy.data.objects.new("PipRig", armature)
    scene.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    rig.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    bones = [
        ("Root", (0, 0, 0), (0, 0, .25), None),
        ("Pelvis", (0, 0, .55), (0, 0, .8), "Root"),
        ("Spine", (0, 0, .8), (0, 0, 1.05), "Pelvis"),
        ("Chest", (0, 0, 1.05), (0, 0, 1.3), "Spine"),
        ("Neck", (0, 0, 1.3), (0, 0, 1.45), "Chest"),
        ("Head", (0, 0, 1.45), (0, 0, 1.9), "Neck"),
        ("Jaw", (0, -.12, 1.5), (0, -.35, 1.42), "Head"),
        ("Tail.01", (0, .18, .65), (0, .45, .72), "Pelvis"),
        ("Tail.02", (0, .45, .72), (0, .7, .9), "Tail.01"),
    ]
    for suffix, sign in [("L", 1), ("R", -1)]:
        bones += [
            ("UpperArm." + suffix, (sign * .28, 0, 1.2), (sign * .53, 0, .99), "Chest"),
            ("Forearm." + suffix, (sign * .53, 0, .99), (sign * .64, -.08, .8), "UpperArm." + suffix),
            ("Hand." + suffix, (sign * .64, -.08, .8), (sign * .65, -.2, .76), "Forearm." + suffix),
            ("Thigh." + suffix, (sign * .17, 0, .6), (sign * .19, 0, .35), "Pelvis"),
            ("Shin." + suffix, (sign * .19, 0, .35), (sign * .2, -.05, .13), "Thigh." + suffix),
            ("Foot." + suffix, (sign * .2, -.05, .13), (sign * .2, -.3, .1), "Shin." + suffix),
            ("Ear." + suffix, (sign * .25, 0, 1.85), (sign * .3, 0, 2.22), "Head"),
        ]
    for name, head, tail, parent in bones:
        bone = armature.edit_bones.new(name)
        # Robot joints share a vertical rest axis; rigid shell pieces retain their
        # own anatomy. This avoids oblique inverse-bind roundoff on reimport.
        bone.head = head
        bone.tail = (head[0], head[1], head[2] + .2)
        if parent:
            bone.parent = armature.edit_bones[parent]
    bpy.ops.object.mode_set(mode="OBJECT")
    for bone in rig.pose.bones:
        bone.rotation_mode = "XYZ"

    def material(name, color, metallic=0):
        mat = bpy.data.materials.new(name)
        mat.diffuse_color = (*color, 1)
        mat.use_nodes = True
        shader = mat.node_tree.nodes.get("Principled BSDF")
        shader.inputs["Base Color"].default_value = (*color, 1)
        shader.inputs["Metallic"].default_value = metallic
        shader.inputs["Roughness"].default_value = .32
        return mat

    teal = material("Ceramic teal", (.04, .5, .48), .2)
    cream = material("Warm ceramic", (.96, .81, .5))
    dark = material("Face screen", (.012, .035, .055), .25)
    coral = material("Coral joints", (.95, .25, .12))
    light = material("LED cyan", (.12, .95, 1))
    shader = light.node_tree.nodes.get("Principled BSDF")
    shader.inputs["Emission Color"].default_value = (.08, .8, 1, 1)
    shader.inputs["Emission Strength"].default_value = 2
    parts = []

    def piece(name, center, scale, bone, mat, face=False):
        bpy.ops.mesh.primitive_uv_sphere_add(segments=24, ring_count=16, location=center)
        obj = bpy.context.object
        obj.name, obj.scale = name, scale
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
        for poly in obj.data.polygons:
            poly.use_smooth = True
        obj.data.materials.append(mat)
        group = obj.vertex_groups.new(name=bone)
        group.add(list(range(len(obj.data.vertices))), 1, "REPLACE")
        if not face:
            parts.append(obj)
        return obj

    piece("Body", (0, 0, .91), (.34, .25, .39), "Spine", teal)
    piece("Belly", (0, -.18, .9), (.24, .12, .27), "Spine", cream)
    piece("Head shell", (0, 0, 1.68), (.43, .3, .34), "Head", teal)
    piece("Face screen", (0, -.255, 1.68), (.34, .085, .22), "Head", dark)
    piece("Jaw plate", (0, -.19, 1.42), (.23, .15, .07), "Jaw", cream)
    piece("Tail base", (0, .34, .7), (.1, .23, .1), "Tail.01", coral)
    piece("Tail tip", (0, .61, .83), (.1, .18, .15), "Tail.02", cream)
    eyes = []
    for suffix, sign in [("L", 1), ("R", -1)]:
        piece("Ear " + suffix, (sign * .28, 0, 2.02), (.11, .12, .3), "Ear." + suffix, cream)
        piece("Shoulder " + suffix, (sign * .32, 0, 1.18), (.12, .13, .13), "UpperArm." + suffix, coral)
        piece("Arm " + suffix, (sign * .46, 0, 1.06), (.15, .1, .16), "UpperArm." + suffix, teal)
        piece("Forearm " + suffix, (sign * .59, -.04, .9), (.095, .1, .15), "Forearm." + suffix, cream)
        piece("Hand " + suffix, (sign * .65, -.12, .77), (.11, .12, .11), "Hand." + suffix, coral)
        piece("Leg " + suffix, (sign * .18, 0, .42), (.13, .13, .2), "Thigh." + suffix, teal)
        piece("Shin " + suffix, (sign * .2, -.02, .23), (.1, .11, .13), "Shin." + suffix, coral)
        piece("Foot " + suffix, (sign * .2, -.14, .1), (.15, .24, .1), "Foot." + suffix, cream)
        eyes.append(piece("Eye " + suffix, (sign * .145, -.33, 1.71), (.065, .025, .09), "Head", light, True))

    def join(objects, name):
        bpy.ops.object.select_all(action="DESELECT")
        for obj in objects:
            obj.select_set(True)
        bpy.context.view_layer.objects.active = objects[0]
        bpy.ops.object.join()
        result = bpy.context.object
        result.name = name
        modifier = result.modifiers.new("Skin", "ARMATURE")
        modifier.object = rig
        result.parent = rig
        return result

    join(parts, "PipBody")
    face = join(eyes, "PipFace")
    face.shape_key_add(name="Basis")
    blink = face.shape_key_add(name="Blink")
    for point in blink.data:
        point.co.z = 1.71 + (point.co.z - 1.71) * .035

    clips = []
    for name in ["Idle", "Cast"]:
        rig.animation_data_create()
        rig.animation_data.action = bpy.data.actions.new(name + "Body")
        for frame in [1, 11, 21, 31, 41, 51, 61]:
            phase = (frame - 1) / 60
            wave = math.sin(phase * math.tau)
            cast = math.sin(phase * math.pi) ** 2 if name == "Cast" else 0
            for bone in rig.pose.bones:
                bone.rotation_euler = (0, 0, 0)
            rig.pose.bones["Spine"].rotation_euler.x = .025 * wave + .06 * cast
            rig.pose.bones["Chest"].rotation_euler.z = .045 * wave
            rig.pose.bones["Head"].rotation_euler.z = -.1 * wave
            rig.pose.bones["Head"].rotation_euler.x = -.035 * wave - .08 * cast
            rig.pose.bones["UpperArm.R"].rotation_euler.x = -.8 * cast
            rig.pose.bones["Forearm.R"].rotation_euler.x = -.4 * cast
            rig.pose.bones["Jaw"].rotation_euler.x = .12 * cast
            for suffix, sign in [("L", 1), ("R", -1)]:
                rig.pose.bones["Ear." + suffix].rotation_euler.y = sign * .07 * wave
            rig.pose.bones["Tail.01"].rotation_euler.z = .16 * wave
            rig.pose.bones["Tail.02"].rotation_euler.z = .18 * math.sin(phase * math.tau + .3) - .18 * math.sin(.3)
            for bone in rig.pose.bones:
                bone.keyframe_insert("rotation_euler", frame=frame)
        body = rig.animation_data.action
        body.use_fake_user = True
        body_slot = body.slots[0].identifier
        keys = face.data.shape_keys
        keys.animation_data_create()
        keys.animation_data.action = bpy.data.actions.new(name + "Face")
        for frame, value in [(1, 0), (18, 0), (22, 1), (26, 0), (61, 0)]:
            blink.value = value
            blink.keyframe_insert("value", frame=frame)
        facial = keys.animation_data.action
        facial.use_fake_user = True
        clips.append({"name": name, "armature_action": body.name, "armature_slot": body_slot,
                      "morph_actions": {"PipFace": {"action": facial.name, "slot": facial.slots[0].identifier}},
                      "duration_s": 2, "loop": True, "moving": True})
        rig.animation_data.action = None
        keys.animation_data.action = None
    for bone in rig.pose.bones:
        bone.rotation_euler = (0, 0, 0)
    blink.value = 0
    scene.frame_set(1)
    bpy.ops.wm.save_as_mainfile(filepath=str(directory / "source.blend"))
    config = run_pipeline.read(run_pipeline.HERE / "config.example.json")
    config.update(model_id="pip", workspace=str(directory / "work"), sources={"model": str(directory / "source.blend")})
    config["anatomy"] = {"mode": "existing", "armature": "PipRig", "roles": {name: name for name, *_ in bones}}
    config["motion"] = {"mode": "reuse", "clips": clips}
    config["face"]["mode"] = "existing"
    config["sampling"]["hz"] = 240
    config["preview"].update(resolution=192, fps=8)
    run_pipeline.write(directory / "pip.json", config)
    print(json.dumps({"config": str(directory / "pip.json"), "bones": len(bones), "clips": [c["name"] for c in clips]}))


if __name__ == "__main__":
    make(sys.argv[sys.argv.index("--") + 1])
