# Resolve the project root from this script's location, not the caller's CWD.
# Previously this was `Push-Location ..`, which only worked when run from
# scripts/. Run from the project root (e.g. `.\scripts\gen_release.ps1`) it
# resolved `..` to the PARENT of the repo, so `Remove-Item "Autona\assets\
# versions.json"` deleted the real versions.json. $PSScriptRoot is CWD-safe
# (mirrors publish_release.ps1).
Push-Location (Join-Path $PSScriptRoot "..")

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

Compress-Archive -Path "Autona" -DestinationPath "release-windows.zip" -Update

Pop-Location
