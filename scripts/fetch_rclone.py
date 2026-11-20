"""Download a pinned rclone release and verify its official SHA-256 checksum."""
import hashlib
import io
import json
from pathlib import Path
import platform
import re
import sys
from urllib.request import urlopen
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "desktop"))
from app import reveal

VERSION = "1.76.0"


def fetch(url):
    with urlopen(url, timeout=120) as response:
        return response.read()


def main():
    system = {"Windows": "windows", "Darwin": "osx", "Linux": "linux"}[platform.system()]
    arch = {"AMD64": "amd64", "x86_64": "amd64", "arm64": "arm64", "aarch64": "arm64"}[platform.machine()]
    package = f"rclone-v{VERSION}-{system}-{arch}"
    base = f"https://downloads.rclone.org/v{VERSION}"
    checksums = fetch(base + "/SHA256SUMS").decode()
    expected = next(line.split()[0] for line in checksums.splitlines() if line.split()[-1].lstrip("*") == package + ".zip")
    archive = fetch(f"{base}/{package}.zip")
    if hashlib.sha256(archive).hexdigest() != expected:
        raise RuntimeError("rclone checksum verification failed")
    destination = ROOT / "desktop" / "bundle"
    destination.mkdir(exist_ok=True)
    binary = "rclone.exe" if system == "windows" else "rclone"
    with ZipFile(io.BytesIO(archive)) as zipfile:
        output = destination / binary
        output.write_bytes(zipfile.read(f"{package}/{binary}"))
        output.chmod(0o755)
    source = fetch(f"https://raw.githubusercontent.com/rclone/rclone/v{VERSION}/backend/drive/drive.go").decode()
    defaults = {
        "client_id": re.search(r'rcloneClientID\s*=\s*"([^"]+)"', source).group(1),
        "client_secret": reveal(re.search(r'rcloneEncryptedClientSecret\s*=\s*"([^"]+)"', source).group(1)),
    }
    (destination / "rclone-oauth.json").write_text(json.dumps(defaults))
    license_text = fetch(f"https://raw.githubusercontent.com/rclone/rclone/v{VERSION}/COPYING")
    (destination / "rclone-LICENSE.txt").write_bytes(license_text)
    print(f"Verified and bundled rclone {VERSION} ({system}, {arch}).")


if __name__ == "__main__":
    main()
