"""Pure locomotion measurements and explicit QA gates (model units)."""
import math
import numpy as np


def stance_slide(points, floor, band, velocity, dt):
    """Per-vertex net slide over contiguous supporting runs; no contact is invalid."""
    points = np.asarray(points, dtype=float)
    if points.ndim != 3 or points.shape[2] != 3 or len(points) < 2 or dt <= 0 or band <= 0 or not np.isfinite(points).all():
        raise ValueError('Finite time x vertex x XYZ samples and positive dt/band required')
    planted = points[:, :, 2] - floor <= band
    both = planted[1:] & planted[:-1]
    count = int(both.sum())
    if not count:
        return {'slide': None, 'support_steps': 0, 'status': 'invalid'}
    dev = np.diff(points[:, :, :2], axis=0) - np.asarray(velocity)[:2] * dt
    acc, worst = np.zeros((points.shape[1], 2)), 0.
    for step, active in zip(dev, both):
        acc = np.where(active[:, None], acc + step, 0.)
        worst = max(worst, float(np.linalg.norm(acc, axis=1).max()))
    return {'slide': worst, 'support_steps': count, 'status': 'valid'}


def gates(name, clip, H, thresholds=None):
    """Explicit PASS/FAIL/INVALID; missing evidence never vacuously passes."""
    if not math.isfinite(H) or H <= 0:
        raise ValueError('Positive finite body height required')
    t = {**THRESHOLDS, **(thresholds or {})}
    out = {}

    def gate(key, value, limit, minimum=False, strict=False, valid=True):
        valid = valid and value is not None and math.isfinite(value)
        ok = valid and ((value >= limit) if minimum else (value < limit if strict else value <= limit))
        out[key] = {'value': value, 'limit': limit, 'pass': bool(ok),
                    'status': 'invalid' if not valid else 'pass' if ok else 'fail'}

    feet = clip.get('contact', {}).get('per_foot', {})
    walk = clip.get('gait') == 'walk'
    for side in ('Left', 'Right'):
        f = feet.get(side, {})
        low = f.get('min_sole_z_rel_floor')
        gate(f'{side}_floor_penetration', None if low is None else max(0., -low), t['max_floor_penetration_fraction_H'] * H)
        for kind, metric, support in [('bearing', 'max_bearing_slide', 'bearing_support_steps'),
                                      ('near_floor', 'max_stance_slide', 'near_floor_support_steps')]:
            gate(f'{side}_{kind}_slide', f.get(metric), t['max_stance_slide_fraction_H'] * H,
                 valid=f.get(support, 0) > 0)
        if walk:
            gate(f'{side}_walk_duty_factor', f.get('duty_factor'), t['walk_min_duty_factor'], minimum=True)
    if walk:
        gate('walk_flight_fraction', clip.get('contact', {}).get('flight_fraction'), t['walk_max_flight_fraction'])
        # Walking permits the limiting case of zero double support, but never negative/absent evidence.
        double = clip.get('contact', {}).get('double_support_fraction')
        gate('walk_double_support', double, t.get('walk_min_double_support_fraction', 0.), minimum=True,
             valid=double is not None and double <= 1)
    if clip.get('loop', False):
        gate('loop_seam', clip.get('loop_seam_vertex'), t['max_loop_seam_vertex_fraction_H'] * H)
        gate('root_drift', clip.get('root_drift', {}).get('hips_net_horizontal'), t['max_root_drift_fraction_H'] * H)
    gate('pop_ratio', clip.get('pop_ratio'), t['max_pop_ratio'])
    for side in ('Left', 'Right'):
        gate(f'{side}_knee_flips', clip.get('knees', {}).get(side, {}).get('bend_direction_flips'), 0)
    for key in ('Left_arm_x_torso', 'Right_arm_x_torso', 'left_leg_x_right_leg'):
        gate(f'{key}_excess_crossings', clip.get('intersections', {}).get(key, {}).get('excess_triangles_over_rest'), t['max_excess_intersecting_triangles'])
    mp = clip.get('motion_principles', {})
    gate('head_moves_on_chest', mp.get('head_vs_chest_rotation_range_deg'), t['min_head_vs_chest_range_deg'], minimum=True)
    for side in ('Left', 'Right'):
        gate(f'{side}_elbow_not_rigid', mp.get('elbow_flex_range_deg', {}).get(side), t['min_elbow_flex_range_deg'], minimum=True)
    if clip.get('gait') in ('walk', 'run'):
        gate('chest_counter_rotates_pelvis', mp.get('chest_vs_pelvis_yaw_range_deg'), t['min_chest_vs_pelvis_yaw_range_deg'], minimum=True)
        gate('shoulders_vs_hips_counter_phase', mp.get('shoulder_line_vs_hip_line_yaw_corr'), 0, strict=True)
        for side in ('Left', 'Right'):
            gate(f'{side}_arm_opposes_thigh', mp.get('arm_vs_same_side_thigh_corr', {}).get(side), 0, strict=True)
    return out

