"""Paper2Lab desktop entry point.

Keeping this tiny wrapper at the repository root makes both ``python main.py``
and the Windows PyInstaller build work without installing the package first.
"""

from paper2lab.main import main


if __name__ == "__main__":
    raise SystemExit(main())

