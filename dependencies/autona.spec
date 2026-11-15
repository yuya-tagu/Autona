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
#       ('../web/build/*', 'web/build'),
        ('../src/TournamentDataProvider/*.txt', 'src/TournamentDataProvider')
    ],
    hookspath=[],
    runtime_hooks=[],
    # Packages that only enter the graph through optional, try/ImportError-
    # guarded or TYPE_CHECKING-only imports in third-party code. Nothing under
    # src/ or web/ imports any of them (verified by grep + the build's own
    # xref-autona.html import graph), so excluding them just stops PyInstaller
    # collecting ~55 MB of dead weight that the launcher would page in.
    #
    #   pandas   <- deepdiff.deephash (`try: import pandas`). StateManager
    #               already forces that import to fail at runtime
    #               (sys.modules.setdefault("pandas", None)).
    #   numpy    <- deepdiff.helper / deephash, both guarded; the no-numpy
    #               branch defines the np_* stubs the rest of deepdiff imports.
    #               Everything else reaching it came via pandas.
    #   openpyxl <- pandas.io.excel only.
    #   pytest / _pytest / pluggy / iniconfig <- pandas._testing,
    #               numpy.testing. Test-only, never imported by the app.
    #   cryptography <- werkzeug.serving (TYPE_CHECKING + ad-hoc-TLS helpers),
    #               flask.cli, requests (SNI fallback), redis — all optional.
    #               HTTPS keeps working: requests uses the stdlib ssl module.
    #   pydantic / pydantic_core / annotated_types <- deepdiff.serialization
    #               (guarded) and yarl._url (find_spec probe + function-local
    #               import), which degrade cleanly when absent.
    #   redis    <- socketio.redis_manager, guarded; only used for the
    #               multi-process message queue, which Autona does not use
    #               (async_mode='threading', single process).
    #
    # NOT excluded on purpose: py7zr + Cryptodome (game-asset download), and
    # tzdata (stdlib zoneinfo has no tz database on Windows without it, and
    # orjson pulls zoneinfo in) — 1.6 MB is not worth the risk.
    #
    # Qt modules (ADR-005 S-final SF-9). The app imports QtCore, QtGui,
    # QtWidgets, QtNetwork, QtWebEngineCore and QtWebEngineWidgets (pinned by
    # tests/test_release_bundle.py); QWebEngineView also imports QtPrintSupport,
    # QtWebChannel and QtNetwork, and qdarktheme QtSvg. These come in anyway:
    #   QtQuick / QtQuickWidgets / QtPositioning <- PyInstaller's Qt hook turns
    #               Qt6WebEngineCore.dll / Qt6WebEngineWidgets.dll's link-time
    #               dependencies into hidden imports; the QtQuick hook then
    #               pulls QtQml, whose hook collects the whole PySide6/qml/
    #               tree (~27 MB), every QML plugin and the DLLs those link
    #               (Quick3D, 3D, Graphs, Charts, Location, Multimedia,
    #               QuickControls2, ...: ~77 MB) and plugins/qmltooling.
    #               QWebEngineView renders through C++ items; it reads no QML.
    #   QtOpenGL / QtOpenGLWidgets / QtDataVisualization <- qtpy (QtGui,
    #               QtWidgets, qtpy/__init__) tries them inside
    #               try/except ImportError or contextlib.suppress(ImportError).
    #   QtUiTools <- qtpy.uic, which nothing imports.
    # Excluding a Python module does not touch PyInstaller's binary dependency
    # analysis: Qt6Quick, Qt6Qml, Qt6QmlMeta, Qt6QmlModels, Qt6QuickWidgets,
    # Qt6OpenGL, Qt6WebChannel, Qt6Positioning, Qt6PrintSupport and
    # Qt6Network are still collected as what Qt6WebEngineCore.dll /
    # Qt6WebEngineWidgets.dll link against. scripts/validate_release_zip.ps1
    # fails a release that lacks any of them.
    excludes=[
        'pandas',
        'numpy',
        'openpyxl',
        'pytest',
        '_pytest',
        'pluggy',
        'iniconfig',
        'cryptography',
        'pydantic',
        'pydantic_core',
        'annotated_types',
        'redis',
        'PySide6.QtQuick',
        'PySide6.QtQml',
        'PySide6.QtQuickWidgets',
        'PySide6.QtPositioning',
        'PySide6.QtUiTools',
        'PySide6.QtDataVisualization',
        'PySide6.QtOpenGL',
        'PySide6.QtOpenGLWidgets',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False
)

# Files the Qt hooks collect that the app never opens (ADR-005 S-final SF-9),
# dropped from the analysed TOCs by destination path. The excludes above
# cannot reach them: they are data or plugin files, not modules.
#   * PySide6/resources/*.debug.pak, v8_context_snapshot.debug.bin: the
#     debug build's WebEngine resources. The release Qt6WebEngineCore.dll
#     names only qtwebengine_resources(_100p/_200p).pak,
#     qtwebengine_devtools_resources.pak, v8_context_snapshot.bin and
#     icudtl.dat, which stay.
#   * PySide6/translations/*.qm: Qt's own translations. The app installs no
#     translator but its src/i18n/Autona_*.qm (AutonaLocaleHelper.LoadLocale).
#   * PySide6/translations/qtwebengine_locales/ except en-US / ja: Chromium's
#     UI strings. For another UI language it falls back to en-US.pak.
#   * libssl-3-x64.dll / libcrypto-3-x64.dll and the TLS plugin that loads
#     them: the QtNetwork hook looks OpenSSL 3 up on PATH and finds Git for
#     Windows' mingw64 build (linked against msvcrt). The app's QtNetwork is
#     QLocalSocket / QLocalServer (main.py), with no TLS, and QtWebEngine has
#     its own. Python's ssl module loads libssl-3.dll / libcrypto-3.dll
#     (_ssl.pyd's), which stay, as do the schannel / certonly TLS backends.
# validate_release_zip.ps1 forbids every one of these, and
# tests/test_release_bundle.py holds the two lists to each other.
_KEPT_WEBENGINE_LOCALES = ('en-US.pak', 'ja.pak')
_DROPPED_FILES = (
    'libssl-3-x64.dll',
    'libcrypto-3-x64.dll',
    'PySide6/plugins/tls/qopensslbackend.dll',
)


def _unused_qt_file(dest_name):
    # TOC destinations use the OS separator; Windows names are case-blind.
    path = dest_name.replace('\\', '/').lower()
    folder, _, name = path.rpartition('/')
    if folder == 'pyside6/resources':
        return '.debug.' in name
    if folder == 'pyside6/translations':
        return name.endswith('.qm')
    if folder == 'pyside6/translations/qtwebengine_locales':
        return name not in {kept.lower() for kept in _KEPT_WEBENGINE_LOCALES}
    return path in {dropped.lower() for dropped in _DROPPED_FILES}


a.datas = [entry for entry in a.datas if not _unused_qt_file(entry[0])]
a.binaries = [entry for entry in a.binaries if not _unused_qt_file(entry[0])]

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
