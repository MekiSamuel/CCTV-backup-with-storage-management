"""
Retention / storage manager for the CCTV backup folder.

Two rules, applied in this order:
  1. Age   : delete any clip older than `retention_days`.
  2. Size  : if the folder is still over `max_size_gb`, delete the
             OLDEST clips until it is back under the limit.

Can be imported (enforce_retention) or run directly:
    python retention.py            # uses config.ini next to this file
    python retention.py --dry-run  # show what WOULD be deleted, delete nothing
"""

from __future__ import annotations

import argparse
import configparser
import logging
import os
import time
from pathlib import Path

# Files we consider "recordings". Anything else in the folder is ignored.
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".ts"}

# Never touch a clip modified within this many seconds - it is very likely
# the segment ffmpeg is writing right now.
ACTIVE_FILE_GRACE_SECONDS = 90

log = logging.getLogger("retention")


def _iter_video_files(backup_dir: Path):
    """Yield (path, size_bytes, mtime) for every recording under backup_dir."""
    for path in backup_dir.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in VIDEO_EXTENSIONS:
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        yield path, stat.st_size, stat.st_mtime


def _safe_delete(path: Path, dry_run: bool) -> int:
    """Delete a file. Return bytes freed (0 on failure or dry-run)."""
    try:
        size = path.stat().st_size
    except OSError:
        return 0
    if dry_run:
        log.info("[dry-run] would delete %s (%.1f MB)", path, size / 1e6)
        return size
    try:
        path.unlink()
        log.info("deleted %s (%.1f MB)", path, size / 1e6)
        return size
    except OSError as exc:
        log.warning("could not delete %s: %s", path, exc)
        return 0


def enforce_retention(
    backup_dir: str | os.PathLike,
    max_size_gb: float,
    retention_days: int,
    dry_run: bool = False,
) -> dict:
    """
    Apply age + size retention rules to backup_dir.
    Returns a small summary dict for logging/monitoring.
    """
    backup_dir = Path(backup_dir)
    if not backup_dir.exists():
        log.warning("backup_dir does not exist yet: %s", backup_dir)
        return {"deleted_files": 0, "freed_bytes": 0, "total_bytes": 0}

    now = time.time()
    max_bytes = int(max_size_gb * (1024 ** 3))
    age_cutoff = now - retention_days * 86400

    files = list(_iter_video_files(backup_dir))
    # Protect the clip(s) currently being written.
    protected = {p for (p, _s, m) in files if (now - m) < ACTIVE_FILE_GRACE_SECONDS}

    deleted = 0
    freed = 0
    gone: set[Path] = set()  # paths removed (real or simulated) this pass

    # --- Rule 1: age ---------------------------------------------------
    for path, _size, mtime in files:
        if path in protected:
            continue
        if mtime < age_cutoff:
            f = _safe_delete(path, dry_run)
            if f:
                deleted += 1
                freed += f
                gone.add(path)

    # --- Rule 2: size --------------------------------------------------
    # Work from the in-memory list minus whatever Rule 1 removed, so the
    # math is identical whether or not we actually deleted (dry-run safe).
    remaining = [(p, s, m) for (p, s, m) in files if p not in gone]
    total = sum(s for (_p, s, _m) in remaining)

    if total > max_bytes:
        remaining.sort(key=lambda t: t[2])  # oldest first
        for path, size, _mtime in remaining:
            if total <= max_bytes:
                break
            if path in protected:
                continue
            f = _safe_delete(path, dry_run)
            if f:
                deleted += 1
                freed += f
                total -= size

    summary = {
        "deleted_files": deleted,
        "freed_bytes": freed,
        "total_bytes": max(total, 0),
        "max_bytes": max_bytes,
    }
    log.info(
        "retention pass: folder now %.2f GB / %.0f GB limit, deleted %d file(s), freed %.2f GB%s",
        summary["total_bytes"] / (1024 ** 3),
        max_size_gb,
        deleted,
        freed / (1024 ** 3),
        " (dry-run)" if dry_run else "",
    )
    return summary


def load_config(config_path: Path) -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    if not cfg.read(config_path, encoding="utf-8"):
        raise FileNotFoundError(f"config file not found: {config_path}")
    return cfg


def main():
    parser = argparse.ArgumentParser(description="Run a CCTV backup retention pass.")
    parser.add_argument(
        "--config",
        default=str(Path(__file__).with_name("config.ini")),
        help="path to config.ini",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="show what would be deleted without deleting anything",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    cfg = load_config(Path(args.config))
    s = cfg["storage"]
    enforce_retention(
        backup_dir=s.get("backup_dir"),
        max_size_gb=s.getfloat("max_size_gb", 10),
        retention_days=s.getint("retention_days", 21),
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
