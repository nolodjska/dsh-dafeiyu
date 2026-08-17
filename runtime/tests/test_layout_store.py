import json
import tempfile
import unittest
from pathlib import Path

from runtime.layout_store import DEFAULT_LAYOUT, load_layout, normalise_layout, save_layout


class LayoutStoreTests(unittest.TestCase):
    def test_corrupt_layout_falls_back_safely(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "layout.json"
            path.write_text("not json", encoding="utf-8")
            self.assertEqual(load_layout(path), DEFAULT_LAYOUT)

    def test_layout_is_clamped_and_saved_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "layout.json"
            save_layout(path, {"x": 120, "y": -20, "scale": 5, "reducedMotion": True})
            self.assertEqual(load_layout(path), {
                "version": 1,
                "x": 120,
                "y": -20,
                "scale": 1.4,
                "reducedMotion": True,
            })
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["scale"], 1.4)
            self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_boolean_is_not_accepted_as_a_coordinate_or_scale(self) -> None:
        self.assertEqual(normalise_layout({"x": True, "scale": False}), DEFAULT_LAYOUT)


if __name__ == "__main__":
    unittest.main()
