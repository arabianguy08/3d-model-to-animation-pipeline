"""Synthetic regression evidence; these tests do not certify a character asset."""
import copy
import importlib
import math
from pathlib import Path
import tempfile
import unittest

import numpy as np

from companion_animation.locomotion.anatomy import anatomical_names
from companion_animation.locomotion.math_core import two_bone, skinned_pin_step, treadmill, clip_extras, runtime_time_scale
from companion_animation.locomotion.metrics import gates, stance_slide, corr, lag_seconds, rng, unwrap, rel_rot_angle
from companion_animation.locomotion.preservation import check_preserved, trs_limit, candidate_path
from companion_animation.locomotion.review_commands import blender_command, parse_args, grid_offset, VIEWS
from companion_animation.locomotion.configure import configuration
from companion_animation.pipeline.glb_io import read, write, append_float


def hierarchy(reverse=False):
    chain = ['Hips', *(['Spine02', 'Spine01', 'Spine'] if reverse else ['Spine', 'Spine1', 'Spine2']), 'Neck', 'Head']
    return {name: chain[i-1] if i else None for i, name in enumerate(chain)}


def good_clip():
    return {'loop': True, 'gait': 'walk', 'treadmill_speed_used': 1.,
            'contact': {'per_foot': {s: {'min_sole_z_rel_floor': 0., 'max_bearing_slide': .012,
                         'max_stance_slide': .012, 'bearing_support_steps': 5, 'near_floor_support_steps': 8,
                         'duty_factor': .5} for s in ('Left', 'Right')},
                        'double_support_fraction': 0., 'flight_fraction': 0.},
            'loop_seam_vertex': 1e-4, 'pop_ratio': 6., 'root_drift': {'hips_net_horizontal': .002},
            'knees': {s: {'bend_direction_flips': 0} for s in ('Left', 'Right')},
            'intersections': {k: {'excess_triangles_over_rest': 0} for k in
                              ('Left_arm_x_torso', 'Right_arm_x_torso', 'left_leg_x_right_leg')},
            'motion_principles': {'head_vs_chest_rotation_range_deg': 1.5, 'elbow_flex_range_deg': {'Left': 3., 'Right': 3.},
                                  'chest_vs_pelvis_yaw_range_deg': 2., 'shoulder_line_vs_hip_line_yaw_corr': -.1,
                                  'arm_vs_same_side_thigh_corr': {'Left': -.1, 'Right': -.1}}}


class AnatomyTests(unittest.TestCase):
    def test_reversed_spine(self):
        roles, chain = anatomical_names(hierarchy(True))
        self.assertEqual(chain, ['Spine02', 'Spine01', 'Spine', 'Neck'])
        self.assertEqual([roles[n] for n in chain], ['Spine', 'Spine1', 'Spine2', 'Neck'])

    def test_standard_and_namespaced(self):
        bones = {'mixamorig:'+n: 'mixamorig:'+p if p else None for n, p in hierarchy().items()}
        self.assertEqual(anatomical_names(bones)[0]['mixamorig:Spine2'], 'Spine2')

    def test_custom_names(self):
        original = hierarchy(True)
        custom = {'joint'+str(i): n for i, n in enumerate(original)}
        inverse = {v: k for k, v in custom.items()}
        bones = {n: inverse[original[c]] if original[c] else None for n, c in custom.items()}
        self.assertEqual(anatomical_names(bones, custom)[0]['joint1'], 'Spine')

    def test_invalid_chains(self):
        for bones in ({}, {'Head': 'Head', 'Hips': None}, {**hierarchy(), 'Neck': 'missing'}):
            with self.subTest(bones=bones), self.assertRaises(ValueError):
                anatomical_names(bones)

    def test_ambiguous_roles(self):
        with self.assertRaises(ValueError):
            anatomical_names({**hierarchy(), 'mixamorigHead': None})


