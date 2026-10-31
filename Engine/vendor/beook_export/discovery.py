"""Locate Beook profile databases on macOS."""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .database import get_course_cover_ref


DEFAULT_BEOOK_ROOT = Path.home() / "Library/Application Support/ionesoft/beook"
SQLITE_NAME = "beook_book_v6.sqlite"


@dataclass(frozen=True)
class Profile:
    profile_id: str
    db_path: Path


def find_beook_root(custom_root: str | Path | None = None) -> Path:
    root = Path(custom_root).expanduser() if custom_root else DEFAULT_BEOOK_ROOT
    if not root.is_dir():
        raise FileNotFoundError(f"Beook data directory not found: {root}")
    return root


def find_profiles(beook_root: Path | None = None) -> list[Profile]:
    root = beook_root or find_beook_root()
    profiles_dir = root / "release/profiles"
    if not profiles_dir.is_dir():
        raise FileNotFoundError(f"Beook profiles directory not found: {profiles_dir}")

    profiles: list[Profile] = []
    for entry in sorted(profiles_dir.iterdir(), key=lambda p: p.name):
        if not entry.is_dir() or not entry.name.isdigit():
            continue
        db_path = entry / "data" / SQLITE_NAME
        if db_path.is_file():
            profiles.append(Profile(profile_id=entry.name, db_path=db_path))
    if not profiles:
        raise FileNotFoundError(f"No Beook sqlite databases found under {profiles_dir}")
    return profiles


def resolve_course_cover_file(
    conn: sqlite3.Connection,
    reference: str,
    beook_root: Path | None = None,
) -> Path | None:
    cover_ref = get_course_cover_ref(conn, reference)
    if not cover_ref or not cover_ref.file_sync_id:
        return None

    root = beook_root or find_beook_root()
    sync_dir = root / "release/assetStage/prod/fileSynch" / cover_ref.file_sync_id
    if not sync_dir.is_dir():
        return None

    candidates = [cover_ref.filename]
    if "@2x" in cover_ref.filename:
        candidates.append(cover_ref.filename.replace("@2x", ""))
    else:
        stem, dot, ext = cover_ref.filename.rpartition(".")
        if dot:
            candidates.append(f"{stem}@2x.{ext}")

    for name in candidates:
        path = sync_dir / name
        if path.is_file():
            return path
    return None


def resolve_db_path(profile: str | None = None, beook_root: Path | None = None) -> Path:
    profiles = find_profiles(beook_root)
    if profile is not None:
        for p in profiles:
            if p.profile_id == profile:
                return p.db_path
        ids = ", ".join(p.profile_id for p in profiles)
        raise FileNotFoundError(f"Profile '{profile}' not found. Available: {ids}")

    for p in profiles:
        if os.path.getsize(p.db_path) > 100_000:
            return p.db_path
    return profiles[0].db_path