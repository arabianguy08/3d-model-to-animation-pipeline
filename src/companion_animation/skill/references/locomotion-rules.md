# Locomotion authoring rules

"Any motion often causes micromovements across the body. Moving a hand isn't just the hand, but also how the rest of the body moves when that hand moves as well."

## Anatomy and keys

- Inspect hierarchy, weighted regions, bind transforms, axes, units and source
  actions before choosing an adapter. The provided adapter supports compatible
  bipeds with three spine joints and a neck. Custom names use explicit
  `source_roles` / `target_roles` (actual bone name to canonical role).
- Resolve Hips-to-Head anatomy by parent order. Some Mixamo sources number the
  spine `Spine02 -> Spine01 -> Spine` from bottom to top; targets may use
  `Spine -> Spine1 -> Spine2`. Never copy those names as anatomical equivalence.
- Transfer evaluated world orientation relative to rest, then derive local
  orientation from the target parent. Swing-align T-pose arms to A-pose arms.
  Scale travel by target/source leg length; do not scale anatomical segments.
- Author 120 Hz keys, evaluate source curves, preserve duration and exact
  endpoints. Export must include authored keys as well as regular samples.
  The biped adapter rejects durations incompatible with its uniform grid;
  do not silently round a fractional endpoint. Keep quaternion signs continuous
  and key the complete deterministic pose so previous actions cannot leak in.

## Support and connected motion

- Use low AND still source contact, then target contact calibration. Pin the
  rolling heel/ball/toe patch on the actual linear-blend-skinned sole, including
  shin influence. Re-solve and re-skin; a rigid ankle pin is insufficient.
- Use the transferred knee plane in a non-stretch two-bone solve. Project the
  previous plane into the new axis through singular intervals; preserve its
  sign. Lower the hips path to retain bend before clamping unreachable targets.
  Default reach is 0.985 of leg length; minimum swing clearance is 0.02 of leg
  length and peak clearance 0.04. These are calibration parameters, not proofs.
- Support must connect feet, pelvis, spine, chest, neck and head with staggered
  timing, counter-rotation, overlap, follow-through and recovery. Never use one
  easing curve for every joint or stretch a neck to clear a collision.
- For gait QA, chest-relative yaw range >= 2 degrees; shoulder/hip yaw
  correlation < 0; head-relative-chest range >= 1.5 degrees; same-side arm/thigh
  correlation < 0; elbow flex range >= 3 degrees. Report pelvis bob/yaw/pitch/roll,
  head stabilization, follow-through lag seconds and optional tail/ear ranges.
  These measurements complement temporal artistic review.

## Export and evidence

- Keep the existing idle first. Declare non-looping idles `loop: false`; do not
  certify their reset as a seamless loop. Append-only export rejects replacing
  any existing action. Existing binary, geometry, skin, nodes, textures,
  materials and every existing sampler/channel must pass byte/prefix checks.
- Default TRS residual is 1e-5. A per-asset exception requires recorded measured
  error, cause and the source hash. Imported-rest noise may justify 2e-5;
  measured 3.066659e-5 serialized-basis error may justify 3.2e-5 for that source.
  Never copy those allowances to other assets. Never relax vertex identity
  (1e-6), reopened deformation (1e-4), timing (1e-6 s), or applicable loop gates.
- H is measured body height. Sole penetration <= 0.006 H. Weight-bearing
  (<= 0.0015 H above floor) and near-floor (<= 0.004 H) slide each <= 0.012 H
  per contiguous planted run in the treadmill frame. Missing consecutive
  supporting-foot contact means **INVALID slide evidence**, never zero-slide PASS.
- Walk duty >= 0.5 per foot, no flight; report double support (default permitted
  range 0..1; anatomy-specific positive minimum may be configured). Loop seam
  <= 1e-4 H, hips drift <= 0.002 H, pop ratio <= 6, zero knee flips; arm/torso
  and leg/leg crossings must not exceed the rest baseline. Missing data is invalid.
- Reopen the saved candidate in a clean Blender scene. Render front, side,
  three-quarter and configured runtime cameras with a moving grid, plus a
  combined reel. Default phone-sized output is 720x720, 30 fps H.264. Watch
  motion at normal speed; stills alone do not establish contact or continuity.
- Candidates never overwrite shipped assets before human approval. Numerical
  certification, temporal review, human approval and runtime verification are
  separate evidence. A retained shipped clip can still be a failed clip.

## Running and runtime

- Use a short Windows checkout/work root and `.venv`; nested script/job paths
  can exceed Blender's Windows path limit. Blender's bundled Python can run
  configuration and pure logic when ordinary Python is unavailable. `bpy`
  adapters run through Blender `--background --factory-startup --python`.
- Store model-units/second speed in animation `extras.treadmillSpeed` and an
  explicit `extras.gait`; do not infer semantics from consumer clip names.
  In-place travel subtracts linear root travel and retains vertical bob.
- Runtime `timeScale = screen_velocity / logical_pixels_per_model_unit /
  treadmillSpeed`. Project the actual fitted model scale at the ground pivot;
  use logical pixels, not framebuffer pixels. Default .6..1.6 clamping is an
  optional presentation policy; clamping can prevent exact foot lock.
  World speed must equal baked speed * model scale * timeScale. Some glTF
  loaders leave animation extras on parser JSON rather than AnimationClip.
  Read that metadata explicitly. Rendering is not a runtime test.
