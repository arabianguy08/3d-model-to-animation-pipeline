import unittest
from companion_animation.pipeline.vertex_match import correspondence


def vertex(x=0, local=None, weights=(('Head', 1.),), morphs=()):
    return {'position': (x, 0., 0.), 'local': (x if local is None else local, 0., 0.),
            'weights': weights, 'morphs': morphs}


class CorrespondenceTests(unittest.TestCase):
    def test_decimal_bucket_boundary_roundoff(self):
        source = [vertex(.0000049999)]
        target = [vertex(.0000050001)]
        self.assertEqual(correspondence(source, target), [0])

    def test_skin_identity_is_required(self):
        with self.assertRaisesRegex(ValueError, 'identity'):
            correspondence([vertex()], [vertex(weights=(('Neck', 1.),))])

    def test_morph_identity_is_required(self):
        source = [vertex(morphs=(('Blink', (0., 0., .1)),))]
        with self.assertRaisesRegex(ValueError, 'identity'):
            correspondence(source, [vertex(morphs=(('Blink', (0., 0., .2)),))])

    def test_anatomical_ambiguity_is_rejected(self):
        source = [vertex(weights=(('Head', .5000003), ('Neck', .4999997))),
                  vertex(weights=(('Head', .4999997), ('Neck', .5000003)))]
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            correspondence(source, [vertex(weights=(('Head', .5), ('Neck', .5)))])

    def test_tiny_world_scale_keeps_local_geometry_identity(self):
        source = [vertex(0., local=.1), vertex(0., local=.2)]
        self.assertEqual(correspondence(source, [vertex(0., local=.2), vertex(0., local=.1)]), [1, 0])

    def test_equivalent_seam_duplicates_are_represented(self):
        self.assertEqual(correspondence([vertex(), vertex()], [vertex()]), [0])

    def test_missing_vertex_fails(self):
        with self.assertRaisesRegex(ValueError, 'omitted'):
            correspondence([vertex(), vertex(.01)], [vertex()])

    def test_position_gate_is_not_relaxed(self):
        with self.assertRaisesRegex(ValueError, 'identity'):
            correspondence([vertex()], [vertex(.000002)])


if __name__ == '__main__':
    unittest.main()
