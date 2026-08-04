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
from fastapi.testclient import TestClient

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


# --- Routing ---------------------------------------------------------------

def test_there_is_no_catch_all_rewrite():
    """A `/(.*)` -> `/api/index` rewrite breaks routing rather than enabling
    it. In Vercel's backend-framework mode the entrypoint in pyproject.toml
    already receives every request with its original path; a rewrite makes
    the app see the literal string `/api/index` instead, so every route
    404s — including `/api/index`.

    Vercel warns about this at build time ("Internal rewrites in backend
    framework projects now route requests using the rewritten destination
    path"). This test is here because that warning was easy to scroll past.
    """
    config = ROOT / "vercel.json"
    if not config.exists():
        return
    import json
    rewrites = json.loads(config.read_text(encoding="utf-8")).get("rewrites", [])
    for rule in rewrites:
        assert "/api/index" not in rule.get("destination", ""), (
            "this rewrite makes the app receive /api/index as the request "
            "path, so every route 404s. Vercel routes to the entrypoint on "
            "its own."
        )


# --- Failing legibly on a serverless host ---------------------------------

@pytest.fixture
def entrypoint(monkeypatch):
    """Build api/index.py fresh under a chosen environment.

    Config is read at import, so the module has to be re-imported for each
    case. Only `api.*` is cleared — nothing under `bot.` caches env.
    """
    import sys

    def build(**env):
        for key in ("DEMO_ACCESS_CODES", "DEMO_SESSION_SECRET", "VERCEL",
                    "OPENAI_API_KEY"):
            monkeypatch.delenv(key, raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        for name in [n for n in list(sys.modules) if n.startswith("api")]:
            del sys.modules[name]
        import api.index
        return TestClient(api.index.app)

    yield build
    for name in [n for n in list(sys.modules) if n.startswith("api")]:
        del sys.modules[name]


def test_a_missing_variable_explains_itself_rather_than_crashing(entrypoint):
    """An exception at import becomes FUNCTION_INVOCATION_FAILED: a 500 with
    no message, identical for a missing variable and a genuine bug, readable
    only by someone who knows to open the platform's log viewer."""
    client = entrypoint(VERCEL="1")
    response = client.get("/")
    assert response.status_code == 503
    assert "DEMO_ACCESS_CODES" in response.text


def test_the_diagnostic_answers_on_every_path(entrypoint):
    """Whichever URL the operator happens to open."""
    client = entrypoint(VERCEL="1")
    for path in ("/", "/health", "/privacy", "/readiness", "/anything"):
        assert client.get(path).status_code == 503


def test_a_misconfigured_deployment_serves_no_reading(entrypoint):
    """The refusal still holds. It is only the reporting that changed."""
    body = entrypoint(VERCEL="1").get("/").text
    assert "恋愛運" not in body
    assert "アクセスコード" not in body     # not even the gate


def test_it_distinguishes_which_variable_is_missing(entrypoint):
    """Setting one of two and not noticing is the likely mistake — Vercel
    scopes variables per environment, so 'I added it' and 'production has
    it' are different claims."""
    body = entrypoint(VERCEL="1", DEMO_ACCESS_CODES="board:x").get("/").text
    assert "<code>DEMO_ACCESS_CODES</code> — set" in body
    assert "DEMO_SESSION_SECRET</code> — <b>not set</b>" in body


def test_the_diagnostic_never_echoes_a_value(entrypoint):
    """It is a public page. Names, never values."""
    body = entrypoint(VERCEL="1", DEMO_ACCESS_CODES="board:s3cret-code").get("/").text
    assert "s3cret-code" not in body


def test_a_configured_deployment_serves_the_real_demo(entrypoint):
    client = entrypoint(VERCEL="1", DEMO_ACCESS_CODES="board:x",
                        DEMO_SESSION_SECRET="secret")
    assert client.get("/health").status_code == 200
    assert client.get("/health").json()["model"] == "stub"
    assert "アクセスコード" in client.get("/").text


def test_a_configured_deployment_still_gates_access(entrypoint):
    """The whole point of the variables it was refusing without."""
    client = entrypoint(VERCEL="1", DEMO_ACCESS_CODES="board:x",
                        DEMO_SESSION_SECRET="secret")
    assert "恋愛運" not in client.get("/").text
    client.post("/enter", headers={"Content-Type": "application/x-www-form-urlencoded"},
                data={"code": "x"})
    assert "恋愛運" in client.get("/").text
