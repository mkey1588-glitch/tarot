#!/usr/bin/env python3
"""
Launcher for the demo that works with any Python 3.9+ on the machine.

    python3 scripts/run_demo.py --port 8100

Why this exists rather than just `.venv/bin/python -m bot.demo`:

  * Running the repo's venv interpreter needs `pyvenv.cfg` to be readable,
    and a sandboxed launcher (the editor's preview pane, some CI runners)
    may be denied access to anything under `.venv/` — on macOS `~/Documents`
    is a TCC-protected location, so the grant is not automatic.
  * `-m bot.demo` needs the repo root on `sys.path`, which relies on the
    working directory being the repo root. A launcher invoked by absolute
    path cannot rely on that.

Both are fixed here by resolving everything from `__file__`: the repo root
and, if the third-party imports are not already satisfied, the venv's
site-packages. Nothing is hardcoded to one machine.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _ensure_importable() -> None:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    try:
        import fastapi  # noqa: F401
        import uvicorn  # noqa: F401
        return
    except ImportError:
        pass

    # Add the repo venv's site-packages for the running interpreter's version.
    version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    candidates = [
        ROOT / ".venv" / "lib" / version / "site-packages",
        *sorted((ROOT / ".venv" / "lib").glob("python*/site-packages")),
    ]
    for candidate in candidates:
        if candidate.is_dir():
            sys.path.append(str(candidate))
            try:
                import fastapi  # noqa: F401
                import uvicorn  # noqa: F401
                return
            except ImportError:
                sys.path.pop()

    sys.exit(
        "fastapi and uvicorn are not importable.\n"
        f"Tried this interpreter ({sys.executable}, {version}) and the venv "
        f"at {ROOT / '.venv'}.\n"
        "Install them with:  .venv/bin/pip install -r requirements.txt"
    )


if __name__ == "__main__":
    _ensure_importable()
    from bot.demo import main

    main()
