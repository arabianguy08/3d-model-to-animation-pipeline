"""Persisted GLB contact, motion and crossing review."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from companion_animation.locomotion.metrics import THRESHOLDS, unwrap, rng, corr, lag_seconds, angle_between, rel_rot_angle, gates, stance_slide
import hashlib
import json
import math

try:
    import bpy
except ImportError:
    bpy = None
import numpy as np
if bpy is not None:
    from mathutils import Matrix, Vector
    from mathutils.bvhtree import BVHTree

def initialize(argv):
    import argparse
    global GLB, OUT, OPTS, HZ, SPEEDS, CUSTOM_NAMES, LIMITS
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('model')
    parser.add_argument('output')
    parser.add_argument('--clips', default='')
    parser.add_argument('--speed', default='')
    parser.add_argument('--hz', type=float, default=120.)
    parser.add_argument('--crossing-hz', type=float, default=30.)
    parser.add_argument('--roles', help='JSON actual bone name to canonical anatomical role')
    parser.add_argument('--thresholds', help='JSON numeric threshold overrides, recorded in report')
    args = parser.parse_args(argv)
    if not math.isfinite(args.hz) or args.hz <= 0 or not math.isfinite(args.crossing_hz) or args.crossing_hz <= 0:
        parser.error('Positive finite sampling rates required')
    GLB, OUT, HZ = Path(args.model), Path(args.output), args.hz
    OPTS = {'--clips': args.clips, '--crossing-hz': args.crossing_hz}
    SPEEDS = {k: float(v) for k, v in (x.split('=') for x in args.speed.split(',') if x)}
    if any(not math.isfinite(v) or v < 0 for v in SPEEDS.values()):
        parser.error('Finite nonnegative speeds required')
    CUSTOM_NAMES = json.loads(Path(args.roles).read_text()) if args.roles else {}
    overrides = json.loads(Path(args.thresholds).read_text()) if args.thresholds else {}
    if any(k not in THRESHOLDS or not math.isfinite(v) or v < 0 for k, v in overrides.items()):
        parser.error('Unknown or invalid threshold')
    LIMITS = {**THRESHOLDS, **overrides}


FORWARD, RIGHT = np.array([0.0, -1.0, 0.0]), np.array([-1.0, 0.0, 0.0])  # characters face -Y, Left = +X
CUSTOM_NAMES = {}


CC_MAIN = {'Hip': 'Hips', 'Waist': 'Spine', 'Spine01': 'Spine1', 'Spine02': 'Spine2', 'NeckTwist01': 'Neck',
           'NeckTwist02': 'Neck1', 'Head': 'Head'}
CC_SIDE = {'Clavicle': 'Shoulder', 'Upperarm': 'Arm', 'Forearm': 'ForeArm', 'Hand': 'Hand', 'Thigh': 'UpLeg',
           'Calf': 'Leg', 'Foot': 'Foot', 'ToeBase': 'ToeBase'}


def canon(name):
    """Mixamo-style anatomical name for Mixamo and Reallusion CC_Base rigs (other names unchanged)."""
    if name in CUSTOM_NAMES:
        return CUSTOM_NAMES[name]
    for prefix in ('mixamorig:', 'mixamorig'):
        if name.startswith(prefix):
            return name[len(prefix):]
    if name.startswith('CC_Base_'):
        rest = name[len('CC_Base_'):]
        if rest in CC_MAIN:
            return CC_MAIN[rest]
        if rest[:2] in ('L_', 'R_'):
            side = 'Left' if rest[0] == 'L' else 'Right'
            if rest[2:] in CC_SIDE:
                return side + CC_SIDE[rest[2:]]
            return side + rest[2:]
    return name


def region(name):
    """Weight region of a bone: twist/share helpers and toes count as their limb segment."""
    c = canon(name)
    for side in ('Left', 'Right'):
        if c.startswith(side):
            body = c[len(side):]
            for key, part in (('ThighTwist', 'UpLeg'), ('KneeShare', 'Leg'), ('CalfTwist', 'Leg'),
                              ('ToeBaseShare', 'ToeBase'), ('UpperarmTwist', 'Arm'), ('ElbowShare', 'ForeArm'),
                              ('ForearmTwist', 'ForeArm')):
                if body.startswith(key):
                    return side + part
            if body.endswith('Toe1') or body.endswith('Toe_End') or body.endswith('Toe_end'):
                return side + 'Toe_End'
    return c


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(GLB))
    arms = [o for o in bpy.context.scene.objects if o.type == 'ARMATURE']
    if len(arms) != 1:
        raise SystemExit(f'expected one armature, got {[a.name for a in arms]}')
    arm = arms[0]
    skinned = [o for o in bpy.context.scene.objects if o.type == 'MESH'
               and any(m.type == 'ARMATURE' and m.object == arm for m in o.modifiers)]
    return arm, skinned


def clip_actions():
    """glTF clip name -> (action, [(animation_data, slot)]) from the importer's NLA ownership."""
    owners = {}
    for obj in bpy.context.scene.objects:
        for data in (obj, obj.data.shape_keys if obj.type == 'MESH' else None):
            if data is None or not data.animation_data:
                continue
            for track in data.animation_data.nla_tracks:
                for strip in track.strips:
                    owners.setdefault(strip.action, []).append((data, strip.action_slot))
    return owners


