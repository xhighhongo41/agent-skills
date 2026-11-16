#!/usr/bin/env bash
# Gather the evidence needed before a release, without changing anything.
#
# A thin entry point: it finds an interpreter that can import pyyaml (the same
# way tools/check.sh does) and hands every argument to tools/release_check.py,
# whose exit code it keeps.
#
# Usage: bash tools/release-check.sh <X.Y.Z> [--fetch] [--base <branch>]
#            [--check-timeout <seconds>] [--skip-checks]
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

# Prefer an interpreter that already has the dependency; otherwise fall back to
# uv, which resolves it into a throwaway environment rather than installing
# anything into the user's Python.
if python3 -c 'import yaml' >/dev/null 2>&1; then
    python_cmd=(python3)
elif command -v uv >/dev/null 2>&1; then
    python_cmd=(uv run --quiet --with pyyaml --with pytest python)
else
    echo "release-check.sh: needs python3 with pyyaml installed, or uv on PATH." >&2
    exit 1
fi

exec "${python_cmd[@]}" tools/release_check.py "$@"
