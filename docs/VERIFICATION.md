# Verification

Verified on Windows x64, 2026-10-17. Results describe this release candidate;
they are not a promise that arbitrary uploads will rig or animate automatically.
Release 0.1.1. [Cross-platform CI run](https://github.com/arabianguy08/companion-animation-pipeline/actions/runs/39060721259).
Machine-readable measurements: [verification-results.json](verification-results.json).

| Check | Result |
| --- | --- |
| GitHub CI: Windows, macOS, Linux | 33 passed and 1 real-Blender skip on each OS; package builds passed |
| Standard test discovery | 33 passed; 1 real-Blender test intentionally skipped |
| Opt-in real Blender 4.5.3 regression test | Passed, including saved GLB reimport and cached resume |
| Checksum-verified managed Blender download and extraction | Passed, actual Windows 4.5.3 portable binary |
| One-shot PowerShell bootstrap | Passed: downloaded uv 0.9.7 and managed Python 3.12.12, installed wheel, MCP dependencies, skill and generic connection |
| Installed wheel, outside source checkout | Real synthetic demo passed through preview using portable Blender 4.5.3; subsequent resume reused stages |
| Installed MCP server | Real stdio handshake, 9 tools, environment check and background Blender script saving a synthetic scene passed |
| Codex registration | Passed using isolated CODEX_HOME; actual CLI registration verified |
| Existing configurations | Unrelated settings retained; conflicting server/modified skill refused by tests |
| Pip showcase, Blender 5.2.1 | Export, numerical certification, facial probes, playback review and final runner success passed |
| macOS/Linux installers and Blender | Not locally executed; managed archive checksums are pinned for supported targets |
| Claude/Cursor interactive host operation | Configuration writing tested; host UI not tested |
| Target game/mobile runtime | Not tested; no frame-rate claim |

The installation tests used isolated runtime/configuration directories. No
private model, application checkout, host AI configuration or external service
was needed. The wheel imports from its own site-packages, not the source tree.

## Synthetic motion evidence

Pip has 23 named bones, two 2-second loops and an animated LED Blink. The saved
GLB was reimported into a clean Blender scene. Each clip was checked at 541
times, including export keys and independent off-grid times. Maximum evaluated
vertex error was 0.000006807 scene units (threshold 0.0001). Loop endpoint error
was below 0.000000001 scene units. Required Blink excursion reached 1.0.

The generator uses aligned robot-joint rest axes and 240 Hz export sampling.
Earlier development candidates correctly failed the strict correspondence or
interpolation checks. The published candidate passed without relaxing gates.
A mesh-name regression also verifies exact datablock names before numeric-prefix
fallbacks, avoiding an ambiguous `Sphere`/`Sphere.015` match.

All 42 neutral/rolled facial probes and all 68 oblique/side preview frames were
inspected. Actual generated browser playback was checked for both clips. Feet
remain planted and the head, tail and arm gesture remain coherent at that review
cadence. The robot's LED compression is not organic eyelid fitting. Final runner
status is `success` only after a review bound to this candidate.

The public GIF/MP4 render imports that same certified GLB; charge-orb VFX are
added afterward as separate presentation effects. The documentation animation
is 16 fps and does not establish real-time renderer performance.

## Reproduce

```sh
python -m pip install .
python -m unittest discover -s tests -v
python tests/test_pipeline.py --blender /path/to/blender BlenderTests -v
companion-animation demo ./independent-demo --blender /path/to/blender
```

For Pip and the public media, follow [SHOWCASE.md](SHOWCASE.md). Numerical
certification, visual inspection, user artistic approval and runtime acceptance
remain separate results. A successful command launch is not a completed job.
