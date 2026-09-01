#!/usr/bin/env bash
# Run every deterministic check this repository has, in one command.
#
# The GitHub Actions workflow calls this script too, so a green run here means a
# green run in CI. Steps are ordered cheapest first, so a formatting slip is
# reported without waiting for the test suite.
#
# Usage: bash tools/check.sh
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

# Number of skills this version ships. Stated here rather than left at the
# default of 1 so that a skill folder disappearing fails the build instead of
# quietly shrinking the release.
expected_skills=9

# Prefer an interpreter that already has the dependencies; otherwise fall back
# to uv, which resolves them into a throwaway environment rather than installing
# anything into the user's Python. CI installs them with pip and takes the first
# branch.
if python3 -c 'import yaml, pytest' >/dev/null 2>&1; then
    python_cmd=(python3)
elif command -v uv >/dev/null 2>&1; then
    python_cmd=(uv run --quiet --with pyyaml --with pytest python)
else
    echo "check.sh: needs python3 with pyyaml and pytest installed, or uv on PATH." >&2
    exit 1
fi

if command -v ruff >/dev/null 2>&1; then
    ruff_cmd=(ruff)
elif command -v uvx >/dev/null 2>&1; then
    ruff_cmd=(uvx ruff)
else
    echo "check.sh: needs ruff installed, or uvx on PATH." >&2
    exit 1
fi

run() {
    echo "==> $*"
    "$@"
}

run "${ruff_cmd[@]}" check .
run "${ruff_cmd[@]}" format --check .
run "${python_cmd[@]}" -m pytest tools/ -q
# Covers the skills (V00..V12) and the generated install manifests (V13/V14).
run "${python_cmd[@]}" tools/validate.py --min-skills "$expected_skills"
# Catches an edited skill that kept its old version. Skips itself, rather than
# failing, in a checkout without tags.
run "${python_cmd[@]}" tools/version_guard.py

echo
echo "check.sh: all checks passed."
