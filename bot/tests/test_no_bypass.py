"""Structural tests: the rules hold for code nobody has written yet.

Every other test in this suite checks the behaviour of a path that exists.
These check the shape of the codebase, so that a path added next month
fails the suite rather than passing review.

They read source rather than call functions on purpose. A test that calls
`generate_reading` proves `generate_reading` screens its input. It proves
nothing about the function someone adds beside it.
"""

import ast
from pathlib import Path

import pytest

BOT = Path(__file__).resolve().parent.parent
MODULES = sorted(p for p in BOT.glob("*.py") if p.name != "__init__.py")

# The single module permitted to talk to a language model.
MODEL_CHOKE_POINT = "llm.py"

# LLM SDKs. A new provider gets added here and to nothing else.
LLM_PACKAGES = {"openai", "anthropic", "google", "cohere", "mistralai",
                "litellm", "transformers"}


def imported_modules(path: Path):
    """Top-level package names imported by a module, however written."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                yield node.module.split(".")[0]


def test_modules_were_actually_found():
    """A glob that silently matches nothing would make every test below
    pass while checking nothing at all."""
    assert len(MODULES) >= 5
    assert MODEL_CHOKE_POINT in {p.name for p in MODULES}


@pytest.mark.parametrize("path", MODULES, ids=lambda p: p.name)
def test_only_the_choke_point_imports_an_llm_sdk(path):
    """No path reaches a model except through ModelGateway.complete, which
    is where screening and the budget guard are enforced. A second module
    importing an SDK is a second door."""
    offending = LLM_PACKAGES & set(imported_modules(path))
    if path.name == MODEL_CHOKE_POINT:
        return
    assert not offending, (
        f"{path.name} imports {sorted(offending)}. Model calls go through "
        f"bot/{MODEL_CHOKE_POINT}, which is what enforces screen_input() "
        f"and the budget guard."
    )


@pytest.mark.parametrize("path", MODULES, ids=lambda p: p.name)
def test_no_module_calls_a_completions_api_directly(path):
    """Catches an SDK reached through a client object passed in from
    elsewhere, which the import check alone would miss."""
    source = path.read_text(encoding="utf-8")
    if path.name == MODEL_CHOKE_POINT:
        return
    for marker in ("chat.completions.create", "messages.create",
                   "responses.create"):
        assert marker not in source, f"{path.name} calls {marker} directly"


def test_the_choke_point_checks_screening_before_spending():
    """Ordering inside complete() is load-bearing. An unscreened call must
    be rejected on its own terms, not incidentally because the budget
    happened to be exhausted — otherwise topping up the budget reopens it."""
    source = (BOT / MODEL_CHOKE_POINT).read_text(encoding="utf-8")
    body = source[source.index("def complete("):]
    assert body.index("UnscreenedInput") < body.index("guard.check")
    assert body.index("guard.check") < body.index("client.generate")
