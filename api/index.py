"""Vercel entrypoint.

Vercel's Python runtime looks for an ASGI callable named `app` in this module and routes
every request here via the rewrite in vercel.json.
"""

import sys
from pathlib import Path

# The function's working directory is the repo root on Vercel, but make the import
# explicit so `python api/index.py` and `uvicorn api.index:app` behave the same locally.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mealwise.web.app import app  # noqa: E402

__all__ = ["app"]
