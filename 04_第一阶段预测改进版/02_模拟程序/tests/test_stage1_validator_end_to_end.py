import os
from pathlib import Path
import subprocess
import sys
import unittest


class Stage1ValidatorEndToEndTests(unittest.TestCase):
    def test_validator_self_test_is_fail_closed_from_clean_environment(self) -> None:
        sim = Path(__file__).resolve().parents[1]
        stage1 = sim.parent
        validator = (
            stage1
            / "03_项目资料"
            / "03_验证协议与历次结果"
            / "02_验证程序"
            / "run_predictive_stage1_validation.py"
        )
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        process = subprocess.run(
            [sys.executable, str(validator), "--self-test-fail-closed"],
            cwd=stage1,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(process.returncode, 0, process.stderr or process.stdout)
        self.assertIn("fail-closed self-test: PASS", process.stdout)

    def test_validator_declares_every_required_test(self) -> None:
        stage1 = Path(__file__).resolve().parents[2]
        validator = (
            stage1
            / "03_项目资料"
            / "03_验证协议与历次结果"
            / "02_验证程序"
            / "run_predictive_stage1_validation.py"
        )
        text = validator.read_text(encoding="utf-8")
        for name in (
            "test_stage1_registry_and_entrypoints.py",
            "test_stage1_shared_observable_operator.py",
            "test_stage1_core_comparison_contract.py",
            "test_stage1_units.py",
            "test_stage1_events_and_sampling.py",
            "test_stage1_camera_integration.py",
            "test_stage1_transport_conservation.py",
            "test_stage1_cls_conservation.py",
            "test_stage1_widest_path_topology.py",
            "test_stage1_hf3d_strict_warnings.py",
            "test_stage1_archive_roundtrip.py",
            "test_stage1_validator_end_to_end.py",
        ):
            self.assertIn(name, text)


if __name__ == "__main__":
    unittest.main()
