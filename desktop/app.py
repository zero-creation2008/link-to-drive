"""Desktop host for the shared Drive Link Importer interface."""

import base64
import configparser
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen
import webbrowser

ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
if sys.platform == "win32":
    CONFIG_HOME = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
elif sys.platform == "darwin":
    CONFIG_HOME = Path.home() / "Library" / "Application Support"
else:
    CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
CONFIG = CONFIG_HOME / "drive-link-importer" / "rclone.conf"
RCLONE = ROOT / "desktop" / "bundle" / ("rclone.exe" if os.name == "nt" else "rclone")
FOLDER = "application/vnd.google-apps.folder"
SHORTCUT = "application/vnd.google-apps.shortcut"
IMPORTS = "Drive Link Imports"


def read_config():
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(CONFIG, encoding="utf-8")
    return parser


def parse_link(value):
    url = urlsplit(value.strip())
    if url.scheme != "https" or url.netloc not in ("drive.google.com", "docs.google.com"):
        raise ValueError("Paste an HTTPS Google Drive file or folder link.")
    match = re.search(r"/(?:folders|d)/([A-Za-z0-9_-]+)(?:/|$)", url.path)
    query = parse_qs(url.query)
    identifier = match.group(1) if match else query.get("id", [""])[0]
    key = query.get("resourcekey", [""])[0]
    if not re.fullmatch(r"[A-Za-z0-9_-]+", identifier):
        raise ValueError("That link doesn't contain a valid Google Drive ID.")
    if key and not re.fullmatch(r"[A-Za-z0-9_-]+", key):
        raise ValueError("That link has an invalid resource key.")
    return identifier, key


def reveal(value):
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    key = bytes.fromhex("9c935b48730a554d6bfd7c63c886a92bd390198eb8128afbf4de162b8b95f638")
    encoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    return Cipher(algorithms.AES(key), modes.CTR(encoded[:16])).decryptor().update(encoded[16:]).decode()


def connection():
    parser = read_config()
    token = json.loads(parser.get("mydrive", "token"))
    defaults = json.loads((ROOT / "desktop" / "bundle" / "rclone-oauth.json").read_text())
    secret = parser.get("mydrive", "client_secret", fallback="")
    return {
        "access_token": token.get("access_token", ""),
        "refresh_token": token["refresh_token"],
        "client_id": parser.get("mydrive", "client_id", fallback="") or defaults["client_id"],
        "client_secret": reveal(secret) if secret else defaults["client_secret"],
        "expiryMillis": int(datetime.fromisoformat(token["expiry"].replace("Z", "+00:00")).timestamp() * 1000),
    }


class Cancelled(Exception):
    pass


