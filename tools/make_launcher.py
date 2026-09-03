r"""Build .venv\Scripts\Arete.exe: the executable the app runs as.

Task Manager does not name a running app after its window. It names it after
the FileDescription resource of the executable behind it, and draws the icon
from the same file. Launched through pythonw.exe the app is therefore "Python"
with the Python icon, no matter what the window or the tray say.

So the launcher is a copy of pythonw.exe with two resources rewritten: the icon
group, and the version block that carries FileDescription.

It is copied from the *base* interpreter, not from the one in .venv\Scripts.
The venv copy on this platform is a redirector stub: it spawns the real
interpreter as a child and waits, so branding it renames a process that owns no
window, and Task Manager still lists the child as Python. A copy of the base
interpreter placed in .venv\Scripts is what a venv looks like natively, so it
picks up the venv from the pyvenv.cfg one directory up and runs in-process.

That is also why the copy has to live in .venv\Scripts: it finds the venv, and
its standard library, by looking beside and above itself.

    .venv\Scripts\python tools\make_launcher.py

Rewriting resources invalidates the Authenticode signature that pythonw.exe
ships with. That costs nothing for a locally built tool, but it is the reason
this works on a copy and never edits the original in place.
"""

from __future__ import annotations

import ctypes
import shutil
import struct
import sys
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from branding import APP_NAME, APP_TAGLINE  # noqa: E402

VENV = ROOT / ".venv"
TARGET = VENV / "Scripts" / f"{APP_NAME}.exe"
ICON = ROOT / "assets" / "arete.ico"

VERSION = (0, 1, 0, 0)
LANG_EN_US = 0x0409
CODEPAGE_UNICODE = 0x04B0
RT_ICON = 3
RT_GROUP_ICON = 14
RT_VERSION = 16
LOAD_LIBRARY_AS_DATAFILE = 0x00000002

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.BeginUpdateResourceW.restype = wintypes.HANDLE
k32.BeginUpdateResourceW.argtypes = [wintypes.LPCWSTR, wintypes.BOOL]
k32.UpdateResourceW.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    ctypes.c_void_p,
    wintypes.WORD,
    ctypes.c_void_p,
    wintypes.DWORD,
]
k32.EndUpdateResourceW.argtypes = [wintypes.HANDLE, wintypes.BOOL]
k32.LoadLibraryExW.restype = wintypes.HMODULE
k32.LoadLibraryExW.argtypes = [wintypes.LPCWSTR, wintypes.HANDLE, wintypes.DWORD]
k32.FreeLibrary.argtypes = [wintypes.HMODULE]

ENUMRESLANGPROC = ctypes.WINFUNCTYPE(
    wintypes.BOOL,
    wintypes.HMODULE,
    ctypes.c_void_p,
    ctypes.c_void_p,
    wintypes.WORD,
    ctypes.c_void_p,
)
k32.EnumResourceLanguagesW.argtypes = [
    wintypes.HMODULE,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ENUMRESLANGPROC,
    ctypes.c_void_p,
]


# ------------------------------------------------------------------ icon data


def read_ico(path: Path) -> tuple[list[bytes], bytes]:
    """Split a .ico into RT_ICON payloads plus an RT_GROUP_ICON directory.

    The on-disk and in-executable directories differ by four bytes per entry:
    the file holds a byte offset to each image, the resource holds the resource
    id that image was written under instead.
    """
    blob = path.read_bytes()
    _, kind, count = struct.unpack_from("<HHH", blob, 0)
    if kind != 1:
        raise ValueError(f"{path} is a cursor, not an icon")

    images: list[bytes] = []
    group = struct.pack("<HHH", 0, 1, count)
    for index in range(count):
        (
            width,
            height,
            colours,
            reserved,
            planes,
            bits,
            size,
            offset,
        ) = struct.unpack_from("<BBBBHHII", blob, 6 + index * 16)
        images.append(blob[offset : offset + size])
        group += struct.pack(
            "<BBBBHHIH",
            width,
            height,
            colours,
            reserved,
            planes or 1,
            bits or 32,
            size,
            index + 1,
        )
    return images, group


# --------------------------------------------------------------- version data


def _utf16(text: str) -> bytes:
    return text.encode("utf-16-le") + b"\x00\x00"


