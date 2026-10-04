# Paper2Lab deliverables

- `Paper2Lab_source_v0.1.0.zip` — source, tests, docs, sample paper, Windows build files.
- `Paper2Lab_demo_bundle_v0.1.0.zip` — complete fictional-paper demo and round-trip project.
- `Paper2Lab_sample_experiment_v0.1.0.zip` — generated experiment skeleton, mock result, and reports.
- `Paper2Lab_linux_x86_64_v0.1.0.tar.gz` — Linux PyInstaller validation build.
- `Paper2Lab-0.1.0-py3-none-any.whl` — installable Python package.
- `paper2lab_test_report.md/.json` — final acceptance results.
- `paper2lab_ui_preview.png` — three-column workbench screenshot.
- `RELEASE_CHECKSUMS.md` — SHA-256 and byte-size manifest for the release files.

`scripts/package_release.py` recreates the source/demo/sample archives with an
explicit file list and does not copy the unrelated legacy `app/` or `data/`
directories. It validates the generated demo, Linux validation tarball, and
wheel before writing any release archive, so a missing build input fails fast
instead of producing a partial package.

The native Windows `Paper2Lab.exe` is produced by `build_windows.ps1` or the
included Windows Actions workflow. The current Linux build environment cannot
emit a valid Windows PE binary.
