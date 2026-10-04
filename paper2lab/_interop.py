"""Small, dependency-free helpers shared by Paper2Lab experiment modules.

The application models are intentionally allowed to evolve independently from
the experiment engine.  These helpers therefore accept mappings, dataclasses,
Pydantic-like objects, and ordinary objects with attributes.  No model class is
imported here, which keeps the modules useful to the generated experiment
projects as well as to the desktop application.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping


def to_plain(value: Any) -> Any:
    """Convert a model-ish value to JSON-friendly Python values.

    ``model_dump`` and ``dict`` are intentionally tried without importing
    Pydantic.  The function is conservative: unknown objects become their
    public attributes rather than being stringified whenever possible.
    """

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(k): to_plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [to_plain(v) for v in value]
    if is_dataclass(value):
        return {str(k): to_plain(v) for k, v in asdict(value).items()}
    for method_name in ("model_dump", "to_dict", "dict"):
        method = getattr(value, method_name, None)
        if callable(method):
            try:
                return to_plain(method())
            except TypeError:
                # Some model APIs require optional keyword arguments.  Falling
                # through to public attributes is more useful than failing.
                pass
    attrs = getattr(value, "__dict__", None)
    if isinstance(attrs, dict):
        return {str(k): to_plain(v) for k, v in attrs.items() if not str(k).startswith("_")}
    return value


def get_field(value: Any, *names: str, default: Any = None) -> Any:
    """Read the first present field from a mapping or object.

    Field aliases make the engine compatible with both snake_case internal
    models and the title-cased field names used by imported paper metadata.
    ``None`` is considered a present value, so callers can distinguish an
    explicit null from an absent field when needed by checking a sentinel.
    """

    if value is None:
        return default
    mapping = value if isinstance(value, Mapping) else None
    for name in names:
        if mapping is not None and name in mapping:
            return mapping[name]
        if hasattr(value, name):
            return getattr(value, name)
    return default


def as_text(value: Any, default: str = "") -> str:
    """Return a stable human-readable string for report and README output."""

    if value is None:
        return default
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple, set, frozenset)):
        return ", ".join(as_text(v) for v in value)
    return str(value)


def ensure_list(value: Any) -> list[Any]:
    """Normalize optional singular/list model fields to a list."""

    if value is None:
        return []
    if isinstance(value, (list, tuple, set, frozenset)):
        return list(value)
    return [value]


def safe_filename(value: Any, default: str = "experiment") -> str:
    """Make a user/model supplied name safe for a generated directory."""

    text = as_text(value, default).strip()
    chars = []
    for char in text:
        if char.isalnum() or char in "._-":
            chars.append(char)
        elif char.isspace():
            chars.append("_")
        else:
            chars.append("_")
    cleaned = "".join(chars).strip("._") or default
    return cleaned[:80]


def json_safe(value: Any) -> Any:
    """Alias used by persistence code; kept explicit for readability."""

    return to_plain(value)

