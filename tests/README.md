# Tests

## The self-contained suite

```sh
python3 -m unittest discover -s tests -v
```

`test_mod.py` needs neither FerroCAD, Coin nor a GPU. It installs fakes for
`FreeCAD`, `FreeCADGui` and `pivy.coin` into `sys.modules` (parameter groups,
documents and observers, the command registry, the pull overlay bridge and the
provider registry, plus the Coin nodes the mod builds), loads `InitGui.py` by
path, and asserts:

- the command id `FerroCADGridOverlay_Settings`, its menu text and the
  six-field dialog spec (`title`, `accept`, `fields`, `on_accept`);
- the knob names, defaults and live seeding, and that a bad colour is rejected
  before it reaches the parameter store;
- `FreeCADGui.addOverlay(root)` on enable and `removeOverlay(root)` on disable;
- `_rebind()` removing **then** re-adding the same root, and the new values
  landing in the stage's value-copied parameter list;
- one overlay per open document: create/activate binds the active document and
  unbinds the others, delete drops that document's grid;
- the provider declaration (`feature="grid"`,
  `backend="ferrocad-grid-overlay"`, label, command, priority 0, activate and
  deactivate callbacks) and that `register_provider()` is re-callable and
  degrades to `False` when the facade has no registry;
- that an inactive backend returns a `status` naming the active provider and
  `Mods_Settings` instead of drawing or applying a stale dialog;
- that the module imports no Qt binding and never touches `getSceneGraph`
  (checked structurally with `ast`, not by substring).

The fakes are deliberately small: they model the arities the mod uses
(`setValue`, 3-argument `setValues`, `set1Value`, `setNum`, `addChild`,
`removeChild`, `set("write FALSE")`), not Coin.

## Manual integration with a real FerroCAD

The default suite proves the mod's side of the contract. A real FerroCAD is
still needed to prove the host side (the Rust `pivy.coin` facade, extraction and
drawing). Neither step below is part of `unittest discover`.

### 1. Headless smoke test (no GPU, no Coin)

`FreeCAD`/`FreeCADGui` import from FerroCAD's `python/` directory. Its provider
registry and dialog protocol work headlessly; `import pivy.coin` does not exist
in a plain interpreter, which the mod tolerates (the command reports it).

```sh
FERROCAD=/path/to/ferrocad
PYTHONPATH="$FERROCAD/python" python3 - <<'PY'
import importlib.util
import FreeCADGui

path = "/path/to/ferrocad-grid-overlay/InitGui.py"  # adjust
spec = importlib.util.spec_from_file_location("FerroCADGridOverlay_InitGui", path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

print(FreeCADGui.providers("grid"))          # this backend in the manifest
print(FreeCADGui.activeProvider("grid"))     # None when it is the only one
spec = mod.FerroCADGridOverlayCommand().Activated()
print(sorted(field["name"] for field in spec["fields"]) if "fields" in spec else spec)
PY
```

Expected: the manifest lists `ferrocad-grid-overlay` with
`command="FerroCADGridOverlay_Settings"` and `priority=0`; `Activated()` either
returns the six fields or a `status` saying the Coin facade is unavailable
(`pivy.coin`), which is the expected plain-interpreter result.

### 2. In the app

Copy or symlink the repository into FerroCAD's `mods/` directory (for a
development checkout, `mods/ferrocad-grid-overlay`), then start FerroCAD:

```sh
cd "$FERROCAD"
cargo run -p ferrocad_gpui
```

Then:

1. Open a document.
2. Run `Mods_Settings`, choose **FerroCAD Grid Overlay (WGSL)** for `grid`, and
   accept. The native grid should disappear and the WGSL grid should draw
   through the overlay bridge.
3. Run `FerroCADGridOverlay_Settings`, change the colour, opacity and spacing,
   and accept. The grid should redraw with the new values (the mod re-registers
   the tree; there is no live graph).
4. Toggle **Show custom grid** off: the overlay should disappear.
5. Switch back to **Semantic (native)** in `Mods_Settings`: the overlay must be
   removed, and running the command while the semantic backend is active must
   return a status, not a dialog.
6. If the build has it, check the viewport console for a shader compile error;
   `Resources/grid_overlay.wgsl` is byte-identical to the crate reference, so a
   failure there is a host regression, not a mod one.
