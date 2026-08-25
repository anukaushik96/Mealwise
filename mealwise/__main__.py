"""Entry point for `python3 -m mealwise` - the browser UI.

The terminal version is `python3 -m mealwise.cli`, and the read-only
prober is `python3 -m mealwise.probe`.
"""

import sys

from .web_app import main

if __name__ == "__main__":
    sys.exit(main())
