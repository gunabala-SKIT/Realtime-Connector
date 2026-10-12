"""Colour augmentation (garment_cv/training/recolour.py): only the labeled scissors/cloth pixels may
change colour - the table, skin and everything outside the boxes must be left exactly as captured,
or the augmented frames stop looking like the real scene."""
import unittest

import cv2
import numpy as np

from garment_cv.training.recolour import angles, recolour


def scene():
    """640x360 'table' (blue) with a yellow object at x 200-300, y 150-210, and a skin patch next to it."""
    hsv = np.zeros((360, 640, 3), np.uint8)
    hsv[:] = (105, 110, 210)                       # table blue
    hsv[150:210, 200:300] = (27, 230, 230)          # yellow cloth
    hsv[150:210, 300:330] = (10, 140, 150)          # skin-like patch, directly beside the cloth
    return cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)


# label: class 1 (fabric), centred at (250, 180), 120 wide, 80 tall - a box that also takes in some
# table and the skin patch, like a real label does
FABRIC_BOX = [f"1 {250/640:.6f} {180/360:.6f} {140/640:.6f} {80/360:.6f}"]


class Recolour(unittest.TestCase):
    def test_the_object_changes_colour(self):
        img = scene()
        out = recolour(img, FABRIC_BOX, 90)
        hue = lambda a: cv2.cvtColor(a, cv2.COLOR_BGR2HSV)[180, 250, 0]
        self.assertNotEqual(int(hue(img)), int(hue(out)))

    def test_pixels_outside_every_box_are_untouched(self):
        img = scene()
        out = recolour(img, FABRIC_BOX, 90)
        outside = np.ones(img.shape[:2], bool)
        outside[140:220, 180:320] = False
        self.assertTrue(np.array_equal(img[outside], out[outside]))

    def test_the_table_inside_the_box_keeps_its_colour(self):
        img = scene()
        out = recolour(img, FABRIC_BOX, 90)
        # a table pixel that sits inside the label box (top-left corner of it)
        self.assertTrue(np.array_equal(img[145:148, 183:187], out[145:148, 183:187]))

    def test_skin_inside_the_box_keeps_its_colour(self):
        img = scene()
        out = recolour(img, FABRIC_BOX, 90)
        self.assertTrue(np.array_equal(img[160:200, 305:325], out[160:200, 305:325]))

    def test_a_frame_with_no_object_boxes_is_skipped(self):
        self.assertIsNone(recolour(scene(), ["0 0.5 0.5 0.2 0.2"], 90))   # only a person label
        self.assertIsNone(recolour(scene(), [], 90))

    def test_angles_are_large_and_either_direction(self):
        a = angles(np.random.default_rng(0), 400)
        self.assertTrue(all(35 <= abs(x) <= 145 for x in a))
        self.assertTrue(any(x < 0 for x in a) and any(x > 0 for x in a))


if __name__ == "__main__":
    unittest.main()