THRESHOLDS = {
    'contact_band_fraction_H': 0.006,        # a foot is in contact when its lowest sole vertex is within 0.6% H
    'planted_band_fraction_H': 0.004,        # near-floor sole skin (within 0.4% H): slide incl. skin creep
    'bearing_band_fraction_H': 0.0015,       # weight-bearing sole vertices (within 0.15% H): foot slide
    'max_floor_penetration_fraction_H': 0.006,
    'max_stance_slide_fraction_H': 0.012,     # per-vertex net treadmill-relative slide over one planted run
    'max_slide_speed_p95_fraction_H_per_s': 0.06,
    'max_loop_seam_vertex_fraction_H': 1e-4,
    'max_pop_ratio': 6.0,                     # biggest per-step vertex move / 99th-percentile move
    'max_root_drift_fraction_H': 0.002,       # hips horizontal net displacement over a loop
    'max_excess_intersecting_triangles': 0,   # arm-torso / leg-leg crossings beyond the rest pose's own
    'walk_min_duty_factor': 0.5, 'walk_max_flight_fraction': 0.0,
    'walk_min_double_support_fraction': 0.0,
    'min_head_vs_chest_range_deg': 1.5, 'min_chest_vs_pelvis_yaw_range_deg': 2.0,
    'min_elbow_flex_range_deg': 3.0,
}

def unwrap(deg):
    return np.degrees(np.unwrap(np.radians(np.asarray(deg))))


def rng(x):
    x = np.asarray(x)
    return float(x.max() - x.min()) if len(x) else 0.0


def corr(a, b):
    a, b = np.asarray(a) - np.mean(a), np.asarray(b) - np.mean(b)
    den = np.linalg.norm(a) * np.linalg.norm(b)
    return float(a @ b / den) if den > 1e-12 else 0.0


def lag_seconds(a, b, dt, loop):
    """Delay of b behind a (s) by circular cross-correlation (positive = b follows a)."""
    a, b = np.asarray(a) - np.mean(a), np.asarray(b) - np.mean(b)
    if np.linalg.norm(a) < 1e-9 or np.linalg.norm(b) < 1e-9:
        return None
    n = len(a)
    best, arg = -np.inf, 0
    for s in range(-n // 2, n // 2):
        c = a @ np.roll(b, -s) if loop else (a[max(0, -s):n - max(0, s)] @ b[max(0, s):n - max(0, -s)])
        if c > best:
            best, arg = c, s
    return arg * dt


def angle_between(u, v):
    u, v = u / np.linalg.norm(u), v / np.linalg.norm(v)
    return math.degrees(math.acos(max(-1.0, min(1.0, float(u @ v)))))


def rel_rot_angle(a, b):
    ra = a[:3, :3] / np.linalg.norm(a[:3, :3], axis=0)
    rb = b[:3, :3] / np.linalg.norm(b[:3, :3], axis=0)
    rel = ra.T @ rb
    return math.degrees(math.acos(max(-1.0, min(1.0, (np.trace(rel) - 1) / 2))))
