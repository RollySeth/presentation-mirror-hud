# Raspberry Pi setup

## 1. Prepare the hardware and OS

Use the hardware checklist in the root README. Back up the microSD card before
writing an OS: imaging erases it. Install the current **Raspberry Pi OS 64-bit
with Desktop** using the official Raspberry Pi Imager. Create your own device
account and unique password; none is supplied by this project. Enable SSH only
if needed and prefer SSH keys.

Boot with the monitor, keyboard and mouse connected. Update the OS and select a
portrait rotation in the desktop's display settings. Which rotation is correct
depends on the physical mount; 1080x1920 is the reference HUD layout. A two-way
mirror in front of a screen normally does not require software text mirroring.
Press `M` if text is reversed.

## 2. Get the source

Replace `REPOSITORY_URL` with the HTTPS clone URL from this repository's Code
button. The service examples expect this directory:

```bash
git clone REPOSITORY_URL "$HOME/presentation-mirror-hud"
cd "$HOME/presentation-mirror-hud"
sudo apt update
grep -vE '^[[:space:]]*(#|$)' deploy/apt-packages.txt | xargs sudo apt install -y
python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install -r requirements.txt
```

The distro provides Pygame, OpenCV and NumPy efficiently on ARM; the virtual
environment can reuse them. Never replace the OS Python or use `sudo pip`.
Python 3.11 or later is required. Initial installation needs internet access.

## 3. Start the companion API

```bash
cd "$HOME/presentation-mirror-hud"
.venv/bin/python -m presenter_hud.api
```

It listens on `127.0.0.1:8765`, not every network interface. On first start it
creates a private token outside the repository:
`~/.config/presentation-mirror-hud/controller.token`.

Open `http://127.0.0.1:8765` in the Pi's browser. Read that file **locally** and
paste its value into the pairing field. Never include it in screenshots,
issues, source files, URLs, terminal recordings or chat messages. See the
[companion guide](../companion/README.md) for access from another device.

## 4. Start the display in a second terminal

Run from the Pi's graphical desktop:

```bash
cd "$HOME/presentation-mirror-hud"
.venv/bin/python -m presenter_hud.hud --size 1080x1920
```

For a landscape development monitor use `--windowed --size 1280x720`.
The companion controls sessions and uploads. Keyboard controls:
`Space` pause/resume, `Left`/`Right` slides, `M` mirror, `Q`/`Esc` quit.
Use large text, high contrast and simple slide designs behind tinted mirrors.

## 5. Optional camera

Connect a standard USB UVC camera. No camera serial is configured:

```bash
.venv/bin/python -m presenter_hud.sensor_runner --camera --camera-device 0 --interval 1
```

Camera `0` is a generic default, not a guarantee. On Linux inspect available
devices with `ls /dev/video*`; use `--camera-device /dev/video0` or the appropriate
local device. Keep machine-specific identifiers in private local configuration,
not in this repository. Only one process should open a camera at a time.

The baseline mode uses face presence/centering and a limited smile indicator.
For optional seven-class **expression estimates**:

```bash
.venv/bin/python deploy/setup_expression_models.py
.venv/bin/python -m presenter_hud.sensor_runner --camera --camera-device 0 \
  --interval 1 --expression-models models/expressions
```

Stop the previous camera worker before running the second command. Models are
approximately 5 MB total and are downloaded with checksum/license verification.
No recognized identity or raw frame is saved.

## 6. Optional microphone and offline speech

```bash
.venv/bin/python -m pip install -r requirements-speech.txt
.venv/bin/python -m sounddevice
.venv/bin/python deploy/speech_prepare.py --pi3-profile
.venv/bin/python -m presenter_hud.speech_runner \
  --model models/vosk-model-small-en-us-0.15-pi3 --count-you-know
```

The device list helps choose an input. Omission uses the system default;
override with `--audio-device INPUT_INDEX` after replacing `INPUT_INDEX` with
the local microphone's index. The tuned Pi 3 model trades some decoding accuracy
for CPU headroom. Larger Pis can use the original downloaded model directory
instead. Recognition starts during an active rehearsal; idle capture supplies
only microphone level. Stop releases the recognizer.

**One microphone owner:** do not run the camera worker with `--audio` while
the speech worker is running. Both camera and speech are optional; the HUD and
companion work without them.

## 7. Optional desktop autostart

First verify every desired component manually. The following examples target
the Wayland desktop on current Raspberry Pi OS, not a headless SSH session:

```bash
mkdir -p "$HOME/.config/systemd/user" "$HOME/.config/autostart"
cp deploy/smartmirror-*.service "$HOME/.config/systemd/user/"
cp deploy/smartmirror.desktop "$HOME/.config/autostart/"
systemctl --user daemon-reload
systemctl --user import-environment WAYLAND_DISPLAY DISPLAY XDG_RUNTIME_DIR
systemctl --user enable --now smartmirror-api.service
systemctl --user start smartmirror-hud.service
```

The base display does **not** automatically enable sensors or load models.
After installing dependencies/models, opt in:

```bash
systemctl --user add-wants smartmirror-hud.service smartmirror-sensors.service
systemctl --user start smartmirror-sensors.service
# Only if offline speech is installed:
systemctl --user add-wants smartmirror-hud.service smartmirror-speech.service
systemctl --user start smartmirror-speech.service
```

Customize services using `systemctl --user edit SERVICE_NAME`, not by committing
your local configuration. To select a microphone or expression models, clear
`ExecStart=` in the override before supplying the complete replacement command.
Keep the API on loopback when using an SSH tunnel.

These templates assume `~/presentation-mirror-hud` with no spaces in its path.
Desktop/session setup can differ across OS releases; if the compositor
environment is unavailable, run the HUD manually from a desktop terminal.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Companion unreachable from a phone | Loopback is intentional; configure a secure connection as described in the companion guide |
| Text reversed | Toggle `M` on the HUD keyboard; display rotation is separate |
| Camera off / no face | Device selection, USB connection, lighting and competing camera processes |
| Microphone off / words stay at zero | Input selection, active unpaused rehearsal, local model path, background noise and worker logs |
| HUD closes immediately | Run in a desktop terminal and inspect the error; check the selected SDL/display backend |
| Model setup fails | Do not bypass checksums; check disk space/network and the explicit installer error |
| Cards show estimates or no face | These are confidence/freshness-gated signals, not guaranteed classifications |

Use `systemctl --user status SERVICE_NAME` and
`journalctl --user -u SERVICE_NAME --no-pager` for local diagnostics. Remove
private paths, tokens and session content before sharing logs.
