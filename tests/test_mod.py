"""Self-contained tests for the FerroCAD Grid Overlay mod.

Nothing here imports FerroCAD, Qt or a GPU stack. The suite installs small
fakes for ``FreeCAD``, ``FreeCADGui`` and ``pivy.coin`` (the nodes, groups and
fields the mod actually uses), loads ``InitGui.py`` by path, and checks the
contract the mod has with FerroCAD:

* the command id, menu text and the six-field dialog spec;
* ``addOverlay`` on enable, ``removeOverlay`` on disable, and the
  remove-then-re-add order of ``_rebind()`` after a parameter change;
* the provider declaration (feature, backend, label, command, priority and the
  activate/deactivate callbacks);
* per-document lifecycle through ``FreeCAD.addDocumentObserver``;
* the absence of Qt, of ``QSettings`` and of ``getSceneGraph``.

A run against a real FerroCAD build is a manual step; see ``tests/README.md``.
"""

import ast
import importlib.util
import pathlib
import sys
import types
import unittest
from unittest import mock

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
INIT_GUI = REPO_ROOT / "InitGui.py"
WGSL_PATH = REPO_ROOT / "Resources" / "grid_overlay.wgsl"

COMMAND = "FerroCADGridOverlay_Settings"
FEATURE = "grid"
BACKEND = "ferrocad-grid-overlay"
LABEL = "FerroCAD Grid Overlay (WGSL)"
KNOBS = {
    "enabled",
    "color",
    "opacity",
    "base_spacing",
    "shadow_enabled",
    "shadow_opacity",
}


# ---------------------------------------------------------------------------
# ``pivy.coin``: fields, groups and shader nodes with the mod's arities
# ---------------------------------------------------------------------------


class FakeField:
    """A Coin field: scalar access, 3-arg ``setValues`` and ``setNum``."""

    def __init__(self, *values):
        self.values = list(values)

    def setValue(self, value):
        if self.values:
            self.values[0] = value
        else:
            self.values.append(value)

    def getValue(self):
        return self.values[0] if self.values else None

    def setValues(self, start, *args):
        if len(args) == 1:
            values = list(args[0])
        else:
            num, values = args
            values = list(values)[: int(num)]
        end = start + len(values)
        if len(self.values) < end:
            self.values.extend([0.0] * (end - len(self.values)))
        self.values[start:end] = values

    def set1Value(self, index, value):
        if len(self.values) <= index:
            self.values.extend([None] * (index + 1 - len(self.values)))
        self.values[index] = value

    def setNum(self, num):
        num = int(num)
        if num < len(self.values):
            del self.values[num:]
        else:
            self.values.extend([0.0] * (num - len(self.values)))

    def __len__(self):
        return len(self.values)


class FakeNode:
    """A structural stand-in for the Coin nodes the mod constructs."""

    def __init__(self):
        self.children = []
        self.whichChild = -3
        self.renderCulling = 0
        self.source = ""
        self.name = ""
        self.value = None
        self.style = None
        self.model = None
        self.parameter = FakeField()
        self.shaderObject = FakeField()
        self.point = FakeField()
        self.numVertices = FakeField()
        self.transparency = FakeField()

    def addChild(self, child):
        self.children.append(child)

    def removeChild(self, child):
        for index, candidate in enumerate(self.children):
            if candidate is child:
                del self.children[index]
                return

    def set(self, spec):
        """``set("write FALSE")`` -- the upstream ``SoDepthBuffer`` workaround."""
        field, value = spec.split(None, 1)
        setattr(self, field, value.strip().lower() in ("true", "1", "yes", "on"))


def fake_type(name, **constants):
    return type(name, (FakeNode,), constants)


class FakeFloatArrayNode(FakeNode):
    """``SoShaderParameterArray1f``: its ``value`` is an ``SoMFFloat``."""

    def __init__(self):
        super().__init__()
        self.value = FakeField()


