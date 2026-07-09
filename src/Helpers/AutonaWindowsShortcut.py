"""Register a Start Menu shortcut tagged with our AppUserModelID so Windows 11
resolves the taskbar icon from it.

Root cause this addresses: on Win11 a process that declares an explicit
AppUserModelID has its grouped taskbar button icon resolved from a registered
Start Menu shortcut whose ``System.AppUserModel.ID`` matches that AppID. The
per-window HICON set via ``setWindowIcon`` is only a best-effort fallback that
Win11 latches at taskbar-button-creation time; during Autona's heavy startup
(splash -> WebEngine/qasync/restoreState -> main window) the button is created
before the HICON is in place and latches the generic icon. Giving Win11 an
authoritative AppID -> shortcut -> icon mapping is the mechanism VS Code,
Discord and Slack use, and the reliable fix here.

Implemented with raw ctypes COM (IShellLinkW + IPropertyStore + IPersistFile)
to avoid pulling in pywin32 as a dependency. Windows-only; every failure is
swallowed and logged because the app must start even if the shortcut cannot be
written.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import sys
from pathlib import Path

from loguru import logger

# Must match the AppUserModelID set at the very top of main.py (before
# `import src`). Keep the two literals in sync.
APP_USER_MODEL_ID = "taguyan.Autona"

_SHORTCUT_NAME = "Autona.lnk"

# ── GUID / PROPERTYKEY / PROPVARIANT plumbing ────────────────────


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    def __init__(self, guid_str: str | None = None) -> None:
        super().__init__()
        if guid_str is not None:
            # CLSIDFromString parses "{........-....-....-....-............}".
            ctypes.windll.ole32.CLSIDFromString(
                ctypes.c_wchar_p(guid_str), ctypes.byref(self))


class _PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", _GUID), ("pid", wintypes.DWORD)]


class _PROPVARIANT(ctypes.Structure):
    # vt + 3 reserved WORDs (8 bytes) then the value union. For VT_LPWSTR the
    # union holds a single CoTaskMem-allocated wide-string pointer at offset 8.
    # Two pointer slots pad the struct to its full 24-byte x64 size (16 on x86)
    # so PropVariantClear reads/writes within bounds.
    _fields_ = [
        ("vt", ctypes.c_ushort),
        ("wReserved1", ctypes.c_ushort),
        ("wReserved2", ctypes.c_ushort),
        ("wReserved3", ctypes.c_ushort),
        ("pwsz", ctypes.c_void_p),
        ("_pad", ctypes.c_void_p),
    ]


_VT_LPWSTR = 31


# Interface IIDs / class CLSID.
_CLSID_ShellLink = "{00021401-0000-0000-C000-000000000046}"
_IID_IShellLinkW = "{000214F9-0000-0000-C000-000000000046}"
_IID_IPersistFile = "{0000010B-0000-0000-C000-000000000046}"
_IID_IPropertyStore = "{886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99}"

# PKEY_AppUserModel_ID = fmtid {9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3}, pid 5.
_PKEY_AppUserModel_ID = _PROPERTYKEY(
    _GUID("{9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3}"), 5)

# vtable indices (IUnknown occupies 0..2: QueryInterface/AddRef/Release).
_IDX_QueryInterface = 0
_IDX_Release = 2
# IShellLinkW
_IDX_SL_SetWorkingDirectory = 9
_IDX_SL_SetArguments = 11
_IDX_SL_SetIconLocation = 17
_IDX_SL_SetPath = 20
# IPropertyStore
_IDX_PS_SetValue = 6
_IDX_PS_Commit = 7
# IPersistFile
_IDX_PF_Save = 6

_CLSCTX_INPROC_SERVER = 1
_COINIT_APARTMENTTHREADED = 0x2


def _method(this: ctypes.c_void_p, index: int, restype, argtypes):
    """Return a callable bound to vtable slot ``index`` of COM object ``this``."""
    vtbl = ctypes.cast(this, ctypes.POINTER(ctypes.c_void_p))[0]
    func_addr = ctypes.cast(vtbl, ctypes.POINTER(ctypes.c_void_p))[index]
    proto = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
    return proto(func_addr)


def _hr(value: int, what: str) -> None:
    if value < 0:
        raise OSError(f"{what} failed: 0x{value & 0xFFFFFFFF:08X}")


def _release(obj: ctypes.c_void_p | None) -> None:
    if obj:
        try:
            _method(obj, _IDX_Release, ctypes.c_ulong, [])(obj)
        except Exception:
            pass


def _query_interface(this: ctypes.c_void_p, iid_str: str) -> ctypes.c_void_p:
    iid = _GUID(iid_str)
    out = ctypes.c_void_p()
    _hr(
        _method(this, _IDX_QueryInterface, ctypes.c_long,
                [ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)])(
            this, ctypes.byref(iid), ctypes.byref(out)),
        "QueryInterface")
    return out


def _write_shortcut(path: str, target: str, arguments: str, workdir: str,
                    icon_path: str, icon_index: int, appid: str) -> None:
    ole32 = ctypes.windll.ole32

    init_hr = ole32.CoInitializeEx(None, _COINIT_APARTMENTTHREADED)
    # S_OK (0) and S_FALSE (1) both require a matching CoUninitialize;
    # RPC_E_CHANGED_MODE (already init in MTA) does not.
    need_uninit = init_hr in (0, 1)

    ole32.CoTaskMemAlloc.restype = ctypes.c_void_p
    ole32.CoTaskMemAlloc.argtypes = [ctypes.c_size_t]

    psl = pps = ppf = None
    propvar = _PROPVARIANT()
    propvar_inited = False
    try:
        clsid = _GUID(_CLSID_ShellLink)
        iid_sl = _GUID(_IID_IShellLinkW)
        psl = ctypes.c_void_p()
        _hr(
            ole32.CoCreateInstance(
                ctypes.byref(clsid), None, _CLSCTX_INPROC_SERVER,
                ctypes.byref(iid_sl), ctypes.byref(psl)),
            "CoCreateInstance(ShellLink)")

        _hr(_method(psl, _IDX_SL_SetPath, ctypes.c_long, [wintypes.LPCWSTR])(
            psl, target), "IShellLink::SetPath")
        if arguments:
            _hr(_method(psl, _IDX_SL_SetArguments, ctypes.c_long,
                        [wintypes.LPCWSTR])(psl, arguments),
                "IShellLink::SetArguments")
        if workdir:
            _hr(_method(psl, _IDX_SL_SetWorkingDirectory, ctypes.c_long,
                        [wintypes.LPCWSTR])(psl, workdir),
                "IShellLink::SetWorkingDirectory")
        _hr(_method(psl, _IDX_SL_SetIconLocation, ctypes.c_long,
                    [wintypes.LPCWSTR, ctypes.c_int])(
            psl, icon_path, icon_index), "IShellLink::SetIconLocation")

        # Stamp the AppUserModelID onto the link's property store. Build the
        # VT_LPWSTR PROPVARIANT by hand (propsys.InitPropVariantFromString is
        # not exported in every Windows build); the string is CoTaskMem-alloc'd
        # so PropVariantClear frees it for us.
        pps = _query_interface(psl, _IID_IPropertyStore)
        nbytes = (len(appid) + 1) * 2
        buf = ole32.CoTaskMemAlloc(nbytes)
        if not buf:
            raise OSError("CoTaskMemAlloc failed")
        ctypes.memmove(buf, ctypes.create_unicode_buffer(appid), nbytes)
        propvar.vt = _VT_LPWSTR
        propvar.pwsz = buf
        propvar_inited = True
        _hr(_method(pps, _IDX_PS_SetValue, ctypes.c_long,
                    [ctypes.POINTER(_PROPERTYKEY), ctypes.POINTER(_PROPVARIANT)])(
            pps, ctypes.byref(_PKEY_AppUserModel_ID), ctypes.byref(propvar)),
            "IPropertyStore::SetValue")
        _hr(_method(pps, _IDX_PS_Commit, ctypes.c_long, [])(pps),
            "IPropertyStore::Commit")

        # Persist the .lnk to disk.
        ppf = _query_interface(psl, _IID_IPersistFile)
        _hr(_method(ppf, _IDX_PF_Save, ctypes.c_long,
                    [wintypes.LPCWSTR, ctypes.c_int])(ppf, path, 1),
            "IPersistFile::Save")
    finally:
        if propvar_inited:
            try:
                ole32.PropVariantClear(ctypes.byref(propvar))
            except Exception:
                pass
        _release(ppf)
        _release(pps)
        _release(psl)
        if need_uninit:
            ole32.CoUninitialize()


def _resolve_targets() -> tuple[str, str, str, str, int]:
    """Return (target, arguments, workdir, icon_path, icon_index) for the
    current launch mode."""
    if getattr(sys, "frozen", False):
        # onedir launcher Autona.exe carries the embedded icon.
        exe = os.path.abspath(sys.executable)
        return exe, "", os.path.dirname(exe), exe, 0

    # Dev mode (`python main.py`): point the shortcut at the interpreter +
    # main.py and at the on-disk .ico. parents[2] of this file is the repo root
    # (src/Helpers/AutonaWindowsShortcut.py -> Autona/).
    root = Path(__file__).resolve().parents[2]
    main_py = str(root / "main.py")
    icon = str(root / "assets" / "icons" / "icon.ico")
    return os.path.abspath(sys.executable), f'"{main_py}"', str(root), icon, 0


def ensure_appid_shortcut() -> None:
    """Create the AppID-tagged Start Menu shortcut once, if it is missing.

    Idempotent and best-effort: any error is logged and swallowed so startup is
    never blocked. Must run before the first window is shown so Win11 can
    resolve the taskbar button's icon from it.
    """
    if sys.platform != "win32":
        return

    appdata = os.environ.get("APPDATA")
    if not appdata:
        logger.debug("APPDATA not set; skipping taskbar shortcut creation")
        return

    lnk = (Path(appdata) / "Microsoft" / "Windows" / "Start Menu"
           / "Programs" / _SHORTCUT_NAME)
    if lnk.exists():
        return

    try:
        target, arguments, workdir, icon, icon_index = _resolve_targets()
        lnk.parent.mkdir(parents=True, exist_ok=True)
        _write_shortcut(str(lnk), target, arguments, workdir, icon,
                        icon_index, APP_USER_MODEL_ID)
        logger.info(f"Created AppID taskbar shortcut: {lnk}")
    except Exception:
        logger.opt(exception=True).warning(
            "Could not create AppID taskbar shortcut")
