# Using the pipeline

## 1. Verify installation

```sh
companion-animation doctor
```

The result names the actual Python and Blender in use. The MCP equivalent is
`environment_info`. No external Blender MCP/add-on needs to be installed.

## 2. Learn with a generated model

```sh
companion-animation demo ./synthetic-demo
companion-animation status ./synthetic-demo/smoke.json
```

This creates an isolated, small synthetic rig, authored actions, a Blink morph,
an exact-timing/held-pose regression fixture, exported GLB and preview evidence.
The default demo runs through preview. It is an engineering example, not a
claim that arbitrary anatomy or artistic quality has been solved.

The more expressive Pip showcase is reproducible from a source checkout:

```sh
blender --background --factory-startup --python examples/make_mascot.py -- ./pip-demo
```

If `blender` is not on PATH, use the executable reported by `doctor`.

`make_mascot.py` already creates `pip-demo/pip.json`. Set that configuration's
`blender` value to the executable reported by `doctor`, then run:

```sh
companion-animation run ./pip-demo/pip.json --through preview
```

The generated native/model files remain outside Git. Only synthetic render
previews and small verification records are published in this repository.

## 3. Bring a real model

```sh
companion-animation init character.json --model ./character.glb --workspace ./derivatives
companion-animation inspect character.json
```

Keep the original model immutable. Use a new workspace instead of an existing
asset directory. Native `.blend` inputs are also supported; inspection imports
supported formats through Blender. Look at the actual inspection report and
geometry before choosing anatomy settings.

A prepared model can declare `anatomy.mode: existing`, its armature name, and
semantic roles mapped to real bone names. For each clip, declare the actual
`armature_action`, optional `armature_slot`, exact `duration_s`, `loop`, and any
`object_actions` or `morph_actions`. The generated demo configuration is a real,
working example of these fields.

An unrigged model needs calibrated fitting and weights. An agent can inspect it
and author Blender preparation scripts through `blender_script`; there is no
universal "rig anything" algorithm hidden behind the CLI. Adapters must use the
job's output paths and return truthful stage reports. See the shipped
[adapter/workflow reference](../src/companion_animation/skill/references/workflow.md).

## 4. Iterate without rebuilding everything

```sh
companion-animation run character.json --through face_probes
companion-animation run character.json --through preview
companion-animation status character.json
```

The runner hashes source files, dependencies, settings, code and outputs.
Unchanged stages are reused; source changes invalidate dependent stages. Edit
facial calibration without needlessly rebaking unchanged body motion. Camera
changes rerender evidence without pretending old evidence still applies.

The inspect/anatomy/motion/face/probe/export/certify/preview stages each leave
reports and logs in the derivative workspace. Missing calibration returns
`needs_input`. Repeated failed attempts return `needs_diagnosis`; establish the
cause and use `diagnose`, rather than deleting the failure or weakening a check.

## 5. Review the saved artifact, then package

Numerical certification reopens the exported GLB and compares evaluated
deformation against the reference. It checks exact clip endpoints, looping,
nonzero motion and morph activity. It does not establish believable contact,
anatomy or temporal quality by itself.

Inspect actual playback, then targeted face, limb and tail views. Write a review
JSON identifying the exported file's SHA-256, reviewer, `pass`/`fail` verdict,
the actual `temporal_playback` inspected, and `facial_views` when applicable.
These fields should contain real observations or paths, not canned approval.

```sh
companion-animation run character.json --through package  # prepares delivery; exit 2 = needs_review
companion-animation review character.json --evidence ./review.json
companion-animation run character.json --through package
```

The delivery manifest is specific to that candidate and remains an immutable
pre-review inventory marked `needs_review`. The runner stores the candidate-bound
review separately in its state; keep the review JSON alongside a shared delivery.
The final runner status becomes `success` after a matching passing review. Relevant changes
invalidate earlier review. User artistic approval and integration into a game
are separate from this agent review and numerical certification.

## MCP operation

Use `pipeline_init` and `pipeline_status` for configuration and inspection.
`pipeline_run` and `blender_script` return a background `job_id`; poll
`job_status` until it finishes, then read the actual result/log. `view_artifact`
returns a PNG/JPEG from the workspace for visual inspection. Do not treat
starting a job as completing it.

The included Blender control runs hidden subprocesses, not an interactive
already-open scene. Scripts have ordinary user permissions, so execute trusted
code only. The server opens no network listener and asks for no API keys.

## VFX boundary

`companion_animation.vfx.charge_orb` authors reusable Blender emission geometry
and timed object animation. The showcase adds this effect after importing the
verified GLB. It demonstrates presentation; it does not assert that a target
engine automatically supports that material, or provide projectile collision,
networking, damage logic or a phone-performance guarantee.
