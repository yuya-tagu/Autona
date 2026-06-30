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

function Write-Log([string]$message) {
    try {
        Add-Content -LiteralPath $Log -Value ("{0}  {1}" -f (Get-Date -Format o), $message) -Encoding UTF8 -ErrorAction SilentlyContinue
    } catch {}
}

function Invoke-Cleanup() {
    try { if (Test-Path -LiteralPath $Zip) { Remove-Item -LiteralPath $Zip -Force -ErrorAction SilentlyContinue } } catch {}
    try { if (Test-Path -LiteralPath $Staging) { Remove-Item -LiteralPath $Staging -Recurse -Force -ErrorAction SilentlyContinue } } catch {}
    # Self-delete the helper's own folder. We can't remove the script while it
    # runs, so spawn a short-lived detached PowerShell that waits, then deletes.
    try {
        $dir = Split-Path -Parent $MyInvocation.MyCommand.Path
        if ($dir -and (Test-Path -LiteralPath $dir)) {
            Start-Process -WindowStyle Hidden powershell -ArgumentList @(
                "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
                "Start-Sleep -Milliseconds 1500; Remove-Item -LiteralPath '$dir' -Recurse -Force -ErrorAction SilentlyContinue")
        }
    } catch {}
}

function Start-OldExeAndExit([string]$reason) {
    Write-Log "FALLBACK: $reason -> relaunching existing exe"
    try { Start-Process -FilePath $ExePath -WorkingDirectory $InstallDir } catch { Write-Log "fallback launch failed: $_" }
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
            $name = $entry.FullName
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