class MathTests(unittest.TestCase):
    def test_two_bone_preserves_lengths_and_plane(self):
        knee, ankle, pole = two_bone([0, 0, 0], [0, 0, -1.5], [0, -1, -.5], 1., 1.)
        self.assertAlmostEqual(np.linalg.norm(knee), 1.)
        self.assertAlmostEqual(np.linalg.norm(ankle-knee), 1.)
        np.testing.assert_allclose(ankle, [0, 0, -1.5])
        self.assertLess(knee[1], 0)
        self.assertAlmostEqual(pole[0], 0)

    def test_singular_plane_transport(self):
        knee, ankle, pole = two_bone([0, 0, 0], [1, 0, -1], [.5, 0, -.5], 1., 1., [0, -1, -.1])
        self.assertAlmostEqual(pole @ (ankle / np.linalg.norm(ankle)), 0.)
        self.assertLess(pole[1], 0)
        self.assertAlmostEqual(np.linalg.norm(knee), 1.)

    def test_plane_sign_and_unreachable_target(self):
        knee, ankle, pole = two_bone([0, 0, 0], [0, 0, -10], [0, 1, -.5], 1., 1., [0, -1, 0])
        self.assertLess(pole[1], 0)
        self.assertLess(np.linalg.norm(ankle), 2.)
        self.assertAlmostEqual(np.linalg.norm(ankle-knee), 1.)

    def test_folded_unequal_lengths(self):
        knee, ankle, _ = two_bone([0, 0, 0], [0, 0, -.1], [0, -1, 0], 2., 1.)
        self.assertAlmostEqual(np.linalg.norm(knee), 2.)
        self.assertAlmostEqual(np.linalg.norm(ankle-knee), 1.)

    def test_zero_target_fails(self):
        with self.assertRaises(ValueError):
            two_bone([0, 0, 0], [0, 0, 0], [1, 0, 0], 1, 1)

    def test_skinned_patch_treadmill_lock(self):
        previous = np.array([[0., 0., 0.], [0., .2, .01]])
        current = previous + [0., .04, -.02]
        correction = skinned_pin_step(current, previous, np.zeros(3), [0., .2, 0.], .1, 0., .05)
        np.testing.assert_allclose(correction, [0., -.02, .02])
        np.testing.assert_allclose((current+correction-previous)[:, :2], [[0., .02], [0., .02]])

    def test_rolling_patch_handover(self):
        previous = np.array([[0., 0., 0.], [0., .2, .1]])
        current = np.array([[0., .1, .2], [0., .3, .02]])
        correction = skinned_pin_step(current, previous, np.zeros(3), [0., .5, 0.], .1, 0., .01)
        self.assertAlmostEqual((current+correction)[:, 2].min(), 0.)
        self.assertAlmostEqual((current+correction)[1, 1], .25)

    def test_pin_touchdown_and_empty(self):
        np.testing.assert_allclose(skinned_pin_step([[0, 0, .2]], None, None, [0, 0, 0], .1, 0, .01), [0, 0, -.2])
        with self.assertRaises(ValueError):
            skinned_pin_step([], None, None, [0, 0, 0], .1, 0, .01)

    def test_treadmill_preserves_bob(self):
        hips = np.array([[1., 2., 1.], [1., 3., 1.2], [1., 4., 1.]])
        ramp, travel, speed = treadmill(hips, [5., 6., 7.])
        self.assertEqual(speed, 1.)
        np.testing.assert_allclose((hips-ramp)[:, 1], 2.)
        np.testing.assert_allclose((hips-ramp)[:, 2], hips[:, 2])
        np.testing.assert_allclose(travel, [0, 2, 0])
        self.assertEqual(treadmill(hips, [5, 6, 7], False)[2], 0.)

    def test_invalid_times(self):
        with self.assertRaises(ValueError):
            treadmill([[0, 0, 0], [0, 1, 0]], [0, 0])

    def test_extras_and_runtime_rate(self):
        self.assertEqual(clip_extras(True, .8, 'walk')['treadmillSpeed'], .8)
        self.assertEqual(runtime_time_scale(80, 100, .8), 1.)
        self.assertEqual(runtime_time_scale(800, 100, .8), 1.6)
        self.assertEqual(runtime_time_scale(1, 0, .8), 1.)
        with self.assertRaises(ValueError):
            clip_extras(True, float('nan'))


