"""Install and securely pair an Android phone with the desktop Drive connection."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "desktop"))
from app import connection

PACKAGE = "local.drivelink.importer"


def run(*args, **kwargs):
    return subprocess.run(["adb", *args], check=True, **kwargs)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--provisioning-apk", type=Path, required=True)
    parser.add_argument("--release-apk", type=Path, default=ROOT / "release-assets" / "Drive-Link-Importer-Android.apk")
    args = parser.parse_args()
    connected = run("devices", capture_output=True, text=True).stdout
    if len([line for line in connected.splitlines()[1:] if line.endswith("\tdevice")]) != 1:
        raise SystemExit("Connect one Android phone and allow USB debugging.")
    credentials = json.dumps(connection()).encode()
    run("install", "-r", str(args.provisioning_apk))
    run("shell", "run-as", PACKAGE, "mkdir", "-p", "files")
    run("shell", "run-as", PACKAGE, "sh", "-c", "'umask 077; cat > files/connection.pending'",
        input=credentials, stdout=subprocess.DEVNULL)
    run("shell", "am", "start", "-W", "-n", PACKAGE + "/.MainActivity",
        stdout=subprocess.DEVNULL)
    time.sleep(1)
    files = run("shell", "run-as", PACKAGE, "ls", "files", capture_output=True, text=True).stdout
    if "connection.enc" not in files or "connection.pending" in files:
        raise SystemExit("The phone couldn't secure the Drive connection. Release installation stopped.")
    run("install", "-r", str(args.release_apk))
    run("shell", "am", "start", "-W", "-n", PACKAGE + "/.MainActivity")
    package = run("shell", "pm", "path", PACKAGE, capture_output=True, text=True).stdout
    if not package.startswith("package:"):
        raise SystemExit("Couldn't verify the installed app.")
    print("Installed and paired. Credentials are encrypted in the app's private storage.")


if __name__ == "__main__":
    main()
