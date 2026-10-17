# 📦 Inbound Surveillance — Champei Spa (Windows Installer)

## 🚀 Package Overview

| Attribute | Details |
| :--- | :--- |
| **Package File** | `Inbound Surveillance_0.1.6_x64-setup.exe` |
| **Type** | Windows installer (NSIS) — one double-click |
| **Target OS** | Windows 10 / Windows 11 (64-bit) |
| **Included AI Models** | Person detection, pose, role classification, ReID (`.onnx`, `.pt`) |
| **Runtime** | Self-contained (the camera engine is bundled as a sidecar) |
| **Dependencies** | WebView2 + Visual C++ runtime — the installer detects and installs them |

---

## 🎁 Why This Installer is Ideal for Non-Technical Customers

1. **One double-click**: the customer runs the `.exe`, clicks Install, and it is done.
2. **No Python, Git, Node.js, or terminal**: the engine is packaged inside the app.
3. **Auto-provisions dependencies**: WebView2 (the UI) and the Visual C++ runtime are installed automatically if missing.
4. **Configured out of the box**: the venue settings and the alert bot are seeded on first run — no manual setup.
5. **No login**: the dashboard opens directly on this computer.

---

## 🌐 Hosting & Distribution Options

### 🏆 Option 1: GitHub Release (Recommended for Public Direct Download)
1. Go to: [New GitHub Release](https://github.com/sothunly-alt/Ibound-survillence-V2/releases/new)
2. Create a tag (e.g., `v0.1.6`).
3. Drag & drop `Inbound Surveillance_0.1.6_x64-setup.exe` into **Attach binaries**.
4. Give the customer your direct download link:
   ```text
   https://github.com/sothunly-alt/Ibound-survillence-V2/releases/latest
   ```

### ☁️ Option 2: Cloud Storage (Google Drive / OneDrive / Dropbox)
1. Upload the `.exe` to Google Drive or OneDrive.
2. Set permissions to **"Anyone with the link can view/download"**.
3. Paste the share link in the instructions below.

### 👥 Option 3: GitHub Actions Artifact (Internal Team Access)
Builds produce the installer as an artifact named `inbound-surveillance-windows-installer`
from the `build-windows.yml` workflow.

---

## 📋 Instructions to Send to the Customer

### 1. Install
* Double-click **`Inbound Surveillance_0.1.6_x64-setup.exe`**.
* If Windows asks for permission, click **Yes**.
* Click **Install** and wait for it to finish. (It is normal for the first
  launch to take a few extra seconds while the engine starts.)

### 2. Open the app
* Launch **Inbound Surveillance** from the Start Menu or the desktop shortcut.
* The dashboard opens by itself — **no login needed**.

### 3. Connect Telegram alerts
* In the dashboard go to **Settings → Integrations → Connect Telegram**.
* Telegram opens; tap **Start** to link the chat. Alerts will arrive there.

### 4. Add the cameras
* Paste the DVR link (e.g. `rtsp://user:password@192.168.1.10:554/...`) and click **Connect**.

---

## 🔄 For Developers / Admins: Building the Installer

The Windows installer is built by CI on a Windows runner (a `.exe` cannot be
cross-compiled from Linux/macOS):

* **Workflow**: `.github/workflows/build-windows.yml`
* **Trigger**: push to `main`/`camera-detection`, or run it manually.
* **Output**: `inbound-surveillance-windows-installer` artifact containing
  `Inbound Surveillance_<version>_x64-setup.exe`.

Validate the shipped installer end-to-end on Windows with:

```powershell
pwsh scripts/windows-smoke-test.ps1 -ConfigFile edge/config.champei.yaml
```
