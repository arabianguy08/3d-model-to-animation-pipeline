"""Append-only GLB export adapter preserving original binary payload."""
import json
import sys
from pathlib import Path

try:
    import bpy
except ImportError:
    bpy = None
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from companion_animation.locomotion.preservation import check_preserved, trs_limit, candidate_path
from companion_animation.locomotion.math_core import clip_extras
import numpy as np
if bpy is not None:
    from mathutils import Matrix

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
    global append_float, read_glb, write_glb
    from glb_io import append_float, read as read_glb, write as write_glb


def main():
    config = CONFIG
    source = Path(JOB['sources']['model']['path'])
    document, binary = read_glb(source)
    existing = [a['name'] for a in document.get('animations', [])]
    BA.load(JOB['inputs']['face']['native'])
    BA.reset()
    native_rest = BA.worlds(config, rest=True)
    bake = json.loads(Path(JOB['inputs']['motion']['bake']).read_text())
    report_path = JOB['inputs']['motion'].get('retarget_report')
    locomotion = json.loads(Path(report_path).read_text())['clips'] if report_path else {}
    for name, matrix in bake['rest'].items():
        if name in native_rest and np.max(np.abs(np.array(matrix) - np.array(native_rest[name]))) > 1e-6:
            raise ValueError(f'Face stage changed cached body rest transform {name}')
    rest, parents = BA.node_worlds(document)
    names = {}
    for index, node in enumerate(document['nodes']):
        if node.get('name') in names:
            raise BA.NeedsInput(f'Duplicate serialized node name {node.get("name")}')
        names[node.get('name')] = index
    if len(document.get('skins', [])) != 1:
        raise BA.NeedsInput('Append adapter requires one inspected skin')
    joints = set(document['skins'][0]['joints'])
    # Blender's importer rests each bone at its bind pose (mesh world @ inverse bind matrix), which
    # on these files differs from the joints' serialized default TRS (measured up to 0.156 units at
    # the fingertips). Joint corrections therefore map native rest onto the bind pose; non-joint
    # parents (the Armature/Scene nodes) stay at their serialized transforms.
    skin = document['skins'][0]
    from glb_io import accessor
    ibm = accessor(document, binary, skin['inverseBindMatrices'])
    mesh_node = next(i for i, node in enumerate(document['nodes']) if node.get('skin') == 0)
    bind = {j: rest[mesh_node] @ Matrix(np.array(ibm[k]).reshape(4, 4).T.tolist()).inverted()
            for k, j in enumerate(skin['joints'])}
    corrections = {}
    for name, matrix in native_rest.items():
        index = names.get(name)
        if index is None or index not in joints:
            continue
        native_matrix = BA.C @ Matrix(matrix) @ BA.C.inverted()
        corrections[index] = Matrix(np.linalg.solve(np.array(native_matrix, dtype=np.float64),
                                                    np.array(bind[index], dtype=np.float64)))
    if len(corrections) != len(joints):
        raise BA.NeedsInput(f'Only {len(corrections)} of {len(joints)} skin joints map to native bones')
    defaults = {}
    for index in corrections:
        node = document['nodes'][index]
        if 'matrix' in node:
            raise BA.NeedsInput(f'Joint {node.get("name")} uses a matrix; TRS defaults required for append-only clips')
        defaults[index] = np.array(node.get('translation', [0, 0, 0]))
    max_residual, max_scale_dev, appended, replaced_clips = 0.0, 0.0, [], []
    limit = trs_limit(config, JOB['sources']['model']['sha256'])
    for clip in config['motion']['clips']:
        if clip['name'] in existing:
            if not clip.get('kept_unchanged'):
                raise ValueError('Append-only export cannot replace an existing clip')
            if existing.index(clip['name']) != config['motion']['clips'].index(clip):
                raise ValueError(f'{clip["name"]}: existing clip order differs from declared order')
            # Authored clip already in the shipped file: samplers/channels kept byte-for-byte; only
            # the loop/event declaration the certify stage reads is added to its JSON extras.
            kept = document['animations'][existing.index(clip['name'])]
            extras = kept.setdefault('extras', {})
            for key, value in {'loop': clip['loop'], 'events': clip.get('events', []), 'kept_unchanged': True}.items():
                if key in extras and extras[key] != value:
                    raise ValueError('Existing clip metadata conflicts with declaration')
                extras[key] = value
            continue
        body = bake['clips'][clip['name']]
        actions = BA.bind(config, clip)
        samples, start, end = BA.frames_for(config, clip, actions)
        samples = sorted(set(samples) | set(body['frames']))
        body_by_frame = dict(zip(body['frames'], body['worlds']))
        tracks = {index: {'translation': [], 'rotation': []} for index in corrections}
        for sample in samples:
            BA.frame(sample)
            live = BA.worlds(config)
            cached = body_by_frame.get(sample, live)
            posed = dict(rest)
            for name, matrix in live.items():
                index = names.get(name)
                if index is None:
                    continue
                if index in corrections:
                    posed[index] = BA.C @ Matrix(cached.get(name, matrix)) @ BA.C.inverted() @ corrections[index]
            for index, channels in tracks.items():
                local = posed[parents[index]].inverted() @ posed[index] if index in parents else posed[index]
                translation, rotation, scale = local.decompose()
                reconstruction = Matrix.LocRotScale(translation, rotation, scale)
                residual = float(np.max(np.abs(np.array(local) - np.array(reconstruction))))
                max_residual = max(max_residual, residual)
                max_scale_dev = max(max_scale_dev, float(np.max(np.abs(np.array(scale) - 1))))
                if residual > limit:
                    raise ValueError(f'{clip["name"]} {document["nodes"][index].get("name")}: TRS residual {residual}')
                q = [rotation.x, rotation.y, rotation.z, rotation.w]
                if channels['rotation'] and sum(a * b for a, b in zip(q, channels['rotation'][-1])) < 0:
                    q = [-x for x in q]
                channels['translation'].append(list(translation))
                channels['rotation'].append(q)
        # Joint scale is not animated: any deviation is float32 noise from the shipped rest data
        # (measured rest orthogonality deviation 1.36e-5). A real scale would exceed 1e-4 and
        # the certify stage's physical vertex bound would catch its effect regardless.
        if max_scale_dev > 1e-4:
            raise ValueError(f'{clip["name"]}: joint scale deviates by {max_scale_dev}; scale channel required')
        times = [(sample - start) / BA.fps() for sample in samples]
        input_index = append_float(document, binary, times, 'SCALAR')
        animation = {'name': clip['name'], 'samplers': [], 'channels': [],
                     'extras': {'loop': clip['loop'], 'events': clip.get('events', []), 'source_start_frame': start,
                                'source_end_frame': end, 'authored_key_times_included': True}}
        treadmill_speed = locomotion.get(clip['name'], {}).get('treadmill_speed', 0.)
        animation['extras'].update(clip_extras(clip['loop'], treadmill_speed, clip.get('gait', 'other'), clip.get('events', [])))

        def channel(index, kind, values, shape):
            output = append_float(document, binary, values, shape)
            animation['samplers'].append({'input': input_index, 'output': output, 'interpolation': 'LINEAR'})
            animation['channels'].append({'sampler': len(animation['samplers']) - 1, 'target': {'node': index, 'path': kind}})

        for index, channels in sorted(tracks.items()):
            channel(index, 'rotation', channels['rotation'], 'VEC4')
            moved = np.max(np.abs(np.array(channels['translation']) - defaults[index]))
            if moved > 1e-6:  # only joints whose translation is animated (the hips)
                channel(index, 'translation', channels['translation'], 'VEC3')
        document.setdefault('animations', []).append(animation)
        appended.append({'name': clip['name'], 'samples': len(samples), 'channels': len(animation['channels'])})
    path = candidate_path(source, OUT / config.get('export', {}).get('filename', 'candidate.glb'))
    candidate_path(JOB['sources']['model']['original'], path)
    if not path.resolve().is_relative_to(OUT.resolve()):
        raise ValueError('Candidate must remain inside the stage output directory')
    write_glb(path, document, binary)
    preservation = check_preserved(source, path)
    report = {'append_only': True, 'preservation': preservation, 'source_sha256': JOB['sources']['model']['sha256'], 'kept_clips': existing,
              'replaced_clips': replaced_clips,
              'appended': appended, 'max_trs_residual': max_residual, 'max_joint_scale_deviation': max_scale_dev,
              'glb_sha256': BA.hashed(path), 'glb_bytes': path.stat().st_size, 'source_bytes': source.stat().st_size}
    return {'glb': str(path), 'export': BA.dump(OUT / 'export.json', report)}


if __name__ == '__main__':
    initialize(sys.argv[sys.argv.index('--') + 1])
    try:
        result = {'run_id': JOB['run_id'], 'status': 'success', 'artifacts': main()}
    except Exception as error:
        import traceback
        traceback.print_exc()
        result = {'run_id': JOB['run_id'], 'status': 'needs_input' if isinstance(error, BA.NeedsInput) else 'failed',
                  'message': str(error)}
    BA.dump(JOB['result_path'], result)
    if result['status'] == 'failed':
        raise SystemExit(1)
