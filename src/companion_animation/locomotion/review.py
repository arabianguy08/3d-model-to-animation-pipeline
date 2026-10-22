"""Persisted GLB multi-view H.264 review with a moving grid."""
import json
import math
import struct
import sys
from pathlib import Path

try:
    import bpy
except ImportError:
    bpy = None
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from companion_animation.locomotion.review_commands import parse_args, VIEWS, grid_offset
import numpy as np
if bpy is not None:
    from mathutils import Vector

def initialize(argv):
    global GLB, OUT, LABEL, OPTS, CLIPS, SPEEDS, COMBINED, LOOPS, SIZE, FPS, LOCOMOTION
    args = parse_args(argv)
    GLB, OUT, LABEL = Path(args.model), Path(args.output), args.label
    OPTS = {'--' + k.replace('_', '-'): v for k, v in vars(args).items()}
    CLIPS = [c for c in args.clips.split(',') if c]
    SPEEDS = {k: float(v) for k, v in (x.split('=') for x in args.speed.split(',') if x)}
    COMBINED = [c for c in args.combined.split(',') if c]
    LOOPS, SIZE, FPS = args.loops, args.size, args.fps
    LOCOMOTION = set()


def setup():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(GLB))
    scene = bpy.context.scene
    src_fps = scene.render.fps / scene.render.fps_base
    raw = GLB.read_bytes()
    gltf = json.loads(raw[20:20 + struct.unpack('<I', raw[12:16])[0]])
    names = [a.get('name') for a in gltf.get('animations', [])]
    global CLIP_LOOPS
    CLIP_LOOPS = {a['name']: a.get('extras', {}).get('loop', False) for a in gltf.get('animations', [])}
    for a in gltf.get('animations', []):
        extras = a.get('extras', {})
        SPEEDS.setdefault(a['name'], extras.get('treadmillSpeed', 0.))
        if extras.get('gait') in ('walk', 'run'):
            LOCOMOTION.add(a['name'])
    arm = [o for o in scene.objects if o.type == 'ARMATURE'][0]
    owners = {}
    for obj in scene.objects:
        if obj.animation_data:
            for track in obj.animation_data.nla_tracks:
                for strip in track.strips:
                    owners.setdefault(strip.action, []).append((obj, strip.action_slot))
    actions = {}
    for name in names:
        for action, users in owners.items():
            if action.name == name or action.name.startswith(name + '_'):
                for obj, slot in users:
                    if obj == arm:
                        actions[name] = (action, slot)
    for obj in scene.objects:
        if obj.animation_data:
            for track in list(obj.animation_data.nla_tracks):
                obj.animation_data.nla_tracks.remove(track)
            obj.animation_data.action = None
    shapes = {bone.custom_shape for bone in arm.pose.bones if bone.custom_shape}
    for bone in arm.pose.bones:
        bone.custom_shape = None
    for obj in shapes:
        if obj.type == 'MESH' and not obj.vertex_groups:
            bpy.data.objects.remove(obj, do_unlink=True)
    root = arm
    while root.parent:
        root = root.parent
    pivot = bpy.data.objects.new('ReviewPivot', None)
    scene.collection.objects.link(pivot)
    root.parent = pivot
    root = pivot
    meshes = [o for o in scene.objects if o.type == 'MESH']
    return scene, src_fps, names, arm, actions, root, meshes


def mesh_points(meshes):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    pts = []
    for obj in meshes:
        ev = obj.evaluated_get(depsgraph)
        m = ev.to_mesh()
        co = np.empty(len(m.vertices) * 3)
        m.vertices.foreach_get('co', co)
        mw = np.array(ev.matrix_world)
        pts.append(co.reshape(-1, 3) @ mw[:3, :3].T + mw[:3, 3])
        ev.to_mesh_clear()
    return np.concatenate(pts)


