# External update helper for Autona's in-app auto-updater.
#
# The updater downloads the release zip, then launches THIS script as a
# hidden-console PowerShell process and exits the app. Running outside the
# install directory (from %TEMP%, via the system powershell.exe) means none of
# Autona's files are locked by us, so once the old process fully exits we can
# replace everything - including the launcher exe and the memory-mapped
# WebEngine resources that the old in-process updater could never swap.
#
# Flow: show the install window -> wait for the old process (ParentPid) to exit
# -> extract the zip into a staging dir (stripping the leading "Autona/") ->
# robocopy staging over the install dir -> relaunch the new exe -> clean up.
#
# THIS FILE MUST STAY UTF-8 *WITH BOM*. Windows PowerShell 5.1 decodes a BOM-less
# .ps1 using the ANSI codepage (932 on a Japanese Windows), which turns every
# Japanese string below into mojibake in the install window. tests/
# test_apply_update_encoding.py guards this.
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

# Window title of the install window below. main.py's startup gate looks this up
# with FindWindowW to foreground it, so the two must stay in sync.
$UI_TITLE = "Autona Updater"

# Hold an exclusive lock for the entire install run. A freshly started Autona
# checks this lock (startup gate in main.py + CleanupUpdateLeftovers in
# AutonaUpdater.py): while it is held the app neither starts over the
# half-mirrored install dir nor purges this workdir. Without it, an app
# launched mid-robocopy deletes the staging dir out from under us (v1.3.0
# E2E: robocopy failed with exit 11 and the update was silently lost).
$LockPath = Join-Path $PSScriptRoot "update_in_progress.lock"

# Written by the app's diff download after it fully assembles $Staging itself
# (only ~6% of a release changes between versions, so the updater fetches just
# those entries and copies the rest from the local install). When this marker
# exists there is NO update.zip - skip the zip check and extraction entirely.
# The name is a contract with _STAGING_MARKER_NAME in src/AutonaUpdater.py,
# pinned by tests/test_diff_update_contract.py.
$MarkerPath = Join-Path $PSScriptRoot "staging_prebuilt.marker"

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

# ---------------------------------------------------------------------------
# Install window
# ---------------------------------------------------------------------------
# Autona is gone from the screen for this whole script - extracting and
# mirroring a ~400 MB release takes the better part of a minute. With no UI at
# all, users concluded it had crashed and relaunched it from the shortcut, which
# (a) locks Autona.exe so robocopy cannot replace the launcher and (b) made this
# script fall back to relaunching the old exe. The update was dropped without a
# word and the user saw a "rollback" to the previous version. So: we own a
# visible window for the entire run, and every failure path says so out loud.

$script:Form = $null
$script:Bar = $null
$script:StepLabel = $null

