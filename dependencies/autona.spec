# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['../main.py'],
    pathex=[],
    binaries=[],
    hiddenimports=[
        'loguru',
        'engineio.async_drivers.threading'
    ],
    datas=[
        ('../assets/versions.json', 'assets'),
        ('../src/i18n/tournament_term/*.json', 'src/i18n/tournament_term'),
#       ('../src/i18n/round_names/*.json', 'src/i18n/round_names'),
        ('../src/i18n/*.json', 'src/i18n'),
        ('../src/i18n/*.qm', 'src/i18n'),
        ('../src/layout/*', 'src/layout'),
#       ('../web/build/*', 'web/build'),
        ('../src/TournamentDataProvider/*.txt', 'src/TournamentDataProvider')
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False
)
pyz = PYZ(
    a.pure,
    a.zipped_data,
    cipher=block_cipher
)
# onedir build: the EXE is a small (~20 MB) launcher and the Python runtime +
# Qt/WebEngine binaries live in the sibling _internal/ folder, loaded directly
# at launch. This avoids the onefile bootloader extracting the entire ~250 MB
# bundle to %TEMP% on every start, which cost ~8-11s of pure startup overhead
# (measured: onefile ~17.5s -> onedir ~3.5s to interactive).
# UPX is off — it is not installed on the build machine (upx=True was a no-op),
# and onedir gains nothing from it.
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Autona',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['../assets/icons/icon.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='Autona'
)
