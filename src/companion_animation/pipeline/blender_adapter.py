"""Built-in stages, executed in an isolated Blender process with a JSON job."""
import hashlib
import html
import json
import math
from pathlib import Path
import sys
import re
import traceback

import bpy
import numpy as np
from mathutils import Matrix, Quaternion, Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from glb_io import accessor, append_float, read as read_glb, write as write_glb
from vertex_match import correspondence as match_vertices

C = Matrix.Rotation(-math.pi / 2, 4, 'X')
BASE_OBJECT_TRANSFORMS = {}


class NeedsInput(Exception):
    pass


def dump(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False), encoding='utf-8')
    return str(path)


def hashed(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path):
    global BASE_OBJECT_TRANSFORMS
    path = Path(path)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    if path.suffix.lower() == '.blend':
        bpy.ops.wm.open_mainfile(filepath=str(path))
    elif path.suffix.lower() in ('.glb', '.gltf'):
        from io_scene_gltf2.blender.imp import animation_node, animation_weight
        originals = [(module, module.make_fcurve) for module in (animation_node, animation_weight)]
        def preserve_import_keys(original):
            def make(action, slot, co, data_path, index=0, group_name='', interpolation=None):
                curve = original(action, slot, co, data_path, index, group_name, interpolation)
                if curve is not None and interpolation in ('LINEAR', 'STEP'):
                    # co already contains the importer's bone/rest/axis conversion.
                    # update() merges nearby keys, moving the later value earlier.
                    actual = [value for key in curve.keyframe_points for value in key.co]
                    expected = np.asarray(co, dtype=np.float32).tolist()
                    if actual != expected:
                        if any(x >= y for x, y in zip(expected[::2], expected[2::2])):
                            raise NeedsInput('Imported animation times collide in Blender FLOAT coordinates')
                        curve.keyframe_points.clear()
                        curve.keyframe_points.add(len(co) // 2)
                        curve.keyframe_points.foreach_set('co', co)
                        kind = 'LINEAR' if interpolation == 'LINEAR' else 'CONSTANT'
                        ipo = bpy.types.Keyframe.bl_rna.properties['interpolation'].enum_items[kind].value
                        curve.keyframe_points.foreach_set('interpolation', [ipo] * (len(co) // 2))
                        # LINEAR/STEP need no handle recalculation or destructive update.
                return curve
            return make
        try:
            for module, original in originals:
                module.make_fcurve = preserve_import_keys(original)
            bpy.ops.import_scene.gltf(filepath=str(path))
        finally:
            for module, original in originals:
                module.make_fcurve = original
        # The importer evaluates the first NLA clip. Cache serialized defaults,
        # rather than that animated pose, for subsequent neutral resets.
        document = read_glb(path)[0] if path.suffix.lower() == '.glb' else json.loads(path.read_text(encoding='utf-8'))
        rest, _ = node_worlds(document)
        named = {}
        for index, node in enumerate(document.get('nodes', [])):
            if node.get('name'):
                if node['name'] in named:
                    raise NeedsInput(f'Ambiguous serialized default node {node["name"]}')
                named[node['name']] = index
        bindings = []
        for obj in bpy.context.scene.objects:
            if obj.animation_data:
                data = obj.animation_data
                bindings.append((data, data.action, data.action_slot, data.use_nla))
                data.action = None
                data.use_nla = False
            if obj.type == 'ARMATURE':
                for bone in obj.pose.bones:
                    bone.matrix_basis = Matrix.Identity(4)
        bpy.context.view_layer.update()
        def depth(obj):
            return 0 if obj.parent is None else 1 + depth(obj.parent)
        for obj in sorted(bpy.context.scene.objects, key=depth):
            if obj.name in named and obj.type in {'MESH', 'EMPTY', 'ARMATURE'}:
                obj.matrix_world = C.inverted() @ rest[named[obj.name]] @ C
                bpy.context.view_layer.update()
        BASE_OBJECT_TRANSFORMS = {obj.name: obj.matrix_basis.copy() for obj in bpy.context.scene.objects}
        for data, action, slot, use_nla in bindings:
            data.action = action
            if action is not None:
                data.action_slot = slot
            data.use_nla = use_nla
        bpy.context.view_layer.update()
        return
    else:
        raise NeedsInput(f'Unsupported source extension {path.suffix}; provide .blend/.glb or a calibrated importer')
    BASE_OBJECT_TRANSFORMS = {obj.name: obj.matrix_basis.copy() for obj in bpy.context.scene.objects}


def save(directory, name='native.blend'):
    path = Path(directory) / name
    bpy.ops.wm.save_as_mainfile(filepath=str(path), check_existing=False)
    return str(path)


def curves(action):
    if hasattr(action, 'fcurves'):
        yield from action.fcurves
    else:
        for layer in action.layers:
            for strip in layer.strips:
                for bag in strip.channelbags:
                    yield from bag.fcurves


def action_times(action):
    return sorted({float(key.co.x) for curve in curves(action) for key in curve.keyframe_points})


def fps():
    return bpy.context.scene.render.fps / bpy.context.scene.render.fps_base


def meshes():
    return [obj for obj in bpy.context.scene.objects if obj.type == 'MESH']


def rig(config):
    name = config.get('anatomy', {}).get('armature')
    candidates = [obj for obj in bpy.context.scene.objects if obj.type == 'ARMATURE' and (not name or obj.name == name)]
    if len(candidates) != 1:
        raise NeedsInput(f'Expected one inspected armature, found {[obj.name for obj in candidates]}; set anatomy.armature')
    return candidates[0]


def reset():
    for obj in bpy.context.scene.objects:
        if obj.animation_data:
            obj.animation_data.action = None
            obj.animation_data.use_nla = False
        if obj.name in BASE_OBJECT_TRANSFORMS:
            obj.matrix_basis = BASE_OBJECT_TRANSFORMS[obj.name].copy()
        if obj.type == 'ARMATURE':
            obj.data.pose_position = 'POSE'
            for bone in obj.pose.bones:
                bone.matrix_basis = Matrix.Identity(4)
        if obj.type == 'MESH' and obj.data.shape_keys:
            keys = obj.data.shape_keys
            if keys.animation_data:
                keys.animation_data.action = None
                keys.animation_data.use_nla = False
            for key in keys.key_blocks:
                key.value = 0
    bpy.context.view_layer.update()


def bind_action(data, action_name, slot_name=None):
    if isinstance(action_name, dict):
        slot_name = action_name.get('slot', slot_name)
        action_name = action_name['action']
    action = bpy.data.actions.get(action_name)
    if not action:
        raise NeedsInput(f'Missing authored action {action_name}; inspect actual inventory')
    data.animation_data_create()
    data.animation_data.use_nla = False
    data.animation_data.action = action
    slots = [s for s in action.slots if (not slot_name or s.identifier == slot_name)]
    compatible = [s for s in slots if s.target_id_type == data.id_type]
    if compatible:
        slots = compatible
    if len(slots) != 1:
        raise NeedsInput(f'Action {action_name} has ambiguous slots; declare its exact slot')
    data.animation_data.action_slot = slots[0]
    return action


def bind(config, clip, include_face=True):
    reset()
    actions = []
    if clip.get('armature_action'):
        actions.append(bind_action(rig(config), clip['armature_action'], clip.get('armature_slot')))
    for name, action in clip.get('object_actions', {}).items():
        obj = bpy.data.objects.get(name)
        if not obj:
            raise NeedsInput(f'Missing animated object {name}')
        actions.append(bind_action(obj, action))
    face_actions = {**clip.get('morph_actions', {})}
    if include_face:
        face_actions.update(config.get('face', {}).get('clip_actions', {}).get(clip['name'], {}))
    for name, action in face_actions.items():
        obj = bpy.data.objects.get(name)
        if not obj or not obj.data.shape_keys:
            raise NeedsInput(f'Missing morph mesh {name}')
        actions.append(bind_action(obj.data.shape_keys, action))
    bpy.context.view_layer.update()
    return actions


def frames_for(config, clip, actions, hz=None):
    keys = sorted({time for action in actions for time in action_times(action)})
    if not keys:
        raise NeedsInput(f'Clip {clip["name"]} contains no keyed motion')
    start, end = clip.get('start_frame', keys[0]), clip.get('end_frame', keys[-1])
    if end <= start:
        raise NeedsInput(f'Clip {clip["name"]} needs a positive exact interval')
    duration = (end - start) / fps()
    if 'duration_s' not in clip or 'loop' not in clip:
        raise NeedsInput(f'Clip {clip["name"]} needs duration_s and loop flag')
    tolerance = config.get('certification', {}).get('timing_tolerance_s', 1e-6)
    if abs(duration - clip['duration_s']) > tolerance:
        raise ValueError(f'{clip["name"]}: actual duration {duration} != declared {clip["duration_s"]}')
    hz = hz or config.get('sampling', {}).get('hz', 120)
    steps = math.ceil(duration * hz)
    values = {start, end, *[key for key in keys if start <= key <= end]}
    values.update(start + i * fps() / hz for i in range(steps) if start + i * fps() / hz < end)
    return sorted(values), start, end


def frame(value):
    bpy.context.scene.frame_set(math.floor(value), subframe=value - math.floor(value))
    bpy.context.view_layer.update()


def worlds(config, rest=False):
    result = {}
    armature = rig(config)
    for obj in bpy.context.scene.objects:
        if obj.type not in ('MESH', 'EMPTY', 'ARMATURE'):
            continue
        result[obj.name] = [list(row) for row in obj.matrix_world]
    for bone in armature.data.bones:
        if bone.name in result:
            raise NeedsInput(f'Object/bone name collision {bone.name}; provide unambiguous names in prepared native')
        matrix = armature.matrix_world @ (bone.matrix_local if rest else armature.pose.bones[bone.name].matrix)
        result[bone.name] = [list(row) for row in matrix]
    return result


def inspection(job):
    load(job['sources']['model']['path'])
    config, directory = job['config'], Path(job['output_directory'])
    if config.get('normalization'):
        raise NeedsInput('Nonidentity normalization requires an inspected anatomical adapter; built-in inspection preserves source transforms')
    report = {'blender_version': bpy.app.version_string, 'fps': fps(),
              'unit_system': bpy.context.scene.unit_settings.system,
              'scale_length': bpy.context.scene.unit_settings.scale_length,
              'coordinate_convention': {'blender_up': '+Z', 'gltf_up': '+Y', 'forward': 'requires runtime/anatomical declaration'},
              'sources': job['sources'], 'actions': [], 'meshes': [], 'armatures': []}
    for action in bpy.data.actions:
        times = action_times(action)
        report['actions'].append({'name': action.name, 'key_frames': times, 'slots': [s.identifier for s in action.slots],
                                  'duration_s': (times[-1] - times[0]) / fps() if times else 0,
                                  'markers': [{'name': marker.name, 'frame': marker.frame} for marker in action.pose_markers]})
    for obj in meshes():
        coordinates = np.array([obj.matrix_world @ vertex.co for vertex in obj.data.vertices])
        report['meshes'].append({'name': obj.name, 'vertices': len(coordinates), 'triangles': sum(len(poly.vertices) - 2 for poly in obj.data.polygons),
                                 'bounds': [coordinates.min(axis=0).tolist(), coordinates.max(axis=0).tolist()] if len(coordinates) else [],
                                 'world_matrix': [list(row) for row in obj.matrix_world],
                                 'morphs': [key.name for key in obj.data.shape_keys.key_blocks] if obj.data.shape_keys else [],
                                 'materials': [material.name if material else None for material in obj.data.materials],
                                 'unweighted_vertices': sum(not vertex.groups for vertex in obj.data.vertices),
                                 'weight_sum_error': max((abs(sum(g.weight for g in vertex.groups) - 1) for vertex in obj.data.vertices if vertex.groups), default=0)})
    for obj in bpy.context.scene.objects:
        if obj.type != 'ARMATURE':
            continue
        report['armatures'].append({'name': obj.name, 'active_action': obj.animation_data.action.name if obj.animation_data and obj.animation_data.action else None,
                                    'active_slot': obj.animation_data.action_slot.identifier if obj.animation_data and obj.animation_data.action_slot else None,
                                    'nla': [{'name': track.name, 'mute': track.mute, 'strips': [strip.action.name for strip in track.strips if strip.action]} for track in obj.animation_data.nla_tracks] if obj.animation_data else [],
                                    'bones': [{'name': bone.name, 'parent': bone.parent.name if bone.parent else None,
                                               'head': list(bone.head_local), 'tail': list(bone.tail_local),
                                               'deform': bone.use_deform, 'rest_matrix': [list(row) for row in bone.matrix_local]} for bone in obj.data.bones]})
    return {'native': save(directory), 'inspection': dump(directory / 'inspection.json', report)}


def anatomy(job):
    config, directory = job['config'], Path(job['output_directory'])
    source = job['sources']['prepared_native']['path'] if config['anatomy']['mode'] == 'prepared' else job['inputs']['inspect']['native']
    load(source)
    armature = rig(config)
    for role, name in config['anatomy']['roles'].items():
        if name not in armature.data.bones:
            raise NeedsInput(f'Inspected role {role} references missing bone {name}')
    weighted = [obj for obj in meshes() if any(mod.type == 'ARMATURE' and mod.object == armature for mod in obj.modifiers)]
    if not weighted:
        raise NeedsInput('No skinned meshes on declared rig; calibrate anatomy/weights with trusted adapter')
    maximum = config['anatomy'].get('max_weight_sum_error', 1e-5)
    for obj in weighted:
        bone_groups = {group.index for group in obj.vertex_groups if group.name in armature.data.bones}
        for vertex in obj.data.vertices:
            weights = [g.weight for g in vertex.groups if g.group in bone_groups]
            if not weights or not all(math.isfinite(x) and x >= 0 for x in weights) or abs(sum(weights) - 1) > maximum:
                raise ValueError(f'Invalid skin weights: {obj.name} vertex {vertex.index}')
    return {'native': save(directory), 'anatomy': dump(directory / 'anatomy.json', {'roles': config['anatomy']['roles'], 'skin_weights': 'pass', 'calibration': 'supplied prepared/existing rig'})}


def motion(job):
    config, directory = job['config'], Path(job['output_directory'])
    load(job['inputs']['anatomy']['native'])
    reset()
    rest = worlds(config, rest=True)
    bake = {'fps': fps(), 'rest': rest, 'clips': {}, 'coordinate_space': 'Blender world matrices', 'sampling_policy': 'regular grid plus all authored keys and exact endpoints'}
    for clip in config['motion']['clips']:
        actions = bind(config, clip, include_face=False)
        samples, start, end = frames_for(config, clip, actions)
        matrices = []
        for sample in samples:
            frame(sample)
            matrices.append(worlds(config))
        bake['clips'][clip['name']] = {'frames': samples, 'times': [(sample - start) / fps() for sample in samples],
                                      'start_frame': start, 'end_frame': end, 'worlds': matrices}
    reset()
    return {'native': save(directory), 'bake': dump(directory / 'body_bake.json', bake)}


def face(job):
    config, directory = job['config'], Path(job['output_directory'])
    load(job['inputs']['motion']['native'])
    if config['face']['mode'] == 'existing':
        name = config['face'].get('blink_name', 'Blink')
        targets = [obj for obj in meshes() if obj.data.shape_keys and name in obj.data.shape_keys.key_blocks]
        if not targets:
            raise NeedsInput(f'No existing {name} morph; inspect face and supply calibrated face.adapter.script')
        for obj in targets:
            basis, blink = obj.data.shape_keys.key_blocks[0], obj.data.shape_keys.key_blocks[name]
            delta = max((a.co - b.co).length for a, b in zip(basis.data, blink.data))
            if delta <= 0:
                raise ValueError(f'{obj.name}: existing Blink morph has zero deformation')
    reset()
    return {'native': save(directory), 'face_checks': dump(directory / 'face_checks.json',
            {'mode': config['face']['mode'], 'neutral_basis': 'unchanged by built-in reuse',
             'anatomical_closure': 'requires facial views; morph existence is not closure certification'})}


def node_worlds(document):
    nodes = document.get('nodes', [])
    parents = {child: parent for parent, node in enumerate(nodes) for child in node.get('children', [])}
    local = []
    for node in nodes:
        if 'matrix' in node:
            values = node['matrix']
            matrix = Matrix([values[i::4] for i in range(4)])
        else:
            q = node.get('rotation', [0, 0, 0, 1])
            matrix = Matrix.LocRotScale(Vector(node.get('translation', [0, 0, 0])),
                                       Quaternion((q[3], *q[:3])), Vector(node.get('scale', [1, 1, 1])))
        local.append(matrix)
    result = {}
    def get(index):
        if index not in result:
            result[index] = get(parents[index]) @ local[index] if index in parents else local[index]
        return result[index]
    for index in range(len(nodes)):
        get(index)
    return result, parents


def export(job):
    config, directory = job['config'], Path(job['output_directory'])
    load(job['inputs']['face']['native'])
    reset()
    native_rest = worlds(config, rest=True)
    bake = json.loads(Path(job['inputs']['motion']['bake']).read_text())
    for name, matrix in bake['rest'].items():
        if name in native_rest and np.max(np.abs(np.array(matrix) - np.array(native_rest[name]))) > 1e-6:
            raise ValueError(f'Facial preparation changed cached body rest transform {name}; invalidate/recalibrate body stage')
    armature = rig(config)
    armature.data.pose_position = 'REST'
    bpy.context.view_layer.update()
    filename = config.get('export', {}).get('filename', 'companion.glb')
    if Path(filename).name != filename or Path(filename).suffix.lower() != '.glb':
        raise ValueError('export.filename must be a plain .glb filename')
    path = directory / filename
    bpy.ops.export_scene.gltf(filepath=str(path), export_format='GLB', export_animations=False,
                              export_skins=True, export_morph=True, export_yup=True, export_extras=True)
    armature.data.pose_position = 'POSE'
    document, binary = read_glb(path)
    rest, parents = node_worlds(document)
    names = {}
    for index, node in enumerate(document.get('nodes', [])):
        name = node.get('name')
        if name in names:
            raise NeedsInput(f'Duplicate exported node name {name}; calibrated export adapter required')
        names[name] = index
    corrections, identity_meshes = {}, []
    for name, matrix in native_rest.items():
        if name not in names:
            continue
        index = names[name]
        native_matrix = C @ Matrix(matrix) @ C.inverted()
        obj, node = bpy.data.objects.get(name), document['nodes'][index]
        if (obj and obj.type == 'MESH' and 'skin' not in node and not obj.modifiers
                and not obj.vertex_groups and not obj.data.shape_keys
                and np.max(np.abs(np.array(native_matrix) - np.array(rest[index]))) < 1e-6):
            def local_row(position):
                return {'position': tuple(position), 'local': tuple(position), 'weights': (), 'morphs': ()}
            source = [local_row(vertex.co) for vertex in obj.data.vertices]
            target = [local_row(C.inverted() @ Vector(position))
                      for primitive in document['meshes'][node['mesh']]['primitives']
                      for position in accessor(document, binary, primitive['attributes']['POSITION'])]
            match_vertices(source, target)
            # Matching local geometry/rest TRS need no bind correction. Inverting
            # hidden FX scales amplifies submicron float noise during activation.
            corrections[index] = Matrix.Identity(4)
            identity_meshes.append(name)
        else:
            corrections[index] = Matrix(np.linalg.solve(np.array(native_matrix, dtype=np.float64),
                                                        np.array(rest[index], dtype=np.float64)))
    animations, max_residual = [], 0.0
    for clip in config['motion']['clips']:
        body = bake['clips'][clip['name']]
        actions = bind(config, clip)
        samples, start, end = frames_for(config, clip, actions)
        samples = sorted(set(samples) | set(body['frames']))
        # Face-authored times missing from body bake are evaluated, never rounded.
        body_by_frame = dict(zip(body['frames'], body['worlds']))
        tracks = {index: {'translation': [], 'rotation': [], 'scale': []} for index in corrections}
        morph_tracks = {index: [] for index, node in enumerate(document['nodes'])
                        if 'mesh' in node and document['meshes'][node['mesh']].get('weights')}
        for sample in samples:
            frame(sample)
            live = worlds(config)
            cached = body_by_frame.get(sample, live)
            posed = dict(rest)
            for name, matrix in live.items():
                if name not in names:
                    continue
                index = names[name]
                matrix = cached.get(name, matrix)
                posed[index] = C @ Matrix(matrix) @ C.inverted() @ corrections[index]
            for index, channels in tracks.items():
                local = posed[parents[index]].inverted() @ posed[index] if index in parents else posed[index]
                translation, rotation, scale = local.decompose()
                reconstruction = Matrix.LocRotScale(translation, rotation, scale)
                residual = float(np.max(np.abs(np.array(local) - np.array(reconstruction))))
                max_residual = max(max_residual, residual)
                if residual > config.get('export', {}).get('max_trs_residual', 1e-5):
                    raise ValueError(f'{clip["name"]} {document["nodes"][index].get("name")}: TRS residual {residual}')
                q = [rotation.x, rotation.y, rotation.z, rotation.w]
                if channels['rotation'] and sum(a * b for a, b in zip(q, channels['rotation'][-1])) < 0:
                    q = [-x for x in q]
                channels['translation'].append(list(translation))
                channels['rotation'].append(q)
                channels['scale'].append(list(scale))
            for index, values in morph_tracks.items():
                node = document['nodes'][index]
                obj = bpy.data.objects.get(node['name'])
                target_names = document['meshes'][node['mesh']].get('extras', {}).get('targetNames', [])
                if not obj or not obj.data.shape_keys or not target_names:
                    raise NeedsInput(f'Cannot map exported morph node {node["name"]}; explicit export adapter required')
                values.extend(obj.data.shape_keys.key_blocks[name].value for name in target_names)
        times = [(sample - start) / fps() for sample in samples]
        input_index = append_float(document, binary, times, 'SCALAR')
        animation = {'name': clip['name'], 'samplers': [], 'channels': [],
                     'extras': {'loop': clip['loop'], 'events': clip.get('events', []), 'source_start_frame': start,
                                'source_end_frame': end, 'authored_key_times_included': True}}
        def channel(index, kind, values, shape):
            output = append_float(document, binary, values, shape)
            sampler = len(animation['samplers'])
            animation['samplers'].append({'input': input_index, 'output': output, 'interpolation': 'LINEAR'})
            animation['channels'].append({'sampler': sampler, 'target': {'node': index, 'path': kind}})
        for index, channels in tracks.items():
            for kind, values in channels.items():
                channel(index, kind, values, 'VEC4' if kind == 'rotation' else 'VEC3')
            document['nodes'][index].pop('matrix', None)
        for index, values in morph_tracks.items():
            channel(index, 'weights', values, 'SCALAR')
        animations.append(animation)
    document['animations'] = animations
    document.setdefault('asset', {}).setdefault('extras', {})['pipeline'] = {'model_id': config['model_id'], 'runtime_verified': False}
    write_glb(path, document, binary)
    return {'glb': str(path), 'export': dump(directory / 'export.json', {'max_trs_residual': max_residual,
            'body_bake_reused': True, 'identity_mesh_corrections': identity_meshes,
            'glb_sha256': hashed(path), 'sampling': 'regular plus authored keys, exact endpoints'})}


def neutral_records():
    reset()
    records = {}
    for obj in meshes():
        if not obj.data.vertices:
            continue
        points = np.array([obj.matrix_world @ vertex.co for vertex in obj.data.vertices], dtype=np.float64)
        records[obj.name] = {'neutral': points, 'obj': obj}
    return records


def evaluated_points(obj):
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    try:
        return np.array([evaluated.matrix_world @ vertex.co for vertex in mesh.vertices], dtype=np.float64)
    finally:
        evaluated.to_mesh_clear()


def vertex_signatures(obj):
    """Position + skin + morph identity disambiguates coincident seam vertices."""
    names = {group.index: group.name for group in obj.vertex_groups}
    keys = obj.data.shape_keys
    basis = keys.key_blocks[0] if keys else None
    rows = []
    for vertex in obj.data.vertices:
        position = tuple(round(x, 5) for x in obj.matrix_world @ vertex.co)
        weights = tuple(sorted((names[g.group], round(g.weight, 4)) for g in vertex.groups if g.weight > 1e-5))
        morphs = tuple((key.name, tuple(round(x, 5) for x in (key.data[vertex.index].co - basis.data[vertex.index].co)))
                       for key in keys.key_blocks[1:]) if keys else ()
        rows.append((position, weights, morphs))
    return rows


def identity_records(obj):
    names = {group.index: group.name for group in obj.vertex_groups}
    keys = obj.data.shape_keys
    basis = keys.key_blocks[0] if keys else None
    matrix = obj.matrix_world.copy()
    return [{'position': tuple(matrix @ vertex.co), 'local': tuple(vertex.co),
             'weights': tuple(sorted((names[g.group], float(g.weight)) for g in vertex.groups if g.weight > 1e-5)),
             'morphs': tuple((key.name, tuple(matrix.to_3x3() @ (key.data[vertex.index].co - basis.data[vertex.index].co)))
                            for key in keys.key_blocks[1:]) if keys else ()}
            for vertex in obj.data.vertices]


def sample_native(config, clip, values, activity=None):
    actions = bind(config, clip)
    _, start, _ = frames_for(config, clip, actions)
    samples = []
    for seconds in values:
        frame(start + seconds * fps())
        samples.append({obj.name: evaluated_points(obj) for obj in meshes()})
        if activity is not None:
            blink_name = config.get('face', {}).get('blink_name', 'Blink')
            for obj in meshes():
                if obj.data.shape_keys and blink_name in obj.data.shape_keys.key_blocks:
                    activity.setdefault(obj.name, []).append(obj.data.shape_keys.key_blocks[blink_name].value)
    return samples


def certify(job):
    config, directory = job['config'], Path(job['output_directory'])
    path = Path(job['inputs']['export']['glb'])
    document, binary = read_glb(path)
    animations = {animation['name']: animation for animation in document.get('animations', [])}
    if len(animations) != len(document.get('animations', [])):
        raise ValueError('Duplicate exported action identities')
    expected = {clip['name'] for clip in config['motion']['clips']}
    if set(animations) != expected:
        raise ValueError(f'Exported clip coverage differs: {set(animations)} != {expected}')
    tolerance = config['certification']['timing_tolerance_s']
    times_by_clip, timings = {}, {}
    for clip in config['motion']['clips']:
        animation = animations[clip['name']]
        times = sorted({row[0] for sampler in animation['samplers'] for row in accessor(document, binary, sampler['input'])})
        if not times or abs(times[0]) > tolerance or abs(times[-1] - clip['duration_s']) > tolerance:
            raise ValueError(f'{clip["name"]}: persisted animation duration/endpoints differ')
        if animation.get('extras', {}).get('loop') != clip['loop'] or animation.get('extras', {}).get('events') != clip.get('events', []):
            raise ValueError(f'{clip["name"]}: loop/event contract differs')
        hz = config['certification'].get('sample_hz', 30)
        # Include every export key plus independent off-grid midpoint times.
        probes = {0., clip['duration_s'], *times}
        probes.update((i + 0.371) / hz for i in range(math.ceil(clip['duration_s'] * hz)) if (i + 0.371) / hz < clip['duration_s'])
        if len(probes) > config['certification'].get('max_samples_per_clip', 10000):
            raise NeedsInput(f'{clip["name"]}: {len(probes)} certification times exceed configured limit; explicitly budget coverage')
        times_by_clip[clip['name']] = sorted(probes)
        timings[clip['name']] = {'duration_s': times[-1], 'samples': len(probes), 'events': clip.get('events', [])}
    load(job['inputs']['face']['native'])
    source_records = neutral_records()
    signatures = {name: vertex_signatures(entry['obj']) for name, entry in source_records.items()}
    identities = {name: identity_records(entry['obj']) for name, entry in source_records.items()}
    source_matrices = {name: np.array(entry['obj'].matrix_world) for name, entry in source_records.items()}
    source_mesh_names = {name: entry['obj'].data.name for name, entry in source_records.items()}
    source_activity = {clip['name']: {} for clip in config['motion']['clips']}
    native = {clip['name']: sample_native(config, clip, times_by_clip[clip['name']], source_activity[clip['name']])
              for clip in config['motion']['clips']}
    blink_name = config.get('face', {}).get('blink_name', 'Blink')
    source_morphs = {name: [key.name for key in obj.data.shape_keys.key_blocks[1:]] for name, entry in source_records.items()
                     if (obj := entry['obj']).data.shape_keys}
    blink_activity = {}
    for clip in config['motion']['clips']:
        exported_targets = {}
        for channel in animations[clip['name']]['channels']:
            if channel['target']['path'] != 'weights':
                continue
            node = document['nodes'][channel['target']['node']]
            target_names = document['meshes'][node['mesh']].get('extras', {}).get('targetNames', [])
            if blink_name not in target_names:
                continue
            index = target_names.index(blink_name)
            sampler = animations[clip['name']]['samplers'][channel['sampler']]
            flattened = [row[0] for row in accessor(document, binary, sampler['output'])]
            exported_targets[node['name']] = flattened[index::len(target_names)]
        required = config.get('face', {}).get('mode') != 'none' and clip.get('blink_required', True)
        targets = config.get('face', {}).get('blink_targets', list(source_activity[clip['name']]))
        if required and not targets:
            raise ValueError(f'{clip["name"]}: required Blink targets missing')
        blink_activity[clip['name']] = {}
        for target in targets:
            source_values = source_activity[clip['name']].get(target, [])
            exported_values = exported_targets.get(target, [])
            blink_activity[clip['name']][target] = {
                'source_min_max': [min(source_values), max(source_values)] if source_values else None,
                'persisted_min_max': [min(exported_values), max(exported_values)] if exported_values else None,
                'source_final': source_values[-1] if source_values else None,
                'persisted_final': exported_values[-1] if exported_values else None, 'required': required}
            if required:
                if not source_values or not exported_values:
                    raise ValueError(f'{clip["name"]}/{target}: required Blink animation track missing')
                excursion = config.get('face', {}).get('min_blink_excursion', .9)
                if max(source_values) - min(source_values) < excursion or max(exported_values) - min(exported_values) < excursion:
                    raise ValueError(f'{clip["name"]}/{target}: required Blink activity below {excursion}; declare blink_required:false only for an intended exception')
            if 'held_blink' in clip:
                if not source_values or not exported_values or max(abs(source_values[-1] - clip['held_blink']), abs(exported_values[-1] - clip['held_blink'])) > tolerance:
                    raise ValueError(f'{clip["name"]}/{target}: final held Blink value differs from contract')
    # Clean scene, fresh reimport of precisely the saved bytes being certified.
    load(path)
    target_records = neutral_records()
    mapped, correspondence = {}, {}
    for name, source_signature in signatures.items():
        def matches(obj):
            while obj:
                if obj.name == name or obj.name.startswith(name + '.'):
                    return True
                obj = obj.parent
            return False
        candidates = [entry['obj'] for entry in target_records.values() if matches(entry['obj'])]
        if not candidates:
            candidates = [entry['obj'] for entry in target_records.values()
                          if entry['obj'].name == source_mesh_names[name]]
        if not candidates:
            candidates = [entry['obj'] for entry in target_records.values()
                          if entry['obj'].name.startswith(source_mesh_names[name] + '.')]
        if not candidates:
            source_set = set(source_signature)
            candidates = [entry['obj'] for entry in target_records.values()
                          if set(vertex_signatures(entry['obj'])) == source_set]
        if len(candidates) != 1:
            raise ValueError(f'Missing/ambiguous reopened character mesh {name}; found {[obj.name for obj in candidates]}')
        obj = candidates[0]
        compatible = np.max(np.abs(source_matrices[name] - np.array(obj.matrix_world))) < 1e-5
        try:
            mapping = match_vertices(identities[name], identity_records(obj), local_compatible=compatible)
        except ValueError as error:
            raise ValueError(f'{name}: {error}') from error
        mapped[name], correspondence[name] = obj, np.array(mapping, dtype=int)
        actual = [key.name for key in obj.data.shape_keys.key_blocks[1:]] if obj.data.shape_keys else []
        if actual != source_morphs.get(name, []):
            raise ValueError(f'{name}: persisted morph coverage differs')
    errors, loop_errors, motion_ranges = {}, {}, {}
    for clip in config['motion']['clips']:
        reset()
        # Blender glTF import creates one multi-slot action per named glTF clip.
        action = bpy.data.actions.get(clip['name'])
        if not action:
            matches = [a for a in bpy.data.actions if a.name.startswith(clip['name'] + '_')]
            if len(matches) != 1:
                raise ValueError(f'Cannot locate reopened action {clip["name"]}; actual {[a.name for a in bpy.data.actions]}')
            action = matches[0]
        bound = 0
        for obj in bpy.context.scene.objects:
            for data in (obj, obj.data.shape_keys if obj.type == 'MESH' else None):
                if data is None or not data.animation_data:
                    continue
                # Importer's NLA strips retain unambiguous action/slot ownership.
                slots = [(strip.action, strip.action_slot) for track in data.animation_data.nla_tracks for strip in track.strips
                         if strip.action == action]
                if len(slots) == 1:
                    data.animation_data.action = action
                    data.animation_data.action_slot = slots[0][1]
                    data.animation_data.use_nla = False
                    bound += 1
        if not bound:
            raise ValueError(f'No actual imported slots bound for {clip["name"]}')
        peak = 0.
        endpoints = []
        for index, seconds in enumerate(times_by_clip[clip['name']]):
            frame(seconds * fps())
            sampled = {}
            for name, obj in mapped.items():
                points = evaluated_points(obj)
                reference = native[clip['name']][index][name][correspondence[name]]
                if points.shape != reference.shape or not np.isfinite(points).all():
                    raise ValueError(f'{name}: invalid evaluated reopened geometry')
                peak = max(peak, float(np.max(np.linalg.norm(points - reference, axis=1))))
                sampled[name] = points
            if index in (0, len(times_by_clip[clip['name']]) - 1):
                endpoints.append(sampled)
        errors[clip['name']] = peak
        if peak > config['certification']['max_vertex_error']:
            raise ValueError(f'{clip["name"]}: persisted skin/morph deformation error {peak} > {config["certification"]["max_vertex_error"]}')
        moving = max(float(np.max(np.linalg.norm(sample[name] - native[clip['name']][0][name], axis=1)))
                     for sample in native[clip['name']] for name in source_records)
        motion_ranges[clip['name']] = moving
        if clip.get('moving', False) and moving < config['certification'].get('min_motion', 1e-6):
            raise ValueError(f'{clip["name"]}: stale/static source evaluation')
        if clip['loop']:
            seam = max(float(np.max(np.linalg.norm(endpoints[0][name] - endpoints[1][name], axis=1))) for name in mapped)
            loop_errors[clip['name']] = seam
            if seam > config['certification']['max_loop_vertex_error']:
                raise ValueError(f'{clip["name"]}: persisted loop seam {seam}')
    proof = {'numerical_status': 'pass', 'glb_path': str(path), 'glb_sha256': hashed(path),
             'clean_scene_reimport': True, 'timings': timings, 'max_vertex_error': errors,
             'loop_vertex_error': loop_errors, 'source_motion': motion_ranges, 'morphs': source_morphs,
             'blink_activity': blink_activity,
             'units': 'native Blender scene units', 'thresholds': config['certification'],
             'visual_status': 'needs_review', 'runtime_status': 'not_verified',
             'holds': ['Temporal playback, anatomical contacts/reach/tail, facial closure and runtime integration require appropriate review/evidence']}
    return {'certification': dump(directory / 'certification.json', proof)}


def setup_camera(config, view, face_view=False):
    preview = config.get('preview', {})
    cameras = config.get('face', {}).get('probes', {}).get('cameras', {}) if face_view else preview.get('cameras', {})
    coordinates = np.concatenate([evaluated_points(obj) for obj in meshes()])
    low, high = coordinates.min(axis=0), coordinates.max(axis=0)
    center = Vector((low + high) / 2)
    size = float(max(high - low))
    directions = {'front': Vector((0, -1, 0.1)), 'oblique': Vector((1, -1.5, 0.5)), 'side': Vector((1, 0, 0.1))}
    if view not in directions and view not in cameras:
        raise NeedsInput(f'Missing camera definition for {view}')
    definition = cameras.get(view, {})
    target = Vector(definition.get('target', list(center)))
    location = Vector(definition.get('location', list(target + directions.get(view, Vector((0, -1, 0))) * size * 2.5)))
    data = bpy.data.cameras.new('PipelineCamera')
    obj = bpy.data.objects.new('PipelineCamera', data)
    bpy.context.collection.objects.link(obj)
    obj.location = location
    obj.rotation_euler = (target - location).to_track_quat('-Z', 'Y').to_euler()
    data.type = 'ORTHO'
    data.ortho_scale = definition.get('ortho_scale', size * 1.35)
    bpy.context.scene.camera = obj
    return obj


def setup_render(config):
    scene = bpy.context.scene
    scene.render.engine = 'BLENDER_EEVEE_NEXT' if 'BLENDER_EEVEE_NEXT' in [item.identifier for item in scene.render.bl_rna.properties['engine'].enum_items] else 'BLENDER_EEVEE'
    scene.render.resolution_x = scene.render.resolution_y = config.get('preview', {}).get('resolution', 192)
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = 'PNG'
    scene.render.film_transparent = False
    scene.world = bpy.data.worlds.new('PipelineWorld')
    scene.world.use_nodes = True
    scene.world.node_tree.nodes['Background'].inputs[0].default_value = (0.35, 0.35, 0.35, 1)
    scene.world.node_tree.nodes['Background'].inputs[1].default_value = 0.8
    for location in ((3, -4, 5), (-3, -2, 3)):
        data = bpy.data.lights.new('PipelineLight', 'AREA')
        data.energy = 500
        data.shape = 'DISK'
        data.size = 5
        obj = bpy.data.objects.new(data.name, data)
        bpy.context.collection.objects.link(obj)
        obj.location = location
        obj.rotation_euler = (-obj.location).to_track_quat('-Z', 'Y').to_euler()


def render(path):
    bpy.context.scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)
    return str(path)


def sheet(paths, destination, columns):
    images = [bpy.data.images.load(str(path), check_existing=False) for path in paths]
    width, height = images[0].size
    rows = math.ceil(len(images) / columns)
    pixels = np.zeros((rows * height, columns * width, 4), dtype=np.float32)
    for index, image in enumerate(images):
        array = np.array(image.pixels[:], dtype=np.float32).reshape(height, width, 4)
        row, col = divmod(index, columns)
        pixels[(rows - row - 1) * height:(rows - row) * height, col * width:(col + 1) * width] = array
        bpy.data.images.remove(image)
    image = bpy.data.images.new('PipelineSheet', width=columns * width, height=rows * height)
    image.pixels.foreach_set(pixels.ravel())
    image.filepath_raw = str(destination)
    image.file_format = 'PNG'
    image.save()
    bpy.data.images.remove(image)
    return str(destination)


def face_probes(job):
    config, directory = job['config'], Path(job['output_directory'])
    probes = config.get('face', {}).get('probes', {})
    if config['face']['mode'] == 'none' or not probes.get('enabled', True):
        return {'probes': dump(directory / 'probes.json', {'status': 'not_applicable' if config['face']['mode'] == 'none' else 'disabled_explicitly', 'visual_status': 'needs_review'})}
    load(job['inputs']['face']['native'])
    reset()
    name = config['face'].get('blink_name', 'Blink')
    targets = [obj for obj in meshes() if obj.data.shape_keys and name in obj.data.shape_keys.key_blocks]
    if not targets:
        raise NeedsInput(f'Facial probe matrix needs calibrated {name} morph')
    setup_render(config)
    values = probes.get('values', [0, .5, .65, .8, .9, .95, 1])
    views = probes.get('views', ['front', 'oblique', 'side'])
    paths, records = [], []
    for rolled in (False, True):
        reset()
        if rolled:
            pose = probes.get('rolled_pose_degrees')
            if not pose:
                raise NeedsInput('Specify anatomical Head+Jaw rolled_pose_degrees for facial probes')
            armature = rig(config)
            for role, angles in pose.items():
                bone_name = config['anatomy']['roles'].get(role)
                if not bone_name:
                    raise NeedsInput(f'Rolled probe missing anatomy.roles.{role}')
                bone = armature.pose.bones[bone_name]
                bone.rotation_mode = 'XYZ'
                bone.rotation_euler = [math.radians(x) for x in angles]
        for view in views:
            camera = setup_camera(config, view, face_view=True)
            for value in values:
                for obj in targets:
                    obj.data.shape_keys.key_blocks[name].value = value
                bpy.context.view_layer.update()
                path = directory / f'{"rolled" if rolled else "neutral"}_{view}_{value:g}.png'
                paths.append(render(path))
                records.append({'pose': 'rolled' if rolled else 'neutral', 'view': view, 'blink': value, 'path': str(path)})
            bpy.data.objects.remove(camera, do_unlink=True)
    artifacts = {'probes': dump(directory / 'probes.json', {'matrix': records, 'visual_status': 'needs_review',
                 'boundary': 'early deformation views; original neutral-art comparison and reopened skinned review remain separate'}),
                 'sheet': sheet(paths, directory / 'contact_sheet.png', len(values))}
    artifacts.update({f'probe_{i}': path for i, path in enumerate(paths)})
    return artifacts


def preview(job):
    config, directory = job['config'], Path(job['output_directory'])
    load(job['inputs']['export']['glb'])
    setup_render(config)
    records, artifacts = [], {}
    for clip in config['motion']['clips']:
        reset()
        candidates = [a for a in bpy.data.actions if a.name == clip['name'] or a.name.startswith(clip['name'] + '_')]
        if len(candidates) != 1:
            raise ValueError(f'Ambiguous reopened playback action {clip["name"]}')
        action = candidates[0]
        for obj in bpy.context.scene.objects:
            for data in (obj, obj.data.shape_keys if obj.type == 'MESH' else None):
                if data is None or not data.animation_data:
                    continue
                slots = [(strip.action, strip.action_slot) for track in data.animation_data.nla_tracks for strip in track.strips if strip.action == action]
                if len(slots) == 1:
                    data.animation_data.action, data.animation_data.action_slot = action, slots[0][1]
                    data.animation_data.use_nla = False
        rate = config.get('preview', {}).get('fps', 12)
        times = sorted({0., clip['duration_s'], *[i / rate for i in range(math.ceil(clip['duration_s'] * rate))]})
        for view in config.get('preview', {}).get('views', ['oblique', 'side']):
            frame(0)
            camera = setup_camera(config, view)
            paths = []
            for index, seconds in enumerate(times):
                frame(seconds * fps())
                safe_name = re.sub(r'[^A-Za-z0-9_-]', '_', clip['name']) + '_' + hashlib.sha256(clip['name'].encode()).hexdigest()[:6]
                path = directory / f'{safe_name}_{view}_{index:04d}.png'
                paths.append(render(path))
                artifacts[f'frame_{len(artifacts)}'] = str(path)
            records.append({'clip': clip['name'], 'view': view, 'times_s': times, 'frames': paths, 'loop': clip['loop']})
            bpy.data.objects.remove(camera, do_unlink=True)
    html = '<!doctype html><meta charset="utf-8"><title>Companion playback review</title><style>body{background:#222;color:#eee;font:16px sans-serif}img{max-width:90vw}section{display:inline-block;margin:1em}</style>'
    html += '<p>Persisted GLB frame playback. Numerical checks do not establish visual or runtime acceptance.</p>'
    for record in records:
        paths = [Path(path).name for path in record['frames']]
        encoded = json.dumps({'frames': paths, 'times': record['times_s'], 'loop': record['loop']}).replace('<', '\\u003c')
        import html as html_module
        html += f'<section><p>{html_module.escape(record["clip"])} / {html_module.escape(record["view"])}</p><img data-sequence="{html_module.escape(encoded, quote=True)}" src="{paths[0]}"><br><button>Replay</button></section>'
    html += '<script>for(const img of document.querySelectorAll("img")){const s=JSON.parse(img.dataset.sequence);let start=performance.now();img.parentNode.querySelector("button").onclick=()=>{start=performance.now()};function tick(now){let t=(now-start)/1000;const duration=s.times.at(-1);t=s.loop?t%duration:Math.min(t,duration);let i=0;while(i+1<s.times.length&&s.times[i+1]<=t)i++;if(img.dataset.index!==String(i)){img.src=s.frames[i];img.dataset.index=String(i)}requestAnimationFrame(tick)}requestAnimationFrame(tick)}</script>'
    html_path = directory / 'playback.html'
    html_path.write_text(html, encoding='utf-8')
    artifacts.update(preview=dump(directory / 'preview.json', {'glb_sha256': hashed(job['inputs']['export']['glb']), 'sequences': records,
                     'visual_status': 'needs_review', 'playback': str(html_path)}), playback=str(html_path))
    return artifacts


def main():
    job = json.loads(Path(sys.argv[sys.argv.index('--') + 1]).read_text(encoding='utf-8'))
    try:
        functions = {'inspect': inspection, 'anatomy': anatomy, 'motion': motion, 'face': face,
                     'face_probes': face_probes, 'export': export, 'certify': certify, 'preview': preview}
        artifacts = functions[job['stage']](job)
        result = {'run_id': job['run_id'], 'status': 'success', 'artifacts': artifacts}
    except Exception as error:
        traceback.print_exc()
        result = {'run_id': job['run_id'], 'status': 'needs_input' if isinstance(error, NeedsInput) else 'failed',
                  'message': str(error)}
    dump(job['result_path'], result)
    if result['status'] == 'failed':
        raise RuntimeError(result['message'])


if __name__ == '__main__':
    main()

