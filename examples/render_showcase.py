"""Render the actual exported GLB with separate presentation-only charge VFX.

blender --background --factory-startup --python examples/render_showcase.py -- GLB OUTPUT
"""
import json
import math
from pathlib import Path
import sys

import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from companion_animation.pipeline import blender_adapter as adapter
from companion_animation.vfx import charge_orb


def render(glb, output):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    adapter.load(Path(glb).resolve())
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 12
    scene.cycles.use_denoising = True
    scene.render.resolution_x = 640
    scene.render.resolution_y = 480
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.world = bpy.data.worlds.new("Studio world")
    scene.world.color = (.035, .045, .08)
    scene.view_settings.view_transform = "AgX"
    scene.render.threads_mode = "FIXED"
    scene.render.threads = 6

    def material(name, color):
        mat = bpy.data.materials.new(name)
        mat.diffuse_color = (*color, 1)
        mat.use_nodes = True
        shader = mat.node_tree.nodes.get("Principled BSDF")
        shader.inputs["Base Color"].default_value = (*color, 1)
        shader.inputs["Roughness"].default_value = .6
        return mat

    bpy.ops.mesh.primitive_plane_add(size=200, location=(0, 0, -.18))
    bpy.context.object.data.materials.append(material("Backdrop", (.025, .035, .065)))
    bpy.ops.mesh.primitive_cylinder_add(vertices=96, radius=1, depth=.16, location=(0, 0, -.08))
    podium = bpy.context.object
    podium.data.materials.append(material("Display plinth", (.06, .11, .16)))
    bevel = podium.modifiers.new("Soft stage rim", "BEVEL")
    bevel.width, bevel.segments = .055, 3
    for location, energy, color, size in [((2, -3, 5), 650, (.65, .85, 1), 4),
                                          ((-3, -1, 2), 400, (1, .55, .3), 3),
                                          ((0, 3, 4), 800, (.2, 1, .8), 3)]:
        light = bpy.data.lights.new("Studio area", "AREA")
        light.energy, light.color, light.shape, light.size = energy, color, "DISK", size
        obj = bpy.data.objects.new(light.name, light)
        scene.collection.objects.link(obj)
        obj.location = location
        obj.rotation_euler = (Vector((0, 0, 1)) - obj.location).to_track_quat("-Z", "Y").to_euler()
    camera_data = bpy.data.cameras.new("Showcase camera")
    camera_data.type, camera_data.ortho_scale = "ORTHO", 4.1
    camera = bpy.data.objects.new("Showcase camera", camera_data)
    scene.collection.objects.link(camera)
    camera.location = (3, -6, 2.9)
    camera.rotation_euler = (Vector((0, 0, 1.12)) - camera.location).to_track_quat("-Z", "Y").to_euler()
    scene.camera = camera
    # The effect is added after import, and is not claimed as certified character motion.
    images = []
    for clip in ("Idle", "Cast"):
        adapter.reset()
        effect = charge_orb((-.83, -.3, 1.32), start=0, impact=scene.render.fps, end=scene.render.fps * 2) if clip == "Cast" else []
        candidates = [a for a in bpy.data.actions if a.name == clip or a.name.startswith(clip + "_")]
        if len(candidates) != 1:
            raise RuntimeError(f"Ambiguous persisted clip: {clip}")
        action = candidates[0]
        for obj in scene.objects:
            for data in (obj, obj.data.shape_keys if obj.type == "MESH" else None):
                if data is None or not data.animation_data:
                    continue
                slots = [(s.action, s.action_slot) for track in data.animation_data.nla_tracks for s in track.strips if s.action == action]
                if len(slots) == 1:
                    data.animation_data.action, data.animation_data.action_slot = action, slots[0][1]
                    data.animation_data.use_nla = False
        for obj in effect:
            obj.hide_render = clip == "Idle"
        for index in range(32):
            seconds = index / 16
            adapter.frame(seconds * scene.render.fps)
            path = output / f"{len(images):04d}.png"
            scene.render.filepath = str(path)
            bpy.ops.render.render(write_still=True)
            images.append({"file": path.name, "clip": clip, "time_s": seconds})
    (output / "frames.json").write_text(json.dumps({"fps": 16, "frames": images}, indent=2))


if __name__ == "__main__":
    args = sys.argv[sys.argv.index("--") + 1:]
    render(args[0], args[1])
