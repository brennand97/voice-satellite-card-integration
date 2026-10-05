"""Thin per-repository entrypoint; shared runtime remains a separate checkout."""

import os
import subprocess
import sys
from pathlib import Path


def main():
    here = Path(__file__).resolve().parent
    shared = Path(
        os.environ.get("HA_TESTBED_ROOT", here.parents[2] / "ha-integration-testbed")
    )
    executable = shared / ".venv/bin/ha-testbed"
    if not executable.exists():
        raise SystemExit(
            "Install ../ha-integration-testbed first (or set HA_TESTBED_ROOT)"
        )
    args = sys.argv[1:]
    if not args:
        args = ["--help"]
    if args[0] in {"prepare", "up"}:
        args += ["--adapter", str(here / "adapter.json")]
    return subprocess.call([str(executable), *args])


if __name__ == "__main__":
    sys.exit(main())
