"""Resumable companion pipeline. Python standard library; Blender runs hidden."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid

HERE = Path(__file__).resolve().parent
STAGES = ('inspect', 'anatomy', 'motion', 'face', 'face_probes', 'export',
          'certify', 'preview', 'package')
DEPS = {'inspect': (), 'anatomy': ('inspect',), 'motion': ('anatomy',),
        'face': ('motion',), 'face_probes': ('face',),
        'export': ('motion', 'face'), 'certify': ('export', 'face'),
        'preview': ('certify', 'export'), 'package': ('preview', 'certify', 'export')}
SECTIONS = {'inspect': ('normalization',), 'anatomy': ('anatomy',),
            'motion': ('motion', 'sampling'), 'face': ('face',),
            'face_probes': ('face', 'preview'), 'export': ('export', 'motion'),
            'certify': ('certification', 'motion', 'face'),
            'preview': ('preview', 'motion'), 'package': ('package',)}
REQUIRED = {'inspect': {'native', 'inspection'}, 'anatomy': {'native'},
            'motion': {'native', 'bake'}, 'face': {'native'},
            'face_probes': {'probes'}, 'export': {'glb'},
            'certify': {'certification'}, 'preview': {'preview'}, 'package': {'package'}}


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False), encoding='utf-8')
    temporary.replace(path)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def pid_alive(pid):
    if pid <= 0 or pid > (0xffffffff if os.name == 'nt' else 0x7fffffff):
        return False
    if os.name == 'nt':
        import ctypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x00100000, False, pid)
        if not handle:
            return ctypes.get_last_error() == 5
        try:
            return kernel.WaitForSingleObject(handle, 0) != 0
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


class PipelineError(Exception):
    def __init__(self, status, message, stage=None, needed=None):
        super().__init__(message)
        self.status, self.stage, self.needed = status, stage, needed


class Runner:
    def __init__(self, config_path):
        self.config_path = Path(config_path).resolve()
        self.config = read(self.config_path)
        if self.config.get('schema_version') != 1:
            raise PipelineError('failed', 'schema_version must be 1')
        model = self.config.get('model_id', '')
        if not re.fullmatch(r'[A-Za-z0-9_-]+', model):
            raise PipelineError('failed', 'model_id must use letters, digits, _ or -')
        self.workspace = self.path(self.config['workspace']) / model
        self.state_path = self.workspace / 'state.json'
        self.state = read(self.state_path) if self.state_path.exists() else {'schema_version': 1, 'stages': {}}
        self.tool_hash = None
        self.snapshots = {}

    def path(self, value):
        path = Path(value)
        return (self.config_path.parent / path).resolve() if not path.is_absolute() else path.resolve()

    def save(self):
        write(self.state_path, self.state)

    def inputs(self, persist=True):
        sources = self.config.get('sources', {})
        if not sources.get('model'):
            raise PipelineError('needs_input', 'Missing sources.model', 'inspect', ['sources.model'])
        for name, value in sources.items():
            original = self.path(value)
            if not original.is_file():
                raise PipelineError('needs_input', f'Missing source: {original}', 'inspect', [str(original)])
            fingerprint = sha(original)
            expected = self.config.get('source_hashes', {}).get(name)
            if expected and expected != fingerprint:
                raise PipelineError('failed', f'Input hash differs from declared source_hashes.{name}', 'inspect')
            copied = self.workspace / 'inputs' / name / (fingerprint + original.suffix.lower())
            if persist:
                copied.parent.mkdir(parents=True, exist_ok=True)
            if copied.exists() and sha(copied) != fingerprint:
                raise PipelineError('failed', f'Immutable snapshot corrupted: {copied}', 'inspect')
            if persist and not copied.exists():
                shutil.copy2(original, copied)
                if sha(original) != fingerprint or sha(copied) != fingerprint:
                    raise PipelineError('failed', f'Input changed during snapshot: {original}', 'inspect')
            self.snapshots[name] = {'path': str(copied), 'sha256': fingerprint, 'original': str(original)}
        if persist:
            self.state['inputs'] = self.snapshots
            self.save()

    def artifacts(self, stage):
        return {name: entry['path'] for name, entry in self.state['stages'][stage]['outputs'].items()}

    def adapter(self, stage):
        section = self.config.get(stage if stage in ('anatomy', 'motion', 'face') else 'adapters', {})
        return section.get('adapter') if stage in ('anatomy', 'motion', 'face') else section.get(stage)

    def key(self, stage):
        relevant = {key: self.config.get(key) for key in SECTIONS[stage]}
        # Probes affect neither facial preparation nor cached body motion.
        if stage == 'face' and isinstance(relevant.get('face'), dict):
            relevant['face'] = {k: v for k, v in relevant['face'].items() if k != 'probes'}
        if stage == 'face_probes':
            relevant['preview'] = {k: self.config.get('preview', {}).get(k) for k in ('resolution',)}
        sources = self.snapshots if stage == 'inspect' else {}
        if self.tool_hash is None:
            executable = self.path(self.config['blender'])
            if not executable.is_file():
                raise PipelineError('needs_input', f'Missing Blender: {executable}', stage)
            self.tool_hash = sha(executable)
        scripts = {p.name: sha(p) for p in (HERE / 'run_pipeline.py', HERE / 'blender_adapter.py', HERE / 'glb_io.py', HERE / 'vertex_match.py')}
        adapter = self.adapter(stage)
        if adapter:
            script = self.path(adapter['script'])
            if not script.is_file():
                raise PipelineError('needs_input', f'Missing adapter: {script}', stage, [str(script)])
            scripts[str(script)] = sha(script)
            for dependency in adapter.get('inputs', []):
                dependency = self.path(dependency)
                if not dependency.is_file():
                    raise PipelineError('needs_input', f'Missing adapter input: {dependency}', stage)
                scripts[str(dependency)] = sha(dependency)
        upstream = {dep: {'key': self.state['stages'][dep]['key'],
                          'outputs': self.state['stages'][dep]['outputs']} for dep in DEPS[stage]}
        return digest({'config': relevant, 'sources': sources, 'scripts': scripts,
                       'tool': self.tool_hash, 'upstream': upstream, 'adapter': adapter})

    def cached(self, stage, key):
        record = self.state['stages'].get(stage, {})
        return (record.get('status') == 'success' and record.get('key') == key
                and bool(record.get('outputs')) and all(Path(v['path']).is_file()
                    and sha(v['path']) == v['sha256'] for v in record['outputs'].values()))

    def prepare(self, stage):
        if stage == 'anatomy':
            section = self.config.get('anatomy', {})
            if section.get('mode') not in ('prepared', 'existing', 'adapter'):
                raise PipelineError('needs_input', 'Inspect anatomy; declare prepared/existing rig or provide trusted adapter',
                                    stage, ['anatomy.mode', 'anatomy.roles', 'inspection.json'])
            if section.get('mode') == 'prepared' and not self.snapshots.get('prepared_native'):
                raise PipelineError('needs_input', 'Prepared reuse requires sources.prepared_native', stage,
                                    ['sources.prepared_native'])
            if not section.get('roles'):
                raise PipelineError('needs_input', 'Missing inspected skeletal role mapping', stage, ['anatomy.roles'])
        if stage == 'motion' and not self.config.get('motion', {}).get('clips'):
            raise PipelineError('needs_input', 'Declare exact approved clips, action bindings, duration and loop flags',
                                stage, ['motion.clips'])
        if stage in ('anatomy', 'motion', 'face'):
            mode = self.config.get(stage, {}).get('mode')
            allowed = {'anatomy': ('prepared', 'existing', 'adapter'),
                       'motion': ('reuse', 'adapter'), 'face': ('existing', 'none', 'adapter')}[stage]
            if mode not in allowed:
                raise PipelineError('needs_input', f'Missing calibrated {stage}.mode', stage, [f'{stage}.mode'])
            if mode == 'adapter' and not self.adapter(stage):
                raise PipelineError('needs_input', f'Missing trusted {stage}.adapter.script', stage,
                                    [f'{stage}.adapter.script'])

    def execute(self, stage, key):
        self.prepare(stage)
        prior = self.state['stages'].get(stage, {})
        failures = prior.get('failures', 0)
        if failures >= 2:
            raise PipelineError('needs_diagnosis', 'Two attempts failed; record root-cause diagnosis before another fix', stage,
                                [f'diagnose {self.config_path} --stage {stage} --note ...'])
        run_id = uuid.uuid4().hex
        started = time.monotonic()
        directory = self.workspace / 'stages' / stage / run_id
        directory.mkdir(parents=True)
        job_path, result_path = directory / 'job.json', directory / 'result.json'
        job = {'run_id': run_id, 'stage': stage, 'config': self.config,
               'config_directory': str(self.config_path.parent), 'output_directory': str(directory),
               'result_path': str(result_path), 'sources': self.snapshots,
               'inputs': {dep: self.artifacts(dep) for dep in DEPS[stage]}}
        if stage in ('certify', 'export', 'face_probes', 'preview'):
            job['inputs']['motion'] = self.artifacts('motion')
            job['inputs']['face'] = self.artifacts('face')
        write(job_path, job)
        script = self.path(self.adapter(stage)['script']) if self.adapter(stage) else HERE / 'blender_adapter.py'
        command = [str(self.path(self.config['blender'])), '--background', '--factory-startup',
                   '--python-exit-code', '1', '--python', str(script), '--', str(job_path)]
        log = directory / 'blender.log'
        self.state['stages'][stage] = {'status': 'running', 'key': key, 'run_id': run_id,
                                      'failures': failures, 'log': str(log), 'diagnoses': prior.get('diagnoses', [])}
        self.save()
        try:
            if stage == 'package' and not self.adapter(stage):
                self.package(job)
            else:
                with log.open('w', encoding='utf-8') as stream:
                    result = subprocess.run(command, cwd=directory, stdout=stream, stderr=subprocess.STDOUT,
                                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                                            timeout=self.config.get('stage_timeout_s', 900), check=False)
                if result.returncode:
                    message = f'Blender/adapter exited {result.returncode}; see {log}'
                    if result_path.is_file():
                        report = read(result_path)
                        if report.get('run_id') == run_id and report.get('status') == 'failed':
                            message = f'{report.get("message", message)}; log: {log}'
                    raise PipelineError('failed', message, stage)
            if not result_path.is_file():
                raise PipelineError('failed', 'Adapter omitted fresh result.json', stage)
            result = read(result_path)
            if result.get('run_id') != run_id:
                raise PipelineError('failed', 'Stale adapter result: run_id mismatch', stage)
            if result.get('status') != 'success':
                raise PipelineError(result.get('status', 'failed'), result.get('message', 'Adapter failed'),
                                    stage, result.get('needed'))
            artifacts = result.get('artifacts', {})
            if not REQUIRED[stage].issubset(artifacts):
                raise PipelineError('failed', f'Missing artifacts: {sorted(REQUIRED[stage] - set(artifacts))}', stage)
            outputs = {}
            for name, value in artifacts.items():
                artifact = Path(value).resolve()
                if not artifact.is_relative_to(directory) or not artifact.is_file():
                    raise PipelineError('failed', f'Output missing/outside stage directory: {artifact}', stage)
                outputs[name] = {'path': str(artifact), 'sha256': sha(artifact)}
            if stage == 'certify':
                proof = read(outputs['certification']['path'])
                if proof.get('numerical_status') != 'pass' or proof.get('glb_sha256') != sha(Path(job['inputs']['export']['glb'])):
                    raise PipelineError('failed', 'Certification must pass and bind actual persisted GLB hash', stage)
            self.state['stages'][stage].update(status='success', outputs=outputs, failures=0,
                                              elapsed_s=round(time.monotonic() - started, 3), evidence=result.get('evidence', {}))
            self.save()
        except Exception as error:
            if not isinstance(error, PipelineError):
                error = PipelineError('failed', str(error), stage)
            counted = failures + (error.status == 'failed')
            self.state['stages'][stage].update(status=error.status, failures=counted, message=str(error))
            self.state['stages'][stage]['elapsed_s'] = round(time.monotonic() - started, 3)
            self.save()
            if counted >= 2:
                raise PipelineError('needs_diagnosis', str(error), stage, ['record diagnosis, then retry']) from error
            raise error

    def package(self, job):
        directory = Path(job['output_directory']) / 'delivery'
        directory.mkdir()
        manifest = {'model_id': self.config['model_id'], 'numerical_status': 'pass',
                    'visual_status': 'needs_review', 'runtime_status': 'not_verified', 'files': {}}
        for stage, artifacts in job['inputs'].items():
            for name, source in artifacts.items():
                destination = directory / stage / Path(source).name
                destination.parent.mkdir(exist_ok=True)
                shutil.copy2(source, destination)
                manifest['files'][destination.relative_to(directory).as_posix()] = {'sha256': sha(destination), 'source': source}
        manifest_path = directory / 'manifest.json'
        write(manifest_path, manifest)
        write(job['result_path'], {'run_id': job['run_id'], 'status': 'success',
                                  'artifacts': {'package': str(manifest_path), **{
                                      name: str(directory / name) for name in manifest['files']}}})

    def packet(self, status, stage=None, message=None, needed=None, executed=None, cached=None):
        result = {'status': status, 'model_id': self.config['model_id'], 'workspace': str(self.workspace)}
        if stage:
            result['stage'] = stage
        if message:
            result['message'] = message
        if needed:
            result['needed'] = needed
        if executed is not None:
            result.update(executed=executed, cached=cached)
            result['stage_elapsed_s'] = {name: self.state['stages'][name].get('elapsed_s', 0) for name in executed}
        if 'inspect' in self.state['stages'] and self.state['stages']['inspect'].get('outputs'):
            result['inspection'] = self.artifacts('inspect')['inspection']
        if self.state['stages'].get('export', {}).get('outputs'):
            result['glb'] = self.artifacts('export')['glb']
        if self.state['stages'].get('package', {}).get('outputs'):
            result['package'] = self.artifacts('package')['package']
        return result

    def run(self, through='package'):
        self.workspace.mkdir(parents=True, exist_ok=True)
        lock = self.workspace / 'owner.lock'
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                owner = int(lock.read_text())
            except (ValueError, OSError):
                raise PipelineError('needs_input', f'Unreadable owner lock: {lock}; inspect before retrying')
            if pid_alive(owner):
                raise PipelineError('needs_input', f'Workspace owned by active PID {owner}: {lock}')
            lock.unlink()
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(descriptor, str(os.getpid()).encode())
        os.close(descriptor)
        self.state = read(self.state_path) if self.state_path.exists() else {'schema_version': 1, 'stages': {}}
        executed, cached = [], []
        try:
            self.inputs()
            for stage in STAGES[:STAGES.index(through) + 1]:
                key = self.key(stage)
                if self.cached(stage, key):
                    cached.append(stage)
                else:
                    self.execute(stage, key)
                    executed.append(stage)
            status = 'success' if through != 'package' else self.visual_status()
            return self.packet(status, executed=executed, cached=cached,
                               needed=['Independent temporal playback and facial/limb/tail review of this GLB']
                               if status == 'needs_review' else None)
        except PipelineError as error:
            return self.packet(error.status, error.stage, str(error), error.needed, executed, cached)
        finally:
            lock.unlink(missing_ok=True)

    def visual_status(self):
        review = self.state.get('review', {})
        if (review.get('artifact_sha256') == sha(self.artifacts('export')['glb'])
                and review.get('binding') == self.review_binding() and review.get('verdict') == 'pass'):
            evidence_path = Path(review['path'])
            if evidence_path.is_file() and sha(evidence_path) == review.get('evidence_sha256'):
                return 'success'
        return 'needs_review'

    def current_chain(self):
        for stage in STAGES:
            if any(dep not in self.state['stages'] for dep in DEPS[stage]) or not self.cached(stage, self.key(stage)):
                raise PipelineError('needs_review', f'Current {stage} stage is incomplete or invalidated; run pipeline before final review', stage)
        proof = read(self.artifacts('certify')['certification'])
        if proof.get('numerical_status') != 'pass' or proof.get('glb_sha256') != sha(self.artifacts('export')['glb']):
            raise PipelineError('failed', 'Current persisted artifact lacks matching numerical certification', 'certify')

    def review_binding(self):
        return digest({stage: self.key(stage) for stage in ('face_probes', 'certify', 'preview', 'package')})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('init', 'inspect', 'run', 'status', 'diagnose', 'review'))
    parser.add_argument('config')
    parser.add_argument('--model')
    parser.add_argument('--workspace')
    parser.add_argument('--blender')
    parser.add_argument('--through', choices=STAGES, default='package')
    parser.add_argument('--stage', choices=STAGES)
    parser.add_argument('--note')
    parser.add_argument('--evidence')
    args = parser.parse_args(argv)
    try:
        if args.command == 'init':
            destination = Path(args.config).resolve()
            if destination.exists():
                raise PipelineError('failed', f'Config exists: {destination}')
            if not all((args.model, args.workspace, args.blender)):
                raise PipelineError('needs_input', 'init requires --model SOURCE --workspace DIRECTORY --blender EXECUTABLE')
            config = read(HERE / 'config.example.json')
            config.update(model_id=re.sub(r'[^A-Za-z0-9_-]', '_', Path(args.model).stem),
                          workspace=str(Path(args.workspace).resolve()), blender=str(Path(args.blender).resolve()),
                          sources={'model': str(Path(args.model).resolve())})
            write(destination, config)
            result = {'status': 'success', 'config': str(destination), 'next': f'inspect {destination}'}
        else:
            runner = Runner(args.config)
            if args.command in ('inspect', 'run'):
                result = runner.run('inspect' if args.command == 'inspect' else args.through)
            elif args.command == 'status':
                runner.inputs(persist=False)
                records = {}
                dirty = set()
                for stage in STAGES:
                    record = runner.state['stages'].get(stage, {})
                    if any(dep in dirty for dep in DEPS[stage]) or not record:
                        valid = False
                    else:
                        valid = runner.cached(stage, runner.key(stage))
                    if not valid:
                        dirty.add(stage)
                    records[stage] = 'cached' if valid else record.get('status', 'pending') if record.get('status') != 'success' else 'invalidated'
                result = runner.packet('needs_review' if not dirty and runner.visual_status() != 'success'
                                       else 'ready' if not dirty else 'incomplete')
                result['stages'] = records
            elif args.command == 'diagnose':
                if not args.stage or not args.note:
                    raise PipelineError('needs_input', 'diagnose requires --stage and --note')
                if (runner.workspace / 'owner.lock').exists():
                    raise PipelineError('needs_input', 'Workspace has an owner lock; finish/recover the running attempt before diagnosis')
                record = runner.state['stages'].get(args.stage, {})
                record.setdefault('diagnoses', []).append({'note': args.note, 'at': time.time(), 'failures': record.get('failures', 0)})
                record['failures'] = 0
                runner.state['stages'][args.stage] = record
                runner.save()
                result = runner.packet('ready', args.stage)
            else:
                if not args.evidence:
                    raise PipelineError('needs_input', 'review requires --evidence JSON with artifact_sha256, reviewer, verdict and temporal_playback')
                if (runner.workspace / 'owner.lock').exists():
                    raise PipelineError('needs_input', 'Workspace has an owner lock; finish/recover the running attempt before review')
                runner.inputs(persist=False)
                runner.current_chain()
                path = runner.path(args.evidence)
                review = read(path)
                if (review.get('artifact_sha256') != sha(runner.artifacts('export')['glb'])
                        or review.get('verdict') not in ('pass', 'fail') or not review.get('reviewer')
                        or not review.get('temporal_playback')):
                    raise PipelineError('failed', 'Review must identify this GLB, reviewer, verdict and actual temporal playback')
                if runner.config.get('face', {}).get('mode') != 'none' and not review.get('facial_views'):
                    raise PipelineError('failed', 'Facial review evidence required')
                runner.state['review'] = {**review, 'path': str(path), 'evidence_sha256': sha(path), 'binding': runner.review_binding()}
                runner.save()
                result = runner.packet('success' if review['verdict'] == 'pass' else 'needs_review')
    except Exception as error:
        result = {'status': getattr(error, 'status', 'failed'), 'message': str(error)}
    print(json.dumps(result, separators=(',', ':'), allow_nan=False))
    return 0 if result['status'] in ('success', 'ready') else 2 if result['status'] in ('needs_input', 'needs_review', 'needs_diagnosis', 'incomplete') else 1


if __name__ == '__main__':
    raise SystemExit(main())
