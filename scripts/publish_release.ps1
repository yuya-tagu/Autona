<#
.SYNOPSIS
    Publish the built release-windows.zip as a GitHub Release on the public
    distribution repo (yuya-tagu/Autona-releases).

.DESCRIPTION
    The Autona source repo (yuya-tagu/Autona) is private, so the in-app updater
    (src/AutonaUpdater.py) reads releases from a separate PUBLIC repo. This
    script uploads the locally built ZIP there as a published Release so that
    anonymous clients (the distributed exe) can fetch /releases/latest and
    download the asset.

    Prerequisites:
      1. Build the app locally first so release-windows.zip exists at the
         project root (run scripts/gen_release.ps1, which needs Autona.exe and
         web/build present).
      2. gh CLI authenticated as an account with write access to the release
         repo. If you use multiple gh accounts, switch to the one with access
         before running (machine-specific steps: CLAUDE.local.md).

    The release tag is derived from assets/versions.json ("program") as
    v<version>, which is exactly what AutonaUpdater compares against the
    local version.

.PARAMETER Repo
    Target release repo. Defaults to yuya-tagu/Autona-releases.

.PARAMETER Notes
    Release notes body. Defaults to "Autona v<version>".

.EXAMPLE
    .\scripts\publish_release.ps1
#>
param(
    [string]$Repo = "yuya-tagu/Autona-releases",
    [string]$Notes = ""
)

# Do NOT set $ErrorActionPreference = "Stop" globally: in Windows PowerShell
# 5.1 that turns the gh CLI's stderr writes (upload progress, "release not
# found", etc.) into terminating errors. We check $LASTEXITCODE explicitly
# after each native gh call instead, and `throw` by hand where needed.

# This script lives in scripts/; run everything from the project root so the
# zip and versions.json resolve regardless of the caller's CWD.
Push-Location (Join-Path $PSScriptRoot "..")
try {
    if (-not (Test-Path "release-windows.zip")) {
        throw "release-windows.zip not found at project root. Build the app and run scripts/gen_release.ps1 first."
    }

    # Refuse to publish a zip that carries dev/runtime data or bad entry
    # separators. gen_release.ps1 builds clean zips, but this gate is what
    # catches a stale or hand-repaired zip (the replacement v1.3.0 asset
    # shipped a test tournament icon exactly this way).
    & (Join-Path $PSScriptRoot "validate_release_zip.ps1") -ZipPath "release-windows.zip"

    $version = (Get-Content "assets/versions.json" -Raw | ConvertFrom-Json).program
    if ([string]::IsNullOrWhiteSpace($version)) {
        throw "Could not read 'program' version from assets/versions.json."
    }
    $tag = "v$version"

    if ([string]::IsNullOrWhiteSpace($Notes)) {
        $Notes = "Autona $tag"
    }

    # Stamp the Qt build into the release notes as a machine-readable marker.
    # The in-app updater (AutonaUpdater._OnNewVersion) reads this BEFORE
    # downloading: if the release's Qt differs from the running app's Qt, the
    # onedir in-place updater can't swap the mmap'd WebEngine resources, so it
    # refuses the auto-update and tells the user to reinstall manually instead
    # of shipping a Qt mismatch that crashes WebEngine. Derived from the runtime
    # here, so it always matches the build published from this same machine.
    $qtBuild = (python -c "from qtpy.QtCore import qVersion; print(qVersion())" 2>$null)
    if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace($qtBuild)) {
        $Notes = $Notes.TrimEnd() + "`n`nQt-Build: " + $qtBuild.Trim()
    } else {
        Write-Warning "Could not determine Qt version; release will ship without a Qt-Build fingerprint (updater can't gate a Qt mismatch for this release)."
    }

    # gh release create fails on its own if a release with this tag already
    # exists, so we rely on its exit code rather than a pre-check (a pre-check
    # with `gh release view` would emit "release not found" to stderr on the
    # normal first-publish path).
    Write-Host "Publishing release $tag to $Repo ..."
    gh release create $tag "release-windows.zip" --repo $Repo --title $tag --notes $Notes
    if ($LASTEXITCODE -ne 0) {
        throw "gh release create failed (exit $LASTEXITCODE). Ensure gh is authenticated with write access to $Repo, and that release $tag doesn't already exist (see CLAUDE.md)."
    }

    Write-Host "Done. Published $tag with release-windows.zip to $Repo."
}
finally {
    Pop-Location
}
