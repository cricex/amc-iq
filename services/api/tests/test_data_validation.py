from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from data.fabric.generate import generate
from tools.validate_consistency import _source_url_allowed


def test_synthetic_source_urls_require_https() -> None:
    trial_ids = {"NCT99004324"}

    assert _source_url_allowed(
        "https://amciq.example.invalid/foundry/protocol",
        trial_ids,
    )
    assert _source_url_allowed(
        "https://clinicaltrials.gov/study/NCT99004324",
        trial_ids,
    )
    assert not _source_url_allowed(
        "http://amciq.example.invalid/foundry/protocol",
        trial_ids,
    )
    assert not _source_url_allowed(
        "http://clinicaltrials.gov/study/NCT99004324",
        trial_ids,
    )


def test_fabric_generator_uses_canonical_lf_endings() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        output_dir = Path(temp_dir)
        generate(output_dir)

        for path in output_dir.glob("*.csv"):
            assert b"\r\n" not in path.read_bytes()
