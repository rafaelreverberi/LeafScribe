"""Filename helpers for Beook OEBPS identifiers."""

from __future__ import annotations

_MEDIA_EXTENSIONS = {
    "application/xhtml+xml": ".xhtml",
    "text/html": ".html",
    "text/css": ".css",
    "text/javascript": ".js",
    "application/javascript": ".js",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/svg+xml": ".svg",
    "font/opentype": ".otf",
    "application/font-woff": ".woff",
    "font/woff": ".woff",
    "font/woff2": ".woff2",
    "font/ttf": ".ttf",
    "text/plain": ".txt",
    "text/xml": ".xml",
    "application/xml": ".xml",
    "video/mp4": ".mp4",
}


def oebps_name(identifier: str, media_type: str) -> str:
    name = identifier
    if name.startswith("OEBPS_"):
        name = name[6:]
    name = name.replace("\\", "/").strip()
    basename = name.rsplit("/", 1)[-1]
    if "." not in basename:
        ext = _MEDIA_EXTENSIONS.get(media_type, "")
        if ext:
            name = f"{name}{ext}"
    return name


def media_type_for_filename(filename: str, fallback: str = "application/octet-stream") -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return {
        "xhtml": "application/xhtml+xml",
        "html": "text/html",
        "css": "text/css",
        "js": "text/javascript",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "svg": "image/svg+xml",
        "otf": "font/otf",
        "woff": "font/woff",
        "woff2": "font/woff2",
        "ttf": "font/ttf",
        "xml": "application/xml",
        "mp4": "video/mp4",
        "txt": "text/plain",
    }.get(ext, fallback)