def bind(owners, action):
    for obj in bpy.context.scene.objects:
        for data in (obj, obj.data.shape_keys if obj.type == 'MESH' else None):
            if data is None or not data.animation_data:
                continue
            data.animation_data.action = None
            data.animation_data.use_nla = False
    for data, slot in owners[action]:
        data.animation_data.action = action
        data.animation_data.action_slot = slot
    bpy.context.view_layer.update()


def clip_name(action):
    # The importer names a clip's action after the glTF animation, possibly with a suffix.
    return action.name


def frame(value):
    bpy.context.scene.frame_set(math.floor(value), subframe=value - math.floor(value))


def points(objs):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    out = []
    for obj in objs:
        ev = obj.evaluated_get(depsgraph)
        mesh = ev.to_mesh()
        co = np.empty(len(mesh.vertices) * 3)
        mesh.vertices.foreach_get('co', co)
        co = co.reshape(-1, 3)
        m = np.array(ev.matrix_world)
        out.append(co @ m[:3, :3].T + m[:3, 3])
        ev.to_mesh_clear()
    return np.concatenate(out)


def triangles(objs):
    tris, offset = [], 0
    for obj in objs:
        mesh = obj.data
        mesh.calc_loop_triangles()
        t = np.empty(len(mesh.loop_triangles) * 3, dtype=np.int64)
        mesh.loop_triangles.foreach_get('vertices', t)
        tris.append(t.reshape(-1, 3) + offset)
        offset += len(mesh.vertices)
    return np.concatenate(tris)


def dominant_bones(objs, arm):
    names = []
    for obj in objs:
        groups = {g.index: g.name for g in obj.vertex_groups}
        for v in obj.data.vertices:
            best = max(v.groups, key=lambda g: g.weight, default=None)
            names.append(region(groups[best.group]) if best else '')
    return np.array(names)


def bone_worlds(arm):
    mw = arm.matrix_world
    return {canon(pb.name): np.array(mw @ pb.matrix) for pb in arm.pose.bones}


def roles_of(arm):
    """Anatomical role -> canonical bone name, from hierarchy (Hips->Head chain)."""
    bones = {canon(b.name): canon(b.parent.name) if b.parent else None for b in arm.data.bones}
    lower = {k.lower(): k for k in bones}
    head = lower.get('head')
    chain, node = [], bones.get(head)
    while node and node.lower() != 'hips':
        chain.append(node)
        node = bones[node]
    chain = chain[::-1]
    chest = bones.get(lower.get('leftshoulder'))  # the clavicles hang from the chest
    neck = chain[chain.index(chest) + 1] if chest in chain and chain.index(chest) + 1 < len(chain) else chain[-1]
    roles = {'pelvis': lower.get('hips'), 'head': head, 'neck': neck, 'chest': chest, 'spine_chain': chain}
    for side in ('Left', 'Right'):
        for part in ('UpLeg', 'Leg', 'Foot', 'ToeBase', 'Shoulder', 'Arm', 'ForeArm', 'Hand'):
            roles[side + part] = lower.get((side + part).lower())
    roles['tail_chain'] = sorted([k for k in bones if 'tail' in k.lower()], key=lambda k: depth(bones, k))
    roles['ear_chains'] = sorted([k for k in bones if 'ear' in k.lower() and 'forearm' not in k.lower()
                                  and 'head' not in k.lower()])
    return roles


