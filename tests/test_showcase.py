"""The portfolio example must demonstrate the real CLI and fail on broken guarantees."""

import json
import subprocess
import sys
from pathlib import Path


def test_showcase_runs_from_an_unrelated_directory(tmp_path):
    script = Path(__file__).resolve().parents[1] / "examples" / "showcase.py"
    result = subprocess.run(
        [sys.executable, str(script), "--json"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(result.stdout)
    assert evidence["recovery"] == evidence["repair"] == "COMPLETE"
    assert evidence["corrupted_status"] == "VERIFICATION_FAILED"
    assert evidence["corruption_detected"] is True
    assert evidence["unapproved_execution_exit"] == evidence["wrong_approval_exit"] == 6
    assert evidence["populated_rows_before_resume"] == 2
    assert evidence["checkpoints"] == 4
    assert evidence["migrated_rows"] + evidence["outside_scope_rows_untouched"] == 8
    assert evidence["original_source_preserved"] is True
    assert evidence["required_checks_passed"] > 0
    assert evidence["audit_events"] > evidence["checkpoints"]
