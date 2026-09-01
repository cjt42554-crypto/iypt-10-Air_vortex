from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path
from typing import Any

WORKSPACE = Path(__file__).resolve().parents[1]
if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))

import p0p2_authorization as authorization
import p0p2_contract as contract


def _authorized_grant() -> dict[str, Any]:
    value: dict[str, Any] = contract.default_authorization()
    value.update(
        {
            "status": contract.AUTHORIZED_STATUS,
            "dynamic_samples_authorized": True,
            "reviewed_snapshot_commit": "a" * 40,
            "ticket_id": "HF3D_P0P2_PRO_TICKET_SYNTHETIC_TEST",
            "grant_nonce": contract.GRANT_NONCE_PREFIX + "b" * 64,
            "maximum_schedule_claims": 1,
        }
    )
    return value


def _blob_record(seed: str) -> dict[str, Any]:
    return {
        "git_oid": seed * 40,
        "sha256": seed * 64,
        "byte_length": 100,
        "mode": "100644",
    }


def _ticket() -> dict[str, Any]:
    ruling_path = "90_候选工作区/current/25_P0P2独立静态复核与父票据裁决.md"
    return {
        "schema_version": 1,
        "status": authorization.TICKET_STATUS,
        "ticket_id": "HF3D_P0P2_PRO_TICKET_SYNTHETIC_TEST",
        "authorization_path": contract.relative(contract.AUTHORIZATION_PATH),
        "ruling_path": ruling_path,
        "reviewed_snapshot": {
            "commit": "a" * 40,
            "tree": "b" * 40,
            "files": [{"synthetic": "validated by repository audit later"}],
        },
        "fixed_schedule": contract.fixed_schedule(),
        "formal_source_tree_sha256": contract.FORMAL_SOURCE_SHA256,
        "e0_canonical_tree_sha256": contract.E0_CANONICAL_TREE_SHA256,
        "denied_parent_blob": _blob_record("c"),
        "authorized_child_blob": _blob_record("d"),
        "review_parent_policy": {
            "allowed_review_paths": [
                contract.relative(contract.TICKET_PATH),
                ruling_path,
            ],
            "protected_prefixes": sorted(authorization._REQUIRED_PROTECTED_PREFIXES),
            "ticket_path": contract.relative(contract.TICKET_PATH),
            "ruling_path": ruling_path,
        },
        "child_commit_policy": {
            "single_parent": True,
            "exactly_one_modified_path": contract.relative(contract.AUTHORIZATION_PATH),
            "old_mode": "100644",
            "new_mode": "100644",
            "rename_copy_symlink_submodule": "PROHIBITED",
        },
        "claim_policy": {
            "maximum_schedule_claims": 1,
            "preflight_consumes_claim": False,
            "claim_before_first_sample": True,
            "sample_replacement": "PROHIBITED",
            "schedule_resume_after_claim": "PROHIBITED",
            "schedule_retry_or_supplement": "PROHIBITED",
            "P1": "PROHIBITED",
        },
    }


class AuthorizationTests(unittest.TestCase):
    def test_tracked_authorization_is_exact_default_denial(self) -> None:
        observed = authorization.load_authorization(require_dynamic=False)
        self.assertEqual(observed["status"], contract.DENIED_STATUS)
        self.assertFalse(observed["authorization_gate_pass"])
        self.assertFalse(observed["dynamic_execution_allowed"])
        self.assertTrue(observed["denial_schema_valid"])

    def test_dynamic_request_is_rejected_under_default_denial(self) -> None:
        with self.assertRaisesRegex(
            contract.P0P2Error, "HF3D_P0P2_DYNAMIC_EXECUTION_NOT_AUTHORIZED"
        ):
            authorization.load_authorization(require_dynamic=True)

    def test_denial_drift_or_extra_field_is_rejected(self) -> None:
        value = contract.default_authorization()
        value["unexpected"] = True
        with self.assertRaisesRegex(contract.P0P2Error, "not the exact frozen denial"):
            authorization.validate_denied_authorization(value)

    def test_future_authorized_grant_schema_is_exact(self) -> None:
        value = _authorized_grant()
        self.assertEqual(authorization.validate_authorized_grant(value), value)
        changed = copy.deepcopy(value)
        changed["grant_nonce"] = "not-a-hex-nonce"
        with self.assertRaisesRegex(contract.P0P2Error, "grant fields failed"):
            authorization.validate_authorized_grant(changed)

    def test_future_parent_ticket_schema_and_policies_are_exact(self) -> None:
        value = _ticket()
        self.assertEqual(authorization.validate_ticket(value), value)
        changed = copy.deepcopy(value)
        changed["claim_policy"]["schedule_resume_after_claim"] = "ALLOWED"
        with self.assertRaisesRegex(contract.P0P2Error, "ticket policy is invalid"):
            authorization.validate_ticket(changed)

    def test_future_ticket_extra_field_is_rejected(self) -> None:
        value = _ticket()
        value["unexpected"] = True
        with self.assertRaisesRegex(contract.P0P2Error, "ticket keys differ"):
            authorization.validate_ticket(value)

    def test_future_ticket_must_bind_complete_e0_i2_and_static_set(self) -> None:
        required = authorization.minimum_reviewed_paths()
        self.assertIn(contract.relative(contract.CONTRACT_PATH), required)
        self.assertIn(contract.relative(contract.AUTHORIZATION_PATH), required)
        self.assertIn(
            contract.relative(
                contract.STATIC_ROOT / "probe_reports/P0P2_B3_ZERO_STEP.json"
            ),
            required,
        )
        self.assertIn(
            contract.relative(
                contract.STATIC_ROOT
                / "formal_round_trip_reports/P0P2_B2_FORMAL_ROUND_TRIP.json"
            ),
            required,
        )
        self.assertIn(
            contract.relative(contract.E0_SNAPSHOT_ROOT / "schedule_ledger.json"),
            required,
        )


if __name__ == "__main__":
    unittest.main()
