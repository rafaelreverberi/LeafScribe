"""Detect EPUB language and provide localized reader UI strings."""

from __future__ import annotations

import re

HTML_LANG_RE = re.compile(
    r"<html[^>]*\s(?:xml:)?lang=[\"']([a-zA-Z]{2}(?:-[a-zA-Z]{2})?)[\"']",
    re.IGNORECASE,
)

READING_MEDIA_TYPES = frozenset({"application/xhtml+xml", "text/html"})

# Minimal UI strings used in exporter-generated pages (cover, nav, annotations).
_UI: dict[str, dict[str, str]] = {
    "de": {
        "cover": "Umschlag",
        "toc": "Inhaltsverzeichnis",
        "start": "Anfang",
        "guide": "Leitfaden",
        "annotations": "Annotationen",
        "highlight": "Markierung",
        "note": "Notiz",
        "bookmark": "Lesezeichen",
        "annotation": "Annotation",
        "topic": "Thema",
        "edition": "Auflage",
        "and_more": "und {n} weitere",
        "navigation": "Navigation",
    },
    "fr": {
        "cover": "Couverture",
        "toc": "Table des matières",
        "start": "Début",
        "guide": "Guide",
        "annotations": "Annotations",
        "highlight": "Surlignage",
        "note": "Note",
        "bookmark": "Signet",
        "annotation": "Annotation",
        "topic": "Sujet",
        "edition": "Édition",
        "and_more": "et {n} autres",
        "navigation": "Navigation",
    },
    "en": {
        "cover": "Cover",
        "toc": "Table of Contents",
        "start": "Start",
        "guide": "Guide",
        "annotations": "Annotations",
        "highlight": "Highlight",
        "note": "Note",
        "bookmark": "Bookmark",
        "annotation": "Annotation",
        "topic": "Topic",
        "edition": "Edition",
        "and_more": "and {n} more",
        "navigation": "Navigation",
    },
}


def detect_language(
    files: dict[str, bytes],
    media_types: dict[str, str],
    fallback: str = "de",
) -> str:
    for path in sorted(files):
        if media_types.get(path) not in READING_MEDIA_TYPES:
            continue
        sample = files[path][:4000].decode("utf-8", errors="replace")
        match = HTML_LANG_RE.search(sample)
        if match:
            return match.group(1).lower().split("-")[0]
    return fallback


def normalize_language(language: str | None, fallback: str = "en") -> str:
    if not language:
        return fallback
    return language.lower().split("-")[0]


def ui_string(language: str | None, key: str) -> str:
    lang = normalize_language(language, "en")
    table = _UI.get(lang) or _UI["en"]
    return table.get(key) or _UI["en"].get(key, key)


def format_author_list(
    authors: tuple[str, ...] | list[str],
    language: str | None,
    *,
    limit: int = 6,
) -> str:
    names = [name for name in authors if name]
    if not names:
        return ""
    if len(names) <= limit:
        return ", ".join(names)
    visible = ", ".join(names[:limit])
    more = ui_string(language, "and_more").format(n=len(names) - limit)
    return f"{visible}, {more}"