class Drive:
    def __init__(self, owner):
        self.owner = owner
        self.access = ""
        self.token_lock = threading.Lock()

    def token(self, refresh=False):
        with self.token_lock:
            if self.access and not refresh:
                return self.access
            data = connection()
            if refresh or data["expiryMillis"] < time.time() * 1000 + 60000:
                body = urlencode({
                    "client_id": data["client_id"], "client_secret": data["client_secret"],
                    "refresh_token": data["refresh_token"], "grant_type": "refresh_token",
                }).encode()
                try:
                    with urlopen(Request("https://oauth2.googleapis.com/token", data=body), timeout=25) as response:
                        data["access_token"] = json.load(response)["access_token"]
                except HTTPError:
                    raise RuntimeError("Google connection expired. Choose Reconnect and sign in again.") from None
            self.access = data["access_token"]
            return self.access

    def request(self, path, params=None, body=None, resource=None):
        url = "https://www.googleapis.com/drive/v3/" + path
        if params:
            url += "?" + urlencode(params)
        for attempt in range(4):
            self.owner.check_cancelled()
            headers = {"Authorization": "Bearer " + self.token()}
            if resource and resource[1]:
                headers["X-Goog-Drive-Resource-Keys"] = "/".join(resource)
            encoded = None
            if body is not None:
                encoded = json.dumps(body).encode()
                headers["Content-Type"] = "application/json"
            try:
                with urlopen(Request(url, data=encoded, headers=headers), timeout=45) as response:
                    return json.load(response)
            except HTTPError as error:
                if error.code == 401 and attempt == 0:
                    self.token(refresh=True)
                    continue
                try:
                    detail = json.load(error)["error"]
                    message = detail.get("message", "Google Drive request failed.")
                except (ValueError, KeyError):
                    detail = {}
                    message = f"Google Drive returned HTTP {error.code}."
                reasons = json.dumps(detail)
                limited = error.code == 429 or (error.code == 403 and (
                    "rateLimitExceeded" in reasons or "RATE_LIMIT_EXCEEDED" in reasons))
                if attempt < 3 and (limited or (body is None and error.code >= 500)):
                    self.owner.update(message="Google Drive is busy. Retrying…")
                    if self.owner.cancelled.wait((attempt + 1) * 5):
                        raise Cancelled()
                    continue
                raise RuntimeError(message) from None
        raise RuntimeError("Google Drive is busy. Try again shortly.")

    def metadata(self, identifier, key):
        return self.request(f"files/{identifier}", {
            "fields": "id,name,mimeType,resourceKey,shortcutDetails", "supportsAllDrives": "true",
        }, resource=(identifier, key))

    def resolve(self, info):
        seen = set()
        while info["mimeType"] == SHORTCUT:
            if info["id"] in seen:
                raise ValueError("This Drive shortcut points to a cycle.")
            seen.add(info["id"])
            target = info["shortcutDetails"]
            info = self.metadata(target["targetId"], target.get("targetResourceKey", ""))
        return info

    def children(self, folder):
        page = ""
        while True:
            response = self.request("files", {
                "q": f"'{folder['id']}' in parents and trashed = false",
                "supportsAllDrives": "true", "includeItemsFromAllDrives": "true", "pageSize": 1000,
                "fields": "nextPageToken,files(id,name,mimeType,resourceKey,shortcutDetails)", "pageToken": page,
            }, resource=(folder["id"], folder.get("resourceKey", "")))
            yield from response["files"]
            page = response.get("nextPageToken")
            if not page:
                return

    def create_folder(self, name, parent):
        return self.request("files", {"fields": "id", "supportsAllDrives": "true"},
                            {"name": name, "mimeType": FOLDER, "parents": [parent]})["id"]

    def imports_folder(self):
        matches = self.request("files", {
            "q": f"'root' in parents and trashed = false and name = '{IMPORTS}' and mimeType = '{FOLDER}'",
            "fields": "files(id)", "pageSize": 1,
        })["files"]
        return matches[0]["id"] if matches else self.create_folder(IMPORTS, "root")

    def copy(self, info, name, parent):
        self.request(f"files/{info['id']}/copy", {"fields": "id", "supportsAllDrives": "true"},
                     {"name": name, "parents": [parent]}, (info["id"], info.get("resourceKey", "")))


