"""Append-only invariants over the actual serialized GLB, without Blender."""
import math
from pathlib import Path
from companion_animation.pipeline.glb_io import read


def check_preserved(source, candidate):
    old, old_bin = read(source)
    new, new_bin = read(candidate)
    failures = []
    if new_bin[:len(old_bin)] != old_bin:
        failures.append('BIN prefix')
    for key, value in old.items():
        if key in ('buffers', 'animations', 'asset'):
            continue
        actual = new.get(key)
        if (actual[:len(value)] if isinstance(value, list) and isinstance(actual, list) else actual) != value:
            failures.append(key)
    if new.get('asset') != old.get('asset'):
        failures.append('asset')
    for i, clip in enumerate(old.get('animations', [])):
        if i >= len(new.get('animations', [])):
            failures.append(f'animation {i} missing')
            continue
        actual = new['animations'][i]
        for key, value in clip.items():
            if key == 'extras':
                if any(actual.get('extras', {}).get(k) != v for k, v in value.items()):
                    failures.append(f'animation {i} extras')
            elif actual.get(key) != value:
                failures.append(f'animation {i} {key}')
    for i, buffer in enumerate(old.get('buffers', [])):
        actual = new.get('buffers', [])
        if i >= len(actual) or actual[i].get('byteLength', 0) < buffer['byteLength'] or any(actual[i].get(k) != v for k, v in buffer.items() if k != 'byteLength'):
            failures.append(f'buffer {i}')
    if failures:
        raise ValueError('Preservation failed: ' + ', '.join(failures))
    return {'all_preserved': True, 'bin_prefix_identical': True,
            'preserved_clips': len(old.get('animations', [])), 'appended_bin_bytes': len(new_bin) - len(old_bin)}


def trs_limit(config, source_sha256):
    """An exception belongs to a diagnosed source hash, never a global default."""
    limit = config.get('export', {}).get('max_trs_residual', 1e-5)
    if not math.isfinite(limit) or limit <= 0:
        raise ValueError('Invalid TRS limit')
    vertex_bound = config.get('certification', {}).get('max_vertex_error', 1e-4)
    if not math.isfinite(vertex_bound) or not 0 < vertex_bound <= 1e-4:
        raise ValueError('Do not relax the evaluated vertex bound')
    if limit > 1e-5:
        diagnosis = config.get('export', {}).get('trs_diagnosis', {})
        measured = diagnosis.get('measured_residual', 0)
        if (diagnosis.get('source_sha256') != source_sha256 or not diagnosis.get('note')
                or not math.isfinite(measured) or not 0 < measured <= limit):
            raise ValueError('Raised TRS limit requires a recorded per-source diagnosis')
    return limit


def candidate_path(source, destination):
    if (Path(source).resolve() == Path(destination).resolve()
            or (Path(source).exists() and Path(destination).exists() and Path(source).samefile(destination))):
        raise ValueError('Candidate must not overwrite shipped source')
    return Path(destination)


if __name__ == '__main__':
    import json
    import sys
    print(json.dumps(check_preserved(*sys.argv[1:3])))
