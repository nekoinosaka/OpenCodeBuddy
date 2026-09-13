<p align="center">
  <a href="./README.md">
    <img alt="English" src="https://img.shields.io/badge/English-111111?style=for-the-badge" />
  </a>
  <a href="./README.zh-CN.md">
    <img alt="简体中文" src="https://img.shields.io/badge/简体中文-EAEAEA?style=for-the-badge&labelColor=EAEAEA&color=111111" />
  </a>
</p>

<p align="center">
  <img src="screenshots/cover.webp" alt="Code Buddy cover" width="100%" />
</p>

<h1 align="center">Code Buddy</h1>

<p align="center">
  A StickS3 companion for <a href="https://opencode.ai">OpenCode</a>, adapted from
  <a href="https://github.com/anthropics/claude-desktop-buddy">Claude Desktop Buddy</a>
  and <a href="https://github.com/CharlexH/CodeBuddy">CodeBuddy</a>.
</p>

<p align="center">
  Flash the device once, run <code>code-buddy</code> once on macOS, then keep using
  <code>opencode</code> normally while approvals and live session status move to dedicated hardware.
</p>

> Building your own hardware client? See [firmware/REFERENCE.md](firmware/REFERENCE.md) for the BLE protocol and JSON payloads.
>
> 中文使用文档 / Chinese usage guide: [docs/USAGE.zh-CN.md](docs/USAGE.zh-CN.md).

## What ships

- A macOS bridge that pairs with the StickS3, syncs time, installs the native BLE helper, and installs an OpenCode plugin.
- An OpenCode plugin that forwards live session events and routes permission prompts to the device.
- A StickS3 firmware build with status, approval, settings, and offline screens.
- A daily workflow designed to stay out of the way: run `code-buddy` once, then just use `opencode`.

## How it works

Code Buddy has three moving parts:

1. **Device firmware** advertises the Nordic UART Service as `OpenCode-XXXX` and renders the pet, stats, and approval screens.
2. **`code-buddy` agent** runs as a launchd service, owns the Bluetooth link, and keeps the device snapshot updated.
3. **OpenCode plugin** (`~/.config/opencode/plugins/code-buddy.js`) runs inside OpenCode. On startup it hands the agent the server URL, forwards bus events (`session.status`, `message.updated`, `message.part.updated`, `permission.*`), and implements the `permission.ask` hook so the device can approve or deny.

When a permission prompt is pending, it is shown on the StickS3: **A** approves once, **B** denies. If the device is offline, the prompt falls back to the normal OpenCode UI (the agent waits up to 60 seconds, then returns `ask`).

Session status and token totals are projected from live plugin events. The optional read-only session watcher can additionally read `GET /session`, but only when `OPENCODE_SERVER_URL` points at a standalone `opencode serve` instance — the TUI's embedded server is not reachable over HTTP.

## Quick start

### 1. Flash the StickS3

Download `code-buddy-sticks3-v{version}-full.bin` from Releases and flash it at `0x0`.

<details>
<summary>Flash commands</summary>

Fallback:

```bash
esptool --chip esp32s3 --port /dev/cu.usbmodem101 --baud 460800 write_flash 0x0 code-buddy-sticks3-v0.1.45-full.bin
```

Developer release build:

```bash
./scripts/build-firmware-release.sh
```

The build produces two distinct artifacts: `*-full.bin` is the USB recovery/
bootstrap image, while `*-app.bin` is the application-only image accepted by
OTA. Never pass the merged full image to the OTA command.
</details>

### 2. Install on macOS

Build from source:

```bash
git clone https://github.com/nekoinosaka/CodeBuddy.git
cd CodeBuddy
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/code-buddy
```

On first run, Code Buddy will:

- install the native Bluetooth helper
- pair with an `OpenCode-*` device
- sync device time
- install the launchd agent
- install the OpenCode plugin at `~/.config/opencode/plugins/code-buddy.js`

Host-only updates do not require reflashing when the installed firmware remains
protocol-compatible. Features that change the display, sound, or OTA runtime do
require the matching firmware release.

### 3. Use it normally

```bash
opencode
```

Restart OpenCode after setup so it loads the plugin. From there, Code Buddy keeps the bridge alive and shows approval prompts on the StickS3 while you keep your normal flow.

Session events arrive through the plugin, so no shell shim or wrapper is needed — run `opencode` exactly as you always do.

### Wireless firmware updates

