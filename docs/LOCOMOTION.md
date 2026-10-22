# Anatomical locomotion extensions

The optional `companion_animation.locomotion` package adds a calibrated biped
workflow to the existing inspect -> animate -> export -> certify stages. It
keeps the source GLB immutable and produces a separate review candidate.
These adapters require inspected anatomy; they are not universal automatic rigs.

"Any motion often causes micromovements across the body. Moving a hand isn't just the hand, but also how the rest of the body moves when that hand moves as well."

## What changed

| Module | Responsibility |
|---|---|
| `anatomy.py` | Parent-path roles, reversed spine numbering, explicit name overrides |
| `math_core.py` | Knee-plane-preserving two-bone solve, skinned patch pin, treadmill travel, extras, runtime rate |
| `retarget.py` | Blender world/rest orientation transfer, arm rest alignment, rolling heel/ball/toe pin, iterative skinned correction, 120 Hz actions |
| `append_export.py` | Bind-space-corrected animation samples appended to the original GLB |
| `preservation.py` | BIN prefix and existing JSON/sampler invariants; diagnosed TRS allowance |
| `metrics.py`, `qa.py` | Pure measurements/gates and saved-GLB Blender sampling |
| `review_commands.py`, `review.py` | Validated review options, cameras, moving grid, H.264 and combined reel |
| `configure.py` | Explicit-path pipeline configuration and hashed adapter dependencies |

The algorithms use Z-up Blender coordinates with forward -Y. Normalize source
and target coordinate conventions before applying this adapter. It supports
one inspected biped armature and one serialized skin with named TRS joints,
three spine joints, neck, arms and two legs with toe joints. Unexpected anatomy
must be calibrated or rejected. Additional chains may retain rest keys or use
configured lagged secondary motion; inspect them before accepting a candidate.

## Local setup

Use a short checkout such as `C:/anim` on Windows, with `.venv` and a short
workspace such as `C:/anim/work`. Long nested script/job paths can exceed
Blender's Windows path limit. No tool in this workflow uploads anything.

```powershell
python -m venv .venv
.venv/Scripts/python -m pip install '.[locomotion]'
$env:PYTHONPATH = (Resolve-Path src).Path
.venv/Scripts/python -m unittest discover -s tests -v
```

When ordinary Python is missing, use the Python executable under your Blender
installation's `<version>/python/bin/` to create/run the venv. Blender includes
NumPy. A `--system-site-packages` venv can reuse it. If that distribution's
`sitecustomize` assumes Blender's executable directory, use normal Python or a
venv-local `sitecustomize.py` instead of editing the Blender installation.
The `bpy` stages still run through Blender, not the standalone Python binary.
The general pipeline remains standard-library based; NumPy is optional outside
Blender and included in the development extra for these tests.

## Configure and bake

Inspect source clip timing first. A motion-spec JSON is an array with explicit
paths and measured durations, for example:

```json
[
  {"name":"walk", "path":"C:/anim/source/walk.fbx", "duration_s":1.0333333333333334,
   "loop":true, "gait":"walk", "in_place":true}
]
```

That example duration is illustrative: use your source's exact duration.
Valid gait declarations are `walk`, `run`, and `other`; names are unrestricted.

```powershell
.venv/Scripts/python -m companion_animation.locomotion.configure --model source.glb --workspace work --blender 'C:/Blender/blender.exe' --specs motion-specs.json --output config.json
.venv/Scripts/python -m companion_animation.cli run config.json --through certify
```

The generated `config.params.json` has source keys, 120 Hz authoring and contact
parameters. Both it and the adapter dependencies participate in pipeline cache
keys. Edit `source_roles` and `target_roles` there for explicit actual-name to
canonical-role overrides. The configurator requires identifiable Mixamo anatomy;
for custom names, supply the equivalent inspected configuration directly.
Do not round a fractional endpoint: the adapter rejects durations off its
uniform 120 Hz grid. The pipeline export also includes actual authored keys.

The existing animation inventory stays first and unchanged. A missing loop
declaration defaults to false, including idles. To replace an existing action,
use a separately scoped workflow; this append-only adapter rejects it.

Anatomical mapping follows the Hips-to-Head parent path. A source ordered
`Spine02 -> Spine01 -> Spine` is anatomically mapped to a target ordered
`Spine -> Spine1 -> Spine2`. Transfer uses world rotation relative to source
rest and target rest, with swing alignment for different arm rest poses.
Target/source leg-length ratio scales translation. Remove constant horizontal
travel for the treadmill while preserving bob and authored phase offsets.

Source low-and-still contact is combined with target sole contact. The rolling
pivot proceeds heel -> ball -> toe. Then the actual linear-blend-skinned patch
is pinned and the non-stretch leg is solved again, because shin-weighted skin
can move while an ankle is stationary. Knee guidance comes from transferred
motion, with previous-plane projection/sign continuity near singularities.
Defaults: reach 0.985 leg length, swing minimum 0.02 and peak 0.04 leg length,
four skinned-contact passes. These calibrations do not imply a passing result.