def depth(bones, name):
    d = 0
    while bones.get(name):
        name, d = bones[name], d + 1
    return d


def yaw_pitch_roll(m):
    r = m[:3, :3] / np.linalg.norm(m[:3, :3], axis=0)
    yaw = math.degrees(math.atan2(r[0, 1], r[1, 1]))
    pitch = math.degrees(math.asin(max(-1, min(1, r[2, 1]))))
    roll = math.degrees(math.atan2(-r[2, 0], r[2, 2]))
    return yaw, pitch, roll


def penetration(P, tris, mask_a, mask_b, exclude_mask):
    """Intersecting triangle pairs between surface region A and region B of the deformed mesh.

    The characters are fused single-shell meshes, so 'inside' tests are meaningless at the shared
    skin; actual interpenetration shows up as triangles of one region crossing the other's.
    Pairs sharing a vertex (or a coincident seam vertex) are adjacency, not intersection."""
    fa = tris[mask_a[tris].all(axis=1) & ~exclude_mask[tris].any(axis=1)]
    fb = tris[mask_b[tris].all(axis=1) & ~exclude_mask[tris].any(axis=1)]
    if not len(fa) or not len(fb):
        return 0, 0
    verts = [Vector(p) for p in P]
    ta = BVHTree.FromPolygons(verts, fa.tolist(), all_triangles=True)
    tb = BVHTree.FromPolygons(verts, fb.tolist(), all_triangles=True)
    pairs, involved = 0, set()
    for i, j in ta.overlap(tb):
        a, b = fa[i], fb[j]
        if set(a.tolist()) & set(b.tolist()):
            continue
        if np.min(np.linalg.norm(P[a][:, None, :] - P[b][None, :, :], axis=2)) < 1e-6:
            continue
        pairs += 1
        involved.add(i)
    return pairs, len(involved)


