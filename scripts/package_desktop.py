"""Build a standalone desktop app on the current operating system."""
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    subprocess.run([sys.executable, str(ROOT / "scripts" / "fetch_rclone.py")], check=True)
    output = ROOT / ".build" / "desktop"
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
               "--name", "Drive Link Importer", "--distpath", str(output),
               "--workpath", str(ROOT / ".build" / "pyinstaller"), "--specpath", str(ROOT / ".build"),
               "--add-data", str(ROOT / "ui") + os.pathsep + "ui",
               "--add-data", str(ROOT / "desktop" / "bundle") + os.pathsep + "desktop/bundle"]
    if sys.platform == "win32":
        command.append("--onefile")
    else:
        command.extend(["--osx-bundle-identifier", "local.drivelink.importer"])
    command.append(str(ROOT / "desktop" / "app.py"))
    subprocess.run(command, check=True, cwd=ROOT)
    executable = output / "Drive Link Importer.exe" if sys.platform == "win32" else (
        output / "Drive Link Importer.app" / "Contents" / "MacOS" / "Drive Link Importer")
    report = output / "startup-check.json"
    subprocess.run([str(executable), "--self-test", str(report)], check=True, timeout=90)
    if json.loads(report.read_text())["startup"] != "passed":
        raise RuntimeError("The packaged app failed its startup check.")
    releases = ROOT / "release-build"
    releases.mkdir(exist_ok=True)
    if sys.platform == "win32":
        shutil.copy2(output / "Drive Link Importer.exe", releases / "Drive-Link-Importer-Windows-x64.exe")
    else:
        arch = "Apple-Silicon" if platform.machine() == "arm64" else "Intel"
        target = releases / f"Drive-Link-Importer-macOS-{arch}.zip"
        subprocess.run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent",
                        str(output / "Drive Link Importer.app"), str(target)], check=True)


if __name__ == "__main__":
    main()
