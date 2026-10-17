# Synthetic showcase

![Pip's authored gesture and separate VFX](media/companion-demo.gif)

[Download the MP4](media/companion-demo.mp4) · [Still image](media/companion-demo.png)

All geometry, materials and motions here were newly generated for this public
project. No past private companion, model binary, game UI or customer data is
included. The repository publishes the generator, not private assets.

## Pip: a reusable export and review example

- 23 semantic bones, with explicit Root/Pelvis/Spine/Chest/Neck/Head/Jaw,
  paired limbs, ears and tail names.
- Rigid robot shells use aligned rest axes to keep inverse-bind float roundoff
  within the strict correspondence gate; the exporter uses 240 Hz sampling to
  retain authored interpolation without relaxing deformation tolerances.
- Two authored 2-second looping actions: Idle and Cast. Feet remain planted;
  torso, head, ears, arm and tail participate in the gesture.
- Separate animated LED Blink morph. These are stylized robot eyes, not a claim
  of automatically fitted organic eyelids.
- Persisted GLB is reopened and checked against the native model for timing,
  skin/morph deformation, loops, nonzero movement and action bindings.
- The showcase render imports that saved GLB. Charge-orb VFX are added afterward
  using the public `vfx.charge_orb` helper, and are labeled render-time effects.

## Engineering successes demonstrated by tests

The small CLI demo separately exercises exact fractional clip timing, a held
final Blink, object-action default restoration, native-versus-export vertex
matching, meshes with colliding numeric name prefixes, cache invalidation,
resumption, and honest pending-review status. These checks are reproducible;
they do not turn a synthetic fixture into proof of every possible body type.

## Reproduce the visuals

Install the project with its optional showcase dependencies:

```sh
python -m pip install '.[showcase]'
blender --background --factory-startup --python examples/make_mascot.py -- ./pip-demo
```

Use the Blender executable from `companion-animation doctor` if it is not on
PATH. Set `pip-demo/pip.json` to that executable, then run the pipeline through
preview. Its status/state report identifies the actual exported GLB path.

```sh
companion-animation run ./pip-demo/pip.json --through preview
blender --background --factory-startup --python examples/render_showcase.py -- ./companion.glb ./showcase-frames
python examples/encode_showcase.py ./showcase-frames ./public-media
```

Replace `./companion.glb` with that actual pipeline output. The renderer uses the
persisted GLB, not the original native scene. Review the actual frames/playback
before recording a review and packaging the candidate.

GIF playback is 16 fps for lightweight documentation. It is not a game-frame-rate
benchmark or a target-device performance claim. Numerical results and the
verification scope are recorded in [verification](VERIFICATION.md).
