# Commands and stages

The Python orchestrator launches isolated, hidden Blender processes. Its stages
are `inspect`, `anatomy`, `motion`, `face`, `face_probes`, `export`, `certify`,
`preview`, and `package`. There is no external service or API key.

```sh
companion-animation init model.json --model /path/to/source.glb --workspace /path/to/derivatives
companion-animation inspect model.json
companion-animation status model.json
companion-animation run model.json --through face_probes
companion-animation run model.json --through preview
companion-animation run model.json --through package  # prepares inventory; needs_review is expected
companion-animation review model.json --evidence /path/to/review.json
companion-animation run model.json --through package
```

`init` creates a real configuration based on the supplied paths. Anatomy/face
calibration is initially unset. Prepared rigs may use `anatomy.mode: existing`
with inspected semantic `roles`. Declare each motion clip's real action, action
slot, duration and loop/held endpoint behavior. Use `face.mode: none` only when
no facial animation is required; it is not a bypass for a broken face.

For new geometry, `anatomy.adapter`, `motion.adapter`, and `face.adapter` can
identify a trusted Blender Python `script` and optional `inputs`; set that
section's mode to `adapter`. Read the emitted job JSON. An adapter receives its
path after `--`, writes only to its isolated output directory and returns a JSON
result with the same `run_id`, a truthful `status`, and an `artifacts` map of
absolute paths. Anatomy/face require `native`; motion requires `native` and
`bake`. Prefer preparing real named actions and using the built-in bake rather
than creating a private replacement exporter.

Dependencies, code, settings and outputs are hashed. Unchanged stages can be
reused; face-only edits should not rebuild unchanged motion. Two failed attempts
require diagnosis. Use `companion-animation diagnose model.json --stage face
--note "..."` only after establishing the actual cause.

The review JSON must identify `artifact_sha256`, a `reviewer`, a `verdict` of
`pass` or `fail`, and the actual `temporal_playback` inspected. Facial models also
require `facial_views`. Relevant setting changes invalidate previous review.
Never generate a pass record from the numerical certification alone.

The delivery manifest remains an immutable pre-review inventory; retain the separate
review JSON with a shared delivery. Candidate-bound review lives in runner state.

Exit codes: 0 ready/success, 2 incomplete/calibration/diagnosis/review required,
1 failure. Full logs and intermediate attempts stay in the model workspace.

The MCP tools are `environment_info`, `pipeline_init`, `pipeline_status`,
`pipeline_run`, `blender_script`, `job_status`, `view_artifact`, `diagnose_stage`,
and `record_review`. No other Blender MCP or Blender add-on is required. Any AI
with local command execution can also operate the CLI without MCP support.
