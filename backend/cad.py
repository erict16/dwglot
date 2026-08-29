"""DWG/DXF conversion via ezdxf odafc addon (ODA File Converter)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from backend.storage import atomic_output_path

# ODA File Converter accepts its own ``ACAD*`` identifiers, not ezdxf's
# ``R2010`` DXF-version label.  This value is passed to ODA directly.
WORK_DXF_VERSION = "ACAD2010"
ODA_OUTPUT_VERSIONS = ("ACAD9", "ACAD10", "ACAD12", "ACAD13", "ACAD14", "ACAD2000", "ACAD2004", "ACAD2007", "ACAD2010", "ACAD2013", "ACAD2018")

# Windows 安装包推荐目录结构（与主程序 exe 同级）：
#   Tuyi.exe
#   ODAFileConverter/   (user-installed sidecar, never in the installer)
#     ODAFileConverter.exe
#     *.dll ...
ODA_BUNDLE_DIR = "ODAFileConverter"
ODA_BUNDLE_EXE = "ODAFileConverter.exe"
ODA_SYSTEM_EXE = r"C:\Program Files\ODA\ODAFileConverter\ODAFileConverter.exe"
ODA_UNIX_EXECUTABLE = "ODAFileConverter"
ODA_MACOS_APP = "ODAFileConverter.app"
ODA_MACOS_SYSTEM_PATHS = (
    "/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter",
    "/Applications/ODA File Converter.app/Contents/MacOS/ODAFileConverter",
)

# DWG 文件头 6 字节版本签名 → ODA File Converter 版本参数
ACAD_SIG_TO_ODA: dict[str, str] = {
    "AC1012": "ACAD13",
    "AC1014": "ACAD14",
    "AC1015": "ACAD2000",
    "AC1018": "ACAD2004",
    "AC1021": "ACAD2007",
    "AC1024": "ACAD2010",
    "AC1027": "ACAD2013",
    "AC1032": "ACAD2018",
}

# Header signature → year label shown in the file list (轻语-style 版本 column).
ACAD_SIG_TO_LABEL: dict[str, str] = {
    "AC1009": "R12",
    "AC1012": "R13",
    "AC1014": "R14",
    "AC1015": "2000",
    "AC1018": "2004",
    "AC1021": "2007",
    "AC1024": "2010",
    "AC1027": "2013",
    "AC1032": "2018",
}

ODA_DOWNLOAD_URL = "https://www.opendesign.com/guestfiles/oda_file_converter"

_DXF_ACADVER = re.compile(r"\$ACADVER[^\n]*\n\s*1[^\n]*\n\s*(AC\d{4})", re.IGNORECASE)

LogFn = Optional[Callable[[str], None]]
_odafc_configured = False
_oda_mount_lock = threading.Lock()
_oda_mount_dir: Optional[Path] = None


@dataclass
class SourceCadMeta:
    original_path: str
    source_ext: str
    acad_sig: str = ""
    oda_version: str = "ACAD2010"

    @property
    def is_dwg(self) -> bool:
        return self.source_ext.lower() == ".dwg"

    @property
    def output_ext(self) -> str:
        return self.source_ext.lower()


def get_app_dir() -> Path:
    """打包后为可执行文件所在目录；开发环境为项目根目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def _macos_app_root() -> Optional[Path]:
    """Return the containing .app bundle when running a frozen macOS app."""
    if sys.platform != "darwin" or not getattr(sys, "frozen", False):
        return None
    executable = Path(sys.executable).resolve()
    for parent in executable.parents:
        if parent.suffix == ".app":
            return parent
    return None


def _log(fn: LogFn, message: str) -> None:
    if fn:
        fn(message)


def _mount_embedded_macos_odafc() -> Optional[Path]:
    """Dwglot does not ship ODA. Never mount a Resources DMG."""
    return None


