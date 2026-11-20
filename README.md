# Drive Link Importer

A desktop and Android app that copies shared Google Drive files and folders into **My Drive → Drive Link Imports**.

Download the Android APK, Windows x64 executable, or macOS Intel / Apple Silicon app from [Releases](https://github.com/zero-creation2008/link-to-drive/releases).

## Use

1. Open the desktop app and choose **Connect Google**.
2. Paste a Google Drive file or folder link.
3. Enter an optional name, or leave it blank to use the original Drive name.
4. Choose **Import link**.

Folders are scanned before copying, so the percentage uses a fixed total of file and folder operations. The app keeps the folder hierarchy, native Google documents, and file extensions. Files are copied inside Google Drive. Each folder import creates a new destination folder.

To connect Android, choose **Pair phone** in the desktop app. Transfer the exported connection file to your phone and choose **Connect Drive** in the Android app. This grants the phone access to the same connected account. Delete the exported connection file after pairing. Android stores its connection encrypted with Android Keystore and excludes it from backups.

The APK also accepts Drive links shared from another Android app. A foreground notification keeps imports running while the app is in the background. Cancelled or interrupted imports retain files already copied.

## Desktop development

Python 3.12 is used by the release builds. Install the [Windows WebView2 runtime](https://developer.microsoft.com/en-us/microsoft-edge/webview2/) if it is missing.

```sh
python -m pip install -r desktop/requirements.txt
python scripts/fetch_rclone.py
python desktop/app.py
```

`scripts/package_desktop.py` bundles the web interface, Python host, and a checksum-verified rclone binary with PyInstaller. Build separately on Windows, macOS Intel, and macOS Apple Silicon. The GitHub Actions workflow produces these three builds and publishes them together.

The desktop connection is stored in the platform's user configuration directory. Linux uses `~/.config/drive-link-importer/rclone.conf`, matching the original desktop app. Google authorization uses rclone. [rclone's Drive documentation](https://rclone.org/drive/) covers custom OAuth clients, scopes, and quota limits.

## Android development

Install Java and the Android SDK, including a platform and Build Tools. Set `ANDROID_HOME`, then run the build script.

```sh
python scripts/build_android.py
```

Optional environment variables are `ANDROID_JAR`, `ANDROID_BUILD_TOOLS`, `ECJ_JAR`, `ANDROID_BUILD_DIR`, and `ANDROID_KEYSTORE`. Signing keys and passwords stay in the build directory. Reuse the same key for updates.

The committed APK is signed with a private local key. CI compiles the Android source and verifies that APK's signature before publishing it. Maintainers must rebuild the signed APK when changing the Android source or shared UI. The default release build disables Android debugging.

For a locally authorized USB device, create a provisioning variant with `--debug --output phone-provisioning.apk`, then run `scripts/install_phone.py --provisioning-apk phone-provisioning.apk`. This pairs through ADB stdin and reinstalls the signed release over the provisioning variant. The final installed app has debugging disabled.

## Release status

Windows and macOS packages are unsigned personal builds. Signing and Apple notarization require the maintainer's signing credentials. The app reports Google Drive permission, storage, and API quota errors. The availability of the shared rclone OAuth client affects Google connectivity.
