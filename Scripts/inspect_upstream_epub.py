#!/usr/bin/env python3
"""CLI wrapper for beook_export.inspect."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "Engine/vendor"))

from beook_export.inspect import inspect_epub  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect an EPUB for completeness")
    parser.add_argument("epub", type=Path)
    parser.add_argument("--reference", default="")
    parser.add_argument("--spine", type=int, default=None)
    parser.add_argument("-o", "--output", type=Path, default=None)
    args = parser.parse_args()

    ok, report = inspect_epub(args.epub, args.reference, args.spine)
    if args.output:
        args.output.write_text(report + "\n")
    print(report)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())