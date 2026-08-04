"""Tests for the serverless deployment entrypoint.

The failure these exist for is a deploy that builds fine and then 500s on
the first request, because someone added a top-level import of a package
that pyproject.toml does not declare. That is only discoverable in
production otherwise: the local venv has everything installed, so nothing
here would notice.
"""

import ast
import importlib.util
import sysconfig
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
ENTRYPOINT = ROOT / "api" / "index.py"
PYPROJECT = ROOT / "pyproject.toml"

STDLIB = Path(sysconfig.get_paths()["stdlib"]).resolve()
LOCAL = {"bot", "engine", "api"}


def _is_stdlib(name: str) -> bool:
    if name in getattr(__import__("sys"), "builtin_module_names", ()):
        return True
    try:
        spec = importlib.util.find_spec(name)
    except (ImportError, ValueError):
        return False
    if spec is None or spec.origin in (None, "built-in", "frozen"):
        return spec is not None
    try:
        return STDLIB in Path(spec.origin).resolve().parents
    except (OSError, ValueError):
        return False


def _module_file(dotted: str):
    candidate = ROOT / (dotted.replace(".", "/") + ".py")
    if candidate.exists():
        return candidate
    package = ROOT / dotted.replace(".", "/") / "__init__.py"
    return package if package.exists() else None


def top_level_third_party(start: Path):
    """Packages that must be installed for `start` to import.

    Only imports at module level count. `openai`, `uvicorn` and `dotenv` are
    imported inside functions on purpose, so they are not needed to load the
    app and are deliberately absent from the deployment.
    """
    required, visited, queue = set(), set(), [start]
    while queue:
        path = queue.pop()
        if path in visited or path is None:
            continue
        visited.add(path)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

        for node in tree.body:                      # module level only
            targets = []
            if isinstance(node, ast.Import):
                targets = [(a.name, a.name) for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                targets = [(node.module, node.module)]
                targets += [(f"{node.module}.{a.name}", node.module)
                            for a in node.names]
            for dotted, base in targets:
                root = base.split(".")[0]
                if root in LOCAL:
                    queue.append(_module_file(dotted))
                    queue.append(_module_file(base))
                elif not _is_stdlib(root):
                    required.add(root)
    return required


def declared_dependencies():
    text = PYPROJECT.read_text(encoding="utf-8")
    block = text.split("dependencies = [", 1)[1].split("]", 1)[0]
    names = []
    for line in block.splitlines():
        line = line.strip().strip(',').strip('"').strip("'")
        if line:
            for separator in ("==", ">=", "<=", "~=", "!=", "[", ";"):
                line = line.split(separator)[0]
            names.append(line.strip())
    return set(names)


# --- The invariant ---------------------------------------------------------

def test_the_entrypoint_exists_and_is_not_the_line_webhook():
    """Vercel's autodetection points at bot/app.py, whose `app` is None so
    the module imports without credentials. Deploying that serves nothing."""
    assert ENTRYPOINT.exists()
    assert 'entrypoint = "api.index:app"' in PYPROJECT.read_text(encoding="utf-8")


def test_every_package_needed_to_import_the_app_is_declared():
    """A top-level import of an undeclared package builds fine and then 500s
    on the first request. The local venv has everything, so only this
    notices."""
    missing = top_level_third_party(ENTRYPOINT) - declared_dependencies()
    assert not missing, (
        f"api/index.py needs {sorted(missing)} at import time, but "
        f"pyproject.toml does not declare it. Add it to [project] "
        f"dependencies, or make the import lazy."
    )


def test_nothing_is_declared_that_is_not_needed():
    """Keeps the deployment minimal, and keeps the next test meaningful."""
    unused = declared_dependencies() - top_level_third_party(ENTRYPOINT)
    assert not unused, f"pyproject.toml declares unused {sorted(unused)}"


def test_the_openai_client_is_not_shipped_to_serverless():
    """The strongest form of the no-spending guarantee on a host where the
    budget guard cannot work: the library is not installed, so a billable
    call is impossible regardless of configuration. bot/demo.py refuses in
    code as well — this is the same promise made in packaging."""
    assert "openai" not in declared_dependencies()
    assert "openai" not in top_level_third_party(ENTRYPOINT)


@pytest.mark.parametrize("package", ["openai", "uvicorn", "dotenv"])
def test_the_lazily_imported_packages_stay_lazy(package):
    assert package not in top_level_third_party(ENTRYPOINT)


def test_uv_is_told_this_is_not_an_installable_package():
    """Without it, `uv lock` tries to build the project and fails — there is
    no build backend and nothing here should be pip-installed."""
    assert "package = false" in PYPROJECT.read_text(encoding="utf-8")


def test_a_project_table_exists_for_uv():
    """Vercel's builder runs `uv lock`, which fails with 'No project table
    found' without one. That is the error this file was written after."""
    text = PYPROJECT.read_text(encoding="utf-8")
    assert "[project]" in text
    assert "requires-python" in text
