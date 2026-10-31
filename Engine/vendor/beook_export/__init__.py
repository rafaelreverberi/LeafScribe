"""Extract eBooks from the Beook macOS app into standard EPUB format."""

try:
    from importlib.metadata import PackageNotFoundError, version as _package_version

    try:
        __version__ = _package_version("beook-export")
    except PackageNotFoundError:
        __version__ = "0.1.27"
except ImportError:  # pragma: no cover - Python < 3.8
    __version__ = "0.1.27"
