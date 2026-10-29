import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "retopo_tool"

# What a core step module may import from inside the package (relative, level 1).
CORE_SHARED = {"mesh", "config", "geom"}
# The orchestrator wires the steps together; nothing else may.
ORCHESTRATOR = "pipeline.py"


def _imports(path):
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            yield from (a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            yield ("." * node.level) + (node.module or "")


def test_core_never_imports_maya_or_adapters():
    for f in (ROOT / "core").rglob("*.py"):
        for name in _imports(f):
            assert not name.startswith(("maya", "PySide", "retopo_tool.maya_adapter", "retopo_tool.ui")), (f, name)


def test_only_adapter_imports_maya():
    for f in ROOT.rglob("*.py"):
        if "maya_adapter" in f.parts:
            continue
        for name in _imports(f):
            assert not name.startswith("maya"), (f, name)


def test_core_steps_import_only_shared_modules():
    for f in (ROOT / "core").glob("*.py"):
        if f.name == ORCHESTRATOR:
            continue
        for name in _imports(f):
            assert not name.startswith("retopo_tool"), (f, name)
            if name.startswith("."):
                assert name.lstrip(".") in CORE_SHARED, (f, name)


def test_fill_templates_import_only_shared_modules_and_registry():
    for f in (ROOT / "core" / "fill").glob("*.py"):
        for name in _imports(f):
            assert not name.startswith("retopo_tool"), (f, name)
            if name.startswith(".."):
                assert name[2:] in CORE_SHARED, (f, name)
            elif name.startswith("."):
                assert name == ".", (f, name)  # the registry; templates never import each other


def test_sources_import_only_core_contracts():
    for f in (ROOT / "sources").glob("*.py"):
        for name in _imports(f):
            if name.startswith(".."):
                assert name in ("..core.mesh", "..core.config"), (f, name)


def test_ui_session_is_plain_python():
    # The window may use Qt and maya_adapter; the session it drives stays testable without Maya.
    for name in _imports(ROOT / "ui" / "session.py"):
        assert not name.startswith(("maya", "PySide", "..maya_adapter")), name