## Certify and review the saved file

Use the current export GLB path reported by the pipeline. Resolve script paths
from the installed package (or use `src/companion_animation/locomotion` in a checkout).

```powershell
$Scripts = 'src/companion_animation/locomotion'
& 'C:/Blender/blender.exe' --background --factory-startup --python "$Scripts/qa.py" -- candidate.glb qa.json --clips walk --hz 120
.venv/Scripts/python -m companion_animation.locomotion.preservation source.glb candidate.glb
& 'C:/Blender/blender.exe' --background --factory-startup --python "$Scripts/review.py" -- candidate.glb review 'Candidate' --clips walk --combined idle,walk --size 720 --loops 3
```

Use the actual existing idle name in `--combined`. Review emits front, side,
three-quarter and runtime views, stills, `combined_three-quarter.mp4` and
`videos.json`. Runtime FOV, height, distance, target and heading are configurable
through `--runtime-*` options. Default output is phone-sized 720x720 H.264,
30 fps; the grid moves at `extras.treadmillSpeed`. `--speed clip=value` explicitly
overrides metadata. Non-looping clips play once; repeats are only for declared
loops. The combined reel's short NLA blends are review presentation, not edits
to the source clips. Watch the side view for sliding and the runtime view for
readability. A still sheet cannot prove temporal quality.

## Independent QA gates

H is measured body height; the sole floor comes from the first idle pose.
The whole-model bounding-box floor is reported separately (robes/tails can
hang lower). Every gate reports its value, limit, `pass`, and status.

| Check | Default pass bound |
|---|---|
| Sole penetration | <= 0.006 H |
| Weight-bearing slide, vertices within 0.0015 H of floor | <= 0.012 H per planted run |
| Near-floor slide, vertices within 0.004 H | <= 0.012 H per planted run |
| Walk duty | >= 0.5 per foot |
| Walk flight | 0 |
| Walk double support | reported, within 0..1; configurable minimum |
| Intended loop seam | <= 1e-4 H |
| Hips horizontal net loop drift | <= 0.002 H |
| Pop ratio: max step / 99th-percentile step | <= 6 |
| Knee-plane flips | 0 |
| Arm/torso and leg/leg crossings | no excess over rest baseline |
| Head-relative-chest range | >= 1.5 degrees |
| Elbow flex range | >= 3 degrees |
| Locomotion chest-relative-pelvis yaw range | >= 2 degrees |
| Shoulder/hip yaw and same-side arm/thigh correlation | < 0 |

Missing supporting-foot contact is **INVALID slide evidence**, not a pass:
without consecutive contact samples, a zero displacement has no evidential
meaning. Missing bones/regions, missing measurements and nonfinite gate values
must not become successful certificates. Crossings are sampled at 30 Hz by
default; increase sampling for fast motion. Pelvis ranges/bob, head world
rotation, follow-through lags and optional tail/ear motion are also reported.
No numerical minimum on lag universally establishes natural motion.

QA accepts `--crossing-hz`, `--roles` (a JSON actual-name to role map), and
`--thresholds` (JSON overrides using the keys in `metrics.THRESHOLDS`). Every
override is recorded in the output. Changing a gait-quality threshold requires
an anatomical rationale; it does not change export preservation bounds. QA
exits nonzero for failed or invalid gates, while retaining the diagnostic JSON.

QA is additional to native-versus-reopened deformation certification. The
default TRS residual gate is 1e-5. A source-specific precision diagnosis may
justify an exception only for that hash: one measured imported-rest residual
of about 1.06736e-5 justified 2e-5; another measured maximum of 3.066659e-5 with
serialized-basis noise justified 3.2e-5. These observations are not defaults.
Record `export.trs_diagnosis` with `source_sha256`, `measured_residual` and `note`,
and use the runner's `diagnose` command to record the failure and cause.
Never relax vertex identity 1e-6, reopened deformation 1e-4, timing 1e-6 s,
or applicable loop gates. Preserve old BIN bytes, meshes, skins, hierarchy,
materials/textures and all existing animation samplers/channels. The checker
allows appended array entries and additive clip extras; it does not claim the
GLB header or JSON whitespace remain byte-identical.

## Runtime and approval

`extras.treadmillSpeed` stores model units/second; `extras.gait` declares gait
semantics. Some loaders do not copy animation extras to their clip objects:
read the parser's animation JSON. With screen motion use
`timeScale = speed / logical_pixels_per_model_unit / treadmillSpeed`.
Measure the projection at the ground pivot after fitting the actual model
scale; device framebuffer pixels are not logical pixels. The provided helper
defaults to .6..1.6 limits, which can prevent exact lock outside that range.
World movement must match baked speed * model scale * timeScale.

Candidates never overwrite shipped assets before human approval. Keep source
hashes, numerical results, temporal review, human approval and runtime evidence
separate. Synthetic unit tests establish algorithm behavior; they do not
certify an arbitrary mesh, successful render, or application integration.
