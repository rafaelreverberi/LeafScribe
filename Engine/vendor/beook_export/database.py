"""SQLite access for Beook Core Data tables."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import unescape
from pathlib import Path

# Core Data stores timestamps as seconds since 2001-01-01 UTC.
_CORE_DATA_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)
_TAG_RE = re.compile(r"<[^>]+>")
_PARAGRAPH_BREAK_RE = re.compile(r"(?i)</\s*(?:p|div|br|li)\s*/?>")
_AUTHOR_SPLIT_RE = re.compile(r"[,;\n]| und | et | and ")


@dataclass(frozen=True)
class Book:
    reference: str
    title: str
    chapter_count: int
    profile_id: str


@dataclass(frozen=True)
class BookMetadata:
    """Optional bibliographic metadata for a book, all best-effort."""

    authors: tuple[str, ...] = ()
    subtitle: str | None = None
    isbn: str | None = None
    publish_date: str | None = None
    edition: str | None = None
    description: str | None = None
    series: str | None = None
    publisher: str | None = None


@dataclass(frozen=True)
class Issue:
    pk: int
    issue_id: str
    title: str
    reference: str
    order: int


@dataclass(frozen=True)
class CourseCoverRef:
    filename: str
    file_sync_id: str | None


@dataclass(frozen=True)
class Resource:
    pk: int
    identifier: str
    media_type: str
    issue_pk: int
    order: int
    data: bytes | None
    plain_data: bytes | None


@dataclass(frozen=True)
class Note:
    title: str | None
    text: str | None
    type: str | None
    color: str | None
    date: str | None
    issue_id: str | None
    issue_title: str | None
    topic_access: str | None


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _strip_html_text(raw: object) -> str:
    if not raw:
        return ""
    text = str(raw)
    # Treat line breaks and block ends as separators before stripping tags.
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = _PARAGRAPH_BREAK_RE.sub("\n", text)
    text = _TAG_RE.sub("", text)
    text = unescape(text)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def _coredata_to_iso(value: object) -> str | None:
    if value in (None, "", 0):
        return None
    try:
        moment = _CORE_DATA_EPOCH + timedelta(seconds=float(value))
    except (TypeError, ValueError, OverflowError):
        return None
    return moment.strftime("%Y-%m-%d")


def _normalize_isbn(value: object) -> str | None:
    if not value:
        return None
    digits = re.sub(r"[^0-9Xx]", "", str(value))
    if len(digits) in (10, 13):
        return digits.upper()
    return None


def _looks_like_isbn(value: object) -> bool:
    if not value:
        return False
    digits = re.sub(r"[^0-9Xx]", "", str(value))
    return len(digits) == 13 and digits.startswith(("978", "979"))


def _parse_authors(raw: object) -> tuple[str, ...]:
    text = _strip_html_text(raw).replace("\n", ", ")
    authors: list[str] = []
    seen: set[str] = set()
    for part in _AUTHOR_SPLIT_RE.split(text):
        name = part.strip(" \t.-")
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        authors.append(name)
    return tuple(authors)


def resolve_course_product_reference(
    conn: sqlite3.Connection,
    reference: str,
) -> str | None:
    """Map an issue/book reference to its ZILPCOURSEPRODUCT.ZCOURSEREFERENCE.

    ISBN-style books use the same reference for issues and course products.
    Multi-issue series (e.g. ``RE01-HEP-RE-5DE``) store course metadata under
    the parent product (``HEP-RE-5DE``), so exact match alone is not enough.
    """
    row = conn.execute(
        """
        SELECT ZCOURSEREFERENCE AS course_reference
        FROM ZILPCOURSEPRODUCT
        WHERE ZCOURSEREFERENCE = ?
        ORDER BY Z_PK
        LIMIT 1
        """,
        (reference,),
    ).fetchone()
    if row and row["course_reference"]:
        return str(row["course_reference"])

    row = conn.execute(
        """
        SELECT cp.ZCOURSEREFERENCE AS course_reference
        FROM ZILPISSUEDEF i
        JOIN ZILPISSUEPRODUCT ip ON i.ZISSUEPRODUCT = ip.Z_PK
        JOIN ZILPCOURSEPRODUCT cp ON ip.ZCOURSEPRODUCT = cp.Z_PK
        WHERE i.ZREFERENCE = ?
          AND cp.ZCOURSEREFERENCE IS NOT NULL
          AND cp.ZCOURSEREFERENCE != ''
        ORDER BY i.ZORDER, cp.Z_PK
        LIMIT 1
        """,
        (reference,),
    ).fetchone()
    if row and row["course_reference"]:
        return str(row["course_reference"])

    row = conn.execute(
        """
        SELECT cp.ZCOURSEREFERENCE AS course_reference
        FROM ZILPISSUEDEF i
        JOIN ZILPCOURSEDEF cd ON i.ZCOURSE = cd.Z_PK
        JOIN ZILPCOURSEPRODUCT cp ON cd.ZILPCOURSEPRODUCT = cp.Z_PK
        WHERE i.ZREFERENCE = ?
          AND cp.ZCOURSEREFERENCE IS NOT NULL
          AND cp.ZCOURSEREFERENCE != ''
        ORDER BY i.ZORDER, cp.Z_PK
        LIMIT 1
        """,
        (reference,),
    ).fetchone()
    if row and row["course_reference"]:
        return str(row["course_reference"])

    # Last resort: RE01-HEP-RE-5DE → HEP-RE-5DE
    if "-" in reference:
        suffix = reference.split("-", 1)[1]
        row = conn.execute(
            """
            SELECT ZCOURSEREFERENCE AS course_reference
            FROM ZILPCOURSEPRODUCT
            WHERE ZCOURSEREFERENCE = ?
            ORDER BY Z_PK
            LIMIT 1
            """,
            (suffix,),
        ).fetchone()
        if row and row["course_reference"]:
            return str(row["course_reference"])

    return None


def get_book_metadata(conn: sqlite3.Connection, reference: str) -> BookMetadata:
    course_reference = resolve_course_product_reference(conn, reference)
    row = None
    if course_reference:
        row = conn.execute(
            """
            SELECT cp.ZAUTHORS AS authors,
                   cp.ZSUBTITLE AS subtitle,
                   cp.ZABSTRACTTEXT AS abstract,
                   cp.ZISBN AS isbn,
                   cp.ZPUBLISHDATE AS publish_date,
                   cp.ZEDITION AS edition,
                   s.ZTITLE AS series,
                   m.ZNAME AS publisher
            FROM ZILPCOURSEPRODUCT cp
            LEFT JOIN ZILPCOURSESERIES s ON cp.ZCOURSESERIES = s.Z_PK
            LEFT JOIN ZILPEPRODUCT ep ON cp.ZEPRODUCT = ep.Z_PK
            LEFT JOIN ZILPMANDANTCONFIG mc ON ep.ZMANDANTCONFIG = mc.Z_PK
            LEFT JOIN ZILPMANDANT m ON mc.ZMANDANT = m.Z_PK
            WHERE cp.ZCOURSEREFERENCE = ?
            ORDER BY cp.Z_PK
            LIMIT 1
            """,
            (course_reference,),
        ).fetchone()

    reference_isbn = _normalize_isbn(reference) if _looks_like_isbn(reference) else None
    if not row:
        return BookMetadata(isbn=reference_isbn)

    edition: str | None = None
    if row["edition"] not in (None, "", 0):
        try:
            edition = str(int(row["edition"]))
        except (TypeError, ValueError):
            edition = str(row["edition"]).strip() or None

    publisher = None
    if "publisher" in row.keys() and row["publisher"]:
        publisher = str(row["publisher"]).strip() or None

    return BookMetadata(
        authors=_parse_authors(row["authors"]),
        subtitle=_strip_html_text(row["subtitle"]) or None,
        isbn=_normalize_isbn(row["isbn"]) or reference_isbn,
        publish_date=_coredata_to_iso(row["publish_date"]),
        edition=edition,
        description=_strip_html_text(row["abstract"]) or None,
        series=(str(row["series"]).strip() or None) if row["series"] else None,
        publisher=publisher,
    )


def get_book_title_for_reference(conn: sqlite3.Connection, reference: str) -> str:
    """Return the best display title for a book/export reference.

    - Whole books that share a course product reference (ISBN products) use
      the product title ("Betriebswirtschaftslehre"), not the first chapter.
    - Multi-issue series chapters exported under their own issue reference
      (``RE01-HEP-RE-5DE``) keep the chapter title so each EPUB stays distinct.
    """
    course_reference = resolve_course_product_reference(conn, reference)
    content_issue = conn.execute(
        """
        SELECT ZTITLE AS title
        FROM ZILPISSUEDEF
        WHERE ZREFERENCE = ?
          AND ZORDER > 0
          AND ZISSUEID NOT LIKE '%00'
          AND ZISSUEID NOT LIKE '%98'
          AND ZISSUEID NOT LIKE '%99'
          AND lower(ZTITLE) NOT IN ('vorspann', 'impressum', 'anhang', 'glossar', 'préface', 'preface')
        ORDER BY ZORDER
        LIMIT 1
        """,
        (reference,),
    ).fetchone()
    any_issue = conn.execute(
        """
        SELECT ZTITLE AS title
        FROM ZILPISSUEDEF
        WHERE ZREFERENCE = ?
        ORDER BY ZORDER
        LIMIT 1
        """,
        (reference,),
    ).fetchone()
    content_title = (
        str(content_issue["title"]).strip()
        if content_issue and content_issue["title"]
        else None
    )
    any_title = str(any_issue["title"]).strip() if any_issue and any_issue["title"] else None

    product_title: str | None = None
    if course_reference:
        row = conn.execute(
            """
            SELECT ep.ZTITLE AS title
            FROM ZILPCOURSEPRODUCT cp
            JOIN ZILPEPRODUCT ep ON cp.ZEPRODUCT = ep.Z_PK
            WHERE cp.ZCOURSEREFERENCE = ?
            ORDER BY cp.Z_PK
            LIMIT 1
            """,
            (course_reference,),
        ).fetchone()
        if row and row["title"]:
            product_title = str(row["title"]).strip()

    # Commercial product / course keys (ISBN books, HEP-RE-5DE, …) → product title.
    is_product_key = (
        conn.execute(
            """
            SELECT 1 AS ok
            FROM ZILPCOURSEPRODUCT
            WHERE ZCOURSEREFERENCE = ?
            LIMIT 1
            """,
            (reference,),
        ).fetchone()
        is not None
    )
    if product_title and is_product_key:
        return product_title
    if content_title:
        return content_title
    if product_title:
        return product_title
    if any_title:
        return any_title
    return reference


def list_books(conn: sqlite3.Connection, profile_id: str) -> list[Book]:
    """List installed books, one entry per course product when possible.

    Beook often stores multi-volume series (Recht, Geografie, …) as many
    separate issue references (RE01-…, RE02-…). Those are one book to the user,
    so they are grouped under the parent course product reference.
    """
    rows = conn.execute(
        """
        SELECT i.ZREFERENCE AS issue_reference,
               COUNT(*) AS issue_count
        FROM ZILPISSUEDEF i
        WHERE i.ZREFERENCE IS NOT NULL AND i.ZREFERENCE != ''
        GROUP BY i.ZREFERENCE
        """
    ).fetchall()

    grouped: dict[str, int] = {}
    for row in rows:
        issue_reference = str(row["issue_reference"])
        book_reference = (
            resolve_course_product_reference(conn, issue_reference) or issue_reference
        )
        grouped[book_reference] = grouped.get(book_reference, 0) + int(row["issue_count"])

    books: list[Book] = []
    for reference, chapter_count in grouped.items():
        books.append(
            Book(
                reference=reference,
                title=get_book_title_for_reference(conn, reference),
                chapter_count=chapter_count,
                profile_id=profile_id,
            )
        )
    books.sort(key=lambda book: (book.title.lower(), book.reference))
    return books


def get_language_for_reference(conn: sqlite3.Connection, reference: str) -> str | None:
    row = conn.execute(
        """
        SELECT c.ZLANGUAGECODE AS language_code
        FROM ZILPISSUEDEF i
        JOIN ZILPCOURSEDEF c ON i.ZCOURSE = c.Z_PK
        WHERE i.ZREFERENCE = ? AND c.ZLANGUAGECODE IS NOT NULL AND c.ZLANGUAGECODE != ''
        LIMIT 1
        """,
        (reference,),
    ).fetchone()
    if row and row["language_code"]:
        return str(row["language_code"]).lower()[:2]
    return None


def get_course_cover_ref(conn: sqlite3.Connection, reference: str) -> CourseCoverRef | None:
    course_reference = resolve_course_product_reference(conn, reference)
    if not course_reference:
        return None
    row = conn.execute(
        """
        SELECT cp.ZCOVERFILENAMELOWRES AS cover_filename,
               d.ZIMAGESZIPFILEREFERENCE AS file_sync_id
        FROM ZILPCOURSEPRODUCT cp
        LEFT JOIN ZILPIMAGEZIPDESCRIPTOR d ON cp.ZIMAGEZIPDESCRIPTOR = d.Z_PK
        WHERE cp.ZCOURSEREFERENCE = ?
        LIMIT 1
        """,
        (course_reference,),
    ).fetchone()
    if not row or not row["cover_filename"]:
        return None
    return CourseCoverRef(
        filename=str(row["cover_filename"]),
        file_sync_id=str(row["file_sync_id"]) if row["file_sync_id"] else None,
    )


def _issues_from_rows(rows: list[sqlite3.Row]) -> list[Issue]:
    return [
        Issue(
            pk=row["pk"],
            issue_id=row["issue_id"],
            title=row["title"],
            reference=row["reference"],
            order=row["issue_order"],
        )
        for row in rows
    ]


def get_issues_for_course_product(
    conn: sqlite3.Connection,
    course_reference: str,
) -> list[Issue]:
    """All issues belonging to a course product, reading order."""
    rows = conn.execute(
        """
        SELECT i.Z_PK AS pk, i.ZISSUEID AS issue_id, i.ZTITLE AS title,
               i.ZREFERENCE AS reference, i.ZORDER AS issue_order
        FROM ZILPISSUEDEF i
        JOIN ZILPISSUEPRODUCT ip ON i.ZISSUEPRODUCT = ip.Z_PK
        JOIN ZILPCOURSEPRODUCT cp ON ip.ZCOURSEPRODUCT = cp.Z_PK
        WHERE cp.ZCOURSEREFERENCE = ?
        ORDER BY i.ZISSUEID, i.ZORDER, i.Z_PK
        """,
        (course_reference,),
    ).fetchall()
    return _issues_from_rows(rows)


def get_issues_for_reference(conn: sqlite3.Connection, reference: str) -> list[Issue]:
    """Issues to export for a list/export reference.

    Accepts either an issue reference (ISBN or RE01-HEP-RE-5DE) or a course
    product reference (HEP-RE-5DE). Multi-issue series are always expanded to
    the full course so one EPUB contains the whole book, not a single chapter.
    """
    course_reference = resolve_course_product_reference(conn, reference)
    if course_reference:
        course_issues = get_issues_for_course_product(conn, course_reference)
        if course_issues:
            return course_issues

    rows = conn.execute(
        """
        SELECT Z_PK AS pk, ZISSUEID AS issue_id, ZTITLE AS title,
               ZREFERENCE AS reference, ZORDER AS issue_order
        FROM ZILPISSUEDEF
        WHERE ZREFERENCE = ?
        ORDER BY ZORDER, Z_PK
        """,
        (reference,),
    ).fetchall()
    return _issues_from_rows(rows)


def get_spine_page_pks(conn: sqlite3.Connection, issue_pk: int) -> list[int]:
    rows = conn.execute(
        """
        SELECT t.ZRESOURCE AS resource_pk
        FROM ZILPTOPIC t
        JOIN ZILPRESOURCE r ON t.ZRESOURCE = r.Z_PK
        WHERE t.ZISSUE = ?
          AND t.ZRESOURCE IS NOT NULL
          AND r.ZMEDIATYPE IN ('application/xhtml+xml', 'text/html')
        ORDER BY t.ZORDER
        """,
        (issue_pk,),
    ).fetchall()
    pks = [row["resource_pk"] for row in rows]
    if pks:
        return pks

    rows = conn.execute(
        """
        SELECT Z_PK AS pk
        FROM ZILPRESOURCE
        WHERE ZISSUE = ? AND ZMEDIATYPE IN ('application/xhtml+xml', 'text/html')
        ORDER BY ZORDER, Z_PK
        """,
        (issue_pk,),
    ).fetchall()
    return [row["pk"] for row in rows]


def get_topic_access_map(conn: sqlite3.Connection, issue_pks: list[int]) -> dict[tuple[str, str], str]:
    if not issue_pks:
        return {}
    placeholders = ",".join("?" for _ in issue_pks)
    rows = conn.execute(
        f"""
        SELECT i.ZISSUEID AS issue_id, t.ZACCESSPATH AS access_path,
               r.ZIDENTIFIER AS identifier, r.ZMEDIATYPE AS media_type
        FROM ZILPTOPIC t
        JOIN ZILPISSUEDEF i ON t.ZISSUE = i.Z_PK
        JOIN ZILPRESOURCE r ON t.ZRESOURCE = r.Z_PK
        WHERE t.ZISSUE IN ({placeholders})
          AND t.ZRESOURCE IS NOT NULL
          AND t.ZACCESSPATH IS NOT NULL
          AND t.ZACCESSPATH != ''
        """,
        issue_pks,
    ).fetchall()
    from .naming import oebps_name

    return {
        (row["issue_id"], row["access_path"]): oebps_name(
            row["identifier"], row["media_type"] or ""
        )
        for row in rows
    }


def get_topic_pk_map(conn: sqlite3.Connection, issue_pks: list[int]) -> dict[int, str]:
    if not issue_pks:
        return {}
    placeholders = ",".join("?" for _ in issue_pks)
    rows = conn.execute(
        f"""
        SELECT t.Z_PK AS topic_pk, r.ZIDENTIFIER AS identifier, r.ZMEDIATYPE AS media_type
        FROM ZILPTOPIC t
        JOIN ZILPRESOURCE r ON t.ZRESOURCE = r.Z_PK
        WHERE t.ZISSUE IN ({placeholders}) AND t.ZRESOURCE IS NOT NULL
        """,
        issue_pks,
    ).fetchall()
    from .naming import oebps_name

    return {
        row["topic_pk"]: oebps_name(row["identifier"], row["media_type"] or "")
        for row in rows
    }


def get_resources_for_issues(conn: sqlite3.Connection, issue_pks: list[int]) -> list[Resource]:
    if not issue_pks:
        return []
    placeholders = ",".join("?" for _ in issue_pks)
    rows = conn.execute(
        f"""
        SELECT Z_PK AS pk, ZIDENTIFIER AS identifier, ZMEDIATYPE AS media_type,
               ZISSUE AS issue_pk, ZORDER AS resource_order, ZDATA AS data,
               ZPLAINDATA AS plain_data
        FROM ZILPRESOURCE
        WHERE ZISSUE IN ({placeholders})
          AND ZDATA IS NOT NULL
        ORDER BY ZISSUE, ZORDER, Z_PK
        """,
        issue_pks,
    ).fetchall()
    return [
        Resource(
            pk=row["pk"],
            identifier=row["identifier"],
            media_type=row["media_type"] or "application/octet-stream",
            issue_pk=row["issue_pk"],
            order=row["resource_order"] or 0,
            data=row["data"],
            plain_data=row["plain_data"],
        )
        for row in rows
    ]


def get_notes_for_issues(conn: sqlite3.Connection, issue_pks: list[int]) -> list[Note]:
    if not issue_pks:
        return []
    placeholders = ",".join("?" for _ in issue_pks)
    try:
        rows = conn.execute(
            f"""
            SELECT n.ZTITLE AS title, n.ZTEXT AS text, n.ZTYPE AS type,
                   n.ZCOLOR AS color, n.ZDATE AS date,
                   i.ZISSUEID AS issue_id, i.ZTITLE AS issue_title,
                   t.ZACCESSPATH AS topic_access
            FROM ZILPNOTE n
            JOIN ZILPTOPIC t ON n.ZTOPIC = t.Z_PK
            JOIN ZILPISSUEDEF i ON t.ZISSUE = i.Z_PK
            WHERE t.ZISSUE IN ({placeholders})
              AND IFNULL(n.ZNOTEDELETED, 0) = 0
              AND (n.ZTITLE IS NOT NULL OR n.ZTEXT IS NOT NULL)
            ORDER BY i.ZORDER, t.ZORDER, n.ZNODEINDEX, n.ZDATE
            """,
            issue_pks,
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [
        Note(
            title=(str(row["title"]).strip() or None) if row["title"] else None,
            text=(str(row["text"]).strip() or None) if row["text"] else None,
            type=(str(row["type"]).strip() or None) if row["type"] else None,
            color=(str(row["color"]).strip() or None) if row["color"] else None,
            date=_coredata_to_iso(row["date"]),
            issue_id=(str(row["issue_id"]).strip() or None) if row["issue_id"] else None,
            issue_title=(str(row["issue_title"]).strip() or None) if row["issue_title"] else None,
            topic_access=(str(row["topic_access"]).strip() or None) if row["topic_access"] else None,
        )
        for row in rows
    ]