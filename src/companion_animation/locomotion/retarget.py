"""Anatomical biped retarget and rolling skinned-sole contact adapter."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from companion_animation.locomotion.anatomy import anatomical_names, canon
from companion_animation.locomotion.math_core import two_bone, skinned_pin_step, treadmill
import json
import math

try:
    import bpy
except ImportError:
    bpy = None
import numpy as np
if bpy is not None:
    from mathutils import Matrix, Quaternion, Vector
    from mathutils.bvhtree import BVHTree

def initialize(job_path):
    global JOB, CONFIG, CONFIG_DIR, OUT, BA, PARAMS, MOTION
    if bpy is None:
        raise RuntimeError('Run this adapter with Blender --background --python')
    JOB = json.loads(Path(job_path).read_text(encoding='utf-8'))
    CONFIG = JOB['config']
    CONFIG_DIR = Path(JOB['config_directory'])
    OUT = Path(JOB['output_directory'])
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'pipeline'))
    import blender_adapter
    BA = blender_adapter
    MOTION = CONFIG['motion']
    parameter_path = Path(MOTION['adapter']['parameters'])
    if not parameter_path.is_absolute():
        parameter_path = CONFIG_DIR / parameter_path
    PARAMS = json.loads(parameter_path.read_text(encoding='utf-8'))

ARM_CHAIN = ('Arm', 'ForeArm', 'Hand')  # swing-aligned (rest pose differs: T vs A)
LEGS = (('Left', 'LeftUpLeg', 'LeftLeg', 'LeftFoot'), ('Right', 'RightUpLeg', 'RightLeg', 'RightFoot'))


def rot3(m):
    r = np.array(m.to_3x3(), dtype=np.float64)
    u, _, vt = np.linalg.svd(r)
    return u @ vt


def swing(a, b):
    """Minimal rotation (3x3) taking unit vector a onto b."""
    a, b = a / np.linalg.norm(a), b / np.linalg.norm(b)
    v, c = np.cross(a, b), float(np.dot(a, b))
    if c < -0.999999:
        axis = np.cross(a, [1, 0, 0])
        if np.linalg.norm(axis) < 1e-6:
            axis = np.cross(a, [0, 1, 0])
        axis /= np.linalg.norm(axis)
        return np.array(Matrix.Rotation(math.pi, 3, Vector(axis)))
    vx = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + vx + vx @ vx / (1 + c)


def axis_angle_matrix(axis, angle):
    return np.array(Matrix.Rotation(angle, 3, Vector(axis)))




# ---------------------------------------------------------------- source sampling
def sample_source(path):
    """Evaluate source world matrices at author_hz, preserving the exact interval."""
    scene = bpy.context.scene
    fps, base = scene.render.fps, scene.render.fps_base
    before = set(bpy.data.objects)
    actions_before = set(bpy.data.actions)
    bpy.ops.import_scene.fbx(filepath=str(path), automatic_bone_orientation=False)
    new = [o for o in bpy.data.objects if o not in before]
    arm = [o for o in new if o.type == 'ARMATURE']
    if len(arm) != 1:
        raise BA.NeedsInput(f'{path.name}: expected one source armature, got {[o.name for o in arm]}')
    arm = arm[0]
    action = arm.animation_data.action
    src_fps = scene.render.fps / scene.render.fps_base
    f0, f1 = map(float, action.frame_range)
    roles, chain = anatomical_names({b.name: b.parent.name if b.parent else None for b in arm.data.bones}, PARAMS.get('source_roles'))
    rest = {roles[b.name]: np.array(arm.matrix_world @ b.matrix_local) for b in arm.data.bones}
    parents = {roles[b.name]: roles[b.parent.name] if b.parent else None for b in arm.data.bones}
    # Evaluate the source at author_hz (its own curve interpolation between its 30 fps keys) so the
    # authored target keys are dense; their frames land exactly on the pipeline's 24 fps grid
    # every fifth key, so no near-coincident key times collide in Blender's float32 frames.
    rate = PARAMS.get('author_hz', 120)
    per = rate / src_fps
    if abs(per - round(per)) > 1e-9:
        raise BA.NeedsInput(f'author_hz {rate} must be a multiple of the source rate {src_fps}')
    per = int(round(per))
    count = (f1 - f0) * per
    if count <= 0 or abs(count - round(count)) > 1e-5:
        raise BA.NeedsInput('Source duration must fit the author_hz grid; do not round endpoints')
    frames = []
    for j in range(int(round(count)) + 1):
        sample = f0 + j / per
        scene.frame_set(math.floor(sample), subframe=sample - math.floor(sample))
        frames.append({roles[pb.name]: np.array(arm.matrix_world @ pb.matrix) for pb in arm.pose.bones})
    info = {'file': str(path), 'action': action.name, 'frame_range': [f0, f1], 'fps': rate, 'source_fps': src_fps,
            'duration_s': (f1 - f0) / src_fps, 'bones': len(arm.data.bones),
            'source_spine_chain': chain, 'role_map': {canon(k): v for k, v in roles.items() if canon(k) != v}}
    for o in new:
        bpy.data.objects.remove(o, do_unlink=True)
    for a in set(bpy.data.actions) - actions_before:
        bpy.data.actions.remove(a)
    for arm_data in [d for d in bpy.data.armatures if d.users == 0]:
        bpy.data.armatures.remove(arm_data)
    scene.render.fps, scene.render.fps_base = fps, base
    return rest, parents, frames, info


# ---------------------------------------------------------------- target data
def target_data(arm):
    world = np.array(arm.matrix_world)
    rest_arm = {b.name: np.array(b.matrix_local) for b in arm.data.bones}
    rest_world = {n: world @ m for n, m in rest_arm.items()}
    order = []
    def visit(bone):
        order.append(bone.name)
        for child in bone.children:
            visit(child)
    for bone in arm.data.bones:
        if bone.parent is None:
            visit(bone)
    parent = {b.name: b.parent.name if b.parent else None for b in arm.data.bones}
    return world, rest_arm, rest_world, order, parent


def leg_length(rest, prefix_up, prefix_leg, prefix_foot):
    up, leg, foot = (rest[n][:3, 3] for n in (prefix_up, prefix_leg, prefix_foot))
    return float(np.linalg.norm(leg - up) + np.linalg.norm(foot - leg))


# ---------------------------------------------------------------- retarget one clip
def retarget(arm, clip_params, src, feet):
    s_rest, s_parent, s_frames, info = src
    world, rest_arm, rest_world, order, parent = target_data(arm)
    roles, _ = anatomical_names(parent, PARAMS.get('target_roles'))
    by_canon = {roles[n]: n for n in rest_arm}
    mapped = {n: roles[n] for n in rest_arm if roles[n] in s_rest}
    # Offsets O_b so that R_t = R_s @ O_b.
    offsets = {}
    for name, c in mapped.items():
        rs0, rt0 = rot3(Matrix(s_rest[c].tolist())), rot3(Matrix(rest_world[name].tolist()))
        if any(c.endswith(part) and c[:-len(part)] in ('Left', 'Right') for part in ARM_CHAIN):
            align = swing(rt0[:, 1], rs0[:, 1])
        else:
            align = np.eye(3)
        offsets[name] = rs0.T @ align @ rt0
    # Scale from leg chains.
    k_t = np.mean([leg_length(rest_world, by_canon[u], by_canon[l], by_canon[f]) for _, u, l, f in LEGS])
    k_s = np.mean([leg_length(s_rest, u, l, f) for _, u, l, f in LEGS])
    k = k_t / k_s
    n = len(s_frames)
    src_fps = info['fps']
    times = np.arange(n) / src_fps
    duration = times[-1]

    def s_pos(i, c):
        return s_frames[i][c][:3, 3]

    hip_c_s = np.array([(s_pos(i, 'LeftUpLeg') + s_pos(i, 'RightUpLeg')) / 2 for i in range(n)])
    hip_c_s0 = (s_rest['LeftUpLeg'][:3, 3] + s_rest['RightUpLeg'][:3, 3]) / 2
    ankles_s = {side: np.array([s_pos(i, f) for i in range(n)]) for side, _, _, f in LEGS}
    # Treadmill: remove constant-speed travel over the cycle (horizontal only).
    ramp, travel, speed_src = treadmill(hip_c_s, times, enabled=clip_params.get('in_place_travel', False))
    centre = np.zeros(3)
    if clip_params.get('center_horizontal'):
        centre[:2] = (hip_c_s - ramp).mean(axis=0)[:2] - hip_c_s0[:2]
    speed_src = float(np.linalg.norm(travel[:2]) / duration)
    hip_c_t0 = (rest_world[by_canon['LeftUpLeg']][:3, 3] + rest_world[by_canon['RightUpLeg']][:3, 3]) / 2
    hips_name = by_canon['Hips']
    hips_rest = rest_world[hips_name]
    hip_offset_local = np.linalg.inv(hips_rest[:3, :3]) @ (hip_c_t0 - hips_rest[:3, 3])
    # The app grounds the idle's first frame; the bind pose may stand above that floor.
    ground = np.array([0.0, 0.0, feet['floor_z'] - feet['rest_sole_z']])
    secondary = PARAMS.get('secondary', {})

    report = {'k_scale': k, 'source_speed': speed_src, 'treadmill_speed': speed_src * k,
              'mapped_bones': len(mapped), 'unmapped_bones_rest': sorted(n for n in rest_arm if n not in mapped)}
    chest_c = 'Spine2'
    max_reach = PARAMS.get('max_leg_reach', 0.985)

    # ------------------------------------------------ sole contact solve (per foot)
    # Contact timing is the source's (heel / ball heights); the target's own sole points are
    # pinned to the runtime floor and move at exactly the treadmill velocity while in contact.
    # Touchdown starts from the k-scaled source ankle; each pin hands over heel -> ball while the
    # foot is flat; the swing blends the stance offset back to the scaled source path before the
    # next touchdown, so no pin ever jumps.
    tread = -k * travel / duration  # contact velocity in the in-place frame (model units / s)
    dt = 1.0 / src_fps
    c_eps = PARAMS.get('source_contact_height', 0.025)
    v_eps = PARAMS.get('source_contact_speed', 0.3)
    ankle_targets, contact_report, stance_by_side = {}, {}, {}
    for side, up, lo, foot in LEGS:
        ft_n = by_canon[foot]
        Rs0 = rot3(Matrix(s_rest[foot].tolist()))
        A_s0 = s_rest[foot][:3, 3]
        toe_s0 = s_rest[side + 'ToeBase'][:3, 3]
        s_pts = {'heel': Rs0.T @ (np.array([A_s0[0], A_s0[1], 0.0]) - A_s0),
                 'ball': Rs0.T @ (np.array([toe_s0[0], toe_s0[1], 0.0]) - A_s0)}
        t_pts = feet[side]['local']
        floor_z = feet['floor_z']
        A_t0 = rest_world[ft_n][:3, 3] + ground
        A0 = np.array([A_t0 + k * (ankles_s[side][i] - A_s0 - ramp[i] - centre) for i in range(n)])
        R_ft = [rot3(Matrix(s_frames[i][foot].tolist())) @ offsets[ft_n] for i in range(n)]
        heights = {key: np.array([(ankles_s[side][i] + rot3(Matrix(s_frames[i][foot].tolist())) @ s_pts[key])[2]
                                  for i in range(n)]) for key in s_pts}
        # A source point is planted when it is low AND (nearly) still in the source's own
        # root-motion world: a low, fast toe in swing is not a contact.
        world_pts = {key: np.array([ankles_s[side][i] + rot3(Matrix(s_frames[i][foot].tolist())) @ s_pts[key]
                                    for i in range(n)]) for key in s_pts}
        speeds = {}
        for key, pts in world_pts.items():
            v = np.zeros(n)
            v[1:-1] = np.linalg.norm(pts[2:] - pts[:-2], axis=1) / (2 * dt)
            v[0], v[-1] = np.linalg.norm(pts[1] - pts[0]) / dt, np.linalg.norm(pts[-1] - pts[-2]) / dt
            speeds[key] = v
        flags = {key: (heights[key] < c_eps) & (speeds[key] < v_eps) for key in heights}
        cycles = 3 if clip_params.get('loop') else 1  # the middle cycle is periodic on both sides
        total = cycles * (n - 1) + 1 if clip_params.get('loop') else n
        delta = np.zeros((total, 3))
        stance = np.zeros(total, bool)
        anchor, P = None, None
        # Pivot candidates: heel, ball (under the toe joint) and the foot's own sole samples.
        pivots = np.vstack([t_pts['heel'], t_pts['ball'], feet[side]['foot_pts']])
        pivot_switches = 0
        # Contact timing calibrated on the target sole as well: a target sole point that is low
        # and nearly still in the target's own root-motion world (scaled travel restored) is down.
        world_t = np.array([A0[i] + pivots @ R_ft[i].T + k * ramp[i] for i in range(n)])  # (n, m, 3)
        h_t = PARAMS.get('target_contact_height_fraction_of_leg', 0.03) * k_t
        v_t = v_eps * k
        vel_t = np.zeros(world_t.shape[:2])
        vel_t[1:-1] = np.linalg.norm(world_t[2:] - world_t[:-2], axis=2) / (2 * dt)
        vel_t[0], vel_t[-1] = vel_t[1], vel_t[-2]
        target_down = ((world_t[:, :, 2] - floor_z < h_t) & (vel_t < v_t)).any(axis=1)
        for g in range(total):
            i = g % (n - 1) if clip_params.get('loop') else g
            R = R_ft[i]
            down = bool(flags['heel'][i] or flags['ball'][i] or target_down[i])
            if not down:
                anchor = None
                continue
            if anchor is None:
                anchor = int(np.argmin((A0[i] + pivots @ R.T)[:, 2]))
                P = A0[i] + R @ pivots[anchor]
                P[2] = floor_z
            else:
                P = P + tread * dt
                # Rolling without slipping: when another sole point reaches the floor, the foot
                # pivots about that point from now on (heel strike -> flat -> ball -> toe-off).
                A_now = P - R @ pivots[anchor]
                z_all = (A_now + pivots @ R.T)[:, 2]
                lowest_pivot = int(np.argmin(z_all))
                if z_all[lowest_pivot] < floor_z - 1e-4 * k_t:
                    P = A_now + R @ pivots[lowest_pivot]
                    P[2] = floor_z
                    anchor = lowest_pivot
                    pivot_switches += 1
            delta[g] = (P - R @ pivots[anchor]) - A0[i]
            stance[g] = True
        # Rigid sole samples of the target's own foot and toes (skinning approximated per bone).
        toe_n = by_canon[side + 'ToeBase']
        R_toe = [rot3(Matrix(s_frames[i][side + 'ToeBase'].tolist())) @ offsets[toe_n] for i in range(n)]
        foot_pts, toe_pts, toe_off = feet[side]['foot_pts'], feet[side]['toe_pts'], feet[side]['toe_offset']

        def lowest(A, i):
            z = (A + foot_pts @ R_ft[i].T)[:, 2].min() if len(foot_pts) else np.inf
            if len(toe_pts):
                z = min(z, (A + R_ft[i] @ toe_off + toe_pts @ R_toe[i].T)[:, 2].min())
            return float(z)

        # Stance: the foot rests on its actual lowest sole point (no sinking, no hovering).
        for g in range(total):
            if stance[g]:
                i = g % (n - 1) if clip_params.get('loop') else g
                delta[g, 2] += floor_z - lowest(A0[i] + delta[g], i)
        # Swing: smooth hand-back of the stance offset to the next touchdown's offset.
        g = 0
        while g < total:
            if stance[g]:
                g += 1
                continue
            start = g
            while g < total and not stance[g]:
                g += 1
            before = delta[start - 1] if start > 0 else (delta[g] if g < total else np.zeros(3))
            after = delta[g] if g < total else before
            span = g - start + 1
            for m in range(start, g):
                x = (m - start + 1) / span
                w = x * x * (3 - 2 * x)
                delta[m] = before + (after - before) * w
        # Swing: keep the whole foot above the floor, with a small mid-swing clearance so the
        # step reads as a lift; the extra lift is smoothed and never reaches into stance frames.
        c_max = PARAMS.get('swing_clearance_fraction_of_leg', 0.04) * k_t
        # A swinging foot never scuffs: it clears the floor by at least c_min from lift-off on.
        c_min = PARAMS.get('swing_min_clearance_fraction_of_leg', 0.02) * k_t
        lift = np.zeros(total)
        g = 0
        while g < total:
            if stance[g]:
                g += 1
                continue
            start = g
            while g < total and not stance[g]:
                g += 1
            span = g - start + 1
            need = np.zeros(g - start)
            for m in range(start, g):
                i = m % (n - 1) if clip_params.get('loop') else m
                x = (m - start + 1) / span
                clear = max(c_min, c_max * math.sin(math.pi * x) ** 2)
                need[m - start] = max(0.0, floor_z + clear - lowest(A0[i] + delta[m], i))
            if len(need) > 2:
                kernel = np.array([1, 4, 6, 4, 1], float) / 16
                padded = np.pad(need, 2, mode="edge")
                smooth = np.convolve(padded, kernel, mode='valid')
                need = np.maximum(need, smooth)
            lift[start:g] = need
        delta[:, 2] += lift
        if clip_params.get('loop'):
            last = delta[(n - 1):(n - 1) + n]
            final = np.array([A0[i] + last[i] for i in range(n)])
            seam_mismatch = float(np.linalg.norm(final[-1] - final[0]))
            final[-1] = final[0]
        else:
            final = A0 + delta
            seam_mismatch = float(np.linalg.norm(final[-1] - final[0]))
        ankle_targets[side] = final
        stance_by_side[side] = stance.copy()
        contact_report[side] = {
            'source_heel_height_range': [float(heights['heel'].min()), float(heights['heel'].max())],
            'source_ball_height_range': [float(heights['ball'].min()), float(heights['ball'].max())],
            'source_contact_fraction': float((flags['heel'] | flags['ball']).mean()),
            'source_contact_frames': [int(i) for i in np.nonzero(flags['heel'] | flags['ball'])[0]],
            'target_calibrated_contact_fraction': float((flags['heel'] | flags['ball'] | target_down).mean()),
            'max_stance_offset_from_scaled_path': float(np.linalg.norm(delta, axis=1).max()),
            'ankle_seam_mismatch_before_close': seam_mismatch,
            'max_swing_lift_added': float(lift.max()), 'swing_clearance_target': c_max,
            'rolling_pivot_switches': pivot_switches}
    report['contact_solve'] = {'treadmill_velocity': tread.tolist(), 'floor_z': feet['floor_z'],
                               'source_contact_height': c_eps, 'source_contact_speed': v_eps,
                               'ground_offset': float(ground[2]), 'feet': contact_report}

    arm_offsets = {'Left': np.zeros((n, 3)), 'Right': np.zeros((n, 3))}
    # Bones the source lacks but anatomy needs alive: tails (Mixamo motion has none).
    def depth_of(bone):
        d = 0
        while parent[bone]:
            bone, d = parent[bone], d + 1
        return d
    tail_chain = sorted([b for b in rest_arm if 'tail' in b.lower()], key=depth_of)
    tail_spec = {'lag_s': 0.1, 'gain': 0.85, **secondary.get('tail', {})}
    loop_clip = bool(clip_params.get('loop'))
    hips_series = [rot3(Matrix(s_frames[i]['Hips'].tolist())) @ offsets[hips_name] for i in range(n)]
    report['tail_follow_through'] = {'bones': tail_chain, **tail_spec} if tail_chain else None

    def solve(drop, clamp):
        """One full pass. drop lowers the whole hips path (model units) so legs stay bent."""
        stats = {'reach': [], 'clamped': 0}
        poses, prev_pole = [], {}
        for i in range(n):
            R = {name: rot3(Matrix(s_frames[i][c].tolist())) @ offsets[name] for name, c in mapped.items()}
            for side_a in ('Left', 'Right'):
                rv = arm_offsets[side_a][i]
                angle = float(np.linalg.norm(rv))
                if angle > 1e-9:
                    Q = axis_angle_matrix(rv / angle, angle)
                    for part in ('Arm', 'ForeArm', 'Hand'):
                        bone = by_canon.get(side_a + part)
                        if bone in R:
                            R[bone] = Q @ R[bone]
            for spec in secondary.get('lag', []):
                bone = by_canon.get(spec['bone'])
                if bone not in R:
                    continue
                lag = int(round(spec['lag_s'] * src_fps))
                j = (i - lag) % (n - 1) if clip_params.get('loop') else max(0, i - lag)
                now = rot3(Matrix(s_frames[i][chest_c].tolist()))
                then = rot3(Matrix(s_frames[j][chest_c].tolist()))
                q = Quaternion(Matrix((then @ now.T).tolist()).to_quaternion())
                q = Quaternion((1, 0, 0, 0)).slerp(q, spec['gain'])
                R[bone] = np.array(q.to_matrix()) @ R[bone]
            target_hip_c = hip_c_t0 + ground + k * (hip_c_s[i] - hip_c_s0 - ramp[i] - centre) - np.array([0, 0, drop])
            hips_pos = target_hip_c - R[hips_name] @ hip_offset_local
            posed_world = {}
            for name in order:
                p = parent[name]
                if p is None:
                    M_rest_rel = rest_world[name]
                    head = hips_pos if name == hips_name else rest_world[name][:3, 3]
                else:
                    M_rest_rel = posed_world[p] @ np.linalg.inv(rest_world[p]) @ rest_world[name]
                    head = M_rest_rel[:3, 3]
                m = np.eye(4)
                m[:3, :3] = R[name] if name in R else M_rest_rel[:3, :3]
                if name in tail_chain and p is not None:
                    # Tail follow-through: each segment trails the pelvis rotation by its own
                    # delay (a travelling wave from base to tip), attenuated toward the tip.
                    k_seg = tail_chain.index(name)
                    lag = int(round(tail_spec['lag_s'] * (k_seg + 1) * src_fps))
                    j = (i - lag) % (n - 1) if loop_clip else max(0, i - lag)
                    E = hips_series[i].T @ hips_series[j]
                    q = Quaternion((1, 0, 0, 0)).slerp(Matrix(E.tolist()).to_quaternion(), tail_spec['gain'])
                    Rp = posed_world[p][:3, :3]
                    m[:3, :3] = Rp @ np.array(q.to_matrix()) @ Rp.T @ M_rest_rel[:3, :3]
                m[:3, 3] = head
                posed_world[name] = m
                for side, up, lo, foot in LEGS:
                    if name != by_canon[up]:
                        continue
                    up_n, lo_n, ft_n = by_canon[up], by_canon[lo], by_canon[foot]
                    o_a = (np.linalg.inv(rest_world[up_n]) @ rest_world[lo_n])[:3, 3]
                    o_b = (np.linalg.inv(rest_world[lo_n]) @ rest_world[ft_n])[:3, 3]
                    a, b = float(np.linalg.norm(o_a)), float(np.linalg.norm(o_b))
                    target = ankle_targets[side][i]
                    H = head
                    knee_fk = H + R[up_n] @ o_a  # bend plane from the rotation transfer
                    d_vec = target - H
                    d = float(np.linalg.norm(d_vec))
                    stats['reach'].append(d / (a + b))
                    if not clamp:
                        continue
                    if d > (a + b) * 0.9995:
                        stats['clamped'] += 1
                        d = (a + b) * 0.9995
                    knee, ankle, pole = two_bone(H, target, knee_fk, a, b, prev_pole.get(side))
                    prev_pole[side] = pole
                    R[up_n] = swing(R[up_n] @ o_a, knee - H) @ R[up_n]
                    R[lo_n] = swing(R[lo_n] @ o_b, ankle - knee) @ R[lo_n]
                    posed_world[name][:3, :3] = R[up_n]
            poses.append(posed_world)
        return poses, stats

    def solve_with_drop():
        _, first = solve(0.0, False)
        drop, low, high = 0.0, 0.0, 0.1 * k_t
        if max(first['reach']) > max_reach:
            for _ in range(18):
                mid = (low + high) / 2
                if max(solve(mid, False)[1]['reach']) > max_reach:
                    low = mid
                else:
                    high = mid
            drop = high
        poses, stats = solve(drop, True)
        return poses, stats, drop, first

    def skinned(poses, side):
        """Linear-blend-skinned sole vertices (the exporter/runtime deformation) per sample."""
        sole = feet[side]['skin']
        out = np.zeros((len(poses), len(sole['rest']), 3))
        for i, posed in enumerate(poses):
            for bone, weights in sole['weights'].items():
                if bone not in posed:
                    continue
                m = posed[bone] @ np.linalg.inv(rest_world[bone])
                out[i] += weights[:, None] * (sole['rest'] @ m[:3, :3].T + m[:3, 3])
        return out

    # The shipped feet carry shin weights on their soles (cross-limb weights up to ~0.4), so a
    # rigid-foot pin still lets the real skinned sole slide. Re-pin on the skinned sole points and
    # re-solve; the correction converges in a few passes because it is nearly a pure translation.
    loop = bool(clip_params.get('loop'))
    total = 3 * (n - 1) + 1 if loop else n
    idx = (lambda g: g % (n - 1)) if loop else (lambda g: g)
    floor_z = feet['floor_z']
    c_max = PARAMS.get('swing_clearance_fraction_of_leg', 0.04) * k_t
    c_min = PARAMS.get('swing_min_clearance_fraction_of_leg', 0.02) * k_t
    passes = []
    for iteration in range(PARAMS.get('skinned_contact_passes', 4)):
        poses, stats, drop, first = solve_with_drop()
        worst = 0.0
        for side in ('Left', 'Right'):
            S = skinned(poses, side)
            stance = stance_by_side[side]
            corr = np.zeros((total, 3))
            band = PARAMS.get('contact_patch_fraction_of_leg', 0.012) * k_t
            prev = None
            for g in range(total):
                i = idx(g)
                if not stance[g]:
                    prev = None
                    continue
                c = skinned_pin_step(S[i], None if prev is None else S[prev[0]],
                                     None if prev is None else prev[1], tread, dt, floor_z, band)
                corr[g] = c
                prev = (i, c)
            # Swing: hand the correction back smoothly, then keep the skinned foot clear.
            g = 0
            while g < total:
                if stance[g]:
                    g += 1
                    continue
                start_g = g
                while g < total and not stance[g]:
                    g += 1
                before = corr[start_g - 1] if start_g > 0 else (corr[g] if g < total else np.zeros(3))
                after = corr[g] if g < total else before
                span = g - start_g + 1
                need = np.zeros(g - start_g)
                for m in range(start_g, g):
                    x = (m - start_g + 1) / span
                    w = x * x * (3 - 2 * x)
                    corr[m] = before + (after - before) * w
                    clear = max(c_min, c_max * math.sin(math.pi * x) ** 2)
                    need[m - start_g] = max(0.0, floor_z + clear - (S[idx(m), :, 2] + corr[m, 2]).min())
                if len(need) > 2:
                    kernel = np.array([1, 4, 6, 4, 1], float) / 16
                    need = np.maximum(need, np.convolve(np.pad(need, 2, mode='edge'), kernel, mode='valid'))
                corr[start_g:g, 2] += need
            cycle = corr[(n - 1):(n - 1) + n] if loop else corr
            cycle = cycle.copy()
            if loop:
                cycle[-1] = cycle[0]
            ankle_targets[side] = ankle_targets[side] + cycle
            worst = max(worst, float(np.linalg.norm(cycle, axis=1).max()))
        passes.append(worst)
        if worst < 2e-5 * k_t:
            break
    poses, stats, drop, first = solve_with_drop()
    # Arm clearance: where a swinging/pumping arm would sink into the (often much rounder)
    # target torso or head, the whole arm chain turns away about the shoulder by the smallest
    # angle that clears, spread smoothly over neighbouring frames (motion phases unchanged).
    arm_report = {}
    body = feet['body']
    mesh = body['mesh']
    step = max(1, int(round(src_fps / PARAMS.get('arm_clearance_hz', 30))))
    # Off by default: on these fused single-shell meshes the arm/torso crossings sit at the
    # skin-blended armpit and do not respond to turning the arm (measured on a calibrated mesh:
    # 35 deg per pass left the crossings unchanged), so it is reported by QA, not 'fixed'.
    for clearance_pass in range(PARAMS.get('arm_clearance_passes', 0)):
        added = 0.0
        for side in ('Left', 'Right'):
            arm_faces, torso_faces = mesh['arm_faces'][side], mesh['torso_faces']
            if not len(arm_faces) or not len(torso_faces):
                continue
            chain = mesh['chain_weight'][side]
            limit = math.radians(PARAMS.get('arm_clearance_max_deg', 35))
            alpha = np.zeros(n)
            axes = np.zeros((n, 3))
            checked = list(range(0, n, step)) + ([n - 1] if (n - 1) % step else [])
            for i in checked:
                V = skin_mesh(poses[i], mesh, rest_world)
                shoulder = poses[i][by_canon[side + 'Arm']][:3, 3]
                hits = crossings(V, arm_faces, torso_faces)
                if not hits:
                    continue
                arm_c = V[np.unique(arm_faces[[a for a, _ in hits]])].mean(axis=0)
                torso_c = V[np.unique(torso_faces[[b for _, b in hits]])].mean(axis=0)
                away = arm_c - torso_c
                axis = np.cross(arm_c - shoulder, away)
                if np.linalg.norm(axis) < 1e-9:
                    continue
                axis /= np.linalg.norm(axis)

                def clear(angle):
                    Q = axis_angle_matrix(axis, angle)
                    moved = V + chain[:, None] * ((V - shoulder) @ Q.T + shoulder - V)
                    return not crossings(moved, arm_faces, torso_faces)
                if not clear(limit):
                    angle = limit  # bounded: report the remainder instead of twisting further
                else:
                    lo, hi = 0.0, limit
                    for _ in range(6):
                        mid = (lo + hi) / 2
                        lo, hi = (lo, mid) if clear(mid) else (mid, hi)
                    angle = hi
                alpha[i], axes[i] = angle, axis
            # Fill unchecked samples from their checked neighbours (held, then smoothed below).
            for i in range(n):
                if i % step and i != n - 1:
                    a, b = (i // step) * step, min((i // step + 1) * step, n - 1)
                    j = a if alpha[a] >= alpha[b] else b
                    alpha[i], axes[i] = alpha[j], axes[j]
            if not alpha.any():
                continue
            # Spread: hold the largest nearby correction, then blur (loop-aware), so the arm
            # eases away and back instead of popping.
            w = max(1, int(round(0.08 * src_fps)))
            rv = np.zeros((n, 3))
            for i in range(n):
                window = [(i + d) % (n - 1) if loop else min(max(i + d, 0), n - 1) for d in range(-w, w + 1)]
                best = max(window, key=lambda j: alpha[j])
                rv[i] = axes[best] * alpha[best] if alpha[best] > 0 else 0
            sigma = 0.05 * src_fps
            ks = np.arange(-int(3 * sigma), int(3 * sigma) + 1)
            kernel = np.exp(-0.5 * (ks / sigma) ** 2)
            kernel /= kernel.sum()
            smooth = np.zeros_like(rv)
            for i in range(n):
                for d, wgt in zip(ks, kernel):
                    j = (i + d) % (n - 1) if loop else min(max(i + d, 0), n - 1)
                    smooth[i] += wgt * rv[j]
            if loop:
                smooth[-1] = smooth[0]
            arm_offsets[side] = arm_offsets[side] + smooth
            added = max(added, float(np.degrees(np.linalg.norm(smooth, axis=1).max())))
            arm_report.setdefault(side, []).append({'pass': clearance_pass, 'frames_penetrating': int((alpha > 0).sum()),
                                                    'max_added_deg': float(np.degrees(np.linalg.norm(smooth, axis=1).max()))})
        if added < 0.5:
            break
        poses, stats, drop, first = solve_with_drop()
    report['arm_clearance'] = arm_report
    report['arm_offset_max_deg'] = {side: float(np.degrees(np.linalg.norm(v, axis=1).max())) for side, v in arm_offsets.items()}
    report.update(source_reach_max_unadjusted=max(first['reach']), hips_drop=drop, hips_drop_fraction_of_leg=drop / k_t,
                  ik_reach_max=max(stats['reach']), ik_clamped_samples=stats['clamped'], max_leg_reach_limit=max_reach,
                  skinned_contact_corrections=passes)
    return poses, times, report, info


def to_basis(arm, poses):
    """Pose-bone matrix_basis per bone per sample from desired world matrices."""
    world, rest_arm, rest_world, order, parent = target_data(arm)
    inv_world = np.linalg.inv(world)
    out = []
    for posed in poses:
        basis = {}
        for name in order:
            m_arm = inv_world @ posed[name]
            p = parent[name]
            if p is None:
                ref = rest_arm[name]
            else:
                ref = (inv_world @ posed[p]) @ np.linalg.inv(rest_arm[p]) @ rest_arm[name]
            b = np.linalg.inv(ref) @ m_arm
            basis[name] = b
        out.append(basis)
    return out


def author_action(arm, name, times, bases, loop, frame_rate):
    """Creates a real named LINEAR action with full deterministic rotation/location keys."""
    old = bpy.data.actions.get(name)
    if old:
        bpy.data.actions.remove(old)
    action = bpy.data.actions.new(name)
    action.use_fake_user = True  # keep the unassigned action in the saved native
    arm.animation_data_create()
    arm.animation_data.action = action
    frames = [t * frame_rate for t in times]
    for bone in arm.pose.bones:
        bone.rotation_mode = 'QUATERNION'
        quats, locs = [], []
        for basis in bases:
            m = Matrix(basis[bone.name].tolist())
            loc, q, _ = m.decompose()
            if quats and q.dot(quats[-1]) < 0:
                q = -q
            quats.append(q)
            locs.append(loc)
        if loop:
            q0 = quats[0] if quats[0].dot(quats[-1]) >= 0 else -quats[0]
            quats[-1], locs[-1] = q0.copy(), locs[0].copy()
        for path, values, width in (('rotation_quaternion', quats, 4), ('location', locs, 3)):
            for index in range(width):
                curve = action.fcurve_ensure_for_datablock(arm, f'pose.bones["{bone.name}"].{path}', index=index,
                                                           group_name=bone.name)
                curve.keyframe_points.clear()
                curve.keyframe_points.add(len(frames))
                co = []
                for f, v in zip(frames, values):
                    co.extend((f, v[index]))
                curve.keyframe_points.foreach_set('co', co)
                ipo = bpy.types.Keyframe.bl_rna.properties['interpolation'].enum_items['LINEAR'].value
                curve.keyframe_points.foreach_set('interpolation', [ipo] * len(frames))
                curve.update()
    slot = arm.animation_data.action_slot
    arm.animation_data.action = None
    return action, slot


def lbs(poses, data, rest_world, normals=False):
    """Linear-blend-skinned positions (and optionally normals) of sampled vertices per pose."""
    out = np.zeros((len(poses), len(data['rest']), 3))
    nrm = np.zeros_like(out) if normals else None
    for i, posed in enumerate(poses):
        for bone, weights in data['weights'].items():
            if bone not in posed:
                continue
            m = posed[bone] @ np.linalg.inv(rest_world[bone])
            out[i] += weights[:, None] * (data['rest'] @ m[:3, :3].T + m[:3, 3])
            if normals:
                nrm[i] += weights[:, None] * (data['normals'] @ m[:3, :3].T)
    if normals:
        nrm /= np.maximum(np.linalg.norm(nrm, axis=2, keepdims=True), 1e-12)
        return out, nrm
    return out


def skin_mesh(posed, mesh, rest_world):
    """Linear-blend skinning (top-4 influences, as exported) of the arm/torso region vertices."""
    mats = np.stack([posed[b] @ mesh['inv_rest'][k] if b in posed else np.eye(4) for k, b in enumerate(mesh['bones'])])
    rest = mesh['rest']
    out = np.zeros_like(rest)
    for k in range(mesh['index'].shape[1]):
        m = mats[mesh['index'][:, k]]
        out += mesh['weight'][:, k:k + 1] * (np.einsum('vij,vj->vi', m[:, :3, :3], rest) + m[:, :3, 3])
    return out


def crossings(V, faces_a, faces_b):
    """Triangle pairs of region A crossing region B (adjacent/seam-coincident pairs excluded)."""
    verts = [Vector(v) for v in V]
    ta = BVHTree.FromPolygons(verts, faces_a.tolist(), all_triangles=True)
    tb = BVHTree.FromPolygons(verts, faces_b.tolist(), all_triangles=True)
    hits = []
    for i, j in ta.overlap(tb):
        a, b = faces_a[i], faces_b[j]
        if set(a.tolist()) & set(b.tolist()):
            continue
        if np.min(np.linalg.norm(V[a][:, None, :] - V[b][None, :, :], axis=2)) < 1e-6:
            continue
        hits.append((i, j))
    return hits

def body_points(arm, rest_pts, rest_normals, dominant, vertex_weights, rest_world, by_role, height, tris):
    """Torso/head and arm-chain surface regions of the skinned mesh for clearance checks."""
    bones = sorted(rest_world)
    lookup = {b: k for k, b in enumerate(bones)}
    index = np.zeros((len(rest_pts), 4), dtype=np.int64)
    weight = np.zeros((len(rest_pts), 4))
    for vi, ws in enumerate(vertex_weights):
        top = sorted(ws.items(), key=lambda x: -x[1])[:4]
        total = sum(w for _, w in top) or 1.0
        for k, (b, w) in enumerate(top):
            index[vi, k], weight[vi, k] = lookup[b], w / total
    torso_set = {'Hips', 'Spine', 'Spine1', 'Spine2', 'Neck', 'Head'}
    torso_v = np.isin(dominant, list(torso_set))
    full_mesh = {'bones': bones, 'index': index, 'weight': weight, 'rest': rest_pts,
                 'inv_rest': [np.linalg.inv(rest_world[b]) for b in bones],
                 'torso_faces': tris[torso_v[tris].all(axis=1)], 'arm_faces': {}, 'chain_weight': {}}
    for side in ('Left', 'Right'):
        arm_v = np.array([d.startswith(side) and ('Arm' in d or 'Hand' in d) and 'Shoulder' not in d for d in dominant])
        shoulder = rest_world[by_role[side + 'Arm']][:3, 3]
        upper = np.linalg.norm(rest_world[by_role[side + 'ForeArm']][:3, 3] - shoulder)
        near = np.linalg.norm(rest_pts - shoulder, axis=1) < 0.6 * upper
        full_mesh['arm_faces'][side] = tris[arm_v[tris].all(axis=1) & ~near[tris].any(axis=1)]
        chain_bones = {b for b in rest_world if any(b.endswith(side + part) for part in ('Arm', 'ForeArm', 'Hand'))
                       or (side + 'Hand') in b}
        full_mesh['chain_weight'][side] = np.array([sum(w for b, w in ws.items() if b in chain_bones) for ws in vertex_weights])
    # Keep only the vertices the clearance test touches, re-indexed (much faster skinning/BVH).
    used = np.unique(np.concatenate([full_mesh['torso_faces'].ravel()] +
                                    [f.ravel() for f in full_mesh['arm_faces'].values()]))
    remap = -np.ones(len(rest_pts), dtype=np.int64)
    remap[used] = np.arange(len(used))
    full_mesh.update(index=index[used], weight=weight[used], rest=rest_pts[used],
                     torso_faces=remap[full_mesh['torso_faces']])
    for side in ('Left', 'Right'):
        full_mesh['arm_faces'][side] = remap[full_mesh['arm_faces'][side]]
        full_mesh['chain_weight'][side] = full_mesh['chain_weight'][side][used]
    torso_roles = {'Hips', 'Spine', 'Spine1', 'Spine2', 'Neck', 'Head'}
    torso = np.nonzero(np.isin(dominant, list(torso_roles)))[0]
    torso = torso[::max(1, len(torso) // 1500)]

    def pack(sel, with_normals=False):
        weights = {}
        for k, vi in enumerate(sel):
            for bone, w in vertex_weights[vi].items():
                weights.setdefault(bone, np.zeros(len(sel)))[k] = w
        data = {'rest': rest_pts[sel], 'weights': weights}
        if with_normals:
            data['normals'] = rest_normals[sel]
        return data

    out = {'torso': pack(torso, True), 'arm': {}, 'reach': 0.06 * height, 'mesh': full_mesh}
    for side in ('Left', 'Right'):
        parts = [side + 'Arm', side + 'ForeArm', side + 'Hand']
        mask = np.array([d.startswith(side) and ('Arm' in d or 'Hand' in d) and 'Shoulder' not in d for d in dominant])
        shoulder = rest_world[by_role[side + 'Arm']][:3, 3]
        upper = np.linalg.norm(rest_world[by_role[side + 'ForeArm']][:3, 3] - shoulder)
        mask &= np.linalg.norm(rest_pts - shoulder, axis=1) > 0.6 * upper
        sel = np.nonzero(mask)[0]
        sel = sel[::max(1, len(sel) // 250)]
        data = pack(sel)
        chain_names = {by_role[p_] for p_ in parts if p_ in by_role}
        chain_names |= {name for name in rest_world if any(name.endswith(side + 'Hand' + f) for f in
                        ('Index1', 'Index2', 'Index3', 'Index4', 'Middle1', 'Middle2', 'Middle3', 'Middle4',
                         'Ring1', 'Ring2', 'Ring3', 'Ring4', 'Pinky1', 'Pinky2', 'Pinky3', 'Pinky4',
                         'Thumb1', 'Thumb2', 'Thumb3', 'Thumb4'))}
        data['chain_weight'] = np.array([sum(w for b, w in vertex_weights[vi].items() if b in chain_names) for vi in sel])
        out['arm'][side] = data
    return out

def sole_points(arm, idle_clip):
    """Target heel / ball contact points in each Foot bone's rest frame, and the runtime floor.

    Floor = lowest skinned vertex in the idle's first frame (the app grounds that pose).
    Heel = rear sole region, ball = sole region under the toe joint, from the real mesh."""
    world, rest_arm, rest_world, order, parent = target_data(arm)
    roles, _ = anatomical_names(parent, PARAMS.get('target_roles'))
    by_role = {roles[n]: n for n in rest_arm}
    meshes = [o for o in bpy.context.scene.objects if o.type == 'MESH'
              and any(m.type == 'ARMATURE' and m.object == arm for m in o.modifiers)]
    arm.data.pose_position = 'REST'
    bpy.context.view_layer.update()
    rest_pts = np.concatenate([BA.evaluated_points(o) for o in meshes])
    normals = []
    depsgraph = bpy.context.evaluated_depsgraph_get()
    for o in meshes:
        ev = o.evaluated_get(depsgraph)
        mesh = ev.to_mesh()
        nr = np.empty(len(mesh.vertices) * 3)
        mesh.vertices.foreach_get('normal', nr)
        normals.append(nr.reshape(-1, 3) @ np.array(ev.matrix_world.to_3x3()).T)
        ev.to_mesh_clear()
    rest_normals = np.concatenate(normals)
    rest_normals /= np.maximum(np.linalg.norm(rest_normals, axis=1, keepdims=True), 1e-12)
    arm.data.pose_position = 'POSE'
    BA.bind(CONFIG, idle_clip, include_face=False)
    BA.frame(0)
    idle_pts = np.concatenate([BA.evaluated_points(o) for o in meshes])
    BA.reset()
    dominant, vertex_weights = [], []
    for o in meshes:
        groups = {g.index: g.name for g in o.vertex_groups}
        for v in o.data.vertices:
            best = max(v.groups, key=lambda g: g.weight, default=None)
            dominant.append(roles.get(groups[best.group], '') if best else '')
            total_w = sum(g.weight for g in v.groups if groups[g.group] in rest_world) or 1.0
            vertex_weights.append({groups[g.group]: g.weight / total_w for g in v.groups
                                   if groups[g.group] in rest_world and g.weight > 0})
    dominant = np.array(dominant)
    # The feet's ground is where the idle's soles stand (a tail or robe may hang lower).
    sole_regions = [side + part for side in ('Left', 'Right') for part in ('Foot', 'ToeBase', 'Toe_End')]
    floor_z = float(idle_pts[np.isin(dominant, sole_regions), 2].min())
    runtime_floor = float(idle_pts[:, 2].min())
    height = float(rest_pts[:, 2].max() - rest_pts[:, 2].min())
    out = {'floor_z': floor_z, 'rest_sole_z': None, 'runtime_floor_z': runtime_floor}
    for side in ('Left', 'Right'):
        parts = [side + 'Foot', side + 'ToeBase', side + 'Toe_End']
        mask = np.isin(dominant, parts)
        if not mask.any():
            raise BA.NeedsInput(f'No vertices weighted to the {side} foot')
        low = rest_pts[mask, 2].min()
        sole = rest_pts[mask & (rest_pts[:, 2] < low + 0.02 * height)]
        out['rest_sole_z'] = float(low) if out['rest_sole_z'] is None else min(out['rest_sole_z'], float(low))
        foot = rest_world[by_role[side + 'Foot']]
        ankle, R0 = foot[:3, 3], rot3(Matrix(foot.tolist()))
        toe = rest_world[by_role[side + 'ToeBase']][:3, 3]
        axis = toe - ankle
        axis[2] = 0
        axis /= np.linalg.norm(axis)
        along = (sole - ankle) @ axis
        toe_along = (toe - ankle) @ axis
        rear = sole[along <= np.percentile(along, 20)]
        ball = sole[np.abs(along - toe_along) <= max(0.15 * (along.max() - along.min()), 1e-4)]
        if not len(ball):
            ball = sole[along >= np.percentile(along, 70)]
        toe_frame = rest_world[by_role[side + 'ToeBase']]
        R_toe0 = rot3(Matrix(toe_frame.tolist()))
        sole_mask = mask & (rest_pts[:, 2] < low + 0.02 * height)
        foot_sel = sole_mask & (dominant == side + 'Foot')
        toe_sel = sole_mask & np.isin(dominant, [side + 'ToeBase', side + 'Toe_End'])
        step_f = max(1, int(foot_sel.sum() // 150))
        step_t = max(1, int(toe_sel.sum() // 150))
        out_foot = (rest_pts[foot_sel][::step_f] - ankle) @ R0
        out_toe = (rest_pts[toe_sel][::step_t] - toe_frame[:3, 3]) @ R_toe0
        pts = {}
        for key, region in (('heel', rear), ('ball', ball)):
            point = region.mean(axis=0)
            if key == 'ball':
                point[:2] = toe[:2]  # pivot under the toe joint, so the toes stay put while the heel rises
            point[2] = region[:, 2].min()
            pts[key] = R0.T @ (point - ankle)
        # Skinned sole samples: rest positions and every bone weight (as exported/runtime LBS).
        sel = np.nonzero(sole_mask)[0][::max(1, int(sole_mask.sum() // 200))]
        weights = {}
        for k, vi in enumerate(sel):
            for bone, w in vertex_weights[vi].items():
                weights.setdefault(bone, np.zeros(len(sel)))[k] = w
        skin = {'rest': rest_pts[sel], 'weights': weights}
        out[side] = {'local': pts, 'vertices': int(len(sole)), 'foot_pts': out_foot, 'toe_pts': out_toe, 'skin': skin,
                     'toe_offset': R0.T @ (toe_frame[:3, 3] - ankle)}
    tris, offset = [], 0
    for o in meshes:
        o.data.calc_loop_triangles()
        t = np.empty(len(o.data.loop_triangles) * 3, dtype=np.int64)
        o.data.loop_triangles.foreach_get('vertices', t)
        tris.append(t.reshape(-1, 3) + offset)
        offset += len(o.data.vertices)
    out['body'] = body_points(arm, rest_pts, rest_normals, dominant, vertex_weights, rest_world, by_role, height,
                              np.concatenate(tris))
    # Rest-pose clearance baseline: the arms' own resting overlap is tolerated, nothing beyond it.
    out['body']['tolerance'] = 0.002 * height
    return out

def loop_residual(bases):
    first, last = bases[0], bases[-1]
    rot = max(Quaternion(Matrix(first[n].tolist()).to_quaternion()).rotation_difference(
        Quaternion(Matrix(last[n].tolist()).to_quaternion())).angle for n in first)
    loc = max(float(np.linalg.norm(first[n][:3, 3] - last[n][:3, 3])) for n in first)
    return math.degrees(rot), loc


def main():
    BA.load(JOB['inputs']['anatomy']['native'])
    scene = bpy.context.scene
    # Remove imported nodes that are not in the shipped glTF scene (they never render in the app).
    removed = []
    arm = BA.rig(CONFIG)
    # Blender's glTF importer adds a bone-display 'Icosphere' (pose-bone custom shape) that is
    # not part of the shipped file; exporting it would put a sphere into the app's scene.
    shapes = {pb.custom_shape for pb in arm.pose.bones if pb.custom_shape is not None}
    for pb in arm.pose.bones:
        pb.custom_shape = None
    for obj in list(shapes) + [bpy.data.objects.get(n) for n in PARAMS.get('remove_unrendered_objects', [])]:
        if obj is not None and obj.name in bpy.data.objects and obj.type == 'MESH' and not obj.vertex_groups:
            removed.append(obj.name)
            bpy.data.objects.remove(obj, do_unlink=True)
    fps = scene.render.fps / scene.render.fps_base
    sources = JOB['sources']
    report = {'removed_unrendered_objects': removed, 'scene_fps': fps, 'clips': {}}
    feet = sole_points(arm, CONFIG['motion']['clips'][0])
    report['feet'] = {k: (v if k in ('floor_z', 'rest_sole_z', 'runtime_floor_z') else {'torso_samples': len(v['torso']['rest'])} if k == 'body' else {'local': {a: b.tolist() for a, b in v['local'].items()}, 'vertices': v['vertices'], 'foot_samples': len(v['foot_pts']), 'toe_samples': len(v['toe_pts']), 'skinned_samples': len(v['skin']['rest'])}) for k, v in feet.items()}
    for clip in MOTION['clips']:
        spec = PARAMS['clips'].get(clip['name'])
        if not spec:
            continue  # authored clip already in the model (idle): reused unchanged
        src = sample_source(Path(sources[spec['source']]['path']))
        poses, times, clip_report, info = retarget(arm, {**spec, 'loop': clip['loop']}, src, feet)
        bases = to_basis(arm, poses)
        pre_rot, pre_loc = loop_residual(bases)
        author_action(arm, clip['name'], times, bases, clip['loop'], fps)
        expected = float(times[-1])
        if abs(expected - clip['duration_s']) > 1e-6:
            raise ValueError(f'{clip["name"]}: source duration {expected} != declared {clip["duration_s"]}')
        clip_report.update(source=info, samples=len(times), duration_s=expected,
                           loop_residual_before_close={'rotation_deg': pre_rot, 'location': pre_loc})
        report['clips'][clip['name']] = clip_report
    for obj in scene.objects:
        if obj.animation_data:
            obj.animation_data.action = None
    authored = OUT / 'authored.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(authored), check_existing=False)
    BA.dump(OUT / 'retarget_report.json', report)
    # Hand over to the pipeline's own body bake (exact keys, endpoints, world matrices).
    bake_job = {**JOB, 'inputs': {**JOB['inputs'], 'anatomy': {**JOB['inputs']['anatomy'], 'native': str(authored)}}}
    artifacts = BA.motion(bake_job)
    artifacts.update(retarget_report=str(OUT / 'retarget_report.json'), authored=str(authored))
    return artifacts


if __name__ == '__main__':
    initialize(sys.argv[sys.argv.index('--') + 1])
    try:
        artifacts = main()
        result = {'run_id': JOB['run_id'], 'status': 'success', 'artifacts': artifacts}
    except Exception as error:  # truthful status back to the runner
        import traceback
        traceback.print_exc()
        result = {'run_id': JOB['run_id'], 'status': 'needs_input' if isinstance(error, BA.NeedsInput) else 'failed',
                  'message': str(error)}
    BA.dump(JOB['result_path'], result)
    if result['status'] == 'failed':
        raise SystemExit(1)
