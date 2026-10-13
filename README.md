# Grid Overlay for FreeCAD

![Grid Overlay demo](https://raw.githubusercontent.com/makzumi/freecad-grid-overlay/main/Resources/Media/demo.png)

A lightweight, procedural grid overlay for the FreeCAD 3D viewport. The grid is
drawn by a Coin3D/GLSL shader on a single full-screen quad — the fragment
shader intersects each pixel's view ray with the z=0 plane and renders
anti-aliased, level-of-detail-blended lines. There is no per-frame Python work:
no camera sensor, no polling timer, no geometry rebuilds.

An optional soft ground shadow is generated beneath visible document geometry,
updated on demand from document events (never polled).

## Features

- **Procedural grid** — infinite z=0 grid rendered in GLSL, with automatic
  level-of-detail blending between minor and major lines.
- **Configurable appearance** — grid color, opacity, and base spacing scale.
- **Optional ground shadow** — soft radial-falloff shadow beneath visible
  geometry, with adjustable opacity.
- **Pick-proof & unlit** — the overlay does not interfere with selection or
  picking, and uses a base-color light model so it is unaffected by scene
  lighting.
- **Depth-safe** — the blended grid writes to the depth buffer with writes
  disabled, so it never hides the shadow beneath it.
- **Toolbar & settings dialog** — a **Grid Overlay** toolbar button opens the
  settings dialog; the button persists across workbenches.
- **Multi-document** — tracks open documents and attaches to the active 3D
  view, including views opened after startup.

## Installation

Place the `GridOverlay` folder in your FreeCAD user `Mod` directory, then
restart FreeCAD. The usual locations are:

- **Windows:** `%APPDATA%\FreeCAD\Mod` (typically `C:\Users\<username>\AppData\Roaming\FreeCAD\Mod`)
- **Linux:** `~/.local/share/FreeCAD/Mod`
- **macOS:** `~/Library/Preferences/FreeCAD/Mod`

Some FreeCAD builds use a version-specific user directory; in that case, use
the `Mod` folder inside that directory.

## Usage

The **Grid Overlay** toolbar appears in the main window. Click the grid button
to open **Settings**:

| Setting              | Description                                              |
| -------------------- | -------------------------------------------------------- |
| Show custom grid     | Toggle the procedural grid overlay on/off.              |
| Show ground shadow   | Toggle the optional soft shadow beneath geometry.       |
| Ground shadow opacity| Opacity of the ground shadow.                            |
| Base spacing scale   | Multiplier on the base grid spacing (0.1–100).           |
| Opacity              | Overall grid opacity.                                    |
| Grid color           | Color of the grid lines.                                 |

The grid is shown automatically whenever a document is open and the overlay is
enabled. It regenerates from the viewport's visible area as you navigate, and
the shadow updates on geometry changes (debounced by 150 ms).

## Requirements

- FreeCAD (tested on the 27-1 release line)
- PySide (2 or 6) for the settings dialog
- `pivy` (Coin3D) for the scene-graph nodes

## License

See the project license file for details.