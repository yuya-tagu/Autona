# Resolve the project root from this script's location, not the caller's CWD.
# Previously this was `Push-Location ..`, which only worked when run from
# scripts/. Run from the project root (e.g. `.\scripts\gen_release.ps1`) it
# resolved `..` to the PARENT of the repo, so `Remove-Item "Autona\assets\
# versions.json"` deleted the real versions.json. $PSScriptRoot is CWD-safe
# (mirrors publish_release.ps1).
Push-Location (Join-Path $PSScriptRoot "..")

# Start from a clean slate: a leftover staging dir would make New-Item fail,
# and a leftover zip must never be appended to (see the zip note below).
Remove-Item -Path "Autona" -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -Path "release-windows.zip" -Force -ErrorAction SilentlyContinue

New-Item -Path "Autona" -ItemType Directory

# Copy ONLY git-tracked files for assets/, layout/ and user_data/. These
# trees double as runtime storage: the app writes downloaded / personal data
# into them (tournament logo layout/logo.png, designer projects layout/custom/,
# rulesets.json, logs, *.tmp, uploaded avatars, copyrighted game images...).
# A blanket folder copy shipped whatever the dev machine had fetched during
# testing, so the release must contain the tracked defaults only. Missing
# runtime files are re-seeded on first run (logo: Window.__init__; personal
# data: SettingsManager / AutonaPlayerDB).
# [System.IO.File]::Copy (not Copy-Item) because tracked filenames may
# contain PowerShell wildcard chars — Copy-Item silently skipped
# user_data\player_avatar\sponsor[space]gamerTag_png.txt. .NET needs
# absolute paths ($PWD) since its CWD is not synced with Push-Location.
foreach ($tree in "assets", "layout", "user_data") {
    git ls-files $tree | ForEach-Object {
        $rel = $_ -replace "/", "\"
        $dst = Join-Path $PWD.Path "Autona\$rel"
        New-Item -ItemType Directory -Path (Split-Path $dst) -Force | Out-Null
        [System.IO.File]::Copy((Join-Path $PWD.Path $rel), $dst, $true)
    }
}

# Tracked but already embedded inside the release exe
Remove-Item -Path "Autona\assets\versions.json" -Force

Copy-Item -Recurse -Force "web\build" "Autona\web\build"
# Legal document set: LICENSE (upstream MIT attribution), TERMS.md (end-user
# terms), THIRD-PARTY-NOTICES.md + licenses/ (third-party licenses incl. the
# LGPL v3 texts required for Qt/PySide6). Ship all of them together.
Copy-Item -Force "LICENSE" "Autona\LICENSE"
Copy-Item -Force "TERMS.md" "Autona\TERMS.md"
Copy-Item -Force "THIRD-PARTY-NOTICES.md" "Autona\THIRD-PARTY-NOTICES.md"
Copy-Item -Force "ATTRIBUTIONS.md" "Autona\ATTRIBUTIONS.md"
Copy-Item -Recurse -Force "licenses" "Autona\licenses"
Copy-Item -Force "Autona.exe" "Autona\Autona.exe"

# onedir build: ship the PyInstaller _internal/ runtime folder next to the
# launcher. Without it the ~20 MB Autona.exe launcher cannot start (the Python
# / Qt / WebEngine binaries live here). This replaced the old onefile bundle
# where everything was embedded inside a single ~250 MB Autona.exe.
Copy-Item -Recurse -Force "_internal" "Autona\_internal"

# Build the zip with .NET ZipArchive and EXPLICIT forward-slash entry names.
# Do NOT use Compress-Archive or ZipFile::CreateFromDirectory here: on Windows
# PowerShell 5.1 / .NET Framework both write entry names with backslash
# separators ("Autona\_internal\..."). The installed update helper
# (apply_update.ps1) strips the leading "Autona/" by looking for '/', so a
# backslash zip extracts ZERO files and the auto-update silently no-ops
# (this exact bug shipped as the v1.3.0 release asset and broke v1.2.x
# auto-updates). The zip spec (APPNOTE 4.4.17) mandates '/' as well.
# Both assemblies are required: FileSystem carries ZipFile/CreateEntryFromFile,
# but the [ZipArchiveMode] / [CompressionLevel] type literals live in
# System.IO.Compression, which PS 5.1 does NOT load transitively — in a fresh
# session the literal below throws "Unable to find type" without this.
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zipPath = Join-Path $PWD.Path "release-windows.zip"
$stagingRoot = Join-Path $PWD.Path "Autona"
$zip = [System.IO.Compression.ZipFile]::Open($zipPath, [System.IO.Compression.ZipArchiveMode]::Create)
try {
    Get-ChildItem -LiteralPath $stagingRoot -Recurse -File | ForEach-Object {
        $rel = $_.FullName.Substring($stagingRoot.Length + 1) -replace "\\", "/"
        [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
            $zip, $_.FullName, "Autona/$rel",
            [System.IO.Compression.CompressionLevel]::Optimal) | Out-Null
    }
} finally {
    $zip.Dispose()
}

# Self-check: separators ('/' or the update helper skips every entry) and
# content (assets/layout/user_data must ship git-tracked files only — the
# staging above guarantees it, but the validator is shared with publish so a
# stale or hand-repaired zip fails the same way).
& (Join-Path $PSScriptRoot "validate_release_zip.ps1") -ZipPath $zipPath

Pop-Location
