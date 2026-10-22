"""NumPy-only contact and retarget math; Z up, horizontal XY, model units."""
import math
import numpy as np


def two_bone(hip, target, knee_hint, upper, lower, previous_pole=None):
    """Non-stretch solve retaining the transferred knee plane through singularities."""
    hip, target, hint = (np.asarray(p, dtype=float) for p in (hip, target, knee_hint))
    if not all(np.isfinite(p).all() for p in (hip, target, hint)) or min(upper, lower) <= 0:
        raise ValueError('Finite positions and positive segment lengths required')
    delta = target - hip
    distance = np.linalg.norm(delta)
    if distance < 1e-12:
        raise ValueError('Target coincides with hip; limb axis is undefined')
    axis = delta / distance
    distance = np.clip(distance, abs(upper - lower) + 1e-9 * (upper + lower), .9995 * (upper + lower))
    pole = hint - hip
    pole -= np.dot(pole, axis) * axis
    previous = None
    if previous_pole is not None:
        previous = np.asarray(previous_pole, dtype=float)
        previous = previous - np.dot(previous, axis) * axis
        if np.linalg.norm(previous) < 1e-10:
            previous = None
    if np.linalg.norm(pole) < 1e-6 * upper:
        pole = previous
        if pole is None:
            basis = np.eye(3)[np.argmin(np.abs(axis))]
            pole = basis - np.dot(basis, axis) * axis
    pole /= np.linalg.norm(pole)
    if previous is not None and np.dot(pole, previous) < 0:
        pole = -pole
    along = (upper * upper - lower * lower + distance * distance) / (2 * distance)
    height = math.sqrt(max(0., upper * upper - along * along))
    return hip + along * axis + height * pole, hip + distance * axis, pole


def skinned_pin_step(current, previous, correction, velocity, dt, floor, band):
    """Least-squares rolling contact patch pin on evaluated skinned sole vertices.

    Corresponding vertices carry shin/toe weights. Switch to the lowest point
    when the old patch leaves contact; reset previous at lift-off. Re-skin and
    re-solve the leg after applying the returned ankle correction.
    """
    current = np.asarray(current, dtype=float)
    if current.ndim != 2 or current.shape[1] != 3 or not len(current) or not np.isfinite(current).all():
        raise ValueError('Nonempty finite Nx3 sole required')
    if dt <= 0 or band <= 0:
        raise ValueError('Positive dt and contact band required')
    out = np.zeros(3)
    if previous is not None:
        previous, correction = np.asarray(previous, dtype=float), np.asarray(correction, dtype=float)
        if previous.shape != current.shape or correction.shape != (3,):
            raise ValueError('Sole vertex correspondence required')
        before, now = previous + correction, current + correction
        patch = (before[:, 2] < floor + band) & (now[:, 2] < floor + band)
        if not patch.any():
            patch[np.argmin(now[:, 2])] = True
        out = correction.copy()
        out[:2] += (before[patch] - now[patch])[:, :2].mean(axis=0) + np.asarray(velocity)[:2] * dt
    out[2] = floor - current[:, 2].min()
    return out


def treadmill(hips, times, enabled=True):
    """Return linear travel to subtract, total horizontal travel and speed."""
    hips, times = np.asarray(hips, dtype=float), np.asarray(times, dtype=float)
    if hips.shape != (len(times), 3) or len(times) < 2 or not np.isfinite(hips).all() or not np.isfinite(times).all() or np.any(np.diff(times) <= 0):
        raise ValueError('Finite ordered samples required')
    duration = times[-1] - times[0]
    travel = hips[-1] - hips[0] if enabled else np.zeros(3)
    travel[2] = 0
    return (times - times[0])[:, None] / duration * travel, travel, float(np.linalg.norm(travel) / duration)


def clip_extras(loop, speed=0., gait='other', events=()):
    if not math.isfinite(speed) or speed < 0 or gait not in ('walk', 'run', 'other'):
        raise ValueError('Finite nonnegative treadmill speed and explicit gait required')
    return {'loop': bool(loop), 'treadmillSpeed': speed, 'gait': gait, 'events': list(events)}


def runtime_time_scale(speed, units_to_pixels, treadmill_speed, limits=(.6, 1.6)):
    """Logical-pixel velocity / projected model-unit scale / baked speed."""
    if not all(math.isfinite(x) for x in (speed, units_to_pixels, treadmill_speed)) or min(units_to_pixels, treadmill_speed) <= 0:
        return 1.
    return max(limits[0], min(limits[1], speed / units_to_pixels / treadmill_speed))
