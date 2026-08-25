from __future__ import annotations

import hashlib
import json
from pathlib import Path
import platform
import sys
import tempfile
import threading
import time
import unittest

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from airvortex import (  # noqa: E402
    BackendMode,
    ConfigError,
    ContainerConfig,
    Control,
    ControlState,
    DriveConfig,
    Event,
    ResultError,
    SimulationConfig,
    SimulationResult,
    StirBarConfig,
)
from airvortex.io import (  # noqa: E402
    ArchiveError,
    capture_peak_rss,
    collect_provenance,
    git_revision_identity,
    StrictJSONError,
    config_sha256,
    load_config_json,
    load_run_archive,
    loads_json_strict,
    save_config_json,
    save_run_archive,
    source_tree_identity,
)


class ConfigTests(unittest.TestCase):
    def test_defaults_round_trip_without_loss(self) -> None:
        config = SimulationConfig()
        decoded = SimulationConfig.from_dict(config.to_dict())
        self.assertEqual(config, decoded)
        self.assertEqual(config_sha256(config), config_sha256(decoded))

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            save_config_json(path, config)
            self.assertEqual(config, load_config_json(path))
            with self.assertRaises(FileExistsError):
                save_config_json(path, config)

        integer_input = SimulationConfig(container=ContainerConfig(radius_m=1))
        self.assertIsInstance(integer_input.container.radius_m, float)
        self.assertEqual(
            config_sha256(integer_input),
            config_sha256(SimulationConfig.from_dict(integer_input.to_dict())),
        )

    def test_unknown_and_wrongly_typed_fields_are_rejected_recursively(self) -> None:
        raw = SimulationConfig().to_dict()
        raw["mystery"] = 7
        with self.assertRaisesRegex(ConfigError, "unknown field"):
            SimulationConfig.from_dict(raw)

        raw = SimulationConfig().to_dict()
        raw["fluid"]["unknown_viscosity"] = 1.0
        with self.assertRaisesRegex(ConfigError, "unknown field"):
            SimulationConfig.from_dict(raw)

        raw = SimulationConfig().to_dict()
        raw["high_fidelity"]["radial_cells"] = 96.0
        with self.assertRaisesRegex(ConfigError, "integer"):
            SimulationConfig.from_dict(raw)

        raw = SimulationConfig().to_dict()
        raw["drive"]["target_rpm"] = True
        with self.assertRaisesRegex(ConfigError, "real number"):
            SimulationConfig.from_dict(raw)

    def test_unphysical_geometry_and_time_settings_are_rejected(self) -> None:
        self.assertEqual(DriveConfig(target_rpm=0.0).target_rpm, 0.0)
        with self.assertRaises(ConfigError):
            ContainerConfig(radius_m=-0.01)
        with self.assertRaisesRegex(ConfigError, "fit inside"):
            SimulationConfig(
                container=ContainerConfig(radius_m=0.01),
                stir_bar=StirBarConfig(half_length_m=0.012, radius_m=0.003),
            )

        raw = SimulationConfig().to_dict()
        raw["fluid"]["liquid_dynamic_viscosity_pa_s"] = float("nan")
        with self.assertRaises(ConfigError):
            SimulationConfig.from_dict(raw)

        raw = SimulationConfig().to_dict()
        raw["forward"]["azimuthal_decay_power"] = 0.8
        with self.assertRaisesRegex(ConfigError, "fixed at 1.0"):
            SimulationConfig.from_dict(raw)

        raw = SimulationConfig().to_dict()
        raw["high_fidelity"]["pressure_solver_device"] = "tpu"
        with self.assertRaisesRegex(ConfigError, "auto.*cpu.*cuda"):
            SimulationConfig.from_dict(raw)

        raw = SimulationConfig().to_dict()
        raw["high_fidelity"]["cfl_controller_target_fraction"] = 1.0
        with self.assertRaisesRegex(ConfigError, "must be less than 1"):
            SimulationConfig.from_dict(raw)

        raw = SimulationConfig().to_dict()
        raw["high_fidelity"]["maximum_cfl_step_retries"] = -1
        with self.assertRaisesRegex(ConfigError, ">= 0"):
            SimulationConfig.from_dict(raw)

        raw = SimulationConfig().to_dict()
        del raw["high_fidelity"]["pressure_solver_device"]
        self.assertEqual(
            SimulationConfig.from_dict(raw).high_fidelity.pressure_solver_device,
            "auto",
        )

    def test_strict_json_rejects_duplicates_extensions_and_nonfinite(self) -> None:
        with self.assertRaisesRegex(StrictJSONError, "duplicate"):
            loads_json_strict('{"rpm": 800, "rpm": 900}')
        with self.assertRaisesRegex(StrictJSONError, "non-standard"):
            loads_json_strict('{"rpm": NaN}')
        with self.assertRaises(StrictJSONError):
            loads_json_strict('{"rpm": 900} trailing')