class MetricsTests(unittest.TestCase):
    def test_boundary_gates_pass(self):
        self.assertTrue(all(g['pass'] for g in gates('custom', good_clip(), 1.).values()))

    def test_missing_contact_invalid(self):
        clip = good_clip()
        clip['contact']['per_foot']['Left'].update(max_bearing_slide=0., bearing_support_steps=0)
        gate = gates('walk', clip, 1.)['Left_bearing_slide']
        self.assertEqual(gate['status'], 'invalid')
        self.assertFalse(gate['pass'])
        clip['contact']['per_foot'] = {}
        self.assertEqual(gates('walk', clip, 1.)['Right_near_floor_slide']['status'], 'invalid')

    def test_slide_requires_consecutive_support(self):
        points = np.array([[[0, 0, 0]], [[0, 1, .1]], [[0, 2, 0]]])
        self.assertEqual(stance_slide(points, 0., .0015, [0, 1], 1.)['status'], 'invalid')

    def test_airborne_zero_motion_is_not_slide_pass(self):
        measured = stance_slide(np.array([[[0., 0., .2]], [[0., 0., .2]]]), 0., .0015, [0, 0], .1)
        self.assertIsNone(measured['slide'])
        clip = good_clip()
        clip['contact']['per_foot']['Left'].update(max_bearing_slide=measured['slide'], bearing_support_steps=measured['support_steps'])
        self.assertEqual(gates('walk', clip, 1.)['Left_bearing_slide']['status'], 'invalid')

    def test_scaled_height_and_rest_crossing_baseline(self):
        clip = good_clip()
        clip['intersections']['Left_arm_x_torso']['excess_triangles_over_rest'] = -2
        clip['contact']['per_foot']['Left']['max_bearing_slide'] = .024
        self.assertTrue(gates('walk', clip, 2.)['Left_bearing_slide']['pass'])
        self.assertTrue(gates('walk', clip, 2.)['Left_arm_x_torso_excess_crossings']['pass'])

    def test_slide_reset_and_treadmill(self):
        points = np.array([[[0., 0., 0.]], [[0., .11, 0.]], [[0., .22, .1]], [[0., .33, 0.]], [[0., .44, 0.]]])
        result = stance_slide(points, 0., .0015, [0, .1], 1.)
        self.assertAlmostEqual(result['slide'], .01)
        self.assertEqual(result['support_steps'], 2)

    def test_nonloop_seam_excluded(self):
        clip = good_clip(); clip['loop'] = False
        self.assertNotIn('loop_seam', gates('idle', clip, 1.))

    def test_nan_is_invalid(self):
        clip = good_clip(); clip['pop_ratio'] = float('nan')
        self.assertEqual(gates('walk', clip, 1.)['pop_ratio']['status'], 'invalid')

    def test_motion_measurements(self):
        wave = np.sin(np.arange(120) * 2 * math.pi / 120)
        self.assertAlmostEqual(corr(wave, -wave), -1.)
        self.assertEqual(corr(np.zeros(120), wave), 0.)
        self.assertAlmostEqual(lag_seconds(wave, np.roll(wave, 12), 1/120, True), .1)
        self.assertIsNone(lag_seconds(np.zeros(120), wave, 1/120, True))
        self.assertAlmostEqual(rng(wave), 2.)
        np.testing.assert_allclose(unwrap([179, -179]), [179, 181])
        self.assertEqual(rel_rot_angle(np.eye(4), np.eye(4)), 0.)


