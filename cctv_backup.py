"""
CCTV backup service.

For each camera in config.ini it runs an ffmpeg process that records the
RTSP stream into time-segmented clips (e.g. 10-minute .mp4 files) inside a
per-camera sub-folder. ffmpeg is auto-restarted if the stream drops.

A background thread periodically enforces the storage rules (see retention.py):
delete clips older than `retention_days`, then trim the OLDEST clips until the
whole folder is under `max_size_gb`.

Run:
    python cctv_backup.py
Stop:
    Ctrl+C  (or stop the Windows scheduled task)
"""

from __future__ import annotations

import configparser
import logging
import logging.handlers
import os
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path

import retention

APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.ini"
LOG_PATH = APP_DIR / "cctv_backup.log"

# Wait this long before restarting ffmpeg after it exits/crashes.
RESTART_DELAY_SECONDS = 5

log = logging.getLogger("cctv")
_stop = threading.Event()


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.handlers.RotatingFileHandler(
                LOG_PATH, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
            ),
            logging.StreamHandler(),
        ],
    )


def find_ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise FileNotFoundError(
            "ffmpeg not found on PATH. Run setup.ps1 to install it "
            "(or install manually: winget install Gyan.FFmpeg)."
        )
    return exe


def load_config() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    if not cfg.read(CONFIG_PATH, encoding="utf-8"):
        raise FileNotFoundError(f"config file not found: {CONFIG_PATH}")
    return cfg


def get_cameras(cfg: configparser.ConfigParser):
    """Return list of (name, url) for every [camera:*] section."""
    cams = []
    for section in cfg.sections():
        if section.startswith("camera:"):
            name = cfg[section].get("name", section.split(":", 1)[1]).strip()
            url = cfg[section].get("url", "").strip()
            if not url or "USERNAME:PASSWORD" in url or "192.168.1.10" in url:
                log.warning(
                    "camera '%s' looks unconfigured (placeholder URL) - skipping. "
                    "Edit config.ini.", name,
                )
                continue
            cams.append((name, url))
    return cams


def build_ffmpeg_cmd(ffmpeg: str, url: str, out_dir: Path, seg_seconds: int, fmt: str):
    pattern = str(out_dir / f"%Y-%m-%d_%H-%M-%S.{fmt}")
    cmd = [
        ffmpeg,
        "-nostdin",
        "-loglevel", "warning",
        "-rtsp_transport", "tcp",   # TCP is far more reliable than UDP
        "-use_wallclock_as_timestamps", "1",
        "-i", url,
        "-c:v", "copy",             # video: never re-encode (low CPU, original quality)
    ]
    if fmt == "mp4":
        # Many cameras send G.711 audio, which MP4 can't stream-copy.
        # Re-encode just the audio to AAC (cheap). If a stream has no audio
        # ffmpeg simply ignores this.
        cmd += ["-c:a", "aac"]
    else:
        cmd += ["-c:a", "copy"]     # mkv/ts can hold the original audio as-is
    cmd += [
        "-f", "segment",
        "-segment_time", str(seg_seconds),
        "-segment_format", "mpegts" if fmt == "ts" else fmt,
        "-reset_timestamps", "1",
        "-strftime", "1",
        pattern,
    ]
    return cmd


def record_camera(ffmpeg: str, name: str, url: str, backup_dir: Path,
                  seg_seconds: int, fmt: str):
    """Run ffmpeg for one camera, auto-restarting until _stop is set."""
    out_dir = backup_dir / name
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = build_ffmpeg_cmd(ffmpeg, url, out_dir, seg_seconds, fmt)
    log.info("[%s] recording to %s", name, out_dir)

    while not _stop.is_set():
        log.info("[%s] starting ffmpeg", name)
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception as exc:
            log.error("[%s] failed to launch ffmpeg: %s", name, exc)
            _stop.wait(RESTART_DELAY_SECONDS)
            continue

        # Block until ffmpeg exits or we are told to stop.
        while not _stop.is_set():
            try:
                proc.wait(timeout=1)
                break
            except subprocess.TimeoutExpired:
                continue

        if _stop.is_set():
            log.info("[%s] stopping ffmpeg", name)
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
            return

        # ffmpeg exited on its own (stream dropped, camera rebooted, etc.)
        err_tail = ""
        if proc.stderr:
            try:
                err_tail = proc.stderr.read().decode("utf-8", "replace")[-500:].strip()
            except Exception:
                pass
        log.warning(
            "[%s] ffmpeg exited (code %s); restarting in %ds. %s",
            name, proc.returncode, RESTART_DELAY_SECONDS, err_tail,
        )
        _stop.wait(RESTART_DELAY_SECONDS)


def retention_loop(cfg: configparser.ConfigParser):
    s = cfg["storage"]
    backup_dir = s.get("backup_dir")
    max_size_gb = s.getfloat("max_size_gb", 10)
    retention_days = s.getint("retention_days", 21)
    interval = s.getint("cleanup_interval_seconds", 300)

    # Run once at startup, then on the interval.
    while not _stop.is_set():
        try:
            retention.enforce_retention(backup_dir, max_size_gb, retention_days)
        except Exception as exc:
            log.error("retention pass failed: %s", exc)
        _stop.wait(interval)


def _handle_signal(signum, _frame):
    log.info("received signal %s, shutting down...", signum)
    _stop.set()


def main():
    setup_logging()
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    cfg = load_config()
    ffmpeg = find_ffmpeg()

    s = cfg["storage"]
    backup_dir = Path(s.get("backup_dir"))
    backup_dir.mkdir(parents=True, exist_ok=True)
    seg_seconds = s.getint("segment_seconds", 600)
    fmt = s.get("segment_format", "mp4").strip().lower()

    cameras = get_cameras(cfg)
    if not cameras:
        log.error("No usable cameras in config.ini. Edit it and restart.")
        return 1

    log.info("CCTV backup starting: %d camera(s), backup_dir=%s, limit=%s GB, keep=%s days",
             len(cameras), backup_dir, s.get("max_size_gb"), s.get("retention_days"))

    threads = []
    for name, url in cameras:
        t = threading.Thread(
            target=record_camera,
            args=(ffmpeg, name, url, backup_dir, seg_seconds, fmt),
            name=f"rec-{name}",
            daemon=True,
        )
        t.start()
        threads.append(t)

    t_ret = threading.Thread(target=retention_loop, args=(cfg,),
                             name="retention", daemon=True)
    t_ret.start()
    threads.append(t_ret)

    # Keep the main thread alive until a stop signal.
    try:
        while not _stop.is_set():
            _stop.wait(1)
    except KeyboardInterrupt:
        _stop.set()

    log.info("waiting for threads to finish...")
    for t in threads:
        t.join(timeout=15)
    log.info("stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
