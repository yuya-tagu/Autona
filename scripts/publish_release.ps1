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
         repo (yuya-tagu). On this machine the active gh account may be
         yuyat-highfield; switch with `gh auth switch --user yuya-tagu` before
         running and switch back afterwards (see CLAUDE.md).

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

    $version = (Get-Content "assets/versions.json" -Raw | ConvertFrom-Json).program
    if ([string]::IsNullOrWhiteSpace($version)) {
        throw "Could not read 'program' version from assets/versions.json."
    }
    $tag = "v$version"

    if ([string]::IsNullOrWhiteSpace($Notes)) {
        $Notes = "Autona $tag"
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
