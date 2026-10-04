"""Package the local Paper2Lab deliverables without touching legacy folders.

The development container cannot create a native Windows PE executable, so
this script packages the source, fictional demo, generated sample experiment,
and the separately-built Linux validation binary.  The file list is explicit
on purpose: the unrelated legacy ``app/`` and ``data/`` directories are never
copied into the Paper2Lab source archive.
"""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"
VERSION = "0.1.0"


def _require_file(path: Path, label: str | None = None) -> Path:
    """Return *path* or fail before creating a partial release archive.

    Earlier versions of this helper silently skipped missing files.  That is
    convenient for ad-hoc snapshots, but dangerous for a release command: a
    typo or an unbuilt wheel would still produce a zip and a checksum table
    that looked complete.  Packaging is an explicit release operation, so
    fail with the concrete path instead of emitting a partial artifact.
    """

    if not path.is_file():
        description = label or str(path)
        raise FileNotFoundError(f"required release file is missing: {description} ({path})")
    return path


def _require_directory(path: Path, label: str | None = None) -> Path:
    """Return *path* or fail when a required generated tree is absent."""

    if not path.is_dir():
        description = label or str(path)
        raise FileNotFoundError(f"required release directory is missing: {description} ({path})")
    return path


def _add_file(archive: zipfile.ZipFile, path: Path, arcname: str) -> None:
    # Callers validate required inputs up front; retaining this guard makes
    # the helper safe for optional report files below.
    if path.is_file():
        archive.write(path, arcname)


def _add_tree(archive: zipfile.ZipFile, directory: Path, prefix: str) -> None:
    if not directory.is_dir():
        return
    for path in sorted(directory.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            archive.write(path, f"{prefix}/{path.relative_to(directory).as_posix()}")


def _zip_source() -> Path:
    target = ARTIFACTS / f"Paper2Lab_source_v{VERSION}.zip"
    files = [
        "README.md", "pyproject.toml", "requirements.txt", "main.py",
        "run_paper2lab.cmd", "build_windows.ps1", "Paper2Lab.spec", ".gitignore",
    ]
    for relative in files:
        _require_file(ROOT / relative, f"source/{relative}")
    for directory in ("paper2lab", "sample", "tests", "scripts", "docs"):
        _require_directory(ROOT / directory, f"source/{directory}/")
    _require_directory(ROOT / ".github", "source/.github/")
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative in files:
            _add_file(archive, ROOT / relative, f"Paper2Lab/{relative}")
        for directory in ("paper2lab", "sample", "tests", "scripts", "docs"):
            _add_tree(archive, ROOT / directory, f"Paper2Lab/{directory}")
        _add_tree(archive, ROOT / ".github", "Paper2Lab/.github")
        for relative in (
            "BUILD_STATUS.md", "DELIVERABLES.md", "frozen_smoke_report.md",
            "paper2lab_test_report.json", "paper2lab_test_report.md", "paper2lab_ui_preview.png",
        ):
            _add_file(archive, ARTIFACTS / relative, f"Paper2Lab/artifacts/{relative}")
    return target


def _zip_demo() -> tuple[Path, Path]:
    demo = ARTIFACTS / "paper2lab_demo"
    _require_directory(demo, "artifacts/paper2lab_demo/")
    _require_file(ROOT / "sample" / "sample_paper.md", "sample/sample_paper.md")
    target = ARTIFACTS / f"Paper2Lab_demo_bundle_v{VERSION}.zip"
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        _add_tree(archive, demo, "Paper2Lab_demo/paper2lab_demo")
        _add_file(archive, ROOT / "sample" / "sample_paper.md", "Paper2Lab_demo/sample_paper.md")

    sample_target = ARTIFACTS / f"Paper2Lab_sample_experiment_v{VERSION}.zip"
    for relative in (
        "demo_summary.json", "sample_experiment", "reports",
        "sample_project.paper2lab.json", "service_workspace/paper2lab.project.json",
    ):
        source = demo / relative
        if source.is_dir():
            _require_directory(source, f"artifacts/paper2lab_demo/{relative}/")
        else:
            _require_file(source, f"artifacts/paper2lab_demo/{relative}")
    with zipfile.ZipFile(sample_target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative in (
            "demo_summary.json", "sample_experiment", "reports",
            "sample_project.paper2lab.json", "service_workspace/paper2lab.project.json",
        ):
            source = demo / relative
            if source.is_dir():
                _add_tree(archive, source, f"Paper2Lab_sample_experiment/{relative}")
            else:
                _add_file(archive, source, f"Paper2Lab_sample_experiment/{relative}")
        _add_file(archive, ROOT / "sample" / "sample_paper.md", "Paper2Lab_sample_experiment/sample_paper.md")
    return target, sample_target


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    linux_tar = ARTIFACTS / f"Paper2Lab_linux_x86_64_v{VERSION}.tar.gz"
    wheel = ARTIFACTS / f"Paper2Lab-{VERSION}-py3-none-any.whl"
    # Validate every required *input* before opening any output archive.  A
    # missing binary or wheel must not leave behind a misleading source/demo
    # zip from an otherwise failed release attempt.
    _require_directory(ARTIFACTS / "paper2lab_demo", "artifacts/paper2lab_demo/")
    _require_file(ROOT / "sample" / "sample_paper.md", "sample/sample_paper.md")
    _require_file(linux_tar, f"artifacts/{linux_tar.name}")
    _require_file(wheel, f"artifacts/{wheel.name}")
    source = _zip_source()
    demo, sample = _zip_demo()
    lines = [
        "# Paper2Lab release artifacts",
        "",
        "| Artifact | SHA-256 | Bytes |",
        "|---|---|---:|",
    ]
    for path in (source, demo, sample, linux_tar, wheel):
        if path.is_file():
            lines.append(f"| `{path.name}` | `{_sha256(path)}` | {path.stat().st_size} |")
    (ARTIFACTS / "RELEASE_CHECKSUMS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(str(path) for path in (source, demo, sample)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