def unmount_embedded_odafc() -> None:
    """Detach the temporary read-only ODA volume created for the packaged app."""
    global _oda_mount_dir
    with _oda_mount_lock:
        mount = _oda_mount_dir
        _oda_mount_dir = None
        if not mount:
            return
        subprocess.run(
            ["hdiutil", "detach", str(mount), "-quiet"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        try:
            mount.rmdir()
        except OSError:
            pass


def odafc_candidate_paths() -> list[Path]:
    app_dir = get_app_dir()
    custom = os.environ.get("CAD_ODA_EXEC", "").strip()
    paths: list[Path] = []
    if custom:
        paths.append(Path(custom))
    mounted = _mount_embedded_macos_odafc()
    if mounted:
        paths.append(mounted)
    if sys.platform == "win32":
        paths.extend(
            [
                app_dir / ODA_BUNDLE_DIR / ODA_BUNDLE_EXE,
                app_dir / ODA_BUNDLE_EXE,
                Path(ODA_SYSTEM_EXE),
            ]
        )
        command = shutil.which(ODA_BUNDLE_EXE) or shutil.which("ODAFileConverter")
        if command:
            paths.append(Path(command))
    else:
        paths.extend([app_dir / ODA_BUNDLE_DIR / ODA_UNIX_EXECUTABLE, app_dir / ODA_UNIX_EXECUTABLE])
        if sys.platform == "darwin":
            paths.append(app_dir / ODA_MACOS_APP / "Contents" / "MacOS" / ODA_UNIX_EXECUTABLE)
        app_root = _macos_app_root()
        if app_root:
            paths.extend(
                [
                    app_root / "Contents" / "Helpers" / ODA_MACOS_APP / "Contents" / "MacOS" / ODA_UNIX_EXECUTABLE,
                    app_root / "Contents" / "Resources" / ODA_MACOS_APP / "Contents" / "MacOS" / ODA_UNIX_EXECUTABLE,
                    app_root / "Contents" / "Resources" / ODA_BUNDLE_DIR / ODA_UNIX_EXECUTABLE,
                    app_root.parent / ODA_MACOS_APP / "Contents" / "MacOS" / ODA_UNIX_EXECUTABLE,
                    app_root.parent / ODA_BUNDLE_DIR / ODA_UNIX_EXECUTABLE,
                ]
            )
        if sys.platform == "darwin":
            paths.extend(Path(path) for path in ODA_MACOS_SYSTEM_PATHS)
        command = shutil.which(ODA_UNIX_EXECUTABLE)
        if command:
            paths.append(Path(command))
    seen: set[str] = set()
    resolved: list[Path] = []
    for path in paths:
        item = _resolve_candidate(path)
        key = str(item)
        if key in seen:
            continue
        seen.add(key)
        resolved.append(item)
    return resolved


def _resolve_candidate(path: Path) -> Path:
    item = path.expanduser()
    text = str(item)
    windows_abs = len(text) >= 3 and text[1] == ":" and text[2] in "\\/"
    if windows_abs:
        if os.name != "nt":
            return Path(text)
        return item.resolve()
    if text.startswith("/"):
        try:
            return item.resolve()
        except OSError:
            return Path(os.path.abspath(text))
    return item


def resolve_odafc_path() -> Optional[str]:
    for path in odafc_candidate_paths():
        if path.is_file():
            return str(path.resolve())
    return None


def configure_odafc() -> Optional[str]:
    """优先使用与主程序同目录的 ODA File Converter。"""
    global _odafc_configured
    path = resolve_odafc_path()
    if path:
        import ezdxf

        option = "win_exec_path" if sys.platform == "win32" else "unix_exec_path"
        ezdxf.options.set("odafc-addon", option, path)
        _odafc_configured = True
    return path


def dwg_unavailable_message() -> str:
    return (
        "未检测到 ODA File Converter，无法自动处理 DWG。图译不附带 ODA。\n"
        "- 从 Open Design Alliance 官网自行安装 ODA File Converter\n"
        "- 或设置环境变量 CAD_ODA_EXEC 指向 ODAFileConverter 可执行文件\n"
        f"- Windows 常见路径：{ODA_SYSTEM_EXE}\n"
        "- macOS 常见路径：/Applications/ODAFileConverter.app\n"
        "- 或将图纸另存为 DXF 后直接翻译（无需 ODA）"
    )


def dwg_unavailable_short() -> str:
    return "未检测到 ODA，无法处理 DWG；请安装 ODA 或将 DWG 另存为 DXF"


def odafc_status() -> dict:
    path = configure_odafc() or resolve_odafc_path()
    if not path:
        return {
            "installed": False,
            "path": "",
            "source": "",
            "message": dwg_unavailable_message(),
            "download_url": ODA_DOWNLOAD_URL,
        }

    app_dir = get_app_dir()
    p = Path(path)
    app_root = _macos_app_root()
    adjacent_roots = [app_dir]
    if app_root:
        adjacent_roots.append(app_root.parent)
    if os.environ.get("CAD_ODA_EXEC"):
        source = "env"
    elif (_oda_mount_dir and _oda_mount_dir in p.parents) or any(root == p.parent or root in p.parents for root in adjacent_roots):
        source = "bundled"
    else:
        source = "system"
    return {"installed": True, "path": path, "source": source, "download_url": ODA_DOWNLOAD_URL}


def odafc_available() -> bool:
    try:
        configure_odafc()
        from ezdxf.addons import odafc

        return odafc.is_installed()
    except Exception:
        return bool(resolve_odafc_path())


def require_odafc(log: LogFn = None) -> None:
    path = configure_odafc()
    if not path or not odafc_available():
        raise RuntimeError(dwg_unavailable_short())
    _log(log, f"ODA File Converter 已就绪 ({path})")


def read_dwg_acad_signature(path: str) -> str:
    with open(path, "rb") as f:
        return f.read(6).decode("ascii", errors="ignore").strip("\x00")


def analyze_source(path: str) -> SourceCadMeta:
    ext = Path(path).suffix.lower()
    if ext not in (".dxf", ".dwg"):
        raise ValueError(f"不支持的文件格式: {ext}")

    meta = SourceCadMeta(original_path=path, source_ext=ext)
    if meta.is_dwg:
        sig = read_dwg_acad_signature(path)
        meta.acad_sig = sig
        meta.oda_version = ACAD_SIG_TO_ODA.get(sig, "ACAD2010")
    return meta


def format_file_size(size: int) -> str:
    n = max(0, int(size or 0))
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        value = n / 1024
        return f"{value:.0f} KB" if value >= 10 else f"{value:.1f} KB"
    value = n / (1024 * 1024)
    return f"{value:.0f} MB" if value >= 10 else f"{value:.1f} MB"


def read_dxf_acad_version(path: str) -> str:
    try:
        with open(path, "rb") as handle:
            head = handle.read(65536)
    except OSError:
        return ""
    match = _DXF_ACADVER.search(head.decode("latin-1", errors="ignore"))
    return match.group(1).upper() if match else ""


def cad_listing(path: str) -> dict:
    """UI row: path, size, CAD year. Never raises; missing files still show a name."""
    raw = str(path or "")
    p = Path(raw)
    suffix = p.suffix.lower()
    ext = suffix[1:].upper()
    listing = {
        "path": raw,
        "name": p.name,
        "ext": ext,
        "dir": str(p.parent) if raw else "",
        "size": 0,
        "size_label": "",
        "cad_version": "",
        "acad_sig": "",
    }
    if not raw or not p.is_file():
        return listing
    try:
        listing["size"] = p.stat().st_size
        listing["size_label"] = format_file_size(listing["size"])
        if suffix == ".dwg":
            sig = read_dwg_acad_signature(raw)
        elif suffix == ".dxf":
            sig = read_dxf_acad_version(raw)
        else:
            sig = ""
        listing["acad_sig"] = sig
        listing["cad_version"] = ACAD_SIG_TO_LABEL.get(sig, sig)
    except OSError:
        pass
    return listing


def output_path_for(meta: SourceCadMeta, output_dir: str, output_name: str) -> str:
    name = output_name.strip()
    return os.path.join(output_dir, name + meta.output_ext)


def _macos_odafc_app(executable: str) -> Optional[Path]:
    """Return the containing ODA application bundle, if the path belongs to one."""
    for parent in Path(executable).resolve().parents:
        if parent.suffix == ".app":
            return parent
    return None


def _convert_with_hidden_macos_odafc(source: str, destination: str, *, version: str, audit: bool, replace: bool) -> bool:
    """Use LaunchServices to run ODA hidden and without activating it.

    ODA's macOS binary is a Qt GUI application and has no supported headless
    command-line option.  ``open -g -j`` is the supported macOS way to launch
    it in the background and hidden; ``-W -n`` waits for an isolated conversion
    instance rather than an ODA window the user may already have open.
    """
    executable = resolve_odafc_path()
    app = _macos_odafc_app(executable) if executable else None
    if not app:
        return False

    source_path = Path(source).resolve()
    destination_path = Path(destination)
    if destination_path.exists():
        if not replace:
            raise FileExistsError(f"Target file already exists: '{destination_path}'")
        destination_path.unlink()
    if not destination_path.parent.is_dir():
        raise FileNotFoundError(f"Destination folder does not exist: '{destination_path.parent}'")

    output_format = destination_path.suffix.upper().lstrip(".")
    if output_format not in {"DXF", "DWG"}:
        raise ValueError(f"Unsupported output file format: '{destination_path.suffix}'")
    with tempfile.TemporaryDirectory(prefix="honsen_oda_output_") as output_dir:
        arguments = [
            str(source_path.parent),
            output_dir,
            version,
            output_format,
            "0",
            "1" if audit else "0",
            source_path.name,
        ]
        subprocess.run(
            ["open", "-g", "-j", "-W", "-n", "-a", str(app), "--args", *arguments],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        converted = next(
            (path for path in Path(output_dir).iterdir() if path.is_file() and path.suffix.lower() == destination_path.suffix.lower()),
            None,
        )
        if not converted:
            raise RuntimeError("ODA File Converter 未生成目标文件")
        shutil.move(str(converted), str(destination_path))
    return True


def _linux_odafc_env(executable: str) -> dict[str, str]:
    env = os.environ.copy()
    binary = Path(executable).resolve()
    usr = binary.parent.parent
    lib_dir = usr / "lib"
    bin_dir = binary.parent
    plugins = usr / "plugins"
    paths = []
    if lib_dir.is_dir():
        paths.append(str(lib_dir))
    paths.append(str(bin_dir))
    existing = env.get("LD_LIBRARY_PATH", "")
    env["LD_LIBRARY_PATH"] = ":".join(paths + ([existing] if existing else []))
    if plugins.is_dir():
        env["QT_PLUGIN_PATH"] = str(plugins)
        env["QT_QPA_PLATFORM_PLUGIN_PATH"] = str(plugins / "platforms")
    runtime = Path(env.get("XDG_RUNTIME_DIR") or f"/tmp/runtime-{os.getuid()}")
    try:
        runtime.mkdir(mode=0o700, exist_ok=True)
    except OSError:
        pass
    env["XDG_RUNTIME_DIR"] = str(runtime)
    return env


def _convert_with_linux_odafc(source: str, destination: str, *, version: str, audit: bool, replace: bool) -> bool:
    """Run ODA File Converter on Linux via CLI + xvfb.

    ezdxf's Linux helper treats any stderr as failure. ODA 27.1 always prints a
    Qt ``XDG_RUNTIME_DIR`` line even when the DXF was written, so 链路 never
    completed. Succeed when the output file exists; ignore that Qt noise.
    """
    executable = resolve_odafc_path()
    if not executable:
        return False
    source_path = Path(source).resolve()
    destination_path = Path(destination)
    if destination_path.exists():
        if not replace:
            raise FileExistsError(f"Target file already exists: '{destination_path}'")
        destination_path.unlink()
    if not destination_path.parent.is_dir():
        raise FileNotFoundError(f"Destination folder does not exist: '{destination_path.parent}'")
    output_format = destination_path.suffix.upper().lstrip(".")
    if output_format not in {"DXF", "DWG"}:
        raise ValueError(f"Unsupported output file format: '{destination_path.suffix}'")
    env = _linux_odafc_env(executable)
    with tempfile.TemporaryDirectory(prefix="honsen_oda_output_") as output_dir:
        arguments = [
            str(source_path.parent),
            output_dir,
            version,
            output_format,
            "0",
            "1" if audit else "0",
            source_path.name,
        ]
        command = [executable, *arguments]
        xvfb = shutil.which("xvfb-run")
        if xvfb:
            command = [xvfb, "-a", "-s", "-screen 0 800x600x24", *command]
        try:
            subprocess.run(
                command,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=180,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("ODA File Converter 转换超时") from exc
        converted = next(
            (
                path
                for path in Path(output_dir).iterdir()
                if path.is_file() and path.suffix.lower() == destination_path.suffix.lower()
            ),
            None,
        )
        if not converted:
            raise RuntimeError("ODA File Converter 未生成目标文件")
        shutil.move(str(converted), str(destination_path))
    return True


def convert_with_odafc(source: str, destination: str, *, version: str, audit: bool = True, replace: bool = False) -> None:
    """Convert a CAD file through ODA, handling a macOS ODA Unicode bug.

    ODA File Converter 27.1 on macOS can display ``There is no matched files
    in input folder`` when its command-line filter contains decomposed Unicode
    (for example filenames with accented French characters).  ``ezdxf`` sends
    the source filename as that filter.  Stage a temporary ASCII-named copy on
    macOS so ODA always receives a stable filter, while preserving the original
    file and the requested destination path.
    """
    if sys.platform.startswith("linux"):
        if _convert_with_linux_odafc(source, destination, version=version, audit=audit, replace=replace):
            return

    if sys.platform != "darwin":
        from ezdxf.addons import odafc

        odafc.convert(source, destination, version=version, audit=audit, replace=replace)
        return

    source_path = Path(source)
    with tempfile.TemporaryDirectory(prefix="honsen_oda_input_") as stage_dir:
        staged_source = Path(stage_dir) / f"input{source_path.suffix.lower()}"
        shutil.copy2(source_path, staged_source)
        if _convert_with_hidden_macos_odafc(str(staged_source), destination, version=version, audit=audit, replace=replace):
            return
        from ezdxf.addons import odafc

        odafc.convert(str(staged_source), destination, version=version, audit=audit, replace=replace)


def dwg_to_work_dxf(dwg_path: str, work_dxf_path: str, log: LogFn = None) -> None:
    require_odafc(log)
    _log(log, "DWG → DXF AutoCAD 2010（工作副本）...")
    convert_with_odafc(dwg_path, work_dxf_path, version=WORK_DXF_VERSION, audit=True, replace=True)
    _log(log, "DWG 已转换为 DXF 中间文件")


def work_dxf_to_dwg(
    work_dxf_path: str,
    dwg_path: str,
    meta: SourceCadMeta,
    log: LogFn = None,
) -> None:
    require_odafc(log)
    _log(log, f"DXF → DWG {meta.oda_version}（还原原版本 {meta.acad_sig or '未知'}）...")
    convert_with_odafc(work_dxf_path, dwg_path, version=meta.oda_version, audit=True, replace=True)
    _log(log, f"已输出 DWG: {dwg_path}")


class CadConversionSession:
    """管理 DWG 往返转换的临时目录。"""

    def __init__(self, input_file: str, log: LogFn = None, output_format: str = "source", output_version: str = ""):
        self.meta = analyze_source(input_file)
        self.log = log
        self.output_is_dwg = output_format == "dwg" or (output_format == "source" and self.meta.is_dwg)
        self.output_version = output_version
        self.output_needs_oda = self.output_is_dwg or bool(output_version)
        self._tmp: Optional[str] = None
        self.work_input: str = input_file

    def __enter__(self) -> CadConversionSession:
        if self.meta.is_dwg or self.output_needs_oda:
            require_odafc(self.log)
            if not self.meta.is_dwg:
                return self
            self._tmp = tempfile.mkdtemp(prefix="cad_tr_")
            self.work_input = os.path.join(self._tmp, "work_input.dxf")
            _log(
                self.log,
                f"检测到 DWG：{self.meta.acad_sig} → 将按 {self.meta.oda_version} 还原",
            )
            dwg_to_work_dxf(self.meta.original_path, self.work_input, self.log)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._tmp and os.path.isdir(self._tmp):
            shutil.rmtree(self._tmp, ignore_errors=True)

    def work_output_path(self) -> str:
        if self.output_needs_oda and not self._tmp:
            self._tmp = tempfile.mkdtemp(prefix="cad_tr_")
        if self._tmp:
            return os.path.join(self._tmp, "work_output.dxf")
        return ""

    def finalize(self, translated_dxf: str, final_output: str) -> None:
        with atomic_output_path(final_output) as temporary_output:
            if self.output_is_dwg:
                if self.output_version:
                    self.meta.oda_version = self.output_version
                work_dxf_to_dwg(translated_dxf, temporary_output, self.meta, self.log)
            elif self.output_version:
                require_odafc(self.log)
                convert_with_odafc(translated_dxf, temporary_output, version=self.output_version, audit=True, replace=True)
            elif os.path.abspath(translated_dxf) != os.path.abspath(final_output):
                shutil.copy2(translated_dxf, temporary_output)
