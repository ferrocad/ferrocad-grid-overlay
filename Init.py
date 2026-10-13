"""FreeCAD startup hook for the Grid Overlay GUI initializer."""

import os
import runpy
import FreeCAD as App


try:
    import FreeCADGui as Gui

    # FreeCAD does not always evaluate InitGui.py for standalone Mod folders.
    # Bootstrap it here when the GUI is available. InitGui.py stores its
    # manager on Gui, so a later normal InitGui evaluation remains harmless.
    if hasattr(Gui, "getMainWindow") and not getattr(Gui, "_GridOverlayManager", None):
        try:
            module_dir = os.path.dirname(os.path.abspath(__file__))
        except NameError:
            # FreeCAD executes Mod Init.py files with exec(), without __file__.
            module_dir = os.path.join(App.getUserAppDataDir(), "Mod", "GridOverlay")
        runpy.run_path(
            os.path.join(module_dir, "InitGui.py"),
            run_name="GridOverlay_InitGui",
        )
except Exception as exc:
    try:
        App.Console.PrintWarning("Grid Overlay: startup failed: %s\n" % exc)
    except Exception:
        pass
