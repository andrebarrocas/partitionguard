"""Validate built artifacts, including the metadata rejected by the CI validator."""

import configparser
import subprocess
import sys
import tarfile
import zipfile
from email.parser import BytesParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def distributions(tmp_path_factory):
    destination = tmp_path_factory.mktemp("distributions")
    result = subprocess.run(
        [sys.executable, "-m", "build", "--no-isolation", "--outdir", str(destination)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return destination


@pytest.mark.parametrize("pattern", ["*.whl", "*.tar.gz"])
def test_built_distribution_passes_strict_publication_validation(distributions, pattern):
    (artifact,) = distributions.glob(pattern)
    result = subprocess.run(
        [sys.executable, "-m", "twine", "check", "--strict", str(artifact)],
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_wheel_contains_executable_entry_point_license_and_tested_engine(distributions):
    (wheel,) = distributions.glob("*.whl")
    with zipfile.ZipFile(wheel) as archive:
        (metadata_path,) = (name for name in archive.namelist() if name.endswith("/METADATA"))
        metadata = BytesParser().parsebytes(archive.read(metadata_path))
        assert metadata["Name"] == "partitionguard"
        assert metadata["License-Expression"] == "Apache-2.0"
        license_path = metadata_path.removesuffix("METADATA") + "licenses/LICENSE"
        assert archive.read(license_path) == (ROOT / "LICENSE").read_bytes()
        entry_points = configparser.ConfigParser()
        entry_points.read_string(
            archive.read(metadata_path.removesuffix("METADATA") + "entry_points.txt").decode()
        )
        assert entry_points["console_scripts"]["partitionguard"] == "partitionguard.cli:app"
        for name in ("engine.py", "ledger.py", "verification.py"):
            relative = "partitionguard/" + name
            assert archive.read(relative) == (ROOT / "src" / relative).read_bytes()


def test_source_distribution_includes_reproduction_files_without_generated_data(distributions):
    (source,) = distributions.glob("*.tar.gz")
    with tarfile.open(source) as archive:
        paths = [Path(member.name) for member in archive.getmembers()]
    files = {str(Path(*path.parts[1:])) for path in paths}
    assert {
        "README.md",
        "LICENSE",
        "tests/test_distribution.py",
        "examples/run_tutorials.py",
        ".github/workflows/ci.yml",
    } <= files
    for path in paths:
        assert not {"site", "work", ".git", "__pycache__", ".venv"}.intersection(path.parts)
        assert path.suffix not in {".duckdb", ".sqlite", ".pyc"}