class ControlTests(unittest.TestCase):
    def test_pause_resume_and_soft_stop_are_real_synchronisation(self) -> None:
        control = Control()
        self.assertTrue(control.pause())
        entered = threading.Event()
        passed = threading.Event()
        values: list[bool] = []

        def worker() -> None:
            entered.set()
            values.append(control.checkpoint())
            passed.set()

        thread = threading.Thread(target=worker)
        thread.start()
        self.assertTrue(entered.wait(0.5))
        self.assertFalse(passed.wait(0.05), "paused checkpoint must block")
        self.assertTrue(control.resume())
        self.assertTrue(passed.wait(0.5))
        thread.join(0.5)
        self.assertEqual(values, [True])
        self.assertEqual(control.state, ControlState.RUNNING)

        self.assertTrue(control.pause())
        stopped = threading.Event()

        def paused_worker() -> None:
            values.append(control.checkpoint())
            stopped.set()

        thread = threading.Thread(target=paused_worker)
        thread.start()
        time.sleep(0.02)
        self.assertTrue(control.request_stop("unit test"))
        self.assertTrue(stopped.wait(0.5), "soft stop must release a paused checkpoint")
        thread.join(0.5)
        self.assertEqual(values[-1], False)
        self.assertTrue(control.stop_requested)
        self.assertEqual(control.stop_reason, "unit test")


