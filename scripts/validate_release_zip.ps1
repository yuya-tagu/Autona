<#
.SYNOPSIS
    Validate a release-windows.zip before it may be published.

.DESCRIPTION
    Two checks, each born from a shipped incident:

    1. Separator check: every zip entry name must contain '/' separators.
       The installed update helper (assets/apply_update.ps1) strips the
       leading "Autona/" by looking for '/', so a backslash-separator zip
       extracts ZERO files and the auto-update silently no-ops (shipped as
       the original v1.3.0 asset).

    2. Content check: every file entry under assets/, layout/ and user_data/
       must be a git-tracked file. gen_release.ps1 stages tracked files only,
       but a hand-repaired or stale zip bypasses that guard: the replacement
       v1.3.0 asset was derived from a pre-cleanup zip and shipped
       dev-fetched data (test tournament icon layout/logo.png, designer test
       project layout/custom/, stale assets/rulesets.json). This check makes
       such a zip fail loudly instead.

    Run automatically by gen_release.ps1 and publish_release.ps1. When
    publishing manually with `gh release create` (release runbook step 8),
    run this script on the zip first.

.PARAMETER ZipPath
    Path to the zip to validate. Defaults to release-windows.zip at the
    project root.
#>
param(
    [string]$ZipPath = "release-windows.zip"
)

# No global $ErrorActionPreference = "Stop": PS 5.1 would turn git's stderr
# writes into terminating errors (same reasoning as publish_release.ps1).
Push-Location (Join-Path $PSScriptRoot "..")
try {
    if (-not (Test-Path $ZipPath)) {
        throw "validate_release_zip: '$ZipPath' not found."
    }

    # The tracked-file set is the contract for what the three runtime trees
    # may ship (see gen_release.ps1). Exact string compare — no wildcards —
    # so bracketed filenames like sponsor[space]gamerTag_png.txt are safe.
    $tracked = New-Object 'System.Collections.Generic.HashSet[string]'
    git ls-files assets layout user_data | ForEach-Object { [void]$tracked.Add($_) }
    if ($LASTEXITCODE -ne 0 -or $tracked.Count -eq 0) {
        throw "validate_release_zip: 'git ls-files' failed or returned nothing (run from the repo)."
    }

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [System.IO.Compression.ZipFile]::OpenRead((Resolve-Path $ZipPath).Path)
    try {
        $entries = @($zip.Entries)

        $badSep = @($entries | Where-Object { $_.FullName -notmatch "/" })
        if ($badSep.Count -gt 0) {
            throw ("release zip has {0} entries without '/' separators (update helper would skip them), e.g. '{1}'" `
                -f $badSep.Count, $badSep[0].FullName)
        }

        # Skip directory placeholder entries (trailing '/').
        $leaked = @($entries | Where-Object {
                $_.FullName -match "^Autona/(assets|layout|user_data)/" -and
                $_.FullName -notmatch "/$" -and
                -not $tracked.Contains(($_.FullName -replace "^Autona/", ""))
            })
        if ($leaked.Count -gt 0) {
            $sample = ($leaked | Select-Object -First 5 | ForEach-Object { $_.FullName }) -join ", "
            throw ("release zip ships {0} file(s) under assets/layout/user_data that are not git-tracked " `
                + "(dev/runtime data must not be distributed): {1}") -f $leaked.Count, $sample
        }

        Write-Host ("{0} OK: {1} entries, all '/' separators, runtime trees contain tracked files only" `
            -f (Split-Path $ZipPath -Leaf), $entries.Count)
    } finally {
        $zip.Dispose()
    }
} finally {
    Pop-Location
}