# Each threshold gets an independent failing regression, including strict zero correlations.
CASES = {
    'penetration': ('Left_floor_penetration', ('contact', 'per_foot', 'Left', 'min_sole_z_rel_floor'), -.00601),
    'bearing_slide': ('Left_bearing_slide', ('contact', 'per_foot', 'Left', 'max_bearing_slide'), .01201),
    'near_slide': ('Left_near_floor_slide', ('contact', 'per_foot', 'Left', 'max_stance_slide'), .01201),
    'duty': ('Left_walk_duty_factor', ('contact', 'per_foot', 'Left', 'duty_factor'), .4999),
    'flight': ('walk_flight_fraction', ('contact', 'flight_fraction'), .001),
    'double_support': ('walk_double_support', ('contact', 'double_support_fraction'), -.01),
    'seam': ('loop_seam', ('loop_seam_vertex',), .000101),
    'pop': ('pop_ratio', ('pop_ratio',), 6.01),
    'hips': ('root_drift', ('root_drift', 'hips_net_horizontal'), .00201),
    'knee': ('Left_knee_flips', ('knees', 'Left', 'bend_direction_flips'), 1),
    'arm_crossing': ('Left_arm_x_torso_excess_crossings', ('intersections', 'Left_arm_x_torso', 'excess_triangles_over_rest'), 1),
    'leg_crossing': ('left_leg_x_right_leg_excess_crossings', ('intersections', 'left_leg_x_right_leg', 'excess_triangles_over_rest'), 1),
    'head': ('head_moves_on_chest', ('motion_principles', 'head_vs_chest_rotation_range_deg'), 1.49),
    'elbow': ('Left_elbow_not_rigid', ('motion_principles', 'elbow_flex_range_deg', 'Left'), 2.99),
    'chest': ('chest_counter_rotates_pelvis', ('motion_principles', 'chest_vs_pelvis_yaw_range_deg'), 1.99),
    'counter_phase': ('shoulders_vs_hips_counter_phase', ('motion_principles', 'shoulder_line_vs_hip_line_yaw_corr'), 0.),
    'arm_opposition': ('Left_arm_opposes_thigh', ('motion_principles', 'arm_vs_same_side_thigh_corr', 'Left'), 0.),
}


def gate_test(key, path, value):
    def test(self):
        clip = good_clip()
        target = clip
        for part in path[:-1]:
            target = target[part]
        target[path[-1]] = value
        self.assertEqual(gates('custom_name', clip, 1.)[key]['status'], 'fail')
    return test


for case, args in CASES.items():
    setattr(MetricsTests, 'test_gate_' + case, gate_test(*args))


class PreservationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source = Path(self.tmp.name) / 'source.glb'
        self.candidate = Path(self.tmp.name) / 'candidate.glb'
        doc = {'asset': {'version': '2.0'}, 'nodes': [{'name': 'Root'}], 'meshes': [], 'skins': [],
               'animations': [{'name': 'idle', 'samplers': [], 'channels': [], 'extras': {'loop': False}}]}
        write(self.source, doc, bytearray(b'original'))

    def test_appended_sampler_and_binary(self):
        doc, binary = read(self.source)
        input_index = append_float(doc, binary, [0., 1.], 'SCALAR')
        doc['animations'].append({'name': 'walk', 'samplers': [{'input': input_index}], 'channels': [], 'extras': clip_extras(True, 1., 'walk')})
        write(self.candidate, doc, binary)
        self.assertTrue(check_preserved(self.source, self.candidate)['all_preserved'])
        self.assertEqual(read(self.candidate)[0]['animations'][1]['extras']['treadmillSpeed'], 1.)

    def test_corrupted_prefix_rejected(self):
        doc, binary = read(self.source); binary[0] ^= 1
        write(self.candidate, doc, binary)
        with self.assertRaisesRegex(ValueError, 'BIN prefix'):
            check_preserved(self.source, self.candidate)

    def test_changed_nodes_and_idle_rejected(self):
        for field in ('nodes', 'animations'):
            doc, binary = read(self.source)
            doc[field][0]['name'] = 'changed'
            write(self.candidate, doc, binary)
            with self.subTest(field=field), self.assertRaises(ValueError):
                check_preserved(self.source, self.candidate)

    def test_every_existing_clip_and_metadata_preserved(self):
        doc, binary = read(self.source)
        doc['animations'].append({'name': 'held', 'samplers': [], 'channels': []})
        write(self.source, doc, binary)
        doc['animations'][1]['channels'] = [{'target': {'node': 0, 'path': 'rotation'}, 'sampler': 0}]
        write(self.candidate, doc, binary)
        with self.assertRaisesRegex(ValueError, 'animation 1'):
            check_preserved(self.source, self.candidate)
        doc, binary = read(self.source)
        doc['animations'][0]['extras']['loop'] = True
        write(self.candidate, doc, binary)
        with self.assertRaisesRegex(ValueError, 'extras'):
            check_preserved(self.source, self.candidate)

    def test_no_overwrite(self):
        with self.assertRaises(ValueError):
            candidate_path(self.source, self.source)

    def test_trs_diagnosis_and_vertex_bound(self):
        self.assertEqual(trs_limit({}, 'abc'), 1e-5)
        config = {'export': {'max_trs_residual': 3.2e-5}}
        with self.assertRaises(ValueError):
            trs_limit(config, 'abc')
        config['export']['trs_diagnosis'] = {'source_sha256': 'abc', 'measured_residual': 3.066659e-5, 'note': 'Measured float32 bind precision'}
        self.assertEqual(trs_limit(config, 'abc'), 3.2e-5)
        with self.assertRaises(ValueError):
            trs_limit(config, 'different')
        config['certification'] = {'max_vertex_error': .001}
        with self.assertRaises(ValueError):
            trs_limit(config, 'abc')


