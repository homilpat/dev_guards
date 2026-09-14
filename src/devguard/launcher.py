"""Run a reviewed checkout with an isolated Python, without a global installation.

Invoke with an absolute interpreter path and ``-I /absolute/path/to/launcher.py``.
Only this launcher's source checkout is added to the isolated import path.
"""

import sys
from pathlib import Path


def main() -> int:
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from devguard.cli import main as cli_main

    return cli_main()


if __name__ == "__main__":
    raise SystemExit(main())
