from __future__ import annotations

import shutil
from pathlib import Path

import pytest


@pytest.fixture
def fixture_root() -> Path:
    return Path(__file__).parent / "fixtures" / "data"


@pytest.fixture
def temp_data_root(tmp_path: Path, fixture_root: Path) -> Path:
    target = tmp_path / "data"
    shutil.copytree(fixture_root, target)
    return target
