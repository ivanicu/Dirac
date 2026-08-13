from __future__ import annotations

import io
import json
from pathlib import Path
import tempfile
import zipfile

import pytest


def test_directory_manifest_is_deterministic_and_rejects_symlinks():
    from motif.artifact_lifecycle import build_directory_manifest
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "b").write_bytes(b"two")
        (root / "a").write_bytes(b"one")
        first = build_directory_manifest(root)
        second = build_directory_manifest(root)
        assert first == second
        assert [row["path"] for row in first["files"]] == ["a", "b"]
        (root / "link").symlink_to(root / "a")
        with pytest.raises(ValueError, match="symlink"):
            build_directory_manifest(root)


def test_gc_preserves_reachable_pinned_and_critical_artifacts():
    from motif.artifact_lifecycle import gc_plan
    rows = [
        {"artifact_id": "root", "dependency_ids": ["child"],
         "retention_tier": "critical_immutable"},
        {"artifact_id": "child", "dependency_ids": [],
         "retention_tier": "recomputable_expensive"},
        {"artifact_id": "temp", "dependency_ids": [], "retention_tier": "temporary"},
        {"artifact_id": "pin", "dependency_ids": [], "retention_tier": "temporary",
         "pinned": True},
    ]
    plan = gc_plan(artifacts=rows, active_root_ids=["root"], free_bytes=5,
                   low_watermark_bytes=30, high_watermark_bytes=20,
                   emergency_watermark_bytes=10)
    assert plan["pressure"] == "emergency"
    assert plan["tombstone_candidates"] == ["temp"]
    assert plan["deletion_performed"] is False


def test_archive_intake_rejects_traversal_and_compression_bomb():
    from motif.safe_inputs import inspect_archive
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "bad.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("../escape.sdf", "x")
        with pytest.raises(ValueError, match="path traversal"):
            inspect_archive(path)
        bomb = Path(directory) / "bomb.zip"
        with zipfile.ZipFile(bomb, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("large.sdf", b"0" * 100000)
        with pytest.raises(ValueError, match="compression-ratio"):
            inspect_archive(bomb, maximum_compression_ratio=2)


def test_docking_validation_is_target_specific_and_fail_closed():
    from motif.docking import assess_docking_validation
    result = assess_docking_validation(
        target_protocol_ref={"kind": "pose_protocol_release", "id": "p1"},
        redocking_symmetry_rmsd_angstrom=1.4,
        cross_docking_success_fraction=.7,
        enrichment={"ef1_percent": 8.0, "roc_auc": .72},
        box_sensitivity_rank_correlation=.9,
        seed_sensitivity_rank_correlation=.85,
        ligand_size_bias_slope=.03,
        known_inactive_false_positive_rate=.12,
        thresholds={"maximum_redocking_rmsd_angstrom": 2.0,
                    "minimum_cross_docking_success_fraction": .6,
                    "minimum_ef1_percent": 5,
                    "minimum_box_rank_correlation": .8,
                    "minimum_seed_rank_correlation": .8,
                    "maximum_absolute_size_bias_slope": .1,
                    "maximum_inactive_false_positive_rate": .1})
    assert not result["production_ranking_eligible"]
    assert result["reason_codes"] == ["KNOWN_INACTIVES_FAILED"]
    assert "no_raw_score_averaging" in result["score_fusion_rule"]


def test_new_scientific_contracts_are_machine_validated():
    from contracts.validation import check_schema
    root = Path(__file__).resolve().parents[2]
    for name in ("measurement-observation", "prepared-receptor-state", "torsion-protocol"):
        schema = json.loads((root / "contracts/domain/motif" / f"{name}.schema.json").read_text())
        check_schema(schema)