def build_coin():
    coin = types.ModuleType("pivy.coin")
    coin.SO_SWITCH_NONE = -1
    coin.SO_SWITCH_INHERIT = -2
    coin.SO_SWITCH_ALL = -3
    coin.SoSeparator = fake_type("SoSeparator", OFF=0, AUTO=1, ON=2)
    coin.SoSwitch = fake_type("SoSwitch")
    coin.SoDepthBuffer = fake_type("SoDepthBuffer")
    coin.SoVertexShader = fake_type("SoVertexShader")
    coin.SoFragmentShader = fake_type("SoFragmentShader")
    coin.SoShaderProgram = fake_type("SoShaderProgram")
    coin.SoCoordinate3 = fake_type("SoCoordinate3")
    coin.SoFaceSet = fake_type("SoFaceSet")
    coin.SoMaterial = fake_type("SoMaterial")
    coin.SoPickStyle = fake_type("SoPickStyle", UNPICKABLE=0, SHAPE=1)
    coin.SoLightModel = fake_type("SoLightModel", BASE_COLOR=0, PHONG=1)
    coin.SoShaderParameter1f = fake_type("SoShaderParameter1f")
    coin.SoShaderParameter3f = fake_type("SoShaderParameter3f")
    coin.SoShaderParameterArray1f = FakeFloatArrayNode
    return coin


# ---------------------------------------------------------------------------
# ``FreeCAD``: parameters, documents, observers and a quiet console
# ---------------------------------------------------------------------------


class FakeParamGroup:
    """A typed, flat parameter store (enough for one mod's knobs)."""

    def __init__(self, store):
        self._store = store

    def _get(self, name, kind, default):
        return self._store.get((name, kind), default)

    def _set(self, name, kind, value):
        self._store[(name, kind)] = value

    def GetBool(self, name, default=False):
        return bool(self._get(name, "bool", default))

    def SetBool(self, name, value):
        self._set(name, "bool", bool(value))

    def GetFloat(self, name, default=0.0):
        return float(self._get(name, "float", default))

    def SetFloat(self, name, value):
        self._set(name, "float", float(value))

    def GetString(self, name, default=""):
        return str(self._get(name, "string", default))

    def SetString(self, name, value):
        self._set(name, "string", str(value))

    def Clear(self):
        self._store.clear()


class FakeConsole:
    def __init__(self):
        self.warnings = []
        self.logs = []

    def PrintWarning(self, message):
        self.warnings.append(message)

    def PrintLog(self, message):
        self.logs.append(message)

    def PrintError(self, message):
        raise AssertionError("unexpected PrintError: %s" % message)


class FakeDocument:
    def __init__(self, name):
        self.Name = name


def build_freecad():
    app = types.ModuleType("FreeCAD")
    app.GuiUp = 0
    app.ActiveDocument = None
    app.Console = FakeConsole()
    app._documents = {}
    app._observers = []
    app._params = {}

    def ParamGet(path="", create=True):
        return FakeParamGroup(app._params.setdefault(path, {}))

    def notify(method, doc):
        for observer in list(app._observers):
            callback = getattr(observer, method, None)
            if callback is not None:
                callback(doc)

    def newDocument(name=None):
        doc = FakeDocument(name or "Unnamed")
        app._documents[doc.Name] = doc
        app.ActiveDocument = doc
        notify("slotCreatedDocument", doc)
        return doc

    def setActiveDocument(name):
        doc = app._documents[name]
        app.ActiveDocument = doc
        notify("slotActivateDocument", doc)

    def closeDocument(name):
        doc = app._documents.pop(name)
        notify("slotDeletedDocument", doc)
        if app.ActiveDocument is doc:
            app.ActiveDocument = next(iter(app._documents.values()), None)

    def listDocuments():
        return list(app._documents)

    def addDocumentObserver(observer):
        app._observers.append(observer)

    def removeDocumentObserver(observer):
        if observer in app._observers:
            app._observers.remove(observer)

    app.ParamGet = ParamGet
    app.newDocument = newDocument
    app.setActiveDocument = setActiveDocument
    app.closeDocument = closeDocument
    app.listDocuments = listDocuments
    app.addDocumentObserver = addDocumentObserver
    app.removeDocumentObserver = removeDocumentObserver
    return app


