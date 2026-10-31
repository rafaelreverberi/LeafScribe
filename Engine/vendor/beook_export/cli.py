"""Command-line entry point for beook-export."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .database import connect, list_books
from .discovery import find_profiles, resolve_db_path
from .epub import write_epub
from .extractor import BookContent, extract_book
from .inspect import inspect_epub
from .paths import DEFAULT_OUTPUT_ROOT, resolve_book_output_path


def cmd_list(args: argparse.Namespace) -> int:
    profiles = find_profiles(args.beook_root)
    found_any = False
    for profile in profiles:
        conn = connect(profile.db_path)
        try:
            books = list_books(conn, profile.profile_id)
        finally:
            conn.close()
        if not books:
            continue
        found_any = True
        print(f"Profile {profile.profile_id} ({profile.db_path}):")
        for book in books:
            print(f"  {book.reference} | {book.title} ({book.chapter_count} chapters)")
    if not found_any:
        print("No books found in any profile.", file=sys.stderr)
        return 1
    return 0


def _format_book_metadata(book: BookContent) -> list[str]:
    lines: list[str] = []
    if book.authors:
        lines.append(f"Authors: {', '.join(book.authors)}")
    if book.subtitle:
        lines.append(f"Subtitle: {book.subtitle}")
    if book.publisher:
        lines.append(f"Publisher: {book.publisher}")
    if book.isbn:
        lines.append(f"ISBN: {book.isbn}")
    if book.edition:
        lines.append(f"Edition: {book.edition}")
    if book.publish_date:
        lines.append(f"Published: {book.publish_date}")
    if book.series:
        lines.append(f"Series: {book.series}")
    if book.cover_image_path:
        lines.append(f"Cover: {book.cover_image_path.split('/', 1)[-1]}")
    return lines


def cmd_export(args: argparse.Namespace) -> int:
    db_path = resolve_db_path(args.profile, args.beook_root)
    conn = connect(db_path)
    try:
        book = extract_book(conn, args.reference, args.beook_root)
    finally:
        conn.close()

    output_path = resolve_book_output_path(
        book.reference,
        book.title,
        output_root=args.output_root,
        explicit=Path(args.output) if args.output else None,
    )
    size = write_epub(book, str(output_path))
    print(f"Exported: {output_path}")
    print(f"Folder: {output_path.parent}")
    print(f"Reference: {book.reference}")
    print(f"Title: {book.title}")
    print(f"Spine pages: {len(book.spine_filenames)}")
    print(f"Files: {len(book.files)}")
    print(f"Size: {size:,} bytes")
    print(f"Language: {book.language}")
    for line in _format_book_metadata(book):
        print(line)
    return 0


def cmd_export_all(args: argparse.Namespace) -> int:
    profile_id = args.profile or "1"
    db_path = resolve_db_path(profile_id, args.beook_root)
    output_root = Path(args.output_root).expanduser()
    output_root.mkdir(parents=True, exist_ok=True)

    conn = connect(db_path)
    try:
        books = list_books(conn, profile_id)
    finally:
        conn.close()

    failures = 0
    total = len(books)
    for index, book in enumerate(books, start=1):
        output_path = resolve_book_output_path(
            book.reference,
            book.title,
            output_root=output_root,
        )
        conn = connect(db_path)
        try:
            content = extract_book(conn, book.reference, args.beook_root)
        except (ValueError, OSError) as exc:
            print(f"[{index}/{total}] SKIP {book.reference}: {exc}", file=sys.stderr)
            failures += 1
            continue
        finally:
            conn.close()
        size = write_epub(content, str(output_path))
        cover_flag = "cover" if content.cover_image_path else "title-page"
        print(
            f"[{index}/{total}] Exported: {output_path} | {book.reference} | "
            f"{content.title} | spine={len(content.spine_filenames)} | "
            f"files={len(content.files)} | {size:,}B | lang={content.language}"
            + (f" | isbn={content.isbn}" if content.isbn else "")
            + (f" | authors={len(content.authors)}" if content.authors else "")
            + (f" | publisher={content.publisher}" if content.publisher else "")
            + f" | {cover_flag}"
        )

    if failures:
        print(f"Completed with {failures} failure(s).", file=sys.stderr)
        return 1
    print(f"Exported {total} book(s) under {output_root}")
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    ok, report = inspect_epub(Path(args.epub), reference=args.reference or "")
    print(report)
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="beook-export",
        description="Export eBooks from the Beook macOS app to EPUB.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--beook-root", type=Path, default=None)
    parser.add_argument("--profile", default=None)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="List installed books").set_defaults(func=cmd_list)

    export_p = sub.add_parser("export", help="Export one book to EPUB")
    export_p.add_argument("reference", help="Book reference / ISBN")
    export_p.add_argument(
        "-o",
        "--output",
        default=None,
        help="Exact output .epub path (default: output/<reference-title>/book.epub)",
    )
    export_p.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help=f"Root folder for per-book directories (default: {DEFAULT_OUTPUT_ROOT})",
    )
    export_p.set_defaults(func=cmd_export)

    export_all_p = sub.add_parser("export-all", help="Export every installed book")
    export_all_p.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help=f"Root folder; each book gets its own subfolder (default: {DEFAULT_OUTPUT_ROOT})",
    )
    export_all_p.set_defaults(func=cmd_export_all)

    inspect_p = sub.add_parser("inspect", help="Inspect an exported EPUB")
    inspect_p.add_argument("epub", help="Path to an .epub file")
    inspect_p.add_argument("--reference", default="", help="Optional book reference label")
    inspect_p.set_defaults(func=cmd_inspect)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
