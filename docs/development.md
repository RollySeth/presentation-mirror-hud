# Desktop development

The Pi display, browser companion and optional workers share the Python source
under `src/presenter_hud`. No Node build or cloud account is needed.

## Windows

Install Python 3.11+ and Git, clone this repository, then open PowerShell in its
root:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
.\.venv\Scripts\python -m presenter_hud.api
```

In a second terminal in the same directory:

```powershell
.\.venv\Scripts\python -m presenter_hud.hud --windowed --size 1080x1920
```

For a shorter development window, use `--size 540x960`. Pair at
`http://127.0.0.1:8765` using the generated private token at
`$HOME\.config\presentation-mirror-hud\controller.token`.

## Linux

Install Python 3.11+ and venv support, then:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m presenter_hud.api
# In a second terminal:
.venv/bin/python -m presenter_hud.hud --windowed --size 540x960
```

Microphone support additionally needs PortAudio (`libportaudio2` on Debian-based
systems). Use `requirements-sensors.txt` and `requirements-speech.txt` only for
optional features. Model assets are separate; see the Pi guide.

## Test and inspect dependencies

```bash
python -m pytest -q
python -m pip_audit --local --skip-editable
```

Use the virtual environment's Python (activate it or use its full path).
Most tests use fake inputs and require no hardware. Native model tests skip
unless their optional dependencies/assets are available. Do not turn on a real
camera or microphone to produce documentation or test fixtures.

Dependency manifests declare compatible ranges. Audit the packages actually
installed on each target and keep the OS, Python and packages updated. An audit
only covers known advisories, not every vulnerability or bundled native library.

## Demo artwork

```bash
python tools/render_demo.py
```

This generates the HUD screenshot and a sample image from synthetic data only,
without reading user state or opening sensors. It does not launch the API.
The companion screenshot shows the same kind of simulated session after pairing;
the pairing secret and browser/address chrome must not be captured.

## Contributing safely

Read [SECURITY.md](../SECURITY.md) before adding uploads, telemetry or network
features. Keep tests hardware-free where possible. Do not commit runtime
`data/`, model weights, device paths with serials, credentials, recordings,
private presentations, generated logs or old Git history.

Before any contribution, inspect `git diff --cached` and the exact staged file
list. Ignore patterns are convenience, not a security boundary. Use clearly
synthetic fixtures and explicitly approved, licensed assets.