# ---------------------------------------------------------------------------
# ``FreeCADGui``: commands, the pull overlay bridge and the provider registry
# ---------------------------------------------------------------------------


class FakeGui(types.ModuleType):
    def __init__(self):
        super().__init__("FreeCADGui")
        self.overlays = []
        self.commands = {}
        self.registry = {}  # (feature, backend) -> manifest
        self.order = {}  # (feature, backend) -> registration serial
        self.active = {}
        self._serial = 0

    # -- commands -------------------------------------------------------
    def addCommand(self, name, command):
        if name in self.commands:
            raise ValueError("command %r is already registered" % name)
        self.commands[name] = command

    def removeCommand(self, name):
        self.commands.pop(name, None)

    def runCommand(self, name):
        return self.commands[name].Activated()

    # -- overlays -------------------------------------------------------
    def addOverlay(self, node):
        self.overlays.append(node)

    def removeOverlay(self, node):
        for index, candidate in enumerate(self.overlays):
            if candidate is node:
                del self.overlays[index]
                return True
        return False

    # -- providers ------------------------------------------------------
    def registerProvider(
        self,
        feature,
        backend,
        label,
        command=None,
        priority=0,
        activate=None,
        deactivate=None,
    ):
        key = (feature, backend)
        if key not in self.registry:
            self._serial += 1
            self.order[key] = self._serial
        self.registry[key] = {
            "feature": feature,
            "backend": backend,
            "label": label,
            "command": command,
            "priority": int(priority),
            "activate": activate,
            "deactivate": deactivate,
        }
        current = self.active.get(feature)
        if current is None or key[1] == current:
            self.active[feature] = backend
        else:
            current_key = (feature, current)
            if self.registry[current_key]["priority"] < int(priority):
                self.active[feature] = backend
        return dict(self.registry[key])

    def unregisterProvider(self, feature, backend):
        existed = self.registry.pop((feature, backend), None) is not None
        if self.active.get(feature) == backend:
            self.active.pop(feature, None)
        return existed

    def providers(self, feature=None):
        return [
            dict(record)
            for key, record in sorted(self.registry.items(), key=lambda item: self.order[item[0]])
            if feature is None or key[0] == feature
        ]

    def activeProvider(self, feature):
        return self.active.get(feature)

    def setActiveProvider(self, feature, backend):
        if (feature, backend) not in self.registry:
            raise ValueError("unknown backend %r for %r" % (backend, feature))
        self.active[feature] = backend


# The fakes must be importable *before* the mod is loaded, and must stay in
# ``sys.modules`` for the rest of the process (the mod holds module references).
COIN = build_coin()
PIVY = types.ModuleType("pivy")
PIVY.coin = COIN
FREECAD = build_freecad()
FREECAD_GUI = FakeGui()
sys.modules["pivy"] = PIVY
sys.modules["pivy.coin"] = COIN
sys.modules["FreeCAD"] = FREECAD
sys.modules["FreeCADGui"] = FREECAD_GUI