class Api:
    def __init__(self):
        self.lock = threading.RLock()
        self.cancelled = threading.Event()
        self.state = {"busy": False, "message": "", "platform": "desktop"}
        self.window = None
        self.process = None

    def connected(self):
        return bool(read_config().get("mydrive", "token", fallback=""))

    def get_state(self):
        with self.lock:
            return {**self.state, "connected": self.connected()}

    def update(self, **state):
        with self.lock:
            self.state.update(state)

    def check_cancelled(self):
        if self.cancelled.is_set():
            raise Cancelled()

    def _launch(self, action):
        with self.lock:
            if self.state["busy"]:
                return self.get_state()
            self.cancelled.clear()
            self.state = {"busy": True, "phase": "scanning", "message": "Checking the Drive link…", "platform": "desktop"}

        def work():
            try:
                action()
            except Cancelled:
                self.update(busy=False, phase="cancelled", percent=None,
                            message="Operation cancelled. Files already copied remain in My Drive.")
            except Exception as error:
                self.update(busy=False, phase="error", percent=None, message="Couldn't finish this operation.", error=str(error))
        threading.Thread(target=work, daemon=True).start()
        return self.get_state()

    def start_import(self, link, name):
        if not self.connected():
            self.update(message="Connect your Google account first.", phase="error")
            return self.get_state()
        try:
            source = parse_link(link)
        except ValueError as error:
            self.update(message=str(error), phase="error", percent=None)
            return self.get_state()
        return self._launch(lambda: self._import(source, name.strip()))

    def _import(self, source, custom_name):
        drive = Drive(self)
        original = drive.metadata(*source)
        root = drive.resolve(original)
        name = custom_name or original["name"]
        suffix = Path(root["name"]).suffix
        if custom_name and root["mimeType"] != FOLDER and suffix and not Path(name).suffix:
            name += suffix
        nodes = []
        file_count = 0
        started = time.monotonic()

        def scan(info, item_name, parent, ancestors):
            nonlocal file_count
            self.check_cancelled()
            node = {"info": info, "name": item_name, "parent": parent}
            nodes.append(node)
            if info["mimeType"] != FOLDER:
                file_count += 1
                return
            if info["id"] in ancestors:
                raise ValueError("This folder contains a circular shortcut.")
            self.update(message=f"Scanning {name}…", metrics=f"{file_count:,} files found")
            for child in drive.children(info):
                scan(drive.resolve(child), child["name"], node, ancestors | {info["id"]})

        scan(root, name, None, set())
        self.update(phase="copying", message=f"Copying {name}…", percent=0)
        parent = drive.imports_folder()
        completed = 0
        copied = 0
        counter_lock = threading.Lock()

        def progress(is_file=False):
            nonlocal completed, copied
            with counter_lock:
                completed += 1
                copied += int(is_file)
                elapsed = int(time.monotonic() - started)
                self.update(phase="copying", message=f"Copying {name}…",
                            percent=min(99, completed * 100 // len(nodes)),
                            metrics=f"{copied:,} copied · {file_count:,} files · {elapsed // 60}m {elapsed % 60:02d}s")

        for node in nodes:
            self.check_cancelled()
            if node["info"]["mimeType"] == FOLDER:
                target = node["parent"]["destination"] if node["parent"] else parent
                node["destination"] = drive.create_folder(node["name"], target)
                progress()

        def copy(node):
            self.check_cancelled()
            target = node["parent"]["destination"] if node["parent"] else parent
            drive.copy(node["info"], node["name"], target)
            progress(True)

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(copy, node) for node in nodes if node["info"]["mimeType"] != FOLDER]
            try:
                for future in as_completed(futures):
                    future.result()
            except Exception:
                for future in futures:
                    future.cancel()
                self.cancelled.set()
                raise
        self.update(busy=False, phase="done", percent=100, message=f"Saved in {IMPORTS} › {name}",
                    finishedAt=str(time.time()), error="")

    def connect(self):
        def authenticate():
            args = ["config", "reconnect", "mydrive:", "--auto-confirm"] if self.connected() else [
                "config", "create", "mydrive", "drive", "scope=drive", "--auto-confirm",
            ]
            self.update(message="Finish Google sign-in in your browser.")
            with tempfile_log() as log:
                self.process = subprocess.Popen(
                    [str(RCLONE), "--config", str(CONFIG), *args],
                    stdin=subprocess.DEVNULL, stdout=log, stderr=log, **subprocess_options(),
                )
                try:
                    while self.process.poll() is None:
                        self.check_cancelled()
                        time.sleep(.2)
                    if self.process.returncode:
                        raise RuntimeError("Google sign-in didn't finish. Try connecting again.")
                finally:
                    if self.process.poll() is None:
                        self.process.terminate()
                    self.process.wait()
                    self.process = None
            self.update(busy=False, phase="ready", message="Google account connected.")
        return self._launch(authenticate)

    def paste(self):
        import pyperclip
        return pyperclip.paste()

    def cancel(self):
        self.cancelled.set()
        self.update(phase="cancelling", message="Cancelling…")
        return self.get_state()

    def pair_phone(self):
        import webview
        if self.get_state()["busy"]:
            return
        paths = self.window.create_file_dialog(webview.FileDialog.SAVE, save_filename="phone.drive-connection.json")
        if paths:
            path = Path(paths[0])
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(connection(), output)
            self.update(phase="ready", message="Open the connection file in the Android app. Delete the file after pairing.")

    def open_drive(self):
        webbrowser.open("https://drive.google.com/drive/my-drive")


def tempfile_log():
    import tempfile
    return tempfile.TemporaryFile()


def subprocess_options():
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


def main():
    import webview
    os.umask(0o077)
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    api = Api()
    api.window = webview.create_window(
        "Drive Link Importer", str(ROOT / "ui" / "index.html"), js_api=api,
        width=680, height=640, min_size=(410, 520), background_color="#101321",
    )

    def closing():
        if api.get_state()["busy"]:
            return api.window.create_confirmation_dialog("Import in progress", "Close the app and cancel this import?")
        return True

    api.window.events.closing += closing
    api.window.events.closed += api.cancel
    webview.start()


if __name__ == "__main__":
    if sys.argv[1:2] == ["--self-test"]:
        import webview
        import pyperclip
        from cryptography.hazmat.primitives.ciphers import Cipher
        assert (ROOT / "ui" / "index.html").is_file()
        assert json.loads((ROOT / "desktop" / "bundle" / "rclone-oauth.json").read_text())["client_id"]
        subprocess.run([str(RCLONE), "version"], stdout=subprocess.DEVNULL, check=True, timeout=20, **subprocess_options())
        Path(sys.argv[2]).write_text(json.dumps({"startup": "passed", "platform": sys.platform}))
    else:
        main()
