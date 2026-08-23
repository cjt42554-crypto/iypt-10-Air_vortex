"""Subprocess gates for the pre-NumPy numerical thread policy."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
THREAD_KEYS = (
    "OPENBLAS_NUM_THREADS",
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "BLIS_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "NUMEXPR_NUM_THREADS",
)


def policy_subprocess(extra: dict[str, str] | None = None) -> dict[str, object]:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT / "src")
    environment.pop("AIRVORTEX_BLAS_THREADS", None)
    for key in THREAD_KEYS:
        environment.pop(key, None)
    if extra:
        environment.update(extra)
    code = (
        "import json, os, airvortex; "
        "print(json.dumps({'policy': airvortex.NUMERICAL_THREAD_POLICY.to_dict(), "
        "'environment': {key: os.environ.get(key) for key in "
        + repr(THREAD_KEYS)
        + "}}))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
    )
    return json.loads(completed.stdout)


class NumericalThreadPolicyTests(unittest.TestCase):
    def test_auto_defaults_to_at_most_two_before_numpy_import(self) -> None:
        payload = policy_subprocess()
        selected = int(payload["policy"]["selected_threads"])
        self.assertGreaterEqual(selected, 1)
        self.assertLessEqual(selected, 2)
        self.assertTrue(
            all(value == str(selected) for value in payload["environment"].values())
        )

    def test_explicit_airvortex_limit_overrides_library_variables(self) -> None:
        payload = policy_subprocess(
            {
                "AIRVORTEX_BLAS_THREADS": "2",
                "OPENBLAS_NUM_THREADS": "32",
                "OMP_NUM_THREADS": "16",
            }
        )
        self.assertEqual(payload["policy"]["selected_threads"], 2)
        self.assertTrue(all(value == "2" for value in payload["environment"].values()))

    def test_inherit_leaves_library_environment_untouched(self) -> None:
        payload = policy_subprocess(
            {
                "AIRVORTEX_BLAS_THREADS": "inherit",
                "OPENBLAS_NUM_THREADS": "7",
            }
        )
        self.assertIsNone(payload["policy"]["selected_threads"])
        self.assertEqual(payload["environment"]["OPENBLAS_NUM_THREADS"], "7")
        self.assertIsNone(payload["environment"]["OMP_NUM_THREADS"])


if __name__ == "__main__":
    unittest.main()
