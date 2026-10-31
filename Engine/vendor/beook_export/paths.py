"""Output path helpers — one folder per exported book."""

from __future__ import annotations

import re
from pathlib import Path

DEFAULT_OUTPUT_ROOT = Path("output")
_UMLAUT_MAP = str.maketrans(
    {
        "ä": "ae",
        "ö": "oe",
        "ü": "ue",
        "Ä": "ae",
        "Ö": "oe",
        "Ü": "ue",
        "ß": "ss",
    }
)


def reference_slug(reference: str) -> str:
    return reference.replace("/", "-").replace(":", "-")


def title_slug(title: str, max_len: int = 48) -> str:
    text = title.strip().translate(_UMLAUT_MAP)
    text = re.sub(r"^\d+\s+", "", text)
    text = re.sub(r"[^\w\s-]", "", text, flags=re.ASCII)
    text = re.sub(r"[\s_]+", "-", text).strip("-").lower()
    return text[:max_len] or "book"


def book_folder_name(reference: str, title: str) -> str:
    return f"{reference_slug(reference)}-{title_slug(title)}"


def book_output_dir(
    reference: str,
    title: str,
    output_root: Path | None = None,
) -> Path:
    root = (output_root or DEFAULT_OUTPUT_ROOT).expanduser()
    return root / book_folder_name(reference, title)


def resolve_book_output_path(
    reference: str,
    title: str,
    *,
    output_root: Path | None = None,
    explicit: Path | None = None,
) -> Path:
    if explicit is not None:
        path = explicit.expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        return path
    folder = book_output_dir(reference, title, output_root)
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{title_slug(title)}.epub"
