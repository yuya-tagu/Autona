# Compile Qt translation files (.ts -> .qm) using the lrelease.exe that ships
# inside the PySide6 package (found through `python`, so the venv need not be
# activated as long as the `python` on PATH has PySide6 installed).
#
# Usage:
#   .\scripts\compile_translations.ps1           # Compile all .ts files
#   .\scripts\compile_translations.ps1 -Lang ja  # Compile only Autona_ja.ts

[CmdletBinding()]
param(
    [string]$Lang = "all"
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$pyside = (& python -c "import PySide6,os;print(os.path.dirname(PySide6.__file__))").Trim()
if ($LASTEXITCODE -ne 0 -or -not $pyside) {
    Write-Error "PySide6 not found via python. Run: pip install -r requirements.txt"
    exit 1
}
$lrelease = Join-Path $pyside "lrelease.exe"
$i18nDir = Join-Path $root "src\i18n"

if (-not (Test-Path $lrelease)) {
    Write-Error "lrelease.exe not found at $lrelease"
    exit 1
}

if ($Lang -eq "all") {
    $tsFiles = Get-ChildItem -Path $i18nDir -Filter "Autona_*.ts"
} else {
    $tsFiles = @(Get-Item -Path (Join-Path $i18nDir "Autona_$Lang.ts"))
}

foreach ($ts in $tsFiles) {
    $qm = [System.IO.Path]::ChangeExtension($ts.FullName, ".qm")
    & $lrelease $ts.FullName -qm $qm
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Failed to compile $($ts.Name)"
        exit $LASTEXITCODE
    }
}
