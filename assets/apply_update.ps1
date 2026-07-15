# External update helper for Autona's in-app auto-updater.
#
# The updater downloads the release zip, then launches THIS script as a
# detached PowerShell process and exits the app. Running outside the install
# directory (from %TEMP%, via the system powershell.exe) means none of Autona's
# files are locked by us, so once the old process fully exits we can replace
# everything - including the launcher exe and the memory-mapped WebEngine
# resources that the old in-process updater could never swap.
#
# Flow: wait for the old process (ParentPid) to exit -> extract the zip into a
# staging dir (stripping the leading "Autona/") -> robocopy staging over the
# install dir -> relaunch the new exe -> clean up. Any failure relaunches the
# existing exe so the user is never left with nothing running.
param(
    [Parameter(Mandatory = $true)][int]$ParentPid,
    [Parameter(Mandatory = $true)][string]$Zip,
    [Parameter(Mandatory = $true)][string]$InstallDir,
    [Parameter(Mandatory = $true)][string]$Staging,
    [string]$ExeName = "Autona.exe",
    [int]$WaitTimeoutSec = 60
)

$ErrorActionPreference = "Stop"
$ExePath = Join-Path $InstallDir $ExeName
$Log = Join-Path $InstallDir "assets\update_log.txt"

# Hold an exclusive lock for the entire install run. A freshly started Autona
# checks this lock (startup gate in main.py + CleanupUpdateLeftovers in
# AutonaUpdater.py): while it is held the app neither starts over the
# half-mirrored install dir nor purges this workdir. Without it, an app
# launched mid-robocopy deletes the staging dir out from under us (v1.3.0
# E2E: robocopy failed with exit 11 and the update was silently lost).
$LockPath = Join-Path $PSScriptRoot "update_in_progress.lock"
$LockStream = $null
try {
    $LockStream = [System.IO.File]::Open(
        $LockPath, [System.IO.FileMode]::Create,
        [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
} catch {}

function Write-Log([string]$message) {
    try {
        Add-Content -LiteralPath $Log -Value ("{0}  {1}" -f (Get-Date -Format o), $message) -Encoding UTF8 -ErrorAction SilentlyContinue
    } catch {}
}

function Invoke-Cleanup() {
    # Release the exclusive lock first so the workdir (which contains the lock
    # file) can actually be deleted, and so a starting app is unblocked.
    try {
        if ($script:LockStream) { $script:LockStream.Dispose(); $script:LockStream = $null }
        if (Test-Path -LiteralPath $script:LockPath) { Remove-Item -LiteralPath $script:LockPath -Force -ErrorAction SilentlyContinue }
    } catch {}
    try { if (Test-Path -LiteralPath $Zip) { Remove-Item -LiteralPath $Zip -Force -ErrorAction SilentlyContinue } } catch {}
    try { if (Test-Path -LiteralPath $Staging) { Remove-Item -LiteralPath $Staging -Recurse -Force -ErrorAction SilentlyContinue } } catch {}
    # Self-delete the helper's own folder. We can't remove the script while it
    # runs, so spawn a short-lived detached PowerShell that waits, then deletes.
    # $PSScriptRoot, not $MyInvocation.MyCommand.Path: inside a function the
    # latter is empty (FunctionInfo has no path), which silently skipped the
    # self-delete and left the workdir for CleanupUpdateLeftovers to purge.
    try {
        $dir = $PSScriptRoot
        if ($dir -and (Test-Path -LiteralPath $dir)) {
            Start-Process -WindowStyle Hidden powershell -ArgumentList @(
                "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
                "Start-Sleep -Milliseconds 1500; Remove-Item -LiteralPath '$dir' -Recurse -Force -ErrorAction SilentlyContinue")
        }
    } catch {}
}

function Start-OldExeAndExit([string]$reason) {
    Write-Log "FALLBACK: $reason -> relaunching existing exe"
    try {
        # Don't stack a second instance on top of one the user already
        # launched (that produced two running Autonas on the v1.3.0 E2E).
        $exeBase = [System.IO.Path]::GetFileNameWithoutExtension($ExeName)
        if (Get-Process -Name $exeBase -ErrorAction SilentlyContinue) {
            Write-Log "an instance is already running; skipping relaunch"
        } else {
            Start-Process -FilePath $ExePath -WorkingDirectory $InstallDir
        }
    } catch { Write-Log "fallback launch failed: $_" }
    Invoke-Cleanup
    exit 1
}

try {
    # 1) Wait for the old Autona process to fully exit so every file unlocks.
    Write-Log "waiting for pid $ParentPid (timeout ${WaitTimeoutSec}s)"
    try {
        $proc = Get-Process -Id $ParentPid -ErrorAction SilentlyContinue
        if ($proc) { $proc.WaitForExit($WaitTimeoutSec * 1000) | Out-Null }
    } catch {}
    # WaitForExit can return before the OS releases handles; poll briefly.
    $deadline = (Get-Date).AddSeconds(5)
    while ((Get-Process -Id $ParentPid -ErrorAction SilentlyContinue) -and (Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 200
    }
    if (Get-Process -Id $ParentPid -ErrorAction SilentlyContinue) {
        Start-OldExeAndExit "parent pid did not exit"
    }
    # Also wait for OTHER instances of the app (a second window the user had
    # open, or one launched right after the updater closed). They hold locks
    # on _internal DLLs, so mirroring over them would half-apply the update.
    $exeBase = [System.IO.Path]::GetFileNameWithoutExtension($ExeName)
    $deadline = (Get-Date).AddSeconds($WaitTimeoutSec)
    while ((Get-Process -Name $exeBase -ErrorAction SilentlyContinue) -and (Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 500
    }
    if (Get-Process -Name $exeBase -ErrorAction SilentlyContinue) {
        Start-OldExeAndExit "another $exeBase instance is still running"
    }
    # Grace period for QtWebEngineProcess children to release mmap'd resources.
    Start-Sleep -Milliseconds 800

    if (-not (Test-Path -LiteralPath $Zip)) {
        Start-OldExeAndExit "update zip not found: $Zip"
    }

    # 2) Extract the zip into staging, stripping the leading "Autona/" folder.
    #    ZipFile API (not Expand-Archive) so we control stripping + slip guard.
    Write-Log "extracting $Zip -> $Staging"
    if (Test-Path -LiteralPath $Staging) { Remove-Item -LiteralPath $Staging -Recurse -Force }
    New-Item -ItemType Directory -Path $Staging -Force | Out-Null
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [System.IO.Compression.ZipFile]::OpenRead($Zip)
    try {
        $stagingFull = (Resolve-Path -LiteralPath $Staging).Path.TrimEnd('\') + '\'
        foreach ($entry in $archive.Entries) {
            # Normalize separators first: zips built by Windows PowerShell 5.1
            # (Compress-Archive / .NET Framework ZipFile) use backslashes in
            # entry names. Without this, no entry matches '/' and the whole
            # update silently extracts nothing (v1.3.0 release asset bug).
            $name = $entry.FullName -replace '\\', '/'
            if ($name -notmatch '/') { continue }
            $relative = $name.Substring($name.IndexOf('/') + 1)
            if ([string]::IsNullOrEmpty($relative)) { continue }
            $dest = Join-Path $Staging ($relative -replace '/', '\')
            # ZIP slip guard: reject any entry that resolves outside staging.
            $destFull = [System.IO.Path]::GetFullPath($dest)
            if (-not $destFull.StartsWith($stagingFull, [System.StringComparison]::OrdinalIgnoreCase)) {
                Write-Log "REJECT unsafe entry $name"
                continue
            }
            if ($name.EndsWith('/')) {
                New-Item -ItemType Directory -Path $destFull -Force | Out-Null
            } else {
                $parent = Split-Path -Parent $destFull
                if (-not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
                [System.IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $destFull, $true)
            }
        }
    } finally {
        $archive.Dispose()
    }

    # Sanity guard: an unexpected zip layout (wrong root folder, separator
    # quirks, empty archive) must not be mirrored as a "successful" no-op.
    # The new launcher exe is the one file every release is guaranteed to ship.
    if (-not (Test-Path -LiteralPath (Join-Path $Staging $ExeName))) {
        Start-OldExeAndExit "staging has no $ExeName after extraction (unexpected zip layout)"
    }

    # 3) Mirror staging over the install dir. No /PURGE: keep user-side assets
    #    (game_images, uploaded avatars, etc.) that the release zip omits.
    #    robocopy exit codes 0-7 are success; >= 8 is a real failure.
    Write-Log "robocopy $Staging -> $InstallDir"
    $rc = Start-Process robocopy -ArgumentList @("`"$Staging`"", "`"$InstallDir`"", "/E", "/R:3", "/W:1", "/NFL", "/NDL", "/NP", "/NJH", "/NJS") -Wait -PassThru -WindowStyle Hidden
    if ($rc.ExitCode -ge 8) {
        Start-OldExeAndExit "robocopy failed (exit $($rc.ExitCode))"
    }
    Write-Log "robocopy ok ($($rc.ExitCode))"

    # 4) Launch the freshly installed version.
    Start-Process -FilePath $ExePath -WorkingDirectory $InstallDir

    # 5) Clean up temp artifacts and self-delete.
    Invoke-Cleanup
    Write-Log "update complete"
    exit 0
} catch {
    Write-Log "ERROR: $_"
    Start-OldExeAndExit "exception: $_"
}
