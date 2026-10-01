import unittest

from garment_cv.backend.zones.geometry import ground_point, point_in_polygon

SQUARE = [(0.2, 0.2), (0.8, 0.2), (0.8, 0.8), (0.2, 0.8)]
TRIANGLE = [(0.1, 0.1), (0.9, 0.1), (0.1, 0.9)]


class PointInPolygon(unittest.TestCase):
    def test_inside_and_outside_a_square(self):
        self.assertTrue(point_in_polygon(0.5, 0.5, SQUARE))
        self.assertFalse(point_in_polygon(0.05, 0.05, SQUARE))
        self.assertFalse(point_in_polygon(0.9, 0.9, SQUARE))

    def test_inside_and_outside_a_triangle(self):
        self.assertTrue(point_in_polygon(0.2, 0.2, TRIANGLE))
        self.assertFalse(point_in_polygon(0.8, 0.8, TRIANGLE))

    def test_point_just_outside_an_edge(self):
        self.assertFalse(point_in_polygon(0.19, 0.5, SQUARE))
        self.assertTrue(point_in_polygon(0.21, 0.5, SQUARE))

    def test_empty_or_degenerate_polygon_is_never_inside(self):
        self.assertFalse(point_in_polygon(0.5, 0.5, []))
        self.assertFalse(point_in_polygon(0.5, 0.5, [(0.1, 0.1), (0.2, 0.2)]))


class GroundPoint(unittest.TestCase):
    def test_uses_bottom_centre_not_the_box_centre(self):
        # a 100x200 box at the left edge of a 1000x1000 frame, feet at the bottom
        gx, gy = ground_point((0.0, 0.0, 100.0, 200.0), 1000, 1000)
        self.assertAlmostEqual(gx, 0.05)   # centre of 0-100 -> 50 / 1000
        self.assertAlmostEqual(gy, 0.2)    # bottom edge y=200 -> 200 / 1000, not the box's midline

    def test_clamped_to_0_1_even_for_an_out_of_frame_box(self):
        gx, gy = ground_point((-50.0, 0.0, 50.0, 1200.0), 1000, 1000)
        self.assertGreaterEqual(gx, 0.0)
        self.assertLessEqual(gy, 1.0)

    def test_degenerate_frame_size_does_not_crash(self):
        self.assertEqual(ground_point((0, 0, 10, 10), 0, 0), (0.0, 0.0))


if __name__ == "__main__":
    unittest.main()