function Show-InstallWindow() {
    try {
        Add-Type -AssemblyName System.Windows.Forms
        Add-Type -AssemblyName System.Drawing
        [System.Windows.Forms.Application]::EnableVisualStyles()
    } catch { return }
    try {
        $f = New-Object System.Windows.Forms.Form
        $f.Text = $UI_TITLE
        $f.FormBorderStyle = [System.Windows.Forms.FormBorderStyle]::FixedDialog
        $f.ControlBox = $false      # closing it would not stop the install
        $f.MaximizeBox = $false
        $f.MinimizeBox = $false
        $f.StartPosition = [System.Windows.Forms.FormStartPosition]::CenterScreen
        $f.TopMost = $true
        $f.ClientSize = New-Object System.Drawing.Size(460, 148)
        $f.BackColor = [System.Drawing.Color]::FromArgb(32, 33, 36)
        $f.ForeColor = [System.Drawing.Color]::WhiteSmoke
        $f.Font = New-Object System.Drawing.Font("Yu Gothic UI", 9)
        # ExtractAssociatedIcon copies the icon resource out and closes the file.
        # Whatever replaces this, it must NOT hold a handle on $ExePath (e.g.
        # Image.FromFile keeps the file open): robocopy has to overwrite the
        # launcher in step 3, and a lock here loses the update.
        try { $f.Icon = [System.Drawing.Icon]::ExtractAssociatedIcon($ExePath) } catch {}

        $title = New-Object System.Windows.Forms.Label
        $title.Text = "Autona をアップデートしています"
        $title.Font = New-Object System.Drawing.Font("Yu Gothic UI", 12, [System.Drawing.FontStyle]::Bold)
        $title.AutoSize = $false
        $title.Size = New-Object System.Drawing.Size(420, 26)
        $title.Location = New-Object System.Drawing.Point(20, 16)
        $f.Controls.Add($title)

        $sub = New-Object System.Windows.Forms.Label
        $sub.Text = "この画面が閉じると自動で再起動します。Autona は起動しないでください。"
        $sub.ForeColor = [System.Drawing.Color]::FromArgb(168, 170, 174)
        $sub.AutoSize = $false
        $sub.Size = New-Object System.Drawing.Size(420, 34)
        $sub.Location = New-Object System.Drawing.Point(20, 46)
        $f.Controls.Add($sub)

        $bar = New-Object System.Windows.Forms.ProgressBar
        $bar.Style = [System.Windows.Forms.ProgressBarStyle]::Marquee
        $bar.MarqueeAnimationSpeed = 30
        $bar.Location = New-Object System.Drawing.Point(20, 86)
        $bar.Size = New-Object System.Drawing.Size(420, 14)
        $f.Controls.Add($bar)

        $step = New-Object System.Windows.Forms.Label
        $step.ForeColor = [System.Drawing.Color]::FromArgb(168, 170, 174)
        $step.AutoSize = $false
        $step.Size = New-Object System.Drawing.Size(420, 20)
        $step.Location = New-Object System.Drawing.Point(20, 110)
        $f.Controls.Add($step)

        $script:Form = $f
        $script:Bar = $bar
        $script:StepLabel = $step
        $f.Show()
        $f.Activate()
        [System.Windows.Forms.Application]::DoEvents()
    } catch {
        $script:Form = $null
    }
}

# Every long-running step below has to call this, or the window stops painting
# and Windows greys it out as "not responding" - the exact impression we are
# trying to remove.
function Update-UI() {
    if (-not $script:Form) { return }
    try { [System.Windows.Forms.Application]::DoEvents() } catch {}
}

# percent < 0 keeps the bar in marquee mode (unknown duration).
function Set-Step([string]$text, [int]$percent = -1) {
    if (-not $script:Form) { return }
    try {
        $script:StepLabel.Text = $text
        if ($percent -lt 0) {
            if ($script:Bar.Style -ne [System.Windows.Forms.ProgressBarStyle]::Marquee) {
                $script:Bar.Style = [System.Windows.Forms.ProgressBarStyle]::Marquee
            }
        } else {
            if ($script:Bar.Style -ne [System.Windows.Forms.ProgressBarStyle]::Continuous) {
                $script:Bar.Style = [System.Windows.Forms.ProgressBarStyle]::Continuous
            }
            $script:Bar.Value = [Math]::Max(0, [Math]::Min(100, $percent))
        }
        [System.Windows.Forms.Application]::DoEvents()
    } catch {}
}

function Close-InstallWindow() {
    if (-not $script:Form) { return }
    try { $script:Form.Close(); $script:Form.Dispose() } catch {}
    $script:Form = $null
    try { [System.Windows.Forms.Application]::DoEvents() } catch {}
}

