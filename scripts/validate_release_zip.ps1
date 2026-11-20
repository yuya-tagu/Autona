<#
.SYNOPSIS
    Validate a release-windows.zip before it may be published.

.DESCRIPTION
    Six checks. The first three were each born from a shipped incident:

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
       such a zip fail loudly instead. The shared Designer runtime
       (layout/designer_runtime/) is tracked and therefore allowed; designer
       projects (layout/custom/) are gitignored and therefore still rejected.

    3. Required-entry check: layout/designer_runtime/runtime.js and
       runtime.css must be in the zip. Every Designer design's index.html
       is a stub that loads them, so a release without them would render
       every user design blank in OBS. So must layout/include/autona.js and
       its table overlay_contract.json (ADR-008 D1): every overlay written
       against the overlay contract loads the first, and the kit reads the
       second.

    4. Persisted-state check: user_data/state.json (the operator's saved
       state, ADR-005 S3b) and its set-aside copies (state.json.*.bak) must
       never be in the zip. The update helper copies the zip over the
       install without asking, so one shipped copy would replace every
       installation's saved state with the build machine's. Check 2 already
       rejects them while they stay untracked; this one also holds if one
       is ever committed by mistake.

    5. Qt bundle check (ADR-005 S-final SF-9): packaging/autona.spec
       keeps out what the Qt hooks collect but the app never opens (QML,
       the debug build's WebEngine resources, Qt's own translations,
       WebEngine locales other than en-US / ja, Git for Windows' OpenSSL).
       A PyInstaller or PySide6 upgrade can change what the hooks collect,
       so the zip fails when (a) a file the runtime needs is missing
       (QtWebEngine and the Qt DLLs it links against, Python's OpenSSL),
       (b) a trimmed file is back, or (c) _internal grew past the size cap.
       tests/test_release_bundle.py holds these lists to the spec.

    6. Worker check (ADR-006 A7): nothing under vodsplit-worker/ (nor its
       install's leftovers vodsplit-worker.part / .new / .old). The archive
       clipper's worker is distributed on its own (a worker-v release) and
       installed into Autona's folder by Autona; the update helper copies
       the zip over the install without asking, so a worker in it would
       overwrite the one the operator installed (another version, maybe a
       format this Autona's page no longer matches).

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

# Check 5 (a): files under _internal/ the runtime needs. The Qt DLLs are what
# Qt6WebEngineCore.dll / Qt6WebEngineWidgets.dll link against: the spec
# excludes the QtQuick / QtQml / ... Python modules, never these DLLs. Also
# kept on purpose: the schannel TLS backend (Qt's TLS once the OpenSSL one is
# dropped), opengl32sw.dll (the software OpenGL fallback, SF-9 decision G) and
# the qt.conf PyInstaller writes (where Qt finds its plugins and resources).
$qtRequired = @(
    "PySide6/Qt6Core.dll",
    "PySide6/Qt6Gui.dll",
    "PySide6/Qt6Widgets.dll",
    "PySide6/Qt6Network.dll",
    "PySide6/Qt6Svg.dll",
    "PySide6/Qt6PrintSupport.dll",
    "PySide6/Qt6OpenGL.dll",
    "PySide6/Qt6Positioning.dll",
    "PySide6/Qt6WebChannel.dll",
    "PySide6/Qt6Qml.dll",
    "PySide6/Qt6QmlMeta.dll",
    "PySide6/Qt6QmlModels.dll",
    "PySide6/Qt6Quick.dll",
    "PySide6/Qt6QuickWidgets.dll",
    "PySide6/Qt6WebEngineCore.dll",
    "PySide6/Qt6WebEngineWidgets.dll",
    "PySide6/QtWebEngineProcess.exe",
    "PySide6/QtCore.pyd",
    "PySide6/QtGui.pyd",
    "PySide6/QtWidgets.pyd",
    "PySide6/QtNetwork.pyd",
    "PySide6/QtSvg.pyd",
    "PySide6/QtPrintSupport.pyd",
    "PySide6/QtWebChannel.pyd",
    "PySide6/QtWebEngineCore.pyd",
    "PySide6/QtWebEngineWidgets.pyd",
    "PySide6/resources/icudtl.dat",
    "PySide6/resources/qtwebengine_resources.pak",
    "PySide6/resources/qtwebengine_resources_100p.pak",
    "PySide6/resources/qtwebengine_resources_200p.pak",
    "PySide6/resources/qtwebengine_devtools_resources.pak",
    "PySide6/resources/v8_context_snapshot.bin",
    "PySide6/translations/qtwebengine_locales/en-US.pak",
    "PySide6/translations/qtwebengine_locales/ja.pak",
    "PySide6/plugins/platforms/qwindows.dll",
    "PySide6/plugins/iconengines/qsvgicon.dll",
    "PySide6/plugins/imageformats/qsvg.dll",
    "PySide6/plugins/tls/qschannelbackend.dll",
    "PySide6/opengl32sw.dll",
    "PySide6/qt.conf",
    "libssl-3.dll",
    "libcrypto-3.dll"
)

# Check 5 (b): what the spec keeps out, as case-insensitive regexes on the
# path under _internal/ (the key) with the reason shown on failure. The first
# two are the spec's excludes taking effect (the Python modules themselves);
# the OpenSSL names match in any folder, as the spec's filter drops them.
$qtForbidden = [ordered]@{
    '^PySide6/Qt(Quick|Qml|QuickWidgets|Positioning|UiTools|DataVisualization|OpenGL|OpenGLWidgets)\.pyd$' = "Python modules the spec excludes"
    '^PySide6/pyside6qml\.abi3\.dll$'                              = "PySide6.QtQml's runtime (the spec excludes PySide6.QtQml)"
    '^PySide6/qml/'                                                = "QML (the spec excludes PySide6.QtQuick / QtQml)"
    '^PySide6/plugins/qmltooling/'                                 = "QML debugger plugins (the spec excludes PySide6.QtQml)"
    '^PySide6/Qt6Quick3D'                                          = "Quick3D DLLs (QML plugins' dependencies)"
    '^PySide6/Qt63D'                                               = "Qt 3D DLLs (QML plugins' dependencies)"
    '^PySide6/Qt6Graphs'                                           = "Qt Graphs DLLs (QML plugins' dependencies)"
    '^PySide6/resources/[^/]+\.debug\.pak$'                        = "debug-build WebEngine resources"
    '^PySide6/resources/[^/]+\.debug\.bin$'                        = "debug-build WebEngine resources"
    '^PySide6/translations/[^/]+\.qm$'                             = "Qt's own translations (the app loads src/i18n only)"
    '^PySide6/translations/qtwebengine_locales/(?!(en-US|ja)\.pak$)' = "WebEngine locales other than en-US / ja"
    '(^|/)libssl-3-x64\.dll$'                                      = "Git for Windows' OpenSSL (Python's is libssl-3.dll)"
    '(^|/)libcrypto-3-x64\.dll$'                                   = "Git for Windows' OpenSSL (Python's is libcrypto-3.dll)"
    '(^|/)qopensslbackend\.dll$'                                   = "Qt's OpenSSL TLS backend (needs the OpenSSL above)"
}

# Check 5 (c): the uncompressed size of _internal/, the backstop for what (b)
# does not name (a module a newer hook starts collecting). Measured 344.4 MiB
# on the 2026-11-16 build (PySide6 6.11.0; 571 MiB before SF-9). Re-set it to
# the measured size plus headroom when a build moves it.
$internalMaxMiB = 390

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

        # Check 4 runs before the tracked-file check so a leaked store is
        # reported as what it is. Independent of git tracking (see the
        # header); -match is case-insensitive, as Windows resolves the names.
        $persisted = @($entries | Where-Object {
                $_.FullName -match "^Autona/user_data/state\.json($|\..*\.bak$)"
            })
        if ($persisted.Count -gt 0) {
            $sample = ($persisted | Select-Object -First 5 | ForEach-Object { $_.FullName }) -join ", "
            throw ("release zip ships the persisted state (would overwrite every installation's saved state): {0}" `
                -f $sample)
        }

        # Check 6: the worker is installed by Autona, never shipped in its
        # zip (see the header). Case-insensitive, as Windows resolves it.
        $worker = @($entries | Where-Object {
                $_.FullName -match "^Autona/vodsplit-worker(/|\.(part|new|old)(/|$))"
            })
        if ($worker.Count -gt 0) {
            $sample = ($worker | Select-Object -First 5 | ForEach-Object { $_.FullName }) -join ", "
            throw ("release zip ships the archive clipper's worker (it is installed separately and would overwrite the installed one): {0}" `
                -f $sample)
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

        $entryNames = New-Object 'System.Collections.Generic.HashSet[string]'
        $entries | ForEach-Object { [void]$entryNames.Add($_.FullName) }
        $missing = @(@(
                "Autona/layout/designer_runtime/runtime.js",
                "Autona/layout/designer_runtime/runtime.css",
                "Autona/layout/include/autona.js",
                "Autona/layout/include/overlay_contract.json"
            ) | Where-Object { -not $entryNames.Contains($_) })
        if ($missing.Count -gt 0) {
            throw ("release zip is missing the shared Designer runtime or the overlay contract (custom designs and overlays load them): {0}" `
                -f ($missing -join ", "))
        }

        # Check 5. Entry names are compared case-insensitively, as Windows
        # resolves them; directory placeholders (trailing '/') are skipped.
        $internalFiles = @($entries | Where-Object {
                $_.FullName -match "^Autona/_internal/" -and $_.FullName -notmatch "/$"
            })
        $internalNames = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
        $internalFiles | ForEach-Object { [void]$internalNames.Add(($_.FullName -replace "^Autona/_internal/", "")) }

        $qtMissing = @($qtRequired | Where-Object { -not $internalNames.Contains($_) })
        if ($qtMissing.Count -gt 0) {
            throw ("release zip lacks {0} file(s) the Qt / WebEngine runtime needs under _internal/: {1}" `
                -f $qtMissing.Count, ($qtMissing -join ", "))
        }

        foreach ($pattern in $qtForbidden.Keys) {
            $back = @($internalNames | Where-Object { $_ -match $pattern })
            if ($back.Count -gt 0) {
                $sample = ($back | Select-Object -First 5) -join ", "
                throw ("release zip ships {0} file(s) packaging/autona.spec keeps out ({1}): {2}" `
                    -f $back.Count, $qtForbidden[$pattern], $sample)
            }
        }

        $internalBytes = ($internalFiles | Measure-Object -Property Length -Sum).Sum
        $internalMiB = $internalBytes / 1MB
        if ($internalMiB -gt $internalMaxMiB) {
            throw ("release zip's _internal/ is {0:N1} MiB uncompressed, over the {1} MiB cap " `
                + "(a hook started collecting something; see check 5)") -f $internalMiB, $internalMaxMiB
        }

        Write-Host ((("{0} OK: {1} entries, all '/' separators, runtime trees contain tracked files only, " `
                + "Qt bundle as trimmed (_internal {2:N1} MiB)") -f (Split-Path $ZipPath -Leaf), $entries.Count, $internalMiB))
    } finally {
        $zip.Dispose()
    }
} finally {
    Pop-Location
}