After the OTA-capable bootstrap has been flashed once and Wi-Fi has been
provisioned from Settings, open **Settings > OTA update** on Code Buddy, then
run:

```bash
code-buddy firmware update
```

For a development build, select the app-only image explicitly:

```bash
code-buddy firmware update --firmware firmware/.pio/build/m5stack-sticks3/firmware.bin
```

The background agent remains the sole Bluetooth owner. It signs an immutable
one-time manifest using the already-pinned trust under `~/.code-buddy/ota`,
serves the image over short-lived local HTTPS, and waits for physical A-button
confirmation. B or Ctrl-C cancels before the boot slot is committed.

The native BLE helper runs as a background macOS agent during normal use, so reconnect attempts should not open a helper window or steal focus. macOS may still show the first Bluetooth permission prompt; that system prompt cannot be skipped. For helper debugging, start it with `CODE_BUDDY_BLE_HELPER_DEBUG_WINDOW=1` to show the event log window.

## Controls

|                         | Normal               | Pet         | Info        | Approval    |
| ----------------------- | -------------------- | ----------- | ----------- | ----------- |
| **A** (front)           | next screen          | next screen | next screen | **approve** |
| **B** (right)           | scroll transcript    | next page   | next page   | **deny**    |
| **Hold A**              | menu                 | menu        | menu        | menu        |
| **Power** (left, short) | toggle screen off    |             |             |             |
| **Power** (left, ~6s)   | hard power off       |             |             |             |
| **Shake**               | dizzy                |             |             | —           |
| **Face-down**           | nap (energy refills) |             |             |             |

The screen auto-powers off after 30 seconds of inactivity and stays on while an approval prompt is pending. Any button press wakes it.

## Buddy states

| State       | Trigger                     | Feel                        |
| ----------- | --------------------------- | --------------------------- |
| `sleep`     | bridge not connected        | eyes closed, slow breathing |
| `idle`      | connected, nothing urgent   | blinking, looking around    |
| `busy`      | sessions actively running   | sweating, working           |
| `attention` | approval pending            | alert, **LED blinks**       |
| `celebrate` | level up (50K tokens), Friday clock | confetti, bouncing          |
| `dizzy`     | you shook the stick         | spiral eyes, wobbling       |
| `heart`     | approved in under 5s        | floating hearts             |

When the StickS3 is on USB power, its RTC retains valid time, and there is no running or waiting session, it can show the charging clock. The device only needs one successful time sync: after a normal reboot it can keep using retained RTC time while the Mac is temporarily offline. If RTC power is lost and the clock resets to 2000-01-01, it still waits for the next trusted sync. On Fridays from 15:00 until midnight, the pet occasionally celebrates: about 4 seconds in each 12-second cycle.

<details>
<summary><strong>Characters and custom packs</strong></summary>

The firmware ships with eighteen ASCII pets. Each one includes seven animations: `sleep`, `idle`, `busy`, `attention`, `celebrate`, `dizzy`, and `heart`.

Use `menu -> next pet` on the device to cycle through them. The selection is saved in device storage.

If you want a custom GIF character, create a pack with a `manifest.json` and 96px-wide GIFs for the same seven states:

```json
{
  "name": "bufo",
  "colors": {
    "body": "#6B8E23",
    "bg": "#000000",
    "text": "#FFFFFF",
    "textDim": "#808080",
    "ink": "#000000"
  },
  "states": {
    "sleep": "sleep.gif",
    "idle": ["idle_0.gif", "idle_1.gif", "idle_2.gif"],
    "busy": "busy.gif",
    "attention": "attention.gif",
    "celebrate": "celebrate.gif",
    "dizzy": "dizzy.gif",
    "heart": "heart.gif"
  }
}
```

Notes:

- `idle` can be a single GIF or an array of GIFs.
- Heights up to about 140px fit well on the StickS3 screen.
- See [firmware/characters/bufo/](firmware/characters/bufo/) for a working example.
- Use [firmware/tools/prep_character.py](firmware/tools/prep_character.py) and [firmware/tools/flash_character.py](firmware/tools/flash_character.py) to prepare and flash assets.
</details>

## Recovery

```bash
code-buddy doctor
code-buddy repair
code-buddy uninstall
```

`doctor` explains what is wrong, why it happened, and what to do next.

<details>
<summary><strong>Build from source</strong></summary>

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/code-buddy
```

Verification:

- Host tests: `.venv/bin/pytest -q`
- Firmware build: `cd firmware && pio run`
</details>
