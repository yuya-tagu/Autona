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

$ErrorActionPreference = "Stop"

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

    # Refuse to clobber an existing release for this version. Use $LASTEXITCODE
    # (not $?) because native-exe stderr handling makes $? unreliable in 5.1.
    gh release view $tag --repo $Repo *> $null
    if ($LASTEXITCODE -eq 0) {
        throw "Release $tag already exists on $Repo. Bump assets/versions.json or delete the existing release first."
    }

    Write-Host "Publishing release $tag to $Repo ..."
    gh release create $tag "release-windows.zip" --repo $Repo --title $tag --notes $Notes
    if ($LASTEXITCODE -ne 0) {
        throw "gh release create failed. Ensure gh is authenticated with write access to $Repo (see CLAUDE.md)."
    }

    Write-Host "Done. Published $tag with release-windows.zip to $Repo."
}
finally {
    Pop-Location
}
