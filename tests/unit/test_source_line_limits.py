from pathlib import Path


def test_source_files_stay_within_workspace_line_limit() -> None:
    source_root = Path(__file__).parents[2] / "src"
    oversized = {
        str(path.relative_to(source_root)): len(path.read_text(encoding="utf-8").splitlines())
        for path in source_root.rglob("*.py")
        if len(path.read_text(encoding="utf-8").splitlines()) > 300
    }
    assert oversized == {}
