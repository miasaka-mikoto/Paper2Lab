# Frozen build smoke report

Status: **PASS**

Validated: 2026-10-04

The Linux validation binary is 26,604,512 bytes. SHA-256:
`3242aa580a7a99b83609e70d6681f4c8e1ddab3f3a58f3265251aedc219fc0ec`.

The Linux PyInstaller validation binary was rebuilt from the hardened source
and passed both command-line smoke paths:

- `Paper2Lab --self-test`
- `Paper2Lab --service-self-test`

The source acceptance suite also covers the process runner lifecycle,
editable metadata/editor records, result provenance graph, and Markdown/HTML
report rendering (29 tests passed).

This is a Linux ELF validation artifact, not a Windows PE executable. A native
Windows build is reproducible with `build_windows.ps1` or the included GitHub
Actions workflow.
