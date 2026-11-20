"""Build the Android app with the Android SDK and Java, without Gradle."""
import argparse
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]


def run(*args, **kwargs):
    subprocess.run([str(arg) for arg in args], check=True, **kwargs)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug", action="store_true", help="Build a USB-provisioning variant with run-as support")
    parser.add_argument("--unsigned", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "release-assets" / "Drive-Link-Importer-Android.apk")
    args = parser.parse_args()
    sdk = Path(os.environ.get("ANDROID_HOME", os.environ.get("ANDROID_SDK_ROOT", Path.home() / "Android" / "Sdk")))
    tools = Path(os.environ["ANDROID_BUILD_TOOLS"]) if os.environ.get("ANDROID_BUILD_TOOLS") else sorted((sdk / "build-tools").iterdir())[-1]
    android = Path(os.environ["ANDROID_JAR"]) if os.environ.get("ANDROID_JAR") else sorted((sdk / "platforms").glob("*/android.jar"))[-1]
    build = Path(os.environ.get("ANDROID_BUILD_DIR", ROOT / ".build" / "android"))
    classes = build / "classes"
    dex = build / "dex"
    for directory in (build, classes, dex):
        directory.mkdir(parents=True, exist_ok=True)
    sources = sorted((ROOT / "android" / "src").rglob("*.java"))
    if os.environ.get("ECJ_JAR"):
        run("java", "-jar", os.environ["ECJ_JAR"], "-8", "-proc:none", "-warn:none",
            "-classpath", android, "-d", classes, *sources)
    else:
        run("javac", "--release", "8", "-cp", android, "-d", classes, *sources)
    class_files = sorted(classes.rglob("*.class"))
    run(tools / "d8", "--lib", android, "--min-api", "26", "--output", dex, *class_files)
    manifest = (ROOT / "android" / "AndroidManifest.xml").read_text()
    manifest = manifest.replace('package="local.drivelink.importer"',
                                'package="local.drivelink.importer" android:versionCode="1" android:versionName="1.0.0"')
    if args.debug:
        manifest = manifest.replace('android:debuggable="false"', 'android:debuggable="true"')
    (build / "AndroidManifest.xml").write_text(manifest)
    unsigned = build / "unsigned.apk"
    run(tools / "aapt", "package", "-f", "-M", build / "AndroidManifest.xml",
        "-S", ROOT / "android" / "res", "-A", ROOT / "ui", "-I", android, "-F", unsigned)
    with ZipFile(unsigned, "a", ZIP_DEFLATED) as archive:
        archive.write(dex / "classes.dex", "classes.dex")
    aligned = build / "aligned.apk"
    run(tools / "zipalign", "-f", "4", unsigned, aligned)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.unsigned:
        shutil.copy2(aligned, args.output)
        return
    key = Path(os.environ.get("ANDROID_KEYSTORE", build / "signing.p12"))
    password_file = key.with_suffix(".password")
    if not password_file.exists():
        descriptor = os.open(password_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "w") as output:
            output.write(secrets.token_urlsafe(36))
    env = {**os.environ, "LINKDRIVE_STORE_PASS": password_file.read_text().strip()}
    if not key.exists():
        run("keytool", "-genkeypair", "-keystore", key, "-storetype", "PKCS12", "-alias", "drivelink",
            "-storepass:env", "LINKDRIVE_STORE_PASS", "-keyalg", "RSA", "-keysize", "3072",
            "-validity", "10000", "-dname", "CN=Drive Link Importer", env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        key.chmod(0o600)
    run(tools / "apksigner", "sign", "--ks", key, "--ks-pass", "env:LINKDRIVE_STORE_PASS",
        "--v4-signing-enabled", "false", "--out", args.output, aligned, env=env)
    run(tools / "apksigner", "verify", "--verbose", args.output)


if __name__ == "__main__":
    main()