def load_mod():
    """Load ``InitGui.py`` by path, exactly as the shell's loader does."""
    spec = importlib.util.spec_from_file_location(
        "FerroCADGridOverlay_InitGui", INIT_GUI
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


MOD = load_mod()


def close_documents():
    for name in list(FREECAD._documents):
        FREECAD.closeDocument(name)


class ModTestCase(unittest.TestCase):
    def setUp(self):
        close_documents()
        MOD.manager.remove_grid()
        MOD.manager.items.clear()
        MOD.SETTINGS.Clear()
        FREECAD_GUI.overlays.clear()
        FREECAD.Console.warnings.clear()
        FREECAD.Console.logs.clear()
        if (FEATURE, BACKEND) in FREECAD_GUI.registry:
            FREECAD_GUI.setActiveProvider(FEATURE, BACKEND)

    def tearDown(self):
        close_documents()
        MOD.manager.remove_grid()
        MOD.manager.items.clear()
        MOD.SETTINGS.Clear()
        FREECAD_GUI.overlays.clear()

    def dialog(self):
        return MOD.FerroCADGridOverlayCommand().Activated()

    # -- the command and the dialog -------------------------------------
    def test_the_command_is_registered_with_its_menu_text(self):
        self.assertIn(COMMAND, FREECAD_GUI.commands)
        self.assertIsInstance(
            FREECAD_GUI.commands[COMMAND], MOD.FerroCADGridOverlayCommand
        )
        resources = MOD.FerroCADGridOverlayCommand().GetResources()
        self.assertEqual(resources["MenuText"], LABEL)
        self.assertTrue(resources["Pixmap"].endswith("grid.svg"))
        self.assertTrue(resources["Pixmap"].startswith(str(REPO_ROOT)))
        self.assertTrue(MOD.FerroCADGridOverlayCommand().IsActive())

    def test_activated_returns_the_six_field_dialog(self):
        spec = self.dialog()
        self.assertEqual(spec["title"], LABEL + " Settings")
        self.assertEqual(spec["accept"], "Apply")
        self.assertTrue(callable(spec["on_accept"]))
        fields = {field["name"]: field for field in spec["fields"]}
        self.assertEqual(set(fields), KNOBS)
        self.assertEqual(fields["enabled"]["value"], True)
        self.assertEqual(fields["color"]["value"], "#808080")
        self.assertAlmostEqual(fields["opacity"]["value"], 0.65)
        self.assertAlmostEqual(fields["base_spacing"]["value"], 1.0)
        self.assertEqual(fields["shadow_enabled"]["value"], True)
        self.assertAlmostEqual(fields["shadow_opacity"]["value"], 0.30)

    def test_the_dialog_seeds_from_the_live_settings(self):
        MOD.SETTINGS.SetBool("enabled", False)
        MOD.SETTINGS.SetString("color", "#123456")
        MOD.SETTINGS.SetFloat("opacity", 0.5)
        MOD.SETTINGS.SetFloat("base_spacing", 2.0)
        MOD.SETTINGS.SetBool("shadow_enabled", False)
        MOD.SETTINGS.SetFloat("shadow_opacity", 0.25)
        values = {field["name"]: field["value"] for field in self.dialog()["fields"]}
        self.assertEqual(
            values,
            {
                "enabled": False,
                "color": "#123456",
                "opacity": 0.5,
                "base_spacing": 2.0,
                "shadow_enabled": False,
                "shadow_opacity": 0.25,
            },
        )

    def test_on_accept_persists_and_returns_the_overlay_view(self):
        result = self.dialog()["on_accept"](
            {
                "enabled": True,
                "color": "#f87171",
                "opacity": 0.5,
                "base_spacing": 2.0,
                "shadow_enabled": True,
                "shadow_opacity": 0.25,
            }
        )
        self.assertEqual(result, {"view": {"grid_overlay": True}})
        self.assertTrue(MOD.setting("enabled"))
        self.assertEqual(MOD.setting("color"), "#f87171")
        self.assertAlmostEqual(MOD.setting("opacity"), 0.5)
        self.assertAlmostEqual(MOD.setting("base_spacing"), 2.0)
        self.assertTrue(MOD.setting("shadow_enabled"))
        self.assertAlmostEqual(MOD.setting("shadow_opacity"), 0.25)

    def test_a_bad_colour_raises_and_leaves_the_store_alone(self):
        with self.assertRaises(ValueError):
            self.dialog()["on_accept"]({"color": "not a colour"})
        self.assertEqual(MOD.setting("color"), "#808080")

    def test_a_build_without_the_bridge_reports_instead_of_crashing(self):
        with mock.patch.object(MOD, "overlay_api", return_value=None):
            result = MOD.FerroCADGridOverlayCommand().Activated()
        self.assertIn("status", result)
        self.assertIn("addOverlay", result["status"])
        self.assertNotIn("fields", result)

    # -- the pull bridge ------------------------------------------------
    def test_enabling_registers_the_switch_and_disabling_removes_it(self):
        grid = MOD.ShaderGrid("Doc")
        grid.set_visible(True)
        self.assertIn(grid.root, FREECAD_GUI.overlays)
        self.assertIsInstance(grid.root, COIN.SoSwitch)
        self.assertEqual(grid.root.whichChild, COIN.SO_SWITCH_ALL)
        self.assertEqual(len(grid.root.children), 3, "pick, light, separator")

        grid.set_visible(False)
        self.assertNotIn(grid.root, FREECAD_GUI.overlays)
        self.assertEqual(grid.root.whichChild, COIN.SO_SWITCH_NONE)

    def test_rebind_removes_then_re_adds_the_same_root(self):
        grid = MOD.ShaderGrid("Doc")
        grid.set_visible(True)
        events = []
        add, remove = FREECAD_GUI.addOverlay, FREECAD_GUI.removeOverlay

        def record_add(node):
            events.append(("add", node))
            return add(node)

        def record_remove(node):
            events.append(("remove", node))
            return remove(node)

        FREECAD_GUI.addOverlay = record_add
        FREECAD_GUI.removeOverlay = record_remove
        try:
            MOD.SETTINGS.SetFloat("base_spacing", 2.0)
            MOD.SETTINGS.SetString("color", "#ff0000")
            grid.apply_settings()
        finally:
            FREECAD_GUI.addOverlay = add
            FREECAD_GUI.removeOverlay = remove

        self.assertEqual(events, [("remove", grid.root), ("add", grid.root)])
        self.assertAlmostEqual(grid.base_spacing.value, 2.0)
        self.assertEqual(grid.grid_color.value, (1.0, 0.0, 0.0))
        # The rebind wrote the new values into the stage's value-copied list.
        params = {p.name: p.value for p in grid.fragment.parameter.values}
        self.assertAlmostEqual(params["base_spacing"], 2.0)
        self.assertEqual(params["grid_color"], (1.0, 0.0, 0.0))

    def test_the_tree_carries_the_wgsl_on_one_stage(self):
        grid = MOD.ShaderGrid("Doc")
        self.assertEqual(grid.vertex.source, WGSL_PATH.read_text(encoding="utf-8"))
        self.assertEqual(grid.fragment.source, "", "WGSL has no per-stage modules")
        names = {parameter.name for parameter in grid.parameters}
        self.assertTrue(
            {
                "grid_color",
                "base_spacing",
                "minor_alpha",
                "major_alpha",
                "shadow_count",
                "shadow_opacity",
            }
            <= names
        )
        self.assertIn("shadow_data", names, "the deferred block is still carried")
        self.assertEqual(grid.shadow_count.value, 0.0, "shadows are deferred")

    # -- per document ---------------------------------------------------
    def test_only_the_active_document_keeps_an_overlay(self):
        doc_a = FREECAD.newDocument("DocA")
        self.assertIn("DocA", MOD.manager.items)
        first = MOD.manager.items["DocA"]
        self.assertIn(first.root, FREECAD_GUI.overlays)

        FREECAD.newDocument("DocB")
        self.assertIn("DocB", MOD.manager.items)
        second = MOD.manager.items["DocB"]
        self.assertIn(second.root, FREECAD_GUI.overlays)
        self.assertNotIn(
            first.root,
            FREECAD_GUI.overlays,
            "only the active document's grid is bound",
        )

        FREECAD.closeDocument("DocB")
        self.assertNotIn("DocB", MOD.manager.items)
        self.assertNotIn(second.root, FREECAD_GUI.overlays)

    # -- the provider declaration ---------------------------------------
    def test_the_provider_is_registered_as_an_alternative_backend(self):
        record = FREECAD_GUI.registry[(FEATURE, BACKEND)]
        self.assertEqual(record["label"], LABEL)
        self.assertEqual(record["command"], COMMAND)
        self.assertEqual(record["priority"], 0)
        self.assertIs(record["activate"], MOD.activate)
        self.assertIs(record["deactivate"], MOD.deactivate)
        self.assertEqual(FREECAD_GUI.activeProvider(FEATURE), BACKEND)
        self.assertIn(COMMAND, FREECAD_GUI.commands)

    def test_register_provider_is_re_callable(self):
        before = dict(FREECAD_GUI.registry[(FEATURE, BACKEND)])
        self.assertTrue(MOD.register_provider())
        after = FREECAD_GUI.registry[(FEATURE, BACKEND)]
        self.assertEqual(before, after, "the same manifest is declared again")
        self.assertEqual(
            len(FREECAD_GUI.providers(FEATURE)), 1, "not a duplicate registration"
        )

    def test_register_provider_reports_a_build_without_the_registry(self):
        with mock.patch.object(FREECAD_GUI, "registerProvider", None):
            self.assertFalse(MOD.register_provider())
        self.assertTrue(MOD.register_provider())

    def test_activate_and_deactivate_own_the_overlay(self):
        FREECAD.newDocument("Doc")
        MOD.activate()
        self.assertEqual(len(FREECAD_GUI.overlays), 1)
        MOD.deactivate()
        self.assertEqual(FREECAD_GUI.overlays, [])
        MOD.deactivate()  # idempotent by contract
        self.assertEqual(FREECAD_GUI.overlays, [])

    def test_an_inactive_backend_reports_instead_of_acting(self):
        FREECAD_GUI.registerProvider(
            FEATURE, "semantic", "Semantic (native)", priority=10
        )
        FREECAD_GUI.setActiveProvider(FEATURE, "semantic")
        try:
            spec = self.dialog()
            self.assertNotIn("fields", spec)
            self.assertIn("semantic", spec["status"])
            self.assertIn("Mods_Settings", spec["status"])

            FREECAD.newDocument("Doc")
            MOD.activate()
            self.assertEqual(FREECAD_GUI.overlays, [], "inactive backend must not draw")

            result = MOD.apply_dialog({"color": "#ff0000"})
            self.assertIn("status", result)
            self.assertEqual(MOD.setting("color"), "#808080")
        finally:
            FREECAD_GUI.unregisterProvider(FEATURE, "semantic")
            FREECAD_GUI.setActiveProvider(FEATURE, BACKEND)

    # -- what must not come back ---------------------------------------
    def test_the_module_uses_no_qt_and_no_live_scene_graph(self):
        for name in ("QtCore", "QtGui", "QtWidgets", "QDialog", "QSettings", "_load_qt"):
            self.assertFalse(hasattr(MOD, name), name)
        self.assertFalse(hasattr(MOD, "SettingsDialog"))
        for owner in (MOD.ShaderGrid, MOD.GridManager):
            self.assertFalse(hasattr(owner, "getSceneGraph"))
            self.assertFalse(hasattr(owner, "getMainWindow"))

        tree = ast.parse(INIT_GUI.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertFalse(
            {name for name in imported if name.startswith(("PySide", "Qt", "shiboken"))},
            "the port must not import Qt",
        )
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                self.assertNotEqual(node.attr, "getSceneGraph")
            elif isinstance(node, ast.Name):
                self.assertNotEqual(node.id, "getSceneGraph")
        # The bridge, not a live graph, is what the module holds on to.
        self.assertIn("addOverlay", INIT_GUI.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