class ReviewTests(unittest.TestCase):
    def test_defaults_and_command_quoting(self):
        argv = ['model with spaces.glb', 'review output', 'Review', '--clips', 'walk', '--combined', 'idle,walk']
        args = parse_args(argv)
        self.assertEqual((args.size, args.fps, args.loops), (720, 30, 3))
        command = blender_command('Blender Path/blender', argv)
        self.assertEqual(command[0], 'Blender Path/blender')
        self.assertEqual(command[-len(argv):], argv)
        self.assertEqual(VIEWS, ('front', 'side', 'three-quarter', 'runtime'))

    def test_invalid_options(self):
        for options in (['--size', '719'], ['--speed', 'walk=nan'], ['--runtime-fov', '180'], ['--unknown', 'x']):
            with self.subTest(options=options), self.assertRaises(SystemExit):
                parse_args(['model.glb', 'output', 'label', '--clips', 'walk', *options])

    def test_grid_speed_direction(self):
        np.testing.assert_allclose(grid_offset(.2, 1., 0., 1.), [0., .2])
        np.testing.assert_allclose(grid_offset(.2, 1., math.pi/2, 1.), [.8, 0.], atol=1e-10)

    def test_adapters_import_without_scene_or_argv(self):
        for name in ('retarget', 'append_export', 'review', 'qa'):
            module = importlib.import_module('companion_animation.locomotion.'+name)
            self.assertFalse(hasattr(module, 'JOB'))


class ConfigurationTests(unittest.TestCase):
    def test_idle_first_and_parameterized_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'model.glb'
            bones = hierarchy(True)
            names = list(bones)
            nodes = [{'name': n, 'children': [names.index(c) for c, p in bones.items() if p == n]} for n in names]
            doc = {'asset': {'version': '2.0'}, 'nodes': nodes, 'skins': [{'joints': list(range(len(nodes)))}],
                   'accessors': [{'min': [0.], 'max': [2.]}],
                   'animations': [{'name': 'resting', 'samplers': [{'input': 0}], 'channels': []}]}
            write(path, doc, bytearray())
            config, params = configuration(path, temp, 'blender', Path(temp)/'params.json',
                          [{'name': 'stroll', 'path': 'walk.fbx', 'duration_s': 1., 'loop': True, 'gait': 'walk', 'in_place': True}])
            self.assertEqual(config['motion']['clips'][0]['name'], 'resting')
            self.assertFalse(config['motion']['clips'][0]['loop'])
            self.assertEqual(config['anatomy']['roles']['Spine'], 'Spine02')
            self.assertEqual(params['author_hz'], 120)
            self.assertEqual(config['motion']['clips'][1]['gait'], 'walk')


if __name__ == '__main__':
    unittest.main()
