"""The synthetic dataset generator (garment_cv/training/make_synthetic_dataset.py) - a deliberate
pipeline proof, not real training data (see that module's own docstring and
`garment_cv/training/README.md`). These tests only check the generator produces well-formed
images/labels; they say nothing about real-world detection, which this dataset was never meant to
provide.
"""
import tempfile
import unittest
from pathlib import Path

import numpy as np

from garment_cv.training.make_synthetic_dataset import (
    CLASS_NAMES, make_image, write_dataset_yaml, write_split,
)


class MakeImage(unittest.TestCase):
    def test_image_has_the_right_shape_and_dtype(self):
        rng = np.random.default_rng(0)
        canvas, _labels = make_image(rng, imgsz=320)
        self.assertEqual(canvas.shape, (320, 320, 3))
        self.assertEqual(canvas.dtype, np.uint8)

    def test_labels_are_valid_yolo_format(self):
        rng = np.random.default_rng(1)
        _canvas, labels = make_image(rng, imgsz=640, max_objects=4)
        self.assertGreaterEqual(len(labels), 1)
        for cls_id, cx, cy, w, h in labels:
            self.assertIn(cls_id, range(len(CLASS_NAMES)))
            for v in (cx, cy, w, h):
                self.assertGreaterEqual(v, 0.0)
                self.assertLessEqual(v, 1.0)
            # the box itself must stay within the frame
            self.assertLessEqual(cx - w / 2, 1.0 + 1e-6)
            self.assertGreaterEqual(cx + w / 2, 0.0 - 1e-6)

    def test_same_seed_is_deterministic(self):
        canvas1, labels1 = make_image(np.random.default_rng(42), imgsz=320)
        canvas2, labels2 = make_image(np.random.default_rng(42), imgsz=320)
        self.assertTrue(np.array_equal(canvas1, canvas2))
        self.assertEqual(labels1, labels2)

    def test_different_seeds_are_not_identical(self):
        _canvas1, labels1 = make_image(np.random.default_rng(1), imgsz=320)
        _canvas2, labels2 = make_image(np.random.default_rng(2), imgsz=320)
        self.assertNotEqual(labels1, labels2)


class WriteSplit(unittest.TestCase):
    def test_writes_matching_image_and_label_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "synthetic"
            write_split(out, "train", n=5, seed=0, imgsz=320)
            images = sorted((out / "images" / "train").glob("*.jpg"))
            labels = sorted((out / "labels" / "train").glob("*.txt"))
            self.assertEqual(len(images), 5)
            self.assertEqual(len(labels), 5)
            self.assertEqual([p.stem for p in images], [p.stem for p in labels])

    def test_label_file_lines_are_five_whitespace_separated_numbers(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "synthetic"
            write_split(out, "val", n=3, seed=0, imgsz=320)
            label_files = list((out / "labels" / "val").glob("*.txt"))
            self.assertTrue(label_files)
            for lf in label_files:
                for line in lf.read_text(encoding="utf-8").splitlines():
                    parts = line.split()
                    self.assertEqual(len(parts), 5)
                    int(parts[0])  # class id must parse as an int
                    for v in parts[1:]:
                        float(v)  # the rest must parse as floats


class DatasetYaml(unittest.TestCase):
    def test_yaml_lists_all_seven_classes_in_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "synthetic"
            out.mkdir(parents=True)
            yaml_path = write_dataset_yaml(out)
            text = yaml_path.read_text(encoding="utf-8")
            for i, name in enumerate(CLASS_NAMES):
                self.assertIn(f"{i}: {name}", text)
            self.assertIn("train: images/train", text)
            self.assertIn("test: images/test", text)


if __name__ == "__main__":
    unittest.main()
