<#
    CCTV Backup - installer / manager  (Windows PowerShell)

    Usage (run from this folder):
        .\setup.ps1                 # install ffmpeg (if needed) + register & start the background task
        .\setup.ps1 -Action Status  # is it running? how big is the folder?
        .\setup.ps1 -Action Start
        .\setup.ps1 -Action Stop
        .\setup.ps1 -Action Uninstall
#>

param(
    [ValidateSet("Install", "Uninstall", "Start", "Stop", "Status")]
    [string]$Action = "Install"
)

$ErrorActionPreference = "Stop"
$AppDir    = $PSScriptRoot
$Script    = Join-Path $AppDir "cctv_backup.py"
$Config    = Join-Path $AppDir "config.ini"
$Example   = Join-Path $AppDir "config.example.ini"
$TaskName  = "CCTV Backup Service"

function Refresh-Path {
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user    = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machine;$user"
}

function Get-BackupDir {
    # crude INI read: first non-comment 'backup_dir = ...' line
    $line = Get-Content $Config | Where-Object { $_ -match '^\s*backup_dir\s*=' } | Select-Object -First 1
    if ($line) { return ($line -replace '^\s*backup_dir\s*=\s*', '').Trim() }
    return $null
}

function Resolve-Pythonw {
    $py = (Get-Command python -ErrorAction SilentlyContinue).Source
    if (-not $py) { throw "Python not found on PATH. Install Python 3 first (https://python.org)." }
    $pyw = Join-Path (Split-Path $py) "pythonw.exe"
    if (Test-Path $pyw) { return $pyw }   # pythonw = no console window
    return $py
}

function Ensure-Ffmpeg {
    if (Get-Command ffmpeg -ErrorAction SilentlyContinue) {
        Write-Host "ffmpeg: already installed." -ForegroundColor Green
        return
    }
    Write-Host "ffmpeg not found - installing via winget..." -ForegroundColor Yellow
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "winget is not available. Install ffmpeg manually from https://ffmpeg.org and re-run."
    }
    winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements
    Refresh-Path
    if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
        Write-Warning "ffmpeg installed but not yet on PATH in this window. Close and reopen PowerShell, then re-run .\setup.ps1"
        exit 1
    }
    Write-Host "ffmpeg: installed." -ForegroundColor Green
}

function Do-Install {
    Write-Host "=== Installing CCTV Backup ===" -ForegroundColor Cyan

    # On a fresh clone there is no config.ini (it's git-ignored so passwords
    # never get pushed). Seed it from the template and ask the user to edit it.
    if (-not (Test-Path $Config)) {
        if (Test-Path $Example) {
            Copy-Item $Example $Config
            Write-Warning "Created config.ini from the template. Edit it with your camera URL/password, then re-run .\setup.ps1"
            return
        } else {
            throw "config.ini not found and no config.example.ini to copy from."
        }
    }

    Ensure-Ffmpeg

    # Create the backup folder and warn if it is inside OneDrive.
    $backup = Get-BackupDir
    if ($backup) {
        New-Item -ItemType Directory -Force -Path $backup | Out-Null
        Write-Host "Backup folder: $backup" -ForegroundColor Green
        if ($backup -match "OneDrive|Dropbox|Google Drive") {
            Write-Warning "Backup folder is inside a cloud-synced location ($backup). This will upload gigabytes of video. Change 'backup_dir' in config.ini to a local path like C:\Users\$env:USERNAME\CCTV-Backup."
        }
    } else {
        Write-Warning "Could not read backup_dir from config.ini - check the file."
    }

    # Reminder if the camera URL is still a placeholder.
    if ((Get-Content $Config -Raw) -match "USERNAME:PASSWORD|192\.168\.1\.10") {
        Write-Warning "config.ini still has a placeholder camera URL. Edit it with your real RTSP URL before recording will work."
    }

    $pythonw = Resolve-Pythonw
    Write-Host "Python: $pythonw"

    # (Re)register the scheduled task: runs at logon, restarts on failure, no time limit.
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    }
    $action  = New-ScheduledTaskAction -Execute $pythonw -Argument "`"$Script`"" -WorkingDirectory $AppDir
    $trigger = New-ScheduledTaskTrigger -AtLogOn
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit (New-TimeSpan -Seconds 0)
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Settings $settings -Principal $principal -Description "Records CCTV RTSP streams and enforces storage limits." | Out-Null

    Write-Host "Scheduled task '$TaskName' registered (starts at logon)." -ForegroundColor Green
    Start-ScheduledTask -TaskName $TaskName
    Write-Host "Started. Check status with: .\setup.ps1 -Action Status" -ForegroundColor Cyan
    Write-Host "Logs: $(Join-Path $AppDir 'cctv_backup.log')"
}

function Do-Uninstall {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Removed scheduled task '$TaskName'. (Your recordings are kept.)" -ForegroundColor Green
    } else {
        Write-Host "Task '$TaskName' not found - nothing to remove."
    }
}

function Do-Status {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task) { Write-Host "Not installed. Run .\setup.ps1" -ForegroundColor Yellow; return }
    $info = Get-ScheduledTaskInfo -TaskName $TaskName
    Write-Host "Task state      : $($task.State)"
    Write-Host "Last run        : $($info.LastRunTime)"
    Write-Host "Last result     : $($info.LastTaskResult)  (0 = OK / running)"
    $backup = Get-BackupDir
    if ($backup -and (Test-Path $backup)) {
        $bytes = (Get-ChildItem $backup -Recurse -File -ErrorAction SilentlyContinue |
                  Measure-Object -Property Length -Sum).Sum
        if (-not $bytes) { $bytes = 0 }
        Write-Host ("Backup folder   : {0}" -f $backup)
        Write-Host ("Folder size     : {0:N2} GB" -f ($bytes / 1GB))
    }
}

function Do-Start { Start-ScheduledTask -TaskName $TaskName; Write-Host "Started." }
function Do-Stop  { Stop-ScheduledTask  -TaskName $TaskName; Write-Host "Stopped." }

switch ($Action) {
    "Install"   { Do-Install }
    "Uninstall" { Do-Uninstall }
    "Start"     { Do-Start }
    "Stop"      { Do-Stop }
    "Status"    { Do-Status }
}