def grid_material(cell_px=64, size=1024):
    img = bpy.data.images.new('Grid', size, size)
    px = np.full((size, size, 4), (0.62, 0.63, 0.64, 1.0), dtype=np.float32)
    for k in range(0, size, cell_px):
        px[k:k + 3, :, :3] = 0.22
        px[:, k:k + 3, :3] = 0.22
    half = cell_px // 2
    for k in range(half, size, cell_px):
        px[k:k + 1, :, :3] = 0.45
        px[:, k:k + 1, :3] = 0.45
    img.pixels.foreach_set(px.ravel())
    img.pack()
    mat = bpy.data.materials.new('GridFloor')
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    tex = nodes.new('ShaderNodeTexImage')
    tex.image = img
    tex.interpolation = 'Closest'
    bsdf = nodes['Principled BSDF']
    bsdf.inputs['Roughness'].default_value = 0.9
    mat.node_tree.links.new(tex.outputs['Color'], bsdf.inputs['Base Color'])
    return mat, size // cell_px


def main():
    scene, src_fps, names, arm, actions, root, meshes = setup()
    scale = FPS / src_fps  # action frames are keyed at the importer's scene rate
    scene.render.fps, scene.render.fps_base = FPS, 1
    # Height and floor from the idle's first frame (what the app grounds).
    first = actions[names[0]]
    arm.animation_data_create()
    arm.animation_data.action = first[0]
    arm.animation_data.action_slot = first[1]
    scene.frame_set(0)
    pts = mesh_points(meshes)
    floor_z = float(pts[:, 2].min())
    H = float(pts[:, 2].max() - floor_z)
    centre = pts.mean(axis=0)
    arm.animation_data.action = None
    # Floor: 16 cells over 4 H (cell = H / 4), moved as a treadmill.
    mat, cells = grid_material()
    cell = H / 4
    bpy.ops.mesh.primitive_plane_add(size=cell * cells, location=(centre[0], centre[1], floor_z))
    floor = bpy.context.object
    floor.data.materials.append(mat)
    # Light and world.
    world = bpy.data.worlds.new('W')
    scene.world = world
    world.use_nodes = True
    world.node_tree.nodes['Background'].inputs[0].default_value = (0.78, 0.8, 0.83, 1)
    world.node_tree.nodes['Background'].inputs[1].default_value = 0.9
    sun = bpy.data.objects.new('Sun', bpy.data.lights.new('Sun', 'SUN'))
    sun.data.energy = 3.0
    sun.data.angle = math.radians(8)
    sun.rotation_euler = (math.radians(40), math.radians(10), math.radians(-30))
    scene.collection.objects.link(sun)
    if OPTS.get('--engine', 'workbench') == 'workbench':
        # Workbench: textured studio shading with cast shadows; fast and stable for motion review.
        scene.render.engine = 'BLENDER_WORKBENCH'
        shading = scene.display.shading
        shading.light = 'STUDIO'
        shading.color_type = 'TEXTURE'
        shading.show_shadows = True
        shading.shadow_intensity = 0.45
        shading.show_cavity = False
        scene.display.light_direction = (0.35, -0.55, 0.75)
        scene.display.shadow_shift = 0.05
        scene.display.render_aa = '8'
    else:
        scene.render.engine = 'BLENDER_EEVEE'
        scene.eevee.taa_render_samples = 16
    scene.view_settings.view_transform = 'Standard'
    scene.view_settings.exposure = float(OPTS.get('--exposure', 0.9))
    scene.render.resolution_x = scene.render.resolution_y = SIZE
    scene.render.resolution_percentage = 100
    if hasattr(scene.render.image_settings, 'media_type'):
        scene.render.image_settings.media_type = 'VIDEO'
    scene.render.image_settings.file_format = 'FFMPEG'
    scene.render.ffmpeg.format = 'MPEG4'
    scene.render.ffmpeg.codec = 'H264'
    scene.render.ffmpeg.constant_rate_factor = 'MEDIUM'
    scene.render.ffmpeg.ffmpeg_preset = 'GOOD'
    scene.render.ffmpeg.gopsize = 15
    scene.render.use_stamp = True
    for flag in ('use_stamp_date', 'use_stamp_time', 'use_stamp_render_time', 'use_stamp_frame', 'use_stamp_scene',
                 'use_stamp_camera', 'use_stamp_filename', 'use_stamp_memory', 'use_stamp_hostname', 'use_stamp_lens',
                 'use_stamp_marker', 'use_stamp_frame_range', 'use_stamp_sequencer_strip'):
        if hasattr(scene.render, flag):
            setattr(scene.render, flag, False)
    scene.render.use_stamp_note = True
    scene.render.stamp_font_size = max(14, SIZE // 40)
    scene.render.stamp_background = (0, 0, 0, 0.55)

    def camera(name, direction, target_h=0.5, lens=50, height_frac=1.45, look=None, loc=None, fov=None):
        cam = bpy.data.objects.new(name, bpy.data.cameras.new(name))
        scene.collection.objects.link(cam)
        target = Vector((centre[0], centre[1], floor_z + target_h * H)) if look is None else Vector(look)
        if loc is None:
            cam.data.lens = lens
            fov_v = 2 * math.atan(cam.data.sensor_width / 2 / lens)
            dist = (height_frac * H / 2) / math.tan(fov_v / 2)
            d = Vector(direction).normalized()
            loc = target + d * dist
        else:
            loc = Vector(loc)
        if fov is not None:
            cam.data.sensor_fit = 'VERTICAL'
            cam.data.angle_y = math.radians(fov)
        cam.location = loc
        cam.rotation_euler = (target - loc).to_track_quat('-Z', 'Y').to_euler()
        return cam

    def runtime_camera():
        return camera('Runtime', None, loc=(centre[0], centre[1] - OPTS['--runtime-distance'] * H, floor_z + OPTS['--runtime-height'] * H),
                      look=(centre[0], centre[1], floor_z + OPTS['--runtime-target'] * H), fov=OPTS['--runtime-fov'])

    views = [('front', (0, -1, 0.12)), ('side', (1, 0, 0.12)), ('three-quarter', (0.75, -1, 0.35))]
    track_pool = []

    def strip(action_slot, start, repeat, track_index):
        while len(arm.animation_data.nla_tracks) <= track_index:
            track_pool.append(arm.animation_data.nla_tracks.new())
        track = arm.animation_data.nla_tracks[track_index]
        action, slot = action_slot
        s = track.strips.new(action.name, int(start), action)
        try:
            s.action_slot = slot
        except (AttributeError, TypeError):
            pass
        s.scale = scale
        s.repeat = repeat
        s.extrapolation = 'NOTHING'
        return s

    def clip_frames(name):
        action = actions[name][0]
        f0, f1 = action.frame_range
        return (f1 - f0) * scale  # output frames per cycle

    still_paths = []

    def render(path, segments, notes, cycle_frames=0):
        """segments: list of (start, end, camera, yaw, speed)."""
        scene.timeline_markers.clear()
        floor.animation_data_clear()
        root.animation_data_clear()
        y = 0.0
        heading = 0.0
        for start, end, cam, yaw, speed in segments:
            marker = scene.timeline_markers.new(cam.name, frame=int(start))
            marker.camera = cam
            for f in range(int(start), int(end) + 1):
                dx, dy = grid_offset(1., y, yaw, cell)
                floor.location = (centre[0] + dx, centre[1] + dy, floor_z)
                floor.keyframe_insert('location', frame=f)
                root.rotation_mode = 'XYZ'
                root.rotation_euler = (0, 0, yaw)
                root.keyframe_insert('rotation_euler', frame=f)
                y += speed / FPS
        for fc in list(floor.animation_data.action.fcurves if hasattr(floor.animation_data.action, 'fcurves') else []):
            for k in fc.keyframe_points:
                k.interpolation = 'CONSTANT'
        scene.frame_start, scene.frame_end = int(segments[0][0]), int(segments[-1][1])
        # Burn-in label per segment via a handler-free approach: one note per render call segment.
        scene.render.stamp_note_text = notes
        scene.render.filepath = str(path)
        bpy.ops.render.render(animation=True)
        stills = int(OPTS.get('--stills', 0))
        if stills and cycle_frames:
            settings = scene.render.image_settings
            if hasattr(settings, 'media_type'):
                settings.media_type = 'IMAGE'
            settings.file_format = 'PNG'
            frames_dir = OUT / 'frames'
            frames_dir.mkdir(exist_ok=True)
            first = int(segments[0][0])
            for k in range(stills):
                f = first + int(round(k * cycle_frames / stills))
                scene.frame_set(f)
                scene.render.filepath = str(frames_dir / f'{path.stem}_{k:02d}.png')
                bpy.ops.render.render(write_still=True)
                still_paths.append(scene.render.filepath)
            if hasattr(settings, 'media_type'):
                settings.media_type = 'VIDEO'
            settings.file_format = 'FFMPEG'
            scene.render.ffmpeg.format = 'MPEG4'
            scene.render.ffmpeg.codec = 'H264'
            scene.render.ffmpeg.constant_rate_factor = 'MEDIUM'
        return str(path)

    OUT.mkdir(parents=True, exist_ok=True)
    produced = {}
    cams = {name: camera(name, d) for name, d in views}
    cams['runtime'] = runtime_camera()
    if any(name not in actions for name in CLIPS + COMBINED):
        raise ValueError('Requested clip is absent or unbound')
    for clip in CLIPS:
        arm.animation_data.action = None
        for track in list(arm.animation_data.nla_tracks):
            arm.animation_data.nla_tracks.remove(track)
        cycle = clip_frames(clip)
        speed = SPEEDS.get(clip, 0.0)
        segments, start = [], 1
        order = VIEWS
        for k, view in enumerate(order):
            repeats = LOOPS if CLIP_LOOPS[clip] else 1
            length = cycle * repeats
            strip(actions[clip], start, repeats, k % 2)
            yaw = OPTS['--runtime-yaw'] if (view == 'runtime' and clip in LOCOMOTION) else 0.0
            segments.append((start, start + round(length) - 1, cams[view], yaw, speed))
            start += round(length)
        # One file per view segment keeps the burned-in label exact for that view.
        paths = []
        for (s0, s1, cam, yaw, spd), view in zip(segments, order):
            note = f'{LABEL} | {clip} | {view} | {LOOPS if CLIP_LOOPS[clip] else 1} plays | treadmill {spd:.3f} u/s'
            path = OUT / f'{clip}_{view}.mp4'
            paths.append(render(path, [(s0, s1, cam, yaw, spd)], note, cycle))
        produced[clip] = paths
    if COMBINED:
        arm.animation_data.action = None
        for track in list(arm.animation_data.nla_tracks):
            arm.animation_data.nla_tracks.remove(track)
        segments, start, notes = [], 1, []
        blend = 6
        for k, clip in enumerate(COMBINED):
            cycle = clip_frames(clip)
            loops = (2 if cycle > 2 * FPS else 3) if CLIP_LOOPS[clip] else 1
            s = strip(actions[clip], start, 1 if cycle > 6 * FPS else loops, k % 2)
            if cycle > 6 * FPS:  # a long idle: show its first 5 s
                loops = 1
                s.action_frame_end = s.action_frame_start + 5 * src_fps
                s.scale = scale
                cycle = 5 * FPS
            s.blend_in = blend if k else 0
            s.blend_out = blend if k < len(COMBINED) - 1 else 0
            length = round(cycle * loops)
            segments.append((start, start + length - 1, cams['three-quarter'], 0.0, SPEEDS.get(clip, 0.0)))
            notes.append(f'{clip} x{loops}')
            start += length - blend
        path = OUT / 'combined_three-quarter.mp4'
        produced['combined'] = [render(path, segments, f'{LABEL} | ' + ' -> '.join(notes))]
    (OUT / 'videos.json').write_text(json.dumps({'glb': str(GLB), 'floor_z': floor_z, 'H': H, 'fps': FPS,
                                                 'loops': LOOPS, 'speeds': SPEEDS, 'videos': produced,
                                                 'stills': still_paths}, indent=1))
    print('RENDER_DONE', json.dumps(produced))


if __name__ == '__main__':
    initialize(sys.argv[sys.argv.index('--') + 1:])
    if bpy is None:
        raise SystemExit('Run with Blender --background --python')
    main()