def main():
    arm, meshes = load()
    scene = bpy.context.scene
    fps = scene.render.fps / scene.render.fps_base
    owners = clip_actions()
    import struct
    raw = GLB.read_bytes()
    gltf = json.loads(raw[20:20 + struct.unpack('<I', raw[12:16])[0]])
    gltf_order = [a.get('name') for a in gltf.get('animations', [])]
    by_name = {}
    for a in owners:
        for name in gltf_order:
            if a.name == name or a.name.startswith(name + '_'):
                by_name.setdefault(name, a)
    order = [by_name[name] for name in gltf_order if name in by_name]
    wanted = [c for c in OPTS.get('--clips', '').split(',') if c]
    if any(name not in by_name for name in wanted) or not order:
        raise ValueError('Requested clip is absent or unbound')
    roles = roles_of(arm)
    dom = dominant_bones(meshes, arm)
    tris = triangles(meshes)
    # Rest pose and runtime floor (the app grounds the idle's first frame).
    for a in order:
        for data, _ in owners[a]:
            data.animation_data.action = None
            data.animation_data.use_nla = False
    arm.data.pose_position = 'REST'
    bpy.context.view_layer.update()
    rest = points(meshes)
    rest_bones = bone_worlds(arm)
    arm.data.pose_position = 'POSE'
    H = float(rest[:, 2].max() - rest[:, 2].min())
    # The app grounds its idle (the first clip that is not the walk) at its first frame.
    names_present = [n for n in gltf_order if n in by_name]
    idle_name = names_present[0]
    bind(owners, by_name[idle_name])
    frame(by_name[idle_name].frame_range[0])
    idle_pts = points(meshes)
    runtime_floor = float(idle_pts[:, 2].min())  # the app grounds the idle's bounding-box bottom
    side_parts = {s: {s + p for p in ('Foot', 'ToeBase', 'Toe_End')} for s in ('Left', 'Right')}
    soles = {}
    for s, parts in side_parts.items():
        mask = np.isin(dom, list(parts))
        if not mask.any():
            continue
        low = rest[mask, 2].min()
        soles[s] = np.nonzero(mask & (rest[:, 2] < low + 0.03 * H))[0]
    # Feet are measured against their own ground: the idle's lowest sole vertex.
    sole_any = np.isin(dom, [s + p for s in ('Left', 'Right') for p in ('Foot', 'ToeBase', 'Toe_End')])
    floor = float(idle_pts[sole_any, 2].min()) if sole_any.any() else runtime_floor
    arm_mask = {s: np.array([d.startswith(s) and any(k in d for k in ('Arm', 'Hand')) for d in dom]) for s in ('Left', 'Right')}
    torso_mask = np.array([d in {roles['pelvis'], roles['chest'], 'Pelvis', *roles['spine_chain']} and d != roles['neck'] for d in dom])
    leg_mask = {s: np.array([d in {s + 'UpLeg', s + 'Leg'} for d in dom]) for s in ('Left', 'Right')}
    # Exclude skin next to the joint where blended weights legitimately overlap.
    near = {}
    for s in ('Left', 'Right'):
        shoulder = rest_bones[roles[s + 'Arm']][:3, 3]
        upper = np.linalg.norm(rest_bones[roles[s + 'ForeArm']][:3, 3] - shoulder)
        near[s] = np.linalg.norm(rest - shoulder, axis=1) < 0.6 * upper
        hip = rest_bones[roles[s + 'UpLeg']][:3, 3]
        near[s + 'leg'] = np.linalg.norm(rest - hip, axis=1) < 0.35 * np.linalg.norm(
            rest_bones[roles[s + 'Leg']][:3, 3] - hip)

    def pen_all(P):
        out = {}
        for s in ('Left', 'Right'):
            out[f'{s}_arm_x_torso'] = penetration(P, tris, arm_mask[s], torso_mask, near[s])
        out['left_leg_x_right_leg'] = penetration(P, tris, leg_mask['Left'], leg_mask['Right'], near['Leftleg'] | near['Rightleg'])
        return out

    baseline = pen_all(rest)
    result = {'glb': str(GLB), 'glb_sha256': sha256(GLB), 'glb_bytes': GLB.stat().st_size,
              'clean_scene_reimport': True, 'blender': bpy.app.version_string, 'scene_fps': fps,
              'body_height_H': H, 'floor_z_idle_frame0': floor, 'runtime_floor_z': runtime_floor,
              'sole_floor_above_runtime_floor': floor - runtime_floor, 'rest_min_z': float(rest[:, 2].min()),
              'clip_order': gltf_order, 'first_clip': gltf_order[0], 'floor_clip': idle_name, 'thresholds': LIMITS,
              'roles': roles, 'sole_vertices': {s: int(len(v)) for s, v in soles.items()},
              'rest_intersections_baseline': {k: {'pairs': v[0], 'triangles': v[1]} for k, v in baseline.items()},
              'clips': {}}
    for name, action in zip([n for n in gltf_order if n in by_name], order):
        if wanted and name not in wanted:
            continue
        metadata = next(a for a in gltf['animations'] if a['name'] == name).get('extras', {})
        bind(owners, action)
        f0, f1 = action.frame_range
        duration = (f1 - f0) / fps
        n = max(2, int(round(duration * HZ)) + 1)
        times = np.linspace(0, duration, n)
        dt = times[1] - times[0]
        P_all, bones, arm_world = [], [], []
        for t in times:
            frame(f0 + t * fps)
            P_all.append(points(meshes))
            bones.append(bone_worlds(arm))
            arm_world.append(list(arm.matrix_world.translation))
        P_all = np.array(P_all)
        rel_floor = P_all[:, :, 2] - floor
        band = LIMITS['contact_band_fraction_H'] * H
        clip = {'duration_s': duration, 'samples': n, 'sample_hz': HZ, 'frame_range': [f0, f1]}
        # --- contact / slide
        contact = {}
        vel_samples = []
        for s, idx in soles.items():
            z = rel_floor[:, idx]
            inside = z < band
            contact[s] = inside.any(axis=1)
            steps = (P_all[1:, idx, :2] - P_all[:-1, idx, :2]) / dt
            both = inside[1:] & inside[:-1]
            vel_samples.append(steps[both])
        vel_samples = np.concatenate(vel_samples) if vel_samples else np.zeros((0, 2))
        est = np.median(vel_samples, axis=0) if len(vel_samples) else np.zeros(2)
        authored = SPEEDS.get(name, metadata.get('treadmillSpeed'))
        if authored is not None:
            direction = est / np.linalg.norm(est) if np.linalg.norm(est) > 1e-9 else np.array([0, 1.0])
            tread = direction * authored
        else:
            tread = est if np.linalg.norm(est) > 0.02 * H else np.zeros(2)
        clip['treadmill_velocity_estimated'] = est.tolist()
        clip['treadmill_speed_estimated'] = float(np.linalg.norm(est))
        clip['treadmill_speed_used'] = float(np.linalg.norm(tread))
        clip['treadmill_speed_authored'] = authored
        slide_stats = {}
        plant = LIMITS['planted_band_fraction_H'] * H
        for s, idx in soles.items():
            z = rel_floor[:, idx]
            bearing = stance_slide(P_all[:, idx], floor, LIMITS['bearing_band_fraction_H'] * H, tread, dt)
            near_floor = stance_slide(P_all[:, idx], floor, plant, tread, dt)
            bearing_slide = bearing['slide']
            planted = z < plant
            dev = (P_all[1:, idx, :2] - P_all[:-1, idx, :2]) - tread * dt
            both = planted[1:] & planted[:-1]
            speeds = np.linalg.norm(dev, axis=2)[both] / dt
            worst, worst_path = 0.0, 0.0
            for v in range(len(idx)):
                acc, path = np.zeros(2), 0.0
                for k in range(len(both)):
                    if both[k, v]:
                        acc += dev[k, v]
                        path += float(np.linalg.norm(dev[k, v]))
                        worst = max(worst, float(np.linalg.norm(acc)))
                        worst_path = max(worst_path, path)
                    else:
                        acc, path = np.zeros(2), 0.0
            lowest = z.min(axis=1)
            in_contact = lowest < band
            swing_low = lowest[~in_contact]
            slide_stats[s] = {'duty_factor': float(in_contact.mean()), 'max_stance_slide': worst,
                              'max_bearing_slide': bearing_slide,
                              'bearing_support_steps': bearing['support_steps'],
                              'near_floor_support_steps': near_floor['support_steps'],
                              'max_stance_slide_path': worst_path,
                              'slide_speed_p95': float(np.percentile(speeds, 95)) if len(speeds) else 0.0,
                              'slide_speed_max': float(speeds.max()) if len(speeds) else 0.0,
                              'min_sole_z_rel_floor': float(z.min()),
                              'max_lowest_z_during_contact': float(lowest[in_contact].max()) if in_contact.any() else None,
                              'max_swing_clearance': float(swing_low.max()) if len(swing_low) else 0.0}
            contact[s] = in_contact
        both_c = contact.get('Left', np.zeros(n, bool)) & contact.get('Right', np.zeros(n, bool))
        none_c = ~contact.get('Left', np.zeros(n, bool)) & ~contact.get('Right', np.zeros(n, bool))
        clip['contact'] = {'per_foot': slide_stats, 'double_support_fraction': float(both_c.mean()),
                           'flight_fraction': float(none_c.mean()),
                           'min_any_vertex_z_rel_floor': float(rel_floor.min())}
        # --- seams, pops, root drift
        seam = float(np.linalg.norm(P_all[0] - P_all[-1], axis=1).max())
        steps = np.linalg.norm(P_all[1:] - P_all[:-1], axis=2).max(axis=1)
        seam_step = float(np.linalg.norm(P_all[1] - P_all[0], axis=1).max())
        clip['loop_seam_vertex'] = seam
        clip['pop_ratio'] = float(steps.max() / max(np.percentile(steps, 99), 1e-12))
        clip['seam_step_ratio'] = float(seam_step / max(np.median(steps), 1e-12))
        pelvis = np.array([b[roles['pelvis']][:3, 3] for b in bones])
        clip['root_drift'] = {'hips_net_horizontal': float(np.linalg.norm(pelvis[-1, :2] - pelvis[0, :2])),
                              'hips_mean_offset_from_rest': (pelvis[:, :2].mean(axis=0) - rest_bones[roles['pelvis']][:2, 3]).tolist(),
                              'armature_object_translation_range': float(np.ptp(np.array(arm_world), axis=0).max())}
        clip['pelvis_bob_range'] = rng(pelvis[:, 2])
        # --- knees
        knees = {}
        for s in ('Left', 'Right'):
            hip = np.array([b[roles[s + 'UpLeg']][:3, 3] for b in bones])
            knee = np.array([b[roles[s + 'Leg']][:3, 3] for b in bones])
            ankle = np.array([b[roles[s + 'Foot']][:3, 3] for b in bones])
            right_axis = np.array([b[roles['pelvis']][:3, 0] for b in bones])
            fwd = np.array([-b[roles['pelvis']][:3, 2] for b in bones])
            flex = [180 - angle_between(hip[i] - knee[i], ankle[i] - knee[i]) for i in range(n)]
            # Knee in front of the hip-ankle line (toward facing -Y in the rest frame).
            facing = rest_bones[roles[s + 'ToeBase']][:3, 3] - rest_bones[roles[s + 'Foot']][:3, 3]
            facing[2] = 0
            facing /= np.linalg.norm(facing)
            sign, poles = [], []
            for i in range(n):
                line = ankle[i] - hip[i]
                off = knee[i] - hip[i] - (knee[i] - hip[i]) @ line / (line @ line) * line
                fwd_i = -np.array(bones[i][roles['pelvis']][:3, :3] @ (np.linalg.inv(rest_bones[roles['pelvis']][:3, :3]) @ np.array([0, 1.0, 0])))
                fwd_i[2] = 0
                fwd_i /= max(np.linalg.norm(fwd_i), 1e-9)
                sign.append(float(off @ fwd_i))
                poles.append(off / max(np.linalg.norm(off), 1e-12))
            pole_steps = [angle_between(poles[i], poles[i + 1]) for i in range(n - 1)]
            knees[s] = {'flex_deg_min': float(np.min(flex)), 'flex_deg_max': float(np.max(flex)),
                        'max_flex_step_deg': float(np.max(np.abs(np.diff(flex)))),
                        'forward_knee_offset_min': float(np.min(sign)),
                        'max_bend_plane_step_deg': float(max(pole_steps)),
                        'bend_direction_flips': int(np.sum(np.array(pole_steps) > 90))}
        clip['knees'] = knees
        # --- interpenetration (30 Hz subset), triangle crossings beyond the rest pose's own
        step = max(1, int(round(HZ / OPTS['--crossing-hz'])))
        worst = {}
        for i in sorted(set(range(0, n, step)) | {n - 1}):
            for key, (pairs, tri_count) in pen_all(P_all[i]).items():
                cur = worst.setdefault(key, {'max_pairs': 0, 'max_triangles': 0, 'time_s': None,
                                             'rest_pairs': baseline[key][0]})
                if tri_count > cur['max_triangles']:
                    cur.update(max_triangles=tri_count, max_pairs=pairs, time_s=float(times[i]))
        for key, cur in worst.items():
            cur['excess_triangles_over_rest'] = cur['max_triangles'] - baseline[key][1]
        clip['intersections'] = worst
        # --- motion principles
        ypr = {}
        for r in ('pelvis', 'chest', 'head'):
            R0 = rest_bones[roles[r]][:3, :3] / np.linalg.norm(rest_bones[roles[r]][:3, :3], axis=0)
            f_loc, r_loc = R0.T @ FORWARD, R0.T @ RIGHT
            rows = []
            for b in bones:
                Rb = b[roles[r]][:3, :3] / np.linalg.norm(b[roles[r]][:3, :3], axis=0)
                f, rt = Rb @ f_loc, Rb @ r_loc
                rows.append((math.degrees(math.atan2(f[0], -f[1])), math.degrees(math.asin(max(-1, min(1, f[2])))),
                             math.degrees(math.asin(max(-1, min(1, rt[2]))))))
            rows = np.array(rows)
            ypr[r] = np.stack([unwrap(rows[:, j]) for j in range(3)], axis=1)
        chest_rel_yaw = ypr['chest'][:, 0] - ypr['pelvis'][:, 0]
        hip_line = np.array([b[roles['RightUpLeg']][:3, 3] - b[roles['LeftUpLeg']][:3, 3] for b in bones])
        sh_line = np.array([b[roles['RightArm']][:3, 3] - b[roles['LeftArm']][:3, 3] for b in bones])
        hip_yaw = unwrap(np.degrees(np.arctan2(hip_line[:, 1], hip_line[:, 0])))
        sh_yaw = unwrap(np.degrees(np.arctan2(sh_line[:, 1], sh_line[:, 0])))
        head_rel = [rel_rot_angle(b[roles['chest']], b[roles['head']]) for b in bones]
        head_rel = np.array(head_rel) - min(head_rel)
        def pitch_of(a, b_):
            v = b_ - a
            return math.degrees(math.atan2(-v[1], -v[2]))
        arm_pitch = {s: np.array([pitch_of(b[roles[s + 'Arm']][:3, 3], b[roles[s + 'ForeArm']][:3, 3]) for b in bones]) for s in ('Left', 'Right')}
        leg_pitch = {s: np.array([pitch_of(b[roles[s + 'UpLeg']][:3, 3], b[roles[s + 'Leg']][:3, 3]) for b in bones]) for s in ('Left', 'Right')}
        elbow = {s: np.array([angle_between(b[roles[s + 'ForeArm']][:3, 3] - b[roles[s + 'Arm']][:3, 3],
                                            b[roles[s + 'Hand']][:3, 3] - b[roles[s + 'ForeArm']][:3, 3]) for b in bones]) for s in ('Left', 'Right')}
        head_z = np.array([b[roles['head']][2, 3] for b in bones])
        loop = metadata.get('loop', False)
        principles = {
            'pelvis_range_deg': {'yaw': rng(ypr['pelvis'][:, 0]), 'pitch': rng(ypr['pelvis'][:, 1]), 'roll': rng(ypr['pelvis'][:, 2])},
            'chest_vs_pelvis_yaw_range_deg': rng(chest_rel_yaw),
            'shoulder_line_vs_hip_line_yaw_corr': corr(sh_yaw, hip_yaw),
            'shoulder_line_yaw_range_deg': rng(sh_yaw), 'hip_line_yaw_range_deg': rng(hip_yaw),
            'head_vs_chest_rotation_range_deg': rng(head_rel),
            'head_world_rotation_range_deg': {k: rng(ypr['head'][:, j]) for j, k in enumerate(('yaw', 'pitch', 'roll'))},
            'chest_world_rotation_range_deg': {k: rng(ypr['chest'][:, j]) for j, k in enumerate(('yaw', 'pitch', 'roll'))},
            'arm_swing_range_deg': {s: rng(arm_pitch[s]) for s in arm_pitch},
            'arm_vs_same_side_thigh_corr': {s: corr(arm_pitch[s], leg_pitch[s]) for s in arm_pitch},
            'elbow_flex_range_deg': {s: rng(elbow[s]) for s in elbow},
            'lag_s': {'pelvis_bob_to_head_bob': lag_seconds(pelvis[:, 2], head_z, dt, loop),
                      'pelvis_yaw_to_chest_yaw': lag_seconds(ypr['pelvis'][:, 0], ypr['chest'][:, 0], dt, loop),
                      'chest_yaw_to_head_yaw': lag_seconds(ypr['chest'][:, 0], ypr['head'][:, 0], dt, loop),
                      'thigh_to_opposite_arm': {s: lag_seconds(leg_pitch[s], arm_pitch['Right' if s == 'Left' else 'Left'], dt, loop) for s in leg_pitch}},
        }
        for chain_name, chain in (('tail', roles['tail_chain']), ('ears', roles['ear_chains'])):
            if chain:
                angles = [[rel_rot_angle(rest_bones[c], b[c]) for b in bones] for c in chain]
                principles[f'{chain_name}_rotation_range_deg'] = {c: rng(a) for c, a in zip(chain, angles)}
        clip['motion_principles'] = principles
        clip['loop'] = loop
        clip['gait'] = metadata.get('gait', 'other')
        clip['gates'] = gates(name, clip, H, LIMITS)
        result['clips'][name] = clip
    result['status'] = 'pass' if result['clips'] and all(g['pass'] for c in result['clips'].values() for g in c['gates'].values()) else 'fail'
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=1, default=float), encoding='utf-8')
    print('QA_DONE', OUT)
    if result['status'] != 'pass':
        raise SystemExit(1)


def _curves(action):
    if hasattr(action, 'fcurves') and len(getattr(action, 'fcurves', [])):
        yield from action.fcurves
    for layer in getattr(action, 'layers', []):
        for strip in layer.strips:
            for bag in strip.channelbags:
                yield from bag.fcurves


if __name__ == '__main__':
    initialize(sys.argv[sys.argv.index('--') + 1:])
    if bpy is None:
        raise SystemExit('Run with Blender --background --python')
    main()
