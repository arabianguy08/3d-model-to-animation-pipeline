"""Build a pipeline config from explicit model/motion paths and measured clip specs."""
import argparse
import json
from pathlib import Path
from companion_animation.pipeline.glb_io import read
from companion_animation.locomotion.anatomy import anatomical_names


def configuration(model, workspace, blender, parameters, specs, model_id='character'):
    here = Path(__file__).resolve().parent
    pipeline = here.parent / 'pipeline'
    doc, _ = read(model)
    nodes = doc['nodes']
    parent = {c: i for i, node in enumerate(nodes) for c in node.get('children', [])}
    joints = {i for skin in doc.get('skins', []) for i in skin['joints']}
    mapping, _ = anatomical_names({nodes[i]['name']: nodes[parent[i]]['name'] if parent.get(i) in joints else None for i in joints})
    roles = {role: bone for bone, role in mapping.items()}
    if not doc.get('animations'):
        raise ValueError('Source must contain the existing idle as its first clip')
    clips = []
    for animation in doc['animations']:
        end = max(doc['accessors'][s['input']]['max'][0] for s in animation['samplers'])
        start = min(doc['accessors'][s['input']]['min'][0] for s in animation['samplers'])
        clips.append({'name': animation['name'], 'armature_action': animation['name'], 'duration_s': end - start,
                      'loop': animation.get('extras', {}).get('loop', False), 'kept_unchanged': True, 'moving': True})
    sources = {'model': str(Path(model).resolve())}
    params = {'author_hz': 120, 'clips': {}}
    for spec in specs:
        name = spec['name']
        if name in {c['name'] for c in clips} or spec['duration_s'] <= 0:
            raise ValueError('New clips need unique names and measured positive durations')
        sources[name] = str(Path(spec['path']).resolve())
        clips.append({'name': name, 'armature_action': name, 'duration_s': spec['duration_s'],
                      'loop': spec['loop'], 'gait': spec.get('gait', 'other'), 'moving': True})
        params['clips'][name] = {'source': name, 'in_place_travel': spec.get('in_place', False),
                                'center_horizontal': spec.get('in_place', False)}
    dependencies = [str(p) for p in (here / 'math_core.py', here / 'anatomy.py', pipeline / 'blender_adapter.py',
                                    pipeline / 'glb_io.py', pipeline / 'vertex_match.py')]
    config = {'schema_version': 1, 'model_id': model_id, 'workspace': str(Path(workspace).resolve()),
              'blender': str(Path(blender).resolve()), 'sources': sources, 'normalization': {},
              'anatomy': {'mode': 'existing', 'roles': roles},
              'motion': {'mode': 'adapter', 'clips': clips, 'adapter': {
                  'script': str(here / 'retarget.py'), 'parameters': str(Path(parameters).resolve()),
                  'inputs': [str(Path(parameters).resolve()), *dependencies]}},
              'adapters': {'export': {'script': str(here / 'append_export.py'),
                                      'inputs': [*dependencies, str(here / 'preservation.py')]}},
              'sampling': {'hz': 24}, 'face': {'mode': 'none', 'probes': {'enabled': False}},
              'export': {'filename': 'candidate.glb', 'max_trs_residual': 1e-5},
              'certification': {'max_vertex_error': 1e-4, 'timing_tolerance_s': 1e-6,
                                'max_loop_vertex_error': 1e-4, 'sample_hz': 120, 'max_samples_per_clip': 10000},
              'preview': {'resolution': 256, 'fps': 8, 'views': ['oblique', 'side']},
              'package': {}, 'stage_timeout_s': 3600}
    return config, params


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('model', 'workspace', 'blender', 'specs', 'output'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    path = Path(args.output).resolve()
    parameter_path = path.with_suffix('.params.json')
    config, params = configuration(args.model, args.workspace, args.blender, parameter_path,
                                   json.loads(Path(args.specs).read_text()))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2), encoding='utf-8')
    parameter_path.write_text(json.dumps(params, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
