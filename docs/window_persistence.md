# Remember window sizes and positions

Issue #18 makes the native desktop launcher restore the main WebUI and
avatar-stage windows where the user left them.

The following state is stored independently for each window:

- X and Y position
- Width and height
- Maximized or restored state

Geometry is measured in pywebview logical pixels, so it follows the
operating system's display scaling rather than raw physical pixels.

## Setting

Persistence is controlled by:

```json
{
  "general": {
    "remember_window_positions": true
  }
}
```

When disabled, Project Akira uses its normal default sizes and lets the
operating system center the windows. No new bounds are saved.

## Command-line overrides

Saved values are defaults, not restrictions. Explicit command-line
options override them for that launch:

```powershell
python desktop.py --width 1400 --height 900 --x 100 --y 80
python desktop.py --maximized
python desktop.py --windowed

python desktop.py --avatar-width 500 --avatar-height 800
python desktop.py --avatar-x 1450 --avatar-y 80
python desktop.py --avatar-maximized
python desktop.py --avatar-windowed
```

The final normal bounds are saved when each native window closes. While
a window is maximized, Project Akira keeps its previous restored bounds
so maximizing does not overwrite them with full-screen dimensions.
