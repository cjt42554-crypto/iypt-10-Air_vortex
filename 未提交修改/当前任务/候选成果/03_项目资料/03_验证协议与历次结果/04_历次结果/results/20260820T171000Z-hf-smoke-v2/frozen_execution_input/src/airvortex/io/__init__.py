"""Strict configuration and self-verifying result archive I/O."""

from .archive import (
    ARCHIVE_FORMAT_VERSION,
    PROVENANCE_FORMAT_VERSION,
    ArchiveError,
    ArchivedRun,
    capture_peak_rss,
    collect_provenance,
    git_revision_identity,
    load_run_archive,
    save_run_archive,
    source_tree_identity,
)
from .config_io import (
    StrictJSONError,
    canonical_json_bytes,
    config_sha256,
    dumps_json_strict,
    load_config_json,
    loads_json_strict,
    read_json_strict,
    save_config_json,
)

__all__ = [
    "ARCHIVE_FORMAT_VERSION",
    "PROVENANCE_FORMAT_VERSION",
    "ArchiveError",
    "ArchivedRun",
    "StrictJSONError",
    "canonical_json_bytes",
    "capture_peak_rss",
    "collect_provenance",
    "config_sha256",
    "dumps_json_strict",
    "load_config_json",
    "load_run_archive",
    "loads_json_strict",
    "read_json_strict",
    "save_config_json",
    "save_run_archive",
    "source_tree_identity",
    "git_revision_identity",
]
