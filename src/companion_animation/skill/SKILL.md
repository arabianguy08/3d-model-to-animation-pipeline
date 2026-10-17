---
name: companion-animation-pipeline
description: Inspect, rig, retarget, animate, export and verify 3D companions with the installed Blender pipeline. Use for uploaded character models, named-bone mappings, supported gestures, facial deformation and GLB delivery. Does not promise universal automatic rigging or replace visual review.
---

# Companion animation pipeline

Use the installed `companion-animation` CLI or the `companion-animation` MCP server.
Read [the workflow](references/workflow.md) for command and adapter details.

- Run `environment_info`/`companion-animation doctor` first. If setup is missing,
  run `companion-animation setup` using the user's chosen client.
- Inspect the actual model in a new derivative workspace. For an existing job,
  read status before rerunning stages. Keep supplied sources immutable.
- Map bones by anatomy and rest transforms, not name guesses alone. Preserve
  proportions and authored action timing. Use connected support-foot, pelvis,
  spine, chest and head motion; never stretch the neck to clear a collision.
- Missing calibration is work for the agent: inspect the geometry and create a
  narrowly scoped local Blender adapter. It does not imply that the user needs
  another autorig service. Do not manufacture calibration or successful reports.
- Use `blender_script` to run trusted preparation scripts inside the workspace.
  These execute with normal user permissions, not a sandbox. They run background
  Blender processes; they do not manipulate an already-open Blender UI session.
- Start long work with `pipeline_run`, then poll `job_status`. Inspect the real
  output and failure log. `needs_input` and `needs_diagnosis` are actionable work;
  `needs_review` requires actual agent inspection, not automatic user approval.
- Inspect facial probes across open, partial and closed states, multiple views,
  and a rolled Head/Jaw pose before polishing a single camera angle.
- Certify the saved GLB, including its actual evaluated deformation, endpoints,
  loops, action slots and animated morph weights. Do not loosen failed tolerances.
- Inspect temporal playback and close facial/limb/tail views against the candidate
  hash. A contact sheet or a numerical pass alone is not visual sign-off.
- Separate caster/spawn animation from game-controlled projectile travel,
  collision and lifetime. Demo VFX do not supply a game integration.
- Record a real review with `record_review`; package only the settled candidate.
  Distinguish numerical proof, agent visual review, user approval and game tests.
