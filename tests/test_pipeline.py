"""Focused orchestration tests; optional real Blender roundtrip integration."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from companion_animation.pipeline import run_pipeline as pipeline

BLENDER = None


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='companion pipeline ')
        self.root = Path(self.temporary.name)
        self.source = self.root / 'source model.blend'
        self.source.write_bytes(b'original source')
        self.executable = self.root / 'fake blender.exe'
        self.executable.write_bytes(b'tool version')
        self.config = json.loads((pipeline.HERE / 'config.example.json').read_text())
        self.config.update(model_id='unit', workspace=str(self.root / 'isolated work'),
                           blender=str(self.executable), sources={'model': str(self.source)})
        self.config['anatomy'] = {'mode': 'existing', 'roles': {'Head': 'Head'}}
        self.config['motion'] = {'mode': 'reuse', 'clips': [{'name': 'Idle', 'duration_s': 1, 'loop': True}]}
        self.config['face']['mode'] = 'existing'
        self.path = self.root / 'config.json'
        pipeline.write(self.path, self.config)
        self.calls = []

    def tearDown(self):
        self.temporary.cleanup()

    def fake_run(self, command, **kwargs):
        job = pipeline.read(command[-1])
        self.calls.append(job['stage'])
        directory = Path(job['output_directory'])
        artifacts = {}
        for name in pipeline.REQUIRED[job['stage']]:
            output = directory / (name + '.json')
            content = {'stage': job['stage'], 'name': name}
            if name == 'certification':
                content = {'numerical_status': 'pass', 'glb_sha256': pipeline.sha(job['inputs']['export']['glb'])}
            pipeline.write(output, content)
            artifacts[name] = str(output)
        pipeline.write(job['result_path'], {'run_id': job['run_id'], 'status': 'success', 'artifacts': artifacts})
        return subprocess.CompletedProcess(command, 0)

    def run_mocked(self, through='export'):
        with patch.object(pipeline.subprocess, 'run', self.fake_run):
            return pipeline.Runner(self.path).run(through)

    def test_resume_noop_and_paths_with_spaces(self):
        self.assertEqual(self.run_mocked()['status'], 'success')
        self.calls.clear()
        result = self.run_mocked()
        self.assertEqual(result['executed'], [])
        self.assertEqual(result['cached'], list(pipeline.STAGES[:6]))
        self.assertFalse(self.calls)
        self.assertEqual(self.source.read_bytes(), b'original source')

    def test_face_change_preserves_body(self):
        self.run_mocked()
        self.config['face']['calibration_revision'] = 'second'
        pipeline.write(self.path, self.config)
        self.calls.clear()
        result = self.run_mocked()
        self.assertEqual(result['cached'], ['inspect', 'anatomy', 'motion'])
        self.assertEqual(self.calls, ['face', 'face_probes', 'export'])

    def test_probe_only_change_preserves_face_and_body(self):
        self.run_mocked()
        self.config['face']['probes']['values'] = [0, 1]
        pipeline.write(self.path, self.config)
        result = self.run_mocked()
        self.assertEqual(result['cached'], ['inspect', 'anatomy', 'motion', 'face', 'export'])
        self.assertEqual(result['executed'], ['face_probes'])

    def test_source_change_invalidates_dependents_preserves_snapshot(self):
        self.run_mocked()
        old = pipeline.Runner(self.path).state['inputs']['model']['path']
        self.source.write_bytes(b'changed source')
        self.calls.clear()
        result = self.run_mocked()
        self.assertEqual(result['cached'], [])
        self.assertEqual(len(self.calls), 6)
        self.assertEqual(Path(old).read_bytes(), b'original source')

    def test_corrupt_output_invalidates_dependents(self):
        self.run_mocked()
        runner = pipeline.Runner(self.path)
        Path(runner.artifacts('motion')['bake']).write_text('corruption')
        result = self.run_mocked()
        self.assertEqual(result['cached'], ['inspect', 'anatomy'])
        self.assertEqual(result['executed'], ['motion', 'face', 'face_probes', 'export'])

    def test_missing_anatomy_actionable_packet(self):
        self.config['anatomy'] = {'mode': None, 'roles': {}}
        pipeline.write(self.path, self.config)
        result = self.run_mocked()
        self.assertEqual(result['status'], 'needs_input')
        self.assertEqual(result['stage'], 'anatomy')
        self.assertIn('anatomy.roles', result['needed'])
        self.assertEqual(result['executed'], ['inspect'])

    def test_failing_adapter_cannot_reuse_prior_success(self):
        self.run_mocked('face')
        self.config['face']['revision'] = 'broken'
        pipeline.write(self.path, self.config)
        with patch.object(pipeline.subprocess, 'run', lambda command, **kwargs: subprocess.CompletedProcess(command, 1)):
            result = pipeline.Runner(self.path).run('face')
        self.assertEqual(result['status'], 'failed')
        self.assertNotIn('outputs', pipeline.Runner(self.path).state['stages']['face'])

    def test_stale_result_rejected_and_two_failures_need_diagnosis(self):
        def stale(command, **kwargs):
            job = pipeline.read(command[-1])
            pipeline.write(job['result_path'], {'run_id': 'old-id', 'status': 'success'})
            return subprocess.CompletedProcess(command, 0)
        with patch.object(pipeline.subprocess, 'run', stale):
            self.assertEqual(pipeline.Runner(self.path).run('inspect')['status'], 'failed')
            self.assertEqual(pipeline.Runner(self.path).run('inspect')['status'], 'needs_diagnosis')
        self.assertEqual(pipeline.Runner(self.path).state['stages']['inspect']['failures'], 2)
        with patch.object(pipeline.subprocess, 'run', self.fake_run):
            result = pipeline.Runner(self.path).run('inspect')
        self.assertEqual(result['status'], 'needs_diagnosis')
        self.assertFalse(self.calls)
        pipeline.main(['diagnose', str(self.path), '--stage', 'inspect', '--note', 'Adapter returned prior attempt ID; fixed generator'])
        self.assertEqual(self.run_mocked('inspect')['status'], 'success')

    def test_wrong_artifact_certification_hash_rejected(self):
        original = self.fake_run
        def false_proof(command, **kwargs):
            result = original(command, **kwargs)
            job = pipeline.read(command[-1])
            if job['stage'] == 'certify':
                pipeline.write(Path(job['output_directory']) / 'certification.json', {'numerical_status': 'pass', 'glb_sha256': 'stale'})
            return result
        with patch.object(pipeline.subprocess, 'run', false_proof):
            result = pipeline.Runner(self.path).run('certify')
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['stage'], 'certify')

    def test_external_output_rejected(self):
        def outside(command, **kwargs):
            job = pipeline.read(command[-1])
            pipeline.write(job['result_path'], {'run_id': job['run_id'], 'status': 'success',
                           'artifacts': {'native': str(self.source), 'inspection': str(self.path)}})
            return subprocess.CompletedProcess(command, 0)
        with patch.object(pipeline.subprocess, 'run', outside):
            result = pipeline.Runner(self.path).run('inspect')
        self.assertEqual(result['status'], 'failed')

    def test_status_does_not_write_state(self):
        self.run_mocked()
        runner = pipeline.Runner(self.path)
        before = runner.state_path.read_bytes()
        before_time = runner.state_path.stat().st_mtime_ns
        self.source.write_bytes(b'new source should not be snapshotted by status')
        pipeline.main(['status', str(self.path)])
        self.assertEqual(runner.state_path.read_bytes(), before)
        self.assertEqual(runner.state_path.stat().st_mtime_ns, before_time)

    def review_file(self):
        runner = pipeline.Runner(self.path)
        path = self.root / 'review.json'
        pipeline.write(path, {'artifact_sha256': pipeline.sha(runner.artifacts('export')['glb']),
                             'reviewer': 'independent specialist', 'verdict': 'pass',
                             'temporal_playback': True, 'facial_views': True})
        return path

    def test_review_cannot_complete_after_export_only(self):
        self.run_mocked()
        evidence = self.review_file()
        self.assertEqual(pipeline.main(['review', str(self.path), '--evidence', str(evidence)]), 2)
        self.assertNotIn('review', pipeline.Runner(self.path).state)

    def test_review_bound_to_current_config_and_outputs(self):
        self.assertEqual(self.run_mocked('package')['status'], 'needs_review')
        evidence = self.review_file()
        self.assertEqual(pipeline.main(['review', str(self.path), '--evidence', str(evidence)]), 0)
        self.config['preview']['resolution'] += 1
        pipeline.write(self.path, self.config)
        self.assertEqual(pipeline.main(['review', str(self.path), '--evidence', str(evidence)]), 2)
        result = self.run_mocked('package')
        self.assertEqual(result['status'], 'needs_review')
        self.assertIn('export', result['cached'])
        self.assertIn('certify', result['cached'])

    def test_stale_lock_and_running_state_resume(self):
        self.run_mocked('inspect')
        runner = pipeline.Runner(self.path)
        (runner.workspace / 'owner.lock').write_text('4294967000')
        runner.state['stages']['inspect']['status'] = 'running'
        runner.save()
        result = self.run_mocked('inspect')
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['executed'], ['inspect'])


class BlenderTests(unittest.TestCase):
    def test_actual_rig_blink_roundtrip(self):
        if not BLENDER:
            self.skipTest('Pass --blender EXECUTABLE for actual Blender smoke')
        temporary = tempfile.TemporaryDirectory(prefix='companion-blender-integration-')
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        directory = root / ('fixture ' + pipeline.uuid.uuid4().hex[:8])
        directory.mkdir()
        self.fixture = directory
        with (directory / 'fixture.log').open('w') as log:
            result = subprocess.run([BLENDER, '--background', '--factory-startup', '--python-exit-code', '1',
                                     '--python', str(pipeline.HERE / 'make_smoke_fixture.py'), '--', str(directory)],
                                     stdout=log, stderr=subprocess.STDOUT,
                                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.assertEqual(result.returncode, 0, str(directory / 'fixture.log'))
        config_path = directory / 'smoke.json'
        config = pipeline.read(config_path)
        config['blender'] = BLENDER
        pipeline.write(config_path, config)
        result = pipeline.Runner(config_path).run()
        pipeline.write(directory / 'smoke_result.json', result)
        self.assertEqual(result['status'], 'needs_review', json.dumps(result))
        runner = pipeline.Runner(config_path)
        proof = pipeline.read(runner.artifacts('certify')['certification'])
        self.assertEqual(proof['numerical_status'], 'pass')
        self.assertEqual(set(proof['timings']), {'Walk', 'Held'})
        self.assertIn('Blink', proof['morphs']['FixtureFace'])
        self.assertEqual(proof['glb_sha256'], pipeline.sha(runner.artifacts('export')['glb']))
        probes = pipeline.read(runner.artifacts('face_probes')['probes'])
        self.assertEqual(len(probes['matrix']), 42)
        resumed = pipeline.Runner(config_path).run()
        self.assertEqual(resumed['executed'], [])
        print(json.dumps({'real_blender_fixture': str(config_path), 'result': result, 'verification': proof['max_vertex_error']}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--blender')
    args, remaining = parser.parse_known_args()
    BLENDER = args.blender
    unittest.main(argv=[sys.argv[0], *remaining])
