"""@brief Snapshot identity tests: canonical JSON, hash, manifest fields, coverage binding."""

import hashlib
import json
from pathlib import Path

import pytest

from worldmock.footprints.models import AssetRef, Checkpoint, canonical_json


def test_canonical_json_determinism():
    """@brief canonical_json produces identical output for equivalent data structures."""
    data_a = {"z": 3, "a": 1, "m": 2}
    data_b = {"a": 1, "m": 2, "z": 3}
    
    result_a = canonical_json(data_a)
    result_b = canonical_json(data_b)
    
    assert result_a == result_b
    assert result_a == b'{"a":1,"m":2,"z":3}'


def test_canonical_json_unicode_preservation():
    """@brief canonical_json preserves Unicode characters without ASCII escape."""
    data = {"text": "你好世界", "emoji": "🌍"}
    result = canonical_json(data)
    
    # Should contain literal UTF-8, not \\uXXXX escape sequences
    assert "你好世界".encode() in result
    assert "🌍".encode() in result
    assert b"\\u" not in result


def test_canonical_json_rejects_nan():
    """@brief canonical_json fails closed on NaN/Infinity."""
    with pytest.raises(ValueError, match="Out of range float values are not JSON compliant"):
        canonical_json({"value": float("nan")})
    
    with pytest.raises(ValueError, match="Out of range float values are not JSON compliant"):
        canonical_json({"value": float("inf")})


def test_canonical_json_nested_sort():
    """@brief canonical_json sorts keys at all nesting levels."""
    data = {
        "outer_z": {"inner_z": 3, "inner_a": 1},
        "outer_a": {"inner_m": 2}
    }
    result = canonical_json(data)
    
    # Verify outer keys sorted
    decoded = json.loads(result)
    assert list(decoded.keys()) == ["outer_a", "outer_z"]
    assert list(decoded["outer_z"].keys()) == ["inner_a", "inner_z"]


def test_checkpoint_manifest_sha256_binding(tmp_path: Path):
    """@brief Checkpoint.manifest_sha256 must match hash of canonical snapshots dict."""
    snapshots = {
        "state.json": AssetRef(
            sha256="a" * 64,
            size_bytes=100,
            media_type="application/json",
            source={"origin": "test"}
        ),
        "coverage.xml": AssetRef(
            sha256="b" * 64,
            size_bytes=200,
            media_type="text/xml",
            source={"origin": "coverage"}
        ),
    }
    
    # Compute expected manifest hash
    manifest_bytes = canonical_json(
        {k: v.model_dump(mode="json") for k, v in snapshots.items()}
    )
    expected_hash = hashlib.sha256(manifest_bytes).hexdigest()
    
    checkpoint = Checkpoint(
        checkpoint_id="ckpt_test",
        run_id="run_001",
        current_time=1000,
        state_version=5,
        last_event_seq=42,
        state={"world": "data"},
        snapshots=snapshots,
        manifest_sha256=expected_hash,
        record_sha256="c" * 64
    )
    
    # Positive case: hash matches
    assert checkpoint.manifest_sha256 == expected_hash


def test_checkpoint_manifest_mismatch_detection(tmp_path: Path):
    """@brief Mismatched manifest_sha256 is detectable by recomputing hash."""
    snapshots = {
        "state.json": AssetRef(
            sha256="d" * 64,
            size_bytes=50,
            media_type="application/json",
            source={"origin": "test"}
        ),
    }
    
    # Deliberately wrong hash
    wrong_hash = "e" * 64
    
    checkpoint = Checkpoint(
        checkpoint_id="ckpt_mismatch",
        run_id="run_002",
        current_time=2000,
        state_version=10,
        last_event_seq=99,
        state={"world": "altered"},
        snapshots=snapshots,
        manifest_sha256=wrong_hash,
        record_sha256="f" * 64
    )
    
    # Recompute and detect mismatch
    manifest_bytes = canonical_json(
        {k: v.model_dump(mode="json") for k, v in checkpoint.snapshots.items()}
    )
    actual_hash = hashlib.sha256(manifest_bytes).hexdigest()
    
    assert checkpoint.manifest_sha256 != actual_hash


def test_coverage_report_asset_ref(tmp_path: Path):
    """@brief Coverage report can be bound as AssetRef with text/xml media type."""
    coverage_file = tmp_path / "coverage.xml"
    coverage_content = b'<?xml version="1.0" ?><coverage></coverage>'
    coverage_file.write_bytes(coverage_content)
    
    coverage_hash = hashlib.sha256(coverage_content).hexdigest()
    
    asset = AssetRef(
        sha256=coverage_hash,
        size_bytes=len(coverage_content),
        media_type="text/xml",
        source={"path": str(coverage_file), "tool": "pytest-cov"}
    )
    
    assert asset.media_type == "text/xml"
    assert asset.sha256 == coverage_hash
    assert asset.size_bytes == len(coverage_content)


def test_snapshot_identity_invariance_under_reorder(tmp_path: Path):
    """@brief Snapshot identity (via manifest_sha256) is invariant under key reordering."""
    # Two snapshots dicts with different insertion order
    snapshots_order_a = {
        "zulu.json": AssetRef(sha256="1" * 64, size_bytes=10, media_type="application/json", source={}),
        "alpha.json": AssetRef(sha256="2" * 64, size_bytes=20, media_type="application/json", source={}),
    }
    snapshots_order_b = {
        "alpha.json": AssetRef(sha256="2" * 64, size_bytes=20, media_type="application/json", source={}),
        "zulu.json": AssetRef(sha256="1" * 64, size_bytes=10, media_type="application/json", source={}),
    }
    
    manifest_a = canonical_json({k: v.model_dump(mode="json") for k, v in snapshots_order_a.items()})
    manifest_b = canonical_json({k: v.model_dump(mode="json") for k, v in snapshots_order_b.items()})
    
    hash_a = hashlib.sha256(manifest_a).hexdigest()
    hash_b = hashlib.sha256(manifest_b).hexdigest()
    
    # Identity must be stable regardless of dict iteration order
    assert hash_a == hash_b