class ArchiveTests(unittest.TestCase):
    def test_source_tree_hash_is_deterministic_and_allow_listed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src" / "airvortex").mkdir(parents=True)
            (root / "outputs" / "cache").mkdir(parents=True)
            (root / "pyproject.toml").write_text(
                '[project]\nname = "airvortex"\n', encoding="utf-8"
            )
            # Deliberately create the lexically later name first.
            (root / "src" / "airvortex" / "zeta.py").write_text(
                "VALUE = 2\n", encoding="utf-8"
            )
            (root / "src" / "airvortex" / "alpha.py").write_text(
                "VALUE = 1\n", encoding="utf-8"
            )
            first = source_tree_identity(root)
            second = source_tree_identity(root)
            self.assertTrue(first["available"])
            self.assertEqual(first, second)
            self.assertEqual(first["algorithm"], "sha256")
            self.assertEqual(first["file_count"], 3)
            self.assertFalse(first["absolute_root_stored"])
            self.assertIn("uint64_be(path_byte_length)", first["canonicalization"])
            manual = hashlib.sha256(b"airvortex-source-tree-v1\0")
            for relative_name in (
                "pyproject.toml",
                "src/airvortex/alpha.py",
                "src/airvortex/zeta.py",
            ):
                relative = relative_name.encode("utf-8")
                payload = (root / relative_name).read_bytes()
                manual.update(len(relative).to_bytes(8, "big"))
                manual.update(relative)
                manual.update(len(payload).to_bytes(8, "big"))
                manual.update(payload)
            self.assertEqual(first["value"], manual.hexdigest())

            clone = root / "different-absolute-root"
            (clone / "src" / "airvortex").mkdir(parents=True)
            for relative_name in (
                "pyproject.toml",
                "src/airvortex/alpha.py",
                "src/airvortex/zeta.py",
            ):
                destination = clone / relative_name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes((root / relative_name).read_bytes())
            self.assertEqual(first["value"], source_tree_identity(clone)["value"])

            # Generated output is outside the positive allow-list.
            (root / "outputs" / "cache" / "private.txt").write_text(
                "do not hash me", encoding="utf-8"
            )
            self.assertEqual(first["value"], source_tree_identity(root)["value"])
            (root / "src" / "airvortex" / "alpha.py").write_text(
                "VALUE = 3\n", encoding="utf-8"
            )
            self.assertNotEqual(first["value"], source_tree_identity(root)["value"])

    def test_resource_provenance_has_explicit_units_and_availability(self) -> None:
        config = SimulationConfig()
        direct = collect_provenance(config)
        cpu = direct["resources"]["process_cpu_time"]
        self.assertFalse(cpu["available"])
        self.assertIsNone(cpu["value"])
        self.assertEqual(cpu["unit"], "s")
        self.assertIn("time.process_time", cpu["reason"])

        peak = capture_peak_rss()
        self.assertEqual(peak["unit"], "byte")
        self.assertIsInstance(peak["available"], bool)
        self.assertIn("not a per-run increment", peak["attribution_note"])
        if platform.system() == "Linux":
            self.assertTrue(peak["available"])
            self.assertEqual(peak["native_unit"], "KiB")
            self.assertEqual(peak["conversion_to_bytes"], 1024)
            self.assertIsInstance(peak["value"], int)
            self.assertGreater(peak["value"], 0)
        else:
            self.assertTrue(peak["available"] or peak["reason"])
        with self.assertRaisesRegex(ResultError, "finite non-negative"):
            collect_provenance(config, process_cpu_seconds=float("nan"))

    def test_source_boundary_mismatch_is_marked_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "src" / "airvortex"
            package.mkdir(parents=True)
            (root / "pyproject.toml").write_text(
                '[project]\nname = "airvortex"\n', encoding="utf-8"
            )
            source = package / "model.py"
            source.write_text("VALUE = 1\n", encoding="utf-8")
            start = {
                **source_tree_identity(root),
                "sample_point": "test execution start",
            }
            source.write_text("VALUE = 2\n", encoding="utf-8")
            end = {
                **source_tree_identity(root),
                "sample_point": "test execution end",
            }
            provenance = collect_provenance(
                SimulationConfig(),
                source_tree_execution_start=start,
                source_tree_execution_end=end,
            )
            identity = provenance["source_identity"]
            self.assertFalse(identity["disk_content_tree"]["boundary_match"])
            self.assertEqual(identity["source_tree_sha256_start"], start["value"])
            self.assertEqual(identity["source_tree_sha256_end"], end["value"])
            self.assertFalse(identity["unchanged"])
            self.assertFalse(identity["execution_source_identity_valid"])
            self.assertEqual(
                identity["reproducibility_status"],
                "invalid_source_tree_changed_between_boundaries",
            )
            self.assertFalse(identity["loaded_module_bytes_verified"])
            self.assertIn("already imported", identity["interpretation"])

    def test_git_revision_is_only_reported_from_resolved_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            revision = git_revision_identity(root)
            self.assertFalse(revision["available"])
            self.assertIsNone(revision["value"])

            git = root / ".git"
            (git / "refs" / "heads").mkdir(parents=True)
            (git / "HEAD").write_text("ref: refs/heads/main\n", encoding="ascii")
            object_id = "0123456789abcdef" * 2 + "01234567"
            (git / "refs" / "heads" / "main").write_text(
                object_id + "\n", encoding="ascii"
            )
            resolved = git_revision_identity(root)
            self.assertTrue(resolved["available"])
            self.assertEqual(resolved["value"], object_id)
            self.assertEqual(resolved["object_id_algorithm"], "sha1")

            (git / "refs" / "heads" / "main").write_text(
                "not-a-git-object-id\n", encoding="ascii"
            )
            invalid = git_revision_identity(root)
            self.assertFalse(invalid["available"])
            self.assertIsNone(invalid["value"])
            self.assertIn("no revision claimed", invalid["reason"])

    def test_json_csv_npz_archive_round_trip_and_hash_verification(self) -> None:
        config = SimulationConfig()
        result = SimulationResult(
            backend=BackendMode.FORWARD,
            time_s=np.array([0.0, 0.5, 1.0]),
            observables={
                "air_core_depth_m": np.array([0.0, 0.001, 0.003]),
                "surface_profile_m": np.array(
                    [[0.06, 0.06], [0.059, 0.061], [0.057, 0.062]]
                ),
            },
            fields={"radius_m": np.array([0.0, 0.045])},
            events=(Event(0.5, "air_core_onset", data={"threshold_m": 0.001}),),
            metadata={"model": "axisymmetric-reduced", "converged": True},
        )
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "run-001"
            save_run_archive(
                archive_path,
                config,
                result,
                extra_provenance={"campaign": "unit-test"},
            )
            self.assertTrue((archive_path / "manifest.json").is_file())
            self.assertTrue((archive_path / "timeseries.csv").is_file())
            self.assertTrue((archive_path / "arrays.npz").is_file())
            loaded = load_run_archive(archive_path)
            self.assertEqual(loaded.config, config)
            self.assertEqual(loaded.manifest["archive_format_version"], 2)
            integrity = loaded.manifest["integrity_model"]
            self.assertFalse(integrity["manifest_self_authenticated"])
            self.assertIn("do not prove authenticity", integrity["security_claim"])
            self.assertIn("independent trusted location", integrity["sealed_blind_requirement"])
            np.testing.assert_array_equal(loaded.result.time_s, result.time_s)
            for name in result.observables:
                np.testing.assert_array_equal(
                    loaded.result.observables[name], result.observables[name]
                )
            self.assertEqual(loaded.result.events[0].kind, "air_core_onset")
            self.assertEqual(loaded.provenance["extra"]["campaign"], "unit-test")
            self.assertEqual(loaded.provenance["provenance_format_version"], 2)
            source_identity = loaded.provenance["source_identity"]
            disk_tree = source_identity["disk_content_tree"]
            self.assertFalse(disk_tree["execution_start"]["available"])
            self.assertTrue(disk_tree["execution_end"]["available"])
            self.assertIsNone(disk_tree["boundary_match"])
            self.assertEqual(
                source_identity["reproducibility_status"],
                "indeterminate_missing_source_tree_boundary",
            )
            revision = source_identity["git_revision"]["execution_end"]
            self.assertEqual(revision["vcs"], "git")
            if not revision["available"]:
                self.assertIsNone(revision["value"])
                self.assertIn("no revision claimed", revision["reason"])
            with tempfile.TemporaryDirectory() as non_git_directory:
                unavailable_revision = git_revision_identity(non_git_directory)
            self.assertFalse(unavailable_revision["available"])
            self.assertIsNone(unavailable_revision["value"])
            self.assertIn("no revision claimed", unavailable_revision["reason"])
            invocation = loaded.provenance["invocation"]
            self.assertFalse(invocation["raw_argv_stored"])
            self.assertFalse(invocation["absolute_paths_stored"])
            self.assertEqual(
                invocation["entry_point"], "airvortex.io.save_run_archive"
            )
            csv_header = (archive_path / "timeseries.csv").read_text().splitlines()[0]
            self.assertEqual(csv_header, "time_s,air_core_depth_m")
            with self.assertRaises(FileExistsError):
                save_run_archive(archive_path, config, result)

            result_path = archive_path / "result.json"
            result_path.write_text(result_path.read_text() + " ", encoding="utf-8")
            with self.assertRaisesRegex(ArchiveError, "size mismatch"):
                load_run_archive(archive_path)

    def test_provenance_tampering_is_detected_by_payload_manifest(self) -> None:
        config = SimulationConfig()
        result = SimulationResult(
            backend=BackendMode.FORWARD,
            time_s=np.array([0.0]),
            observables={"air_core_depth_m": np.array([0.0])},
        )
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "run"
            save_run_archive(
                archive_path,
                config,
                result,
                extra_provenance={"campaign": "AAAA"},
            )
            manifest_path = archive_path / "manifest.json"
            manifest_original = manifest_path.read_text(encoding="utf-8")
            manifest = json.loads(manifest_original)
            self.assertFalse(manifest["execution_source_identity_valid"])
            manifest["execution_source_identity_valid"] = True
            manifest_path.write_text(
                json.dumps(
                    manifest,
                    ensure_ascii=False,
                    allow_nan=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ArchiveError, "source-quality gate mismatch"):
                load_run_archive(archive_path)
            manifest_path.write_text(manifest_original, encoding="utf-8")

            provenance_path = archive_path / "provenance.json"
            original = provenance_path.read_text(encoding="utf-8")
            self.assertIn('"campaign": "AAAA"', original)
            provenance_path.write_text(
                original.replace('"campaign": "AAAA"', '"campaign": "BBBB"'),
                encoding="utf-8",
            )
            # Equal-length tampering defeats size-only checks but not SHA-256.
            with self.assertRaisesRegex(ArchiveError, "SHA-256 mismatch"):
                load_run_archive(archive_path)

    def test_legacy_provenance_remains_readable_without_invented_metrics(self) -> None:
        config = SimulationConfig()
        result = SimulationResult(
            backend=BackendMode.FORWARD,
            time_s=np.array([0.0]),
            observables={"air_core_depth_m": np.array([0.0])},
        )
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "legacy"
            save_run_archive(archive_path, config, result)
            current = json.loads(
                (archive_path / "provenance.json").read_text(encoding="utf-8")
            )
            legacy = {
                key: current[key]
                for key in (
                    "recorded_at_utc",
                    "airvortex_version",
                    "config_sha256",
                    "python",
                    "platform",
                    "dependencies",
                    "extra",
                )
            }
            provenance_bytes = (
                json.dumps(
                    legacy,
                    ensure_ascii=False,
                    allow_nan=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n"
            ).encode("utf-8")
            (archive_path / "provenance.json").write_bytes(provenance_bytes)
            manifest_path = archive_path / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["archive_format_version"] = 1
            for current_only_field in (
                "provenance_reproducibility_status",
                "execution_source_identity_valid",
                "integrity_model",
            ):
                manifest.pop(current_only_field)
            descriptor = manifest["files"]["provenance.json"]
            descriptor["size_bytes"] = len(provenance_bytes)
            descriptor["sha256"] = hashlib.sha256(provenance_bytes).hexdigest()
            manifest_path.write_text(
                json.dumps(
                    manifest,
                    ensure_ascii=False,
                    allow_nan=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            loaded = load_run_archive(archive_path)
            self.assertNotIn("provenance_format_version", loaded.provenance)
            self.assertNotIn("resources", loaded.provenance)

    def test_manifest_semantics_are_cross_checked_against_every_payload(self) -> None:
        config = SimulationConfig()
        result = SimulationResult(
            backend=BackendMode.FORWARD,
            time_s=np.array([0.0, 0.5]),
            observables={
                "depth_m": np.array([0.0, 0.001]),
                "profile_m": np.array([[0.0, 0.0], [0.0, 0.001]]),
            },
        )
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "run"
            save_run_archive(archive_path, config, result)
            manifest_path = archive_path / "manifest.json"
            original = json.loads(manifest_path.read_text(encoding="utf-8"))
            cases = (
                ("problem_id", "tampered-problem", "problem_id mismatch"),
                ("backend", "high_fidelity", "backend mismatch"),
                ("status", "stopped", "status mismatch"),
                (
                    "csv_observable_columns",
                    ["profile_m"],
                    "scalar-observable columns mismatch",
                ),
            )
            for field, value, message in cases:
                with self.subTest(field=field):
                    changed = json.loads(json.dumps(original))
                    changed[field] = value
                    manifest_path.write_text(
                        json.dumps(
                            changed,
                            ensure_ascii=False,
                            allow_nan=False,
                            indent=2,
                            sort_keys=True,
                        )
                        + "\n",
                        encoding="utf-8",
                    )
                    with self.assertRaisesRegex(ArchiveError, message):
                        load_run_archive(archive_path)
            changed = json.loads(json.dumps(original))
            changed["unknown_manifest_field"] = True
            manifest_path.write_text(
                json.dumps(changed, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ArchiveError, "top-level fields"):
                load_run_archive(archive_path)
            manifest_path.write_text(
                json.dumps(
                    original,
                    ensure_ascii=False,
                    allow_nan=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )

            csv_path = archive_path / "timeseries.csv"
            csv_bytes = csv_path.read_bytes().replace(b"time_s,depth_m", b"time_s,other_m")
            csv_path.write_bytes(csv_bytes)
            changed = json.loads(json.dumps(original))
            changed["files"]["timeseries.csv"] = {
                "sha256": hashlib.sha256(csv_bytes).hexdigest(),
                "size_bytes": len(csv_bytes),
            }
            manifest_path.write_text(
                json.dumps(
                    changed,
                    ensure_ascii=False,
                    allow_nan=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ArchiveError, "header does not match"):
                load_run_archive(archive_path)

    def test_backend_mismatch_is_rejected(self) -> None:
        result = SimulationResult(
            backend=BackendMode.HIGH_FIDELITY,
            time_s=np.array([0.0]),
            observables={"air_core_depth_m": np.array([0.0])},
        )
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ArchiveError):
                save_run_archive(Path(directory) / "run", SimulationConfig(), result)


if __name__ == "__main__":
    unittest.main()
