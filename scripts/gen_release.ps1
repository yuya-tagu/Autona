# Resolve the project root from this script's location, not the caller's CWD.
# Previously this was `Push-Location ..`, which only worked when run from
# scripts/. Run from the project root (e.g. `.\scripts\gen_release.ps1`) it
# resolved `..` to the PARENT of the repo, so `Remove-Item "Autona\assets\
# versions.json"` deleted the real versions.json. $PSScriptRoot is CWD-safe
# (mirrors publish_release.ps1).
Push-Location (Join-Path $PSScriptRoot "..")

New-Item -Path "Autona" -ItemType Directory

Copy-Item -Recurse -Force "assets" "Autona\assets"

# Already embedded inside release exe
Remove-Item -Path "Autona\assets\versions.json" -Force

# Strip runtime logs that may contain personal data (tournament URLs, etc.)
Remove-Item -Path "Autona\assets\log.txt", "Autona\assets\log_error.txt" -Force -ErrorAction SilentlyContinue

# Copy layout excluding game_images and symlinks
Get-ChildItem -Path "layout" -Recurse | Where-Object {
    $_.FullName -notmatch "layout\\game_images" -and 
    $_.FullName -notmatch "layout\\game_screenshots" -and 
    -not $_.Attributes.HasFlag([System.IO.FileAttributes]::ReparsePoint)
} | ForEach-Object {
    $destination = $_.FullName.Replace("layout", "Autona\layout")
    if ($_.PSIsContainer) {
        New-Item -ItemType Directory -Path $destination -Force
    } else {
        Copy-Item -Path $_.FullName -Destination $destination -Force
    }
}

# Copy ONLY git-tracked user_data files. This deliberately excludes both
# personal data (settings.json, local_players.json, tournament_banner.png,
# uploaded avatars/sponsor/team logos) and copyrighted game assets — all of
# which are gitignored. Missing personal files are regenerated as empty
# defaults on first run (see SettingsManager / AutonaPlayerDB).
git ls-files user_data | ForEach-Object {
    $dst = Join-Path "Autona" $_
    New-Item -ItemType Directory -Path (Split-Path $dst) -Force | Out-Null
    Copy-Item -Path $_ -Destination $dst -Force
}

Copy-Item -Recurse -Force "web\build" "Autona\web\build"
Copy-Item -Force "LICENSE" "Autona\LICENSE"
Copy-Item -Force "ATTRIBUTIONS.md" "Autona\ATTRIBUTIONS.md"
Copy-Item -Force "Autona.exe" "Autona\Autona.exe"

# onedir build: ship the PyInstaller _internal/ runtime folder next to the
# launcher. Without it the ~20 MB Autona.exe launcher cannot start (the Python
# / Qt / WebEngine binaries live here). This replaced the old onefile bundle
# where everything was embedded inside a single ~250 MB Autona.exe.
Copy-Item -Recurse -Force "_internal" "Autona\_internal"

Compress-Archive -Path "Autona" -DestinationPath "release-windows.zip" -Update

Pop-Location
