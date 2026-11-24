"""Open locally exported G-code in Bambu Studio or OrcaSlicer. Python 3.8+."""

import argparse
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote


PUBLISHED_ORIGINS = {"https://alexxpowered.top", "https://alexx-powered.github.io"}
LIMIT = 256 * 1024 * 1024
APPS = {
    "bambu": ("bambu-studio", "BambuStudio", "Bambu Studio", "bambustudio"),
    "orca": ("orca-slicer", "OrcaSlicer", "OrcaSlicer", "orcaslicer"),
}


def windows_slicer(app):
    import winreg

    name, _, folder, scheme = APPS[app]
    exe = name + ".exe"
    candidates = []
    for key in (rf"Applications\{exe}\shell\open\command", rf"{scheme}\shell\open\command"):
        try:
            command = winreg.QueryValue(winreg.HKEY_CLASSES_ROOT, key)
        except OSError:
            continue
        match = re.match(r'^\s*(?:"([^"]+)"|(.+?\.exe)(?:\s|$))', command, re.I)
        if match:
            candidates.append(Path(os.path.expandvars(match[1] or match[2])))
    for root in ("ProgramFiles", "ProgramW6432", "LOCALAPPDATA"):
        if os.environ.get(root):
            candidates.append(Path(os.environ[root]) / folder / exe)
    return next((path for path in candidates if path.name.lower() == exe and path.is_file()), None)


def viewer_command(app, override, file):
    name, mac_name, _, _ = APPS[app]
    if override:
        executable = Path(override).expanduser().resolve(strict=True)
        return [str(executable), str(file)]
    if os.name == "nt":
        executable = windows_slicer(app)
        if executable:
            return [str(executable), str(file)]
    elif sys.platform == "darwin":
        return ["/usr/bin/open", "-a", mac_name, str(file)]
    elif shutil.which(name):
        return [shutil.which(name), str(file)]
    raise FileNotFoundError(f"未找到切片器。请用 --{app} 指定它的可执行文件，再运行助手。")


def export_name(value):
    # The browser supplies a display name only. Always create a new .gcode in a private directory.
    name = unquote(value, errors="replace")
    suffix = ".gcode.3mf" if name.lower().endswith(".gcode.3mf") else ".gcode"
    stem = name[:-len(suffix)] if name.lower().endswith(suffix) else name
    name = re.sub(r"[^\w .()\-]", "_", stem).strip(" .")[:90] or "model"
    return "preview-" + name + suffix


class OpenGcode(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(45)

    def log_message(self, *_):
        pass

    def trusted(self):
        return (self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"
                and self.headers.get("Origin") in self.server.origins)

    def respond(self, status, message=""):
        body = json.dumps({"message": message}, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        if self.trusted():
            self.send_header("Access-Control-Allow-Origin", self.headers["Origin"])
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Slicer-Token, X-Gcode-Name")
            self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.respond(200 if self.trusted() and self.path in ("/open/bambu", "/open/orca", "/open-package/bambu", "/open-package/orca") else 403)

    def do_POST(self):
        if not self.trusted():
            return self.respond(403, "来源未授权。")
        supplied = self.headers.get("X-Slicer-Token", "").encode("utf-8")
        if not hmac.compare_digest(supplied, self.server.token.encode()):
            return self.respond(403, "配对码已失效，请复制助手当前显示的配对码。")
        if self.path not in ("/open/bambu", "/open/orca", "/open-package/bambu", "/open-package/orca"):
            return self.respond(404)
        if self.headers.get_content_type() != "application/octet-stream":
            return self.respond(415, "需要 G-code 文件。")
        try:
            count = int(self.headers["Content-Length"])
        except (KeyError, TypeError, ValueError):
            count = 0
        if not 0 < count <= LIMIT:
            return self.respond(413, "本机助手支持 256 MiB 以内的文件，请下载后在切片器中打开。")
        if not self.server.busy.acquire(blocking=False):
            return self.respond(409, "另一份 G-code 正在传输，请稍后重试。")
        try:
            app = self.path.rsplit("/", 1)[1]
            directory = Path(tempfile.mkdtemp(prefix="job-", dir=self.server.exports))
            file = directory / export_name(self.headers.get("X-Gcode-Name", "model.gcode"))
            command = viewer_command(app, self.server.overrides[app], file)
            with file.open("xb") as target:
                while count:
                    chunk = self.rfile.read(min(count, 1024 * 1024))
                    if not chunk:
                        raise OSError("文件传输中断，请重新导出。")
                    target.write(chunk)
                    count -= len(chunk)
            if sys.platform == "darwin" and command[0] == "/usr/bin/open":
                subprocess.run(command, check=True, timeout=15, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError) as error:
            return self.respond(422, str(error))
        finally:
            self.server.busy.release()
        self.respond(200, "已将文件交给切片器。")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for app in APPS:
        parser.add_argument("--" + app, metavar="EXECUTABLE", help="path to this slicer executable")
    parser.add_argument("--allow-file", action="store_true", help="also allow a locally opened HTML demo with the pairing code")
    parser.add_argument("--origin", help="add a local development origin, e.g. http://127.0.0.1:8000")
    args = parser.parse_args()
    origins = set(PUBLISHED_ORIGINS)
    if args.allow_file:
        origins.add("null")
    if args.origin:
        if not re.fullmatch(r"http://(?:localhost|127\.0\.0\.1):[1-9]\d{0,4}", args.origin):
            parser.error("--origin must be a loopback HTTP origin with a port")
        origins.add(args.origin)
    with tempfile.TemporaryDirectory(prefix="slicer-preview-") as exports:
        with ThreadingHTTPServer(("127.0.0.1", 0), OpenGcode) as server:
            server.origins = origins
            server.token = secrets.token_urlsafe(18)
            server.exports = exports
            server.busy = threading.Lock()
            server.overrides = {app: getattr(args, app) for app in APPS}
            print("在演示页的切片器菜单中输入配对码 / Pairing code:", flush=True)
            print(f"{server.server_port}.{server.token}", flush=True)
            print("使用时保留此窗口，按 Ctrl+C 结束。", flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass


if __name__ == "__main__":
    main()
