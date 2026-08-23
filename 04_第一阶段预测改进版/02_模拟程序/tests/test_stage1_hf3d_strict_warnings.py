import json
import os
from pathlib import Path
import subprocess
import sys
import unittest


class Stage1HF3DStrictWarningsTests(unittest.TestCase):
    def test_cli_self_check_passes_with_warnings_as_errors(self) -> None:
        sim = Path(__file__).resolve().parents[1]
        env = os.environ.copy()
        env["PYTHONPATH"] = str(sim / "src")
        env["PYTHONWARNINGS"] = "error"
        env["AIRVORTEX_PYTHON"] = sys.executable
        process = subprocess.run(
            [sys.executable, "run_stage1_cli.py", "self-check"],
            cwd=sim,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(process.returncode, 0, process.stderr or process.stdout)
        payload = json.loads(process.stdout)
        self.assertTrue(payload["ok"])
        for key in (
            "backend_discovery",
            "backend_forward",
            "backend_high_fidelity",
            "forward_minimal_smoke",
            "high_fidelity_16x16x24_smoke",
        ):
            self.assertTrue(payload["checks"][key]["ok"], key)


if __name__ == "__main__":
    unittest.main()
