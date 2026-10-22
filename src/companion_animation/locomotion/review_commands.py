"""Review CLI and command construction, usable without Blender."""
import argparse
import math
from pathlib import Path

VIEWS = ('front', 'side', 'three-quarter', 'runtime')


def parse_args(argv):
    parser = argparse.ArgumentParser(description='Render candidate playback with treadmill grid')
    parser.add_argument('model')
    parser.add_argument('output')
    parser.add_argument('label')
    parser.add_argument('--clips', required=True)
    parser.add_argument('--combined', default='')
    parser.add_argument('--speed', default='')
    parser.add_argument('--loops', type=int, default=3)
    parser.add_argument('--size', type=int, default=720)
    parser.add_argument('--fps', type=int, default=30)
    parser.add_argument('--stills', type=int, default=8)
    parser.add_argument('--engine', choices=('workbench', 'eevee'), default='workbench')
    parser.add_argument('--exposure', type=float, default=.9)
    parser.add_argument('--runtime-fov', type=float, default=28.)
    parser.add_argument('--runtime-height', type=float, default=.52)
    parser.add_argument('--runtime-distance', type=float, default=2.5)
    parser.add_argument('--runtime-target', type=float, default=.47)
    parser.add_argument('--runtime-yaw', type=float, default=.4 * math.pi)
    args = parser.parse_args(argv)
    if args.loops <= 0 or args.fps <= 0 or args.size < 2 or args.size % 2 or args.stills < 0:
        parser.error('Positive loops/fps and an even H.264 size required')
    if not 0 < args.runtime_fov < 180 or args.runtime_distance <= 0:
        parser.error('Invalid runtime camera')
    if any(not math.isfinite(getattr(args, key)) for key in ('runtime_fov', 'runtime_height', 'runtime_distance', 'runtime_target', 'runtime_yaw', 'exposure')):
        parser.error('Finite camera and exposure values required')
    try:
        speeds = dict(item.split('=') for item in args.speed.split(',') if item)
        if any(not math.isfinite(float(v)) or float(v) < 0 for v in speeds.values()):
            raise ValueError()
    except ValueError:
        parser.error('Speeds must be clip=nonnegative_number')
    if not any(args.clips.split(',')):
        parser.error('At least one clip required')
    return args


def blender_command(blender, argv):
    parse_args(argv)
    return [str(blender), '--background', '--factory-startup', '--python',
            str(Path(__file__).with_name('review.py')), '--', *map(str, argv)]


def grid_offset(time_s, speed, yaw, cell):
    if cell <= 0:
        raise ValueError('Positive grid cell required')
    return (-math.sin(yaw) * speed * time_s % cell,
            math.cos(yaw) * speed * time_s % cell)
