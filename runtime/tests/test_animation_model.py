import json
import unittest
from pathlib import Path

from runtime.animation_model import AnimationModel


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = json.loads((ROOT / "assets" / "pet-manifest.json").read_text(encoding="utf-8"))


class AnimationModelTests(unittest.TestCase):
    def test_working_activity_selects_a_persistent_loop(self) -> None:
        model = AnimationModel(MANIFEST)
        model.apply_state("WORKING", "searching")
        self.assertEqual(model.active_clip_name, "searching_reading")
        for tick in range(12):
            model.advance(150, tick * 150)
        self.assertEqual(model.active_clip_name, "searching_reading")

    def test_interaction_returns_to_latest_agent_state(self) -> None:
        model = AnimationModel(MANIFEST)
        model.apply_state("THINKING")
        model.play_overlay("head_pat")
        model.apply_state("WAITING")
        for tick in range(8):
            model.advance(200, tick * 200)
        self.assertEqual(model.active_clip_name, "waiting")
        self.assertEqual(model.base_state, "WAITING")

    def test_pulse_expires_to_current_base_state(self) -> None:
        model = AnimationModel(MANIFEST)
        model.apply_state("WORKING", "editing")
        model.apply_pulse("SUCCESS", 1000, 100, "IDLE")
        self.assertEqual(model.active_clip_name, "success")
        model.advance(100, 1200)
        self.assertEqual(model.active_clip_name, "idle")

    def test_idle_micro_does_not_interrupt_agent_work(self) -> None:
        model = AnimationModel(MANIFEST)
        model.apply_state("THINKING")
        self.assertFalse(model.play_idle_micro())
        self.assertEqual(model.active_clip_name, "thinking")


if __name__ == "__main__":
    unittest.main()