def _node(key: str, value: bytes, text: bool, children: list[bytes]) -> bytes:
    """One VS_VERSIONINFO node.

    Nodes start on a 4-byte boundary and pad to one after the key and between
    children. wLength counts that internal padding but not the padding that
    follows the node, which is why the caller aligns rather than the node.
    """
    value_length = (len(value) // 2) if text else len(value)
    head = struct.pack("<HHH", 0, value_length, 1 if text else 0) + _utf16(key)
    body = head + b"\x00" * (-len(head) % 4) + value
    for child in children:
        body += b"\x00" * (-len(body) % 4) + child
    return struct.pack("<H", len(body)) + body[2:]


def build_version() -> bytes:
    major, minor, patch_, build = VERSION
    ms, ls = (major << 16) | minor, (patch_ << 16) | build
    fixed = struct.pack(
        "<IIIIIIIIIIIII",
        0xFEEF04BD,  # dwSignature
        0x00010000,  # dwStrucVersion
        ms,
        ls,  # dwFileVersion
        ms,
        ls,  # dwProductVersion
        0x3F,  # dwFileFlagsMask
        0,  # dwFileFlags
        0x00040004,  # dwFileOS: VOS_NT_WINDOWS32
        0x00000001,  # dwFileType: VFT_APP
        0,  # dwFileSubtype
        0,  # dwFileDateMS
        0,  # dwFileDateLS
    )
    version_text = ".".join(str(part) for part in VERSION)
    # FileDescription is the one that matters: it is the text Task Manager puts
    # in the Apps list, and the tooltip Explorer shows on the executable.
    strings = [
        ("CompanyName", APP_NAME),
        ("FileDescription", APP_NAME),
        ("FileVersion", version_text),
        ("InternalName", APP_NAME),
        ("OriginalFilename", f"{APP_NAME}.exe"),
        ("ProductName", APP_NAME),
        ("ProductVersion", version_text),
        ("Comments", APP_TAGLINE),
    ]
    table = _node(
        f"{LANG_EN_US:04X}{CODEPAGE_UNICODE:04X}",
        b"",
        True,
        [_node(name, _utf16(value), True, []) for name, value in strings],
    )
    return _node(
        "VS_VERSION_INFO",
        fixed,
        False,
        [
            _node("StringFileInfo", b"", True, [table]),
            _node(
                "VarFileInfo",
                b"",
                True,
                [
                    _node(
                        "Translation",
                        struct.pack("<HH", LANG_EN_US, CODEPAGE_UNICODE),
                        False,
                        [],
                    )
                ],
            ),
        ],
    )


# ------------------------------------------------------------------- patching


def existing_languages(exe: Path, rtype: int, name: int) -> list[int]:
    module = k32.LoadLibraryExW(str(exe), None, LOAD_LIBRARY_AS_DATAFILE)
    if not module:
        return []
    langs: list[int] = []

    def collect(_module, _rtype, _name, lang, _param) -> bool:
        langs.append(lang)
        return True

    try:
        k32.EnumResourceLanguagesW(
            module,
            ctypes.c_void_p(rtype),
            ctypes.c_void_p(name),
            ENUMRESLANGPROC(collect),
            None,
        )
    finally:
        k32.FreeLibrary(module)
    return langs


def patch(exe: Path, images: list[bytes], group: bytes, version: bytes) -> None:
    # Anything the source executable already carries under these ids has to go.
    # One left behind under a different language id is enough for the shell to
    # keep resolving the Python icon in preference to ours.
    stale = [(RT_GROUP_ICON, 1), (RT_VERSION, 1)]
    stale += [(RT_ICON, index) for index in range(1, 33)]
    deletions = [
        (rtype, name, lang)
        for rtype, name in stale
        for lang in existing_languages(exe, rtype, name)
    ]

    # bDeleteExistingResources stays false so the application manifest, which
    # sets DPI awareness and the asInvoker execution level, survives untouched.
    handle = k32.BeginUpdateResourceW(str(exe), False)
    if not handle:
        raise OSError(ctypes.get_last_error(), "BeginUpdateResource failed")

    def write(rtype: int, name: int, data: bytes) -> None:
        buffer = ctypes.create_string_buffer(data, len(data))
        if not k32.UpdateResourceW(
            handle,
            ctypes.c_void_p(rtype),
            ctypes.c_void_p(name),
            LANG_EN_US,
            ctypes.cast(buffer, ctypes.c_void_p),
            len(data),
        ):
            raise OSError(ctypes.get_last_error(), f"UpdateResource {rtype}/{name}")

    try:
        for rtype, name, lang in deletions:
            if not k32.UpdateResourceW(
                handle, ctypes.c_void_p(rtype), ctypes.c_void_p(name), lang, None, 0
            ):
                raise OSError(ctypes.get_last_error(), f"delete {rtype}/{name}")
        for index, image in enumerate(images, start=1):
            write(RT_ICON, index, image)
        write(RT_GROUP_ICON, 1, group)
        write(RT_VERSION, 1, version)
    except Exception:
        k32.EndUpdateResourceW(handle, True)  # discard, leave the copy unpatched
        raise
    if not k32.EndUpdateResourceW(handle, False):
        raise OSError(ctypes.get_last_error(), "EndUpdateResource failed")


def base_interpreter() -> Path | None:
    """The windowed executable of the interpreter the venv was created from.

    sys._base_executable is the direct answer when this runs inside the venv;
    pyvenv.cfg carries the same path for the case where it does not.
    """
    candidates: list[Path] = []
    base = getattr(sys, "_base_executable", None)
    if base:
        candidates.append(Path(base))

    config = VENV / "pyvenv.cfg"
    if config.is_file():
        for line in config.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            if key.strip() in {"executable", "home"} and value.strip():
                candidates.append(Path(value.strip()))

    for candidate in candidates:
        # `home` names the directory, `executable` names python.exe inside it.
        directory = candidate if candidate.is_dir() else candidate.parent
        windowed = directory / "pythonw.exe"
        if windowed.is_file():
            return windowed
    return None


def main() -> int:
    source = base_interpreter()
    if source is None:
        print("Could not find the pythonw.exe the venv was built from.")
        return 1
    if not ICON.is_file():
        print(f"{ICON} is missing. Run tools/make_icon.py first.")
        return 1

    images, group = read_ico(ICON)
    version = build_version()

    if TARGET.exists():
        # Fails loudly while the app is running, which beats a half-written exe.
        TARGET.unlink()
    shutil.copy2(source, TARGET)
    patch(TARGET, images, group, version)

    print(f"built {TARGET} from {source} with {len(images)} icon sizes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
