# Update Qt translation sources (.ts) from the Python code using the lupdate.exe
# that ships inside the PySide6 package.
#
# Scans main.py and src/ for QCoreApplication.translate("ctx", "text") calls with
# constant arguments (and QT_TRANSLATE_NOOP / self.tr). New messages are added to
# every Autona_*.ts as unfinished entries to translate; messages no longer in the
# code are removed. Then run .\scripts\compile_translations.ps1 to build the .qm.
#
# Usage:
#   .\scripts\update_translations.ps1

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$i18nDir = Join-Path $root "src\i18n"

$pyside = (& python -c "import PySide6,os;print(os.path.dirname(PySide6.__file__))").Trim()
if ($LASTEXITCODE -ne 0 -or -not $pyside) {
    Write-Error "PySide6 not found via python. Run: pip install -r requirements.txt"
    exit 1
}
$lupdate = Join-Path $pyside "lupdate.exe"

$tsFiles = Get-ChildItem -Path $i18nDir -Filter "Autona_*.ts" | ForEach-Object { $_.FullName }

Push-Location $root
try {
    # -locations none: the repo keeps .ts files without <location> (pinned by tests).
    & $lupdate -extensions py -locations none -no-obsolete main.py src -ts @tsFiles
    if ($LASTEXITCODE -ne 0) {
        Write-Error "lupdate failed"
        exit $LASTEXITCODE
    }
} finally {
    Pop-Location
}
