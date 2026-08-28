"""Native file dialogs exposed to the React UI via pywebview."""

import os
import subprocess
import sys
from datetime import datetime

import webview


class NativeBridge:
    @staticmethod
    def _window():
        if not webview.windows:
            raise RuntimeError("桌面窗口尚未就绪")
        return webview.windows[0]

    def _open_dialog(self, *, multiple: bool = False, file_types=()) -> list[str]:
        paths = self._window().create_file_dialog(
            webview.OPEN_DIALOG,
            allow_multiple=multiple,
            file_types=file_types,
        )
        return list(paths or ())

    def _save_dialog(self, filename: str, file_types=()) -> str:
        paths = self._window().create_file_dialog(
            webview.SAVE_DIALOG,
            save_filename=filename,
            file_types=file_types,
        )
        return str(paths[0]) if paths else ""

    def pick_dxf_file(self) -> dict:
        return self.pick_cad_file()

    def pick_cad_file(self) -> dict:
        paths = self._open_dialog(file_types=("CAD files (*.dxf;*.dwg)", "All files (*.*)"))
        path = paths[0] if paths else ""
        if not path:
            return {"path": "", "dir": "", "base": "", "ext": ""}
        base, ext = os.path.splitext(os.path.basename(path))
        return {
            "path": path,
            "dir": os.path.dirname(path),
            "base": base,
            "ext": ext.lower(),
        }

    def pick_cad_files(self) -> dict:
        return {"paths": self._open_dialog(multiple=True, file_types=("CAD files (*.dxf;*.dwg)",))}

    def pick_output_dir(self) -> dict:
        paths = self._window().create_file_dialog(webview.FOLDER_DIALOG)
        path = str(paths[0]) if paths else ""
        return {"path": path or ""}

    def open_url(self, url: str) -> dict:
        if not url or not url.startswith(("https://", "http://")):
            return {"error": "无效链接"}
        if sys.platform == "darwin":
            subprocess.Popen(["open", url])
        elif sys.platform == "win32":
            os.startfile(url)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", url])
        return {"ok": True}

    def toggle_maximize(self) -> None:
        if webview.windows:
            webview.windows[0].toggle_fullscreen()

    def pick_term_package(self) -> dict:
        paths = self._open_dialog(file_types=("图译术语包 (*.hcterms.json)", "JSON files (*.json)"))
        path = paths[0] if paths else ""
        return {"path": path or ""}

    def save_term_package(self) -> dict:
        path = self._save_dialog("项目术语.hcterms.json", ("图译术语包 (*.hcterms.json)",))
        return {"path": path or ""}

    def export_logs(self) -> dict:
        path = self._save_dialog(
            f"Tuyi_log_{datetime.now():%Y%m%d_%H%M%S}.txt",
            ("Text files (*.txt)",),
        )
        if not path:
            return {"path": ""}
        from backend.api import service
        service.export_logs(path)
        return {"path": path}

    def print_pdf(self, path: str) -> dict:
        from backend.drawings import print_pdf as do_print

        try:
            return do_print(path)
        except FileNotFoundError:
            return {"ok": False, "message": "PDF 不存在"}
        except Exception:
            return {"ok": False, "message": "打印失败"}

    def reveal_file(self, path: str) -> dict:
        if not path or not os.path.isfile(path):
            return {"error": "输出文件不存在，可能已被移动或删除"}
        normalized = os.path.normpath(path)
        if sys.platform == "win32":
            command = ["explorer.exe", "/select,", normalized]
        elif sys.platform == "darwin":
            command = ["open", "-R", normalized]
        else:
            command = ["xdg-open", os.path.dirname(normalized)]
        try:
            subprocess.Popen(command)
        except OSError as exc:
            return {"error": f"无法在文件管理器中定位输出文件: {exc}"}
        return {"ok": True}

    def minimize_window(self) -> None:
        if webview.windows:
            webview.windows[0].minimize()

    def close_window(self) -> None:
        from backend.api import service
        from backend.cad import unmount_embedded_odafc
        service.shutdown()
        unmount_embedded_odafc()
        if webview.windows:
            webview.windows[0].destroy()
        os._exit(0)
