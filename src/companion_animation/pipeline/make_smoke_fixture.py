"""Synthetic, explicitly nonproduction rig/shape-key fixture. Blender CLI only."""
import json
from pathlib import Path
import sys

import bpy
sys.path.insert(0, str(Path(__file__).resolve().parent))
import blender_adapter


def make(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.scene.render.fps = 30
    bpy.ops.object.armature_add()
    armature = bpy.context.object
    armature.name = 'FixtureRig'
    bpy.ops.object.mode_set(mode='EDIT')
    root = armature.data.edit_bones[0]
    root.name = 'Root'
    root.head, root.tail = (0, 0, 0), (0, 0, .3)
    head = armature.data.edit_bones.new('Head')
    head.head, head.tail, head.parent = (0, 0, .3), (0, 0, 1), root
    jaw = armature.data.edit_bones.new('Jaw')
    jaw.head, jaw.tail, jaw.parent = (0, -.1, .55), (0, -.4, .4), head
    jaw.roll = .37
    bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.mesh.primitive_uv_sphere_add(segments=12, ring_count=8, radius=.45, location=(0, 0, .6))
    mesh = bpy.context.object
    mesh.name = 'FixtureFace'
    modifier = mesh.modifiers.new('Skin', 'ARMATURE')
    modifier.object = armature
    groups = {name: mesh.vertex_groups.new(name=name) for name in ('Head', 'Jaw')}
    for vertex in mesh.data.vertices:
        jaw_weight = .5 if vertex.co.z < -.15 and vertex.co.y < 0 else 0
        groups['Head'].add([vertex.index], 1 - jaw_weight, 'REPLACE')
        if jaw_weight:
            groups['Jaw'].add([vertex.index], jaw_weight, 'REPLACE')
    mesh.shape_key_add(name='Basis')
    blink = mesh.shape_key_add(name='Blink')
    for vertex in blink.data:
        if vertex.co.y < -.1 and vertex.co.z > 0:
            vertex.co.z *= .65
    material = bpy.data.materials.new('FixtureSkin')
    material.diffuse_color = (.3, .65, .8, 1)
    mesh.data.materials.append(material)
    parent = bpy.data.objects.new('FixtureFxParent', None)
    bpy.context.collection.objects.link(parent)
    parent.location = (.7, -.2, .6)
    parent.rotation_euler = (.2, .1, .3)
    bpy.ops.mesh.primitive_cube_add(size=.2)
    effect = bpy.context.object
    effect.name = 'FixtureTinyFx'
    effect.parent = parent
    effect.location = (.07, -.2, .4)
    effect.scale = (1e-6,) * 3
    for name, end, loop in (('Walk', 31.25, True), ('Held', 16.5, False)):
        armature.animation_data_create()
        armature.animation_data.action = None
        for bone in armature.pose.bones:
            bone.rotation_mode = 'XYZ'
            bone.rotation_euler = (0, 0, 0)
        for value, angle in ((1, 0), (8.25 if loop else end, .18), (end, 0 if loop else .18)):
            armature.pose.bones['Head'].rotation_euler.z = angle
            armature.pose.bones['Head'].keyframe_insert('rotation_euler', frame=value)
            armature.pose.bones['Jaw'].rotation_euler.x = angle * .5
            armature.pose.bones['Jaw'].keyframe_insert('rotation_euler', frame=value)
        if loop:
            # Import at24FPS places these nonidentity-bind keys .00848frame apart.
            armature.pose.bones['Jaw'].rotation_euler.x = .095
            armature.pose.bones['Jaw'].keyframe_insert('rotation_euler', frame=8.2606)
        armature.animation_data.action.name = name + 'Body'
        mesh.data.shape_keys.animation_data_create()
        mesh.data.shape_keys.animation_data.action = None
        for value, amount in ((1, 0), (6.5, .5), (8.25, 1), (9.5, 0), (end, 0)) if loop else ((1, 0), (end, 1)):
            blink.value = amount
            blink.keyframe_insert('value', frame=value)
        mesh.data.shape_keys.animation_data.action.name = name + 'Face'
        effect.animation_data_create()
        effect.animation_data.action = None
        # Regression: first animated position differs from the serialized default.
        for value, position in ((1, (.09, -.16, .41)), (8.25 if loop else end, (.1, -.1, .45)), (end, (.09, -.16, .41) if loop else (.1, -.1, .45))):
            effect.location = position
            effect.keyframe_insert('location', frame=value)
        for value, amount in ((1, 1e-6), (8.25 if loop else end, 1), (end, 1e-6 if loop else 1)):
            effect.scale = (amount,) * 3
            effect.keyframe_insert('scale', frame=value)
        effect.animation_data.action.name = name + 'TinyFx'
    for action in bpy.data.actions:
        for layer in action.layers:
            for strip in layer.strips:
                for bag in strip.channelbags:
                    for curve in bag.fcurves:
                        for key in curve.keyframe_points:
                            key.interpolation = 'LINEAR'
        action.use_fake_user = True
    marker = bpy.data.objects.new('FixtureMarker', None)
    bpy.context.collection.objects.link(marker)
    marker.location.x = 0
    marker.keyframe_insert('location', index=0, frame=1)
    marker.location.x = 1
    marker.keyframe_insert('location', index=0, frame=31.25)
    marker.animation_data.action.name = 'MarkerX'
    marker.animation_data.action.use_fake_user = True
    marker.animation_data.action = None
    marker.location = (0, 0, 0)
    effect.animation_data.action = None
    effect.location = (.07, -.2, .4)
    effect.scale = (1e-6,) * 3
    marker.keyframe_insert('location', index=1, frame=1)
    marker.location.y = 1
    marker.keyframe_insert('location', index=1, frame=16.5)
    marker.animation_data.action.name = 'MarkerY'
    marker.animation_data.action.use_fake_user = True
    marker.animation_data.action = None
    marker.location = (0, 0, 0)
    # Regression: partial object actions must restore neutral transforms.
    blender_adapter.BASE_OBJECT_TRANSFORMS = {obj.name: obj.matrix_basis.copy() for obj in bpy.context.scene.objects}
    blender_adapter.bind_action(marker, {'action': 'MarkerX', 'slot': bpy.data.actions['MarkerX'].slots[0].identifier})
    blender_adapter.frame(31.25)
    assert marker.location.x > .99
    blender_adapter.reset()
    blender_adapter.bind_action(marker, {'action': 'MarkerY', 'slot': bpy.data.actions['MarkerY'].slots[0].identifier})
    blender_adapter.frame(16.5)
    assert abs(marker.location.x) < 1e-6 and marker.location.y > .99, 'Prior object action leaked transforms'
    blender_adapter.reset()
    armature.animation_data.action = None
    mesh.data.shape_keys.animation_data.action = None
    blink.value = 0
    for bone in armature.pose.bones:
        bone.rotation_euler = (0, 0, 0)
    bpy.context.scene.frame_set(1)
    # Real import regression: Sphere and Sphere.015 are separate skinned meshes,
    # not interchangeable numeric suffixes of one character object.
    shell = mesh.copy()
    shell.data = mesh.data.copy()
    shell.name = 'FixtureBody'
    shell.data.name = mesh.data.name + '.015'
    bpy.context.collection.objects.link(shell)
    shell.shape_key_clear()
    shell.location.x += 1.2
    bpy.ops.wm.save_as_mainfile(filepath=str(directory / 'source.blend'))
    config = json.loads((Path(__file__).parent / 'config.example.json').read_text())
    config.update(model_id='synthetic_smoke', workspace=str(directory / 'workspace with spaces'),
                  sources={'model': str(directory / 'source.blend')})
    config['anatomy'] = {'mode': 'existing', 'armature': 'FixtureRig', 'roles': {'Root': 'Root', 'Head': 'Head', 'Jaw': 'Jaw'}}
    config['motion'] = {'mode': 'reuse', 'clips': [
        {'name': 'Walk', 'armature_action': 'WalkBody', 'morph_actions': {'FixtureFace': {'action': 'WalkFace', 'slot': bpy.data.actions['WalkFace'].slots[0].identifier}},
         'object_actions': {'FixtureMarker': 'MarkerX', 'FixtureTinyFx': 'WalkTinyFx'},
         'duration_s': 30.25 / 30, 'loop': True, 'moving': True, 'events': [{'name': 'contact', 'time_s': 7.25 / 30}]},
        {'name': 'Held', 'armature_action': 'HeldBody', 'morph_actions': {'FixtureFace': 'HeldFace'},
         'object_actions': {'FixtureMarker': 'MarkerY', 'FixtureTinyFx': 'HeldTinyFx'},
         'duration_s': 15.5 / 30, 'loop': False, 'moving': True, 'held_blink': 1}
    ]}
    config['face']['mode'] = 'existing'
    config['sampling'] = {'hz': 12}
    config['certification'].update(sample_hz=9, max_vertex_error=.0001, max_loop_vertex_error=.0001)
    config['preview'].update(resolution=64, fps=3)
    (directory / 'smoke.json').write_text(json.dumps(config, indent=2), encoding='utf-8')
    print(json.dumps({'fixture': str(directory / 'smoke.json'), 'engines': [item.identifier for item in bpy.context.scene.render.bl_rna.properties['engine'].enum_items]}))


if __name__ == '__main__':
    make(sys.argv[sys.argv.index('--') + 1])