function Show-Failure([string]$userMessage) {
    Close-InstallWindow
    try { Add-Type -AssemblyName System.Windows.Forms } catch { return }
    try {
        [System.Windows.Forms.MessageBox]::Show(
            ("アップデートを適用できませんでした。" + [Environment]::NewLine +
             $userMessage + [Environment]::NewLine + [Environment]::NewLine +
             "ログ: " + $Log),
            "Autona",
            [System.Windows.Forms.MessageBoxButtons]::OK,
            [System.Windows.Forms.MessageBoxIcon]::Warning) | Out-Null
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
    try { if (Test-Path -LiteralPath $MarkerPath) { Remove-Item -LiteralPath $MarkerPath -Force -ErrorAction SilentlyContinue } } catch {}
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

# Bail out of the install and put the user back on a running app. $userMessage
# is shown in a dialog: this leaves them on the old version, and saying nothing
# is what made the whole thing read as a deliberate rollback.
function Stop-UpdateAndExit([string]$reason, [string]$userMessage) {
    Write-Log "FAILED: $reason -> keeping the current version"
    Show-Failure $userMessage
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

# Runs one robocopy pass and keeps the install window alive while it works.
# Process.Start (not Start-Process -Wait) so the wait loop can keep painting the
# window, and so ExitCode is reliably populated. robocopy exit codes 0-7 are
# success; >= 8 is a real failure. $label names the pass in the log so a failure
# says WHICH tree could not be copied.
function Invoke-RobocopyPass([string]$label, [string]$arguments) {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    # Full path, like _LaunchHelper does for powershell.exe: don't depend on
    # PATH resolution from a workdir we don't control.
    $psi.FileName = Join-Path $env:SystemRoot "System32\robocopy.exe"
    if (-not (Test-Path -LiteralPath $psi.FileName)) { $psi.FileName = "robocopy.exe" }
    $psi.Arguments = $arguments
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $rc = [System.Diagnostics.Process]::Start($psi)
    while (-not $rc.WaitForExit(150)) { Update-UI }
    if ($rc.ExitCode -ge 8) {
        Stop-UpdateAndExit "robocopy failed in the $label pass (exit $($rc.ExitCode))" `
            "ファイルのコピーに失敗しました (robocopy exit $($rc.ExitCode))。Autona やそのフォルダを開いているアプリを閉じてから、やり直してください。"
    }
    Write-Log "robocopy ok ($($rc.ExitCode)) [$label]"
}

try {
    # A second helper must NEVER run concurrently: both instances share the
    # same workdir, so the loser's Invoke-Cleanup deletes the zip and staging
    # out from under the winner's robocopy. That is exactly what killed every
    # v1.3.0 -> v1.3.1 update (update_log 2026-07-16: helper #2's fallback
    # cleanup at 17:55:48 -> helper #1's robocopy exit 11 at 17:56:42). If the
    # exclusive lock is already held, another helper owns this install run -
    # touch nothing, clean nothing, just vanish.
    if (-not $LockStream) {
        $lockHeld = $false
        try {
            if (Test-Path -LiteralPath $LockPath) {
                Remove-Item -LiteralPath $LockPath -Force -ErrorAction Stop
            }
        } catch { $lockHeld = $true }
        if ($lockHeld) {
            Write-Log "another update helper is already running; exiting without cleanup (pid $PID)"
            exit 0
        }
        # The lock file was stale (deletable = crashed helper); take it now.
        try {
            $LockStream = [System.IO.File]::Open(
                $LockPath, [System.IO.FileMode]::Create,
                [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
        } catch {}
    }

    Write-Log "helper started (pid $PID) for parent $ParentPid"
    Show-InstallWindow

    # 1) Wait for the old Autona process to fully exit so every file unlocks.
    Write-Log "waiting for pid $ParentPid (timeout ${WaitTimeoutSec}s)"
    Set-Step "Autona の終了を待っています..."
    $deadline = (Get-Date).AddSeconds($WaitTimeoutSec)
    while ((Get-Process -Id $ParentPid -ErrorAction SilentlyContinue) -and (Get-Date) -lt $deadline) {
        Update-UI
        Start-Sleep -Milliseconds 150
    }
    if (Get-Process -Id $ParentPid -ErrorAction SilentlyContinue) {
        Stop-UpdateAndExit "parent pid did not exit" `
            "Autona を終了できませんでした。Autona を閉じてから、メニューの「ヘルプ」→「アップデートを確認」でやり直してください。"
    }

    # Also wait for OTHER instances of the app (a second window the user had
    # open, or one launched right after the updater closed). They hold locks on
    # the launcher and on _internal DLLs, so mirroring over them half-applies
    # the update. Counting down in the window gives the user something to act
    # on instead of a frozen screen.
    $exeBase = [System.IO.Path]::GetFileNameWithoutExtension($ExeName)
    $deadline = (Get-Date).AddSeconds($WaitTimeoutSec)
    while ((Get-Process -Name $exeBase -ErrorAction SilentlyContinue) -and (Get-Date) -lt $deadline) {
        $left = [int][Math]::Ceiling(($deadline - (Get-Date)).TotalSeconds)
        Set-Step ("起動中の Autona を閉じてください... (残り {0} 秒)" -f $left)
        Start-Sleep -Milliseconds 250
    }
    if (Get-Process -Name $exeBase -ErrorAction SilentlyContinue) {
        Stop-UpdateAndExit "another $exeBase instance is still running" `
            "他の Autona が起動しているため、ファイルを置き換えられませんでした。すべての Autona を終了してから、メニューの「ヘルプ」→「アップデートを確認」でやり直してください。"
    }
    # Grace period for QtWebEngineProcess children to release mmap'd resources.
    Set-Step "準備しています..."
    Start-Sleep -Milliseconds 800
    Update-UI

    # Diff downloads pre-assemble $Staging inside the app (only the changed
    # entries are fetched over HTTP Range; the rest is copied from the local
    # install). In that case there is no update.zip at all, so this check
    # must come BEFORE the zip check or every diff update would fail here.
    if (Test-Path -LiteralPath $MarkerPath) {
        Write-Log "prebuilt staging detected (diff download); skipping extraction"
        Set-Step "ファイルを確認しています..."
        if (-not (Test-Path -LiteralPath $Staging)) {
            Stop-UpdateAndExit "marker present but staging dir missing" `
                "アップデートの一時ファイルが見つかりませんでした。もう一度アップデートをお試しください。"
        }
    } else {
        if (-not (Test-Path -LiteralPath $Zip)) {
            Stop-UpdateAndExit "update zip not found: $Zip" `
                "ダウンロードしたファイルが見つかりませんでした。もう一度アップデートをお試しください。"
        }

        # 2) Extract the zip into staging, stripping the leading "Autona/"
        #    folder. ZipFile API (not Expand-Archive) so we control
        #    stripping + slip guard.
        Write-Log "extracting $Zip -> $Staging"
        Set-Step "ファイルを展開しています..." 0
        if (Test-Path -LiteralPath $Staging) { Remove-Item -LiteralPath $Staging -Recurse -Force }
        New-Item -ItemType Directory -Path $Staging -Force | Out-Null
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $archive = [System.IO.Compression.ZipFile]::OpenRead($Zip)
        try {
            $stagingFull = (Resolve-Path -LiteralPath $Staging).Path.TrimEnd('\') + '\'
            $entryTotal = $archive.Entries.Count
            $entryIndex = 0
            foreach ($entry in $archive.Entries) {
                $entryIndex++
                # Repainting per entry would cost more than the extraction
                # itself on a release with thousands of small asset files.
                if (($entryIndex % 50) -eq 0 -and $entryTotal -gt 0) {
                    Set-Step ("ファイルを展開しています... ({0} / {1})" -f $entryIndex, $entryTotal) `
                        ([int](100 * $entryIndex / $entryTotal))
                }
                # Normalize separators first: zips built by Windows PowerShell
                # 5.1 (Compress-Archive / .NET Framework ZipFile) use
                # backslashes in entry names. Without this, no entry matches
                # '/' and the whole update silently extracts nothing (v1.3.0
                # release asset bug).
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
    }

    # Sanity guard: an unexpected zip layout (wrong root folder, separator
    # quirks, empty archive) must not be mirrored as a "successful" no-op.
    # The new launcher exe is the one file every release is guaranteed to ship.
    if (-not (Test-Path -LiteralPath (Join-Path $Staging $ExeName))) {
        Stop-UpdateAndExit "staging has no $ExeName after extraction (unexpected zip layout)" `
            "ダウンロードしたファイルの中身が想定と異なります。リリースページから手動で入手してください。"
    }

    $StagingInternal = (Join-Path $Staging "_internal").TrimEnd('\')
    $InstallInternal = (Join-Path $InstallDir "_internal").TrimEnd('\')

    # Same sanity guard as the exe above: _internal is mirrored with /MIR, and
    # mirroring a missing source would DELETE the installed runtime. An
    # unexpected staging layout must fail loudly instead.
    if (-not (Test-Path -LiteralPath $StagingInternal)) {
        Stop-UpdateAndExit "staging has no _internal (unexpected zip layout)" `
            "ダウンロードしたファイルの中身が想定と異なります。リリースページから手動で入手してください。"
    }

    # 3) Mirror staging over the install dir in TWO passes. _internal goes
    #    first: it is the runtime the relaunched exe loads, so if anything
    #    fails we fail before the launcher is swapped, and the install is never
    #    left with a new exe on top of an old runtime.
    Write-Log "robocopy $Staging -> $InstallDir (_internal mirrored, rest merged)"
    Set-Step "ファイルをコピーしています..."

    # 3a) _internal with /MIR (i.e. /E + /PURGE). This tree is owned 100% by the
    #     release - user-side assets (layout/, user_data/, game_images, uploaded
    #     avatars, ...) all live under the install ROOT, never in here - so
    #     purging stale files is safe, and it is required for correctness:
    #     without it, packages dropped from a newer build's PyInstaller
    #     excludes (numpy/pandas in v1.5.0) survive as leftover binary-only
    #     directories. Python then imports them as NAMESPACE packages, which
    #     shadows the new PYZ and makes the app unstartable (v1.4.1 -> v1.5.0
    #     auto-update: deepdiff saw a truthy but gutted numpy, so its
    #     `except ImportError` fallback never fired and np.array raised
    #     AttributeError). A full-zip install never hit this because it lands
    #     in a clean tree.
    Invoke-RobocopyPass "_internal" ('"{0}" "{1}" /MIR /R:3 /W:1 /NFL /NDL /NP /NJH /NJS' -f `
        $StagingInternal, $InstallInternal)

    # 3b) Everything else, unchanged policy: /E with no purge so user-side
    #     assets the release zip omits are kept. _internal is excluded because
    #     pass 3a already mirrored it (and a second non-purging pass would just
    #     re-copy it).
    Invoke-RobocopyPass "root" ('"{0}" "{1}" /E /XD "{2}" /R:3 /W:1 /NFL /NDL /NP /NJH /NJS' -f `
        $Staging.TrimEnd('\'), $InstallDir.TrimEnd('\'), $StagingInternal)

    # 4) Launch the freshly installed version. Drop TopMost first so the new
    #    app's splash isn't stuck behind this window, and hold the window until
    #    the splash has had a moment to appear - otherwise we reintroduce the
    #    blank-screen gap that started this whole problem.
    Set-Step "Autona を起動しています..." 100
    try { $script:Form.TopMost = $false } catch {}
    Start-Process -FilePath $ExePath -WorkingDirectory $InstallDir
    $until = (Get-Date).AddMilliseconds(1500)
    while ((Get-Date) -lt $until) { Update-UI; Start-Sleep -Milliseconds 100 }
    Close-InstallWindow

    # 5) Clean up temp artifacts and self-delete.
    Invoke-Cleanup
    Write-Log "update complete"
    exit 0
} catch {
    Write-Log "ERROR: $_"
    Stop-UpdateAndExit "exception: $_" `
        "予期しないエラーが発生しました。しばらくしてから、メニューの「ヘルプ」→「アップデートを確認」でやり直してください。"
}
