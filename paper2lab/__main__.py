"""Allow ``python -m paper2lab`` to launch the same entry point as the exe."""

from .main import main


if __name__ == "__main__":
    raise SystemExit(main())

