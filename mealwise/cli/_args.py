"""Tiny flag parser shared by the CLIs.

Deliberately not argparse: the flags here are positional-plus-``--key=value`` and the
error messages need to say what to type instead, which argparse's usage dumps obscure.
"""

from __future__ import annotations

import os
import sys

__all__ = ["flag", "num_list", "positional", "require_token", "die"]


def die(message: str) -> "None":
    # Flush stdout first: it is block-buffered when piped while stderr is not, so
    # without this the error surfaces BEFORE the output it refers to in `2>&1`.
    sys.stdout.flush()
    print(message, file=sys.stderr)
    raise SystemExit(1)


def _argv() -> list[str]:
    return sys.argv[1:]


def flag(name: str) -> str | None:
    """``--name`` returns "", ``--name=v`` returns "v", absent returns None."""
    for arg in _argv():
        if arg == f"--{name}":
            return ""
        if arg.startswith(f"--{name}="):
            return arg.split("=", 1)[1]
    return None


def positional() -> list[str]:
    return [a for a in _argv() if not a.startswith("--")]


def num_list(name: str) -> list[int] | None:
    """Parse a comma-separated positive-integer list, so ``--pick``/``--qty`` share a path."""
    raw = flag(name)
    if raw is None or raw == "":
        return None
    out: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        try:
            value = int(part)
        except ValueError:
            value = 0
        if value < 1 or str(value) != part:
            # console message + exit, not a traceback: a bad flag is a usage mistake,
            # and a stack trace buries the one line that says what to type instead.
            die(f'--{name} takes positive whole numbers, got "{part}"')
        out.append(value)
    return out


def require_token() -> str:
    token = os.environ.get("SWIGGY_TOKEN")
    if not token:
        die("SWIGGY_TOKEN is not set. Run `python -m swiggy.cli.login` first.")
    return token  # type: ignore[return-value]
