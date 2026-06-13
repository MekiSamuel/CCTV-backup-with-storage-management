# CCTV Backup

Records your CCTV camera(s) over RTSP into a local folder as time-segmented
clips, and automatically keeps the folder under control:

- **Deletes clips older than 3 weeks** (21 days).
- **Caps total size at 10 GB** — when exceeded, the oldest clips are deleted
  first until it's back under the limit.
- Runs **in the background** via Windows Task Scheduler and restarts itself if
  a camera drops or the PC reboots.

All limits are configurable in [`config.ini`](config.ini).

---

## 1. Configure

Open **`config.ini`** and set:

- `backup_dir` — where to save recordings. **Keep this out of OneDrive/Dropbox**
  or it will try to upload all your video. Default: `C:\Users\<you>\CCTV-Backup`.
- `[camera:cam1] url` — your camera's RTSP URL. Examples for common brands are
  listed in the file. You usually find it in the camera/NVR network settings,
  or the manufacturer's documentation.

To add more cameras, copy the `[camera:cam1]` block and give each a unique
`name` and `url`.

## 2. Install (one time)

Open PowerShell **in this folder** and run:

```powershell
.\setup.ps1
```

This will:
1. Install **ffmpeg** if it's missing (via `winget`).
2. Create the backup folder.
3. Register a background task that starts at logon and starts it now.

> If ffmpeg was just installed, you may need to **close and reopen PowerShell**
> once, then run `.\setup.ps1` again.

## 3. Manage it

```powershell
.\setup.ps1 -Action Status      # running? folder size?
.\setup.ps1 -Action Stop
.\setup.ps1 -Action Start
.\setup.ps1 -Action Uninstall   # removes the task; keeps your recordings
```

Live activity is logged to **`cctv_backup.log`**.

---

## Manual / testing

Run the recorder in the foreground (Ctrl+C to stop):

```powershell
python cctv_backup.py
```

Run only the cleanup once, or preview what it would delete:

```powershell
python retention.py --dry-run   # show, delete nothing
python retention.py             # actually enforce the limits
```

---

## How it works

- `cctv_backup.py` — for each camera, runs `ffmpeg` to copy the RTSP stream
  into `<backup_dir>\<camera name>\YYYY-MM-DD_HH-MM-SS.mp4` clips
  (`segment_seconds` long, default 10 min). Video is stream-copied (no
  re-encode → low CPU). A background thread runs the cleanup every
  `cleanup_interval_seconds`.
- `retention.py` — the age + size cleanup rules (also usable standalone).
- `setup.ps1` — installer / task manager.
- The clip currently being written is never deleted by cleanup.

## Notes & limits

- A pure RTSP URL is a **live stream**, so this tool builds your local backup by
  *recording* the stream. There are no pre-existing camera files to copy.
- `mp4` is the default format (best compatibility). If your PC may lose power
  mid-recording, set `segment_format = mkv` in `config.ini` — partial `.mkv`
  clips stay playable.
- Recording 24/7 uses bandwidth and disk continuously. With a 10 GB cap you'll
  typically retain the most recent hours/days depending on camera bitrate; the
  3-week rule only matters if your bitrate is low enough to fit 3 weeks in 10 GB.
