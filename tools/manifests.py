#!/usr/bin/env python3
"""Generate and verify the install manifests that this repository ships.

Two agents install skills straight from this repository over HTTP, each through
its own manifest:

* ``skills/index.json`` — OpenCode's ``skills.urls`` mechanism. It must sit next
  to the skill folders because OpenCode derives every download URL from the
  directory that holds the manifest.
* ``.claude-plugin/marketplace.json`` — Claude Code's plugin marketplace.

Both files are *generated* from the skills themselves plus the project version in
``VERSION``; they are never hand-edited.  Because the agents fetch the files as
they are committed, generation happens locally and the result is committed.  CI
does not write: it regenerates and fails when the committed bytes differ, which
turns "someone edited a manifest by hand" and "someone added a file and forgot to
regenerate" into build failures.

Run from the repository root::

    python tools/manifests.py --check    # default; verify the committed files
    python tools/manifests.py --write    # regenerate them

The exit code is 0 when the committed manifests match the generated ones and 1
otherwise.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import validate

# --------------------------------------------------------------------------- #
# Conventions
# --------------------------------------------------------------------------- #

#: File (relative to the repository root) holding the project version, which is
#: the single machine-readable declaration of it.  Skill versions live in each
#: ``SKILL.md`` and are a separate axis.
VERSION_FILENAME = "VERSION"

#: Generated manifest consumed by OpenCode's ``skills.urls`` mechanism.
INDEX_RELPATH = Path(validate.SKILLS_DIRNAME) / "index.json"

#: Generated manifest consumed by Claude Code's plugin marketplace.
MARKETPLACE_RELPATH = Path(".claude-plugin") / "marketplace.json"

#: Optional Claude Code plugin manifest.  This repository deliberately does not
#: ship one: its ``version`` would silently win over the marketplace entry's,
#: letting a stale value mask the real project version.
PLUGIN_JSON_RELPATH = Path(".claude-plugin") / "plugin.json"

#: Marketplace identifier used in ``/plugin marketplace add <owner>/<repo>``.
MARKETPLACE_NAME = "agent-skills"

#: Owner of the public repository, as required by the marketplace schema.
MARKETPLACE_OWNER = "xhighhongo41"

#: Single plugin entry.  Every skill ships in one plugin, so the invocation name
#: becomes ``/agent-skills:<skill-name>``.
PLUGIN_NAME = "agent-skills"

#: Plugin source, relative to the marketplace root.  With this value and no
#: ``skills`` key, Claude Code scans the default ``skills/`` directory, so adding
#: or removing a skill never requires touching the manifest.
PLUGIN_SOURCE = "./"

#: One-line summary shown in the plugin browser.
PLUGIN_DESCRIPTION = "Agent Skills for coding agents, kept in one place."

#: Directory names never listed in a skill's ``files``.
EXCLUDED_DIR_NAMES = frozenset({"__pycache__"})

#: Indentation of the generated JSON.  Fixed so the output is byte-stable.
JSON_INDENT = 2


# --------------------------------------------------------------------------- #
# Data structures
# --------------------------------------------------------------------------- #


class ManifestError(RuntimeError):
    """Raised when the manifests cannot be generated from the repository.

    Generation needs facts that the convention checks in :mod:`validate` already
    guarantee (a parseable ``SKILL.md``, a ``metadata.version``, a well-formed
    ``VERSION``).  When one is missing the generator refuses rather than emitting
    a manifest with a hole in it.
    """


@dataclasses.dataclass(frozen=True)
class ManifestMismatch:
    """A difference between a generated manifest and the file on disk.

    Attributes:
        rel_path: Repository-relative path of the manifest that differs.
        message: Human-readable description of the difference.
    """

    rel_path: str
    message: str


# --------------------------------------------------------------------------- #
# Inputs
# --------------------------------------------------------------------------- #


def read_project_version(repo_root: Path) -> str:
    """Return the project version declared in ``VERSION``.

    Args:
        repo_root: Repository root (the directory containing ``VERSION``).

    Returns:
        The version string with surrounding whitespace removed.

    Raises:
        ManifestError: When the file is missing, empty, holds more than one
            non-empty line, or does not look like ``x.y.z``.
    """
    raise NotImplementedError


def collect_skill_files(skill_dir: Path) -> list[str]:
    """Return every distributable file in one skill folder.

    OpenCode downloads files one by one, so directories cannot be named; the
    manifest has to enumerate each path.  Hidden files and ``__pycache__`` are
    excluded because they are local artefacts rather than part of the skill.

    Args:
        skill_dir: The skill folder.

    Returns:
        Slash-separated paths relative to ``skill_dir``, sorted so that the
        generated manifest is byte-stable across platforms.

    Raises:
        ManifestError: When the folder holds no distributable file.
    """
    raise NotImplementedError


# --------------------------------------------------------------------------- #
# Generation
# --------------------------------------------------------------------------- #


def build_index(repo_root: Path) -> dict[str, Any]:
    """Build the OpenCode manifest.

    Args:
        repo_root: Repository root (the directory containing ``skills/``).

    Returns:
        The manifest as ``{"skills": [{"name", "version", "files"}, ...]}``,
        with skills in name order.

    Raises:
        ManifestError: When a skill cannot be parsed or declares no version.
    """
    raise NotImplementedError


def build_marketplace(repo_root: Path) -> dict[str, Any]:
    """Build the Claude Code marketplace manifest.

    The entry deliberately carries no ``skills`` key: listing paths under a
    marketplace-root ``source`` replaces the default scan instead of adding to
    it, which would mean editing this manifest every time a skill is added.

    Args:
        repo_root: Repository root (the directory containing ``VERSION``).

    Returns:
        The manifest as a mapping ready to be rendered.

    Raises:
        ManifestError: When the project version cannot be read.
    """
    raise NotImplementedError


def render(document: Mapping[str, Any]) -> str:
    """Render a manifest as the exact text that belongs on disk.

    Args:
        document: The manifest mapping.

    Returns:
        Pretty-printed JSON with a trailing newline, non-ASCII characters kept
        verbatim and key order preserved.
    """
    raise NotImplementedError


def manifest_targets(repo_root: Path) -> list[tuple[Path, str]]:
    """Return every manifest together with the text it should contain.

    Args:
        repo_root: Repository root.

    Returns:
        Pairs of (absolute path, expected file content).

    Raises:
        ManifestError: When a manifest cannot be generated.
    """
    raise NotImplementedError


# --------------------------------------------------------------------------- #
# Writing and checking
# --------------------------------------------------------------------------- #


def write_manifests(repo_root: Path) -> list[Path]:
    """Regenerate every manifest on disk, creating parent directories as needed.

    Args:
        repo_root: Repository root.

    Returns:
        The paths that were written, in a stable order.

    Raises:
        ManifestError: When a manifest cannot be generated.
    """
    raise NotImplementedError


def check_manifests(repo_root: Path) -> list[ManifestMismatch]:
    """Compare the committed manifests against freshly generated ones.

    Also reports a committed ``.claude-plugin/plugin.json``, which must not
    exist: its ``version`` would take precedence over the marketplace entry's
    without any warning.

    Args:
        repo_root: Repository root.

    Returns:
        One mismatch per problem found, in manifest path order. Empty when the
        committed files are exactly what the generator produces.

    Raises:
        ManifestError: When a manifest cannot be generated at all, which is a
            different failure from the committed copy being out of date.
    """
    raise NotImplementedError


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point.

    Args:
        argv: Command-line arguments, defaulting to ``sys.argv[1:]``.

    Returns:
        0 when the manifests were written, or when every committed manifest
        matches the generated one; 1 otherwise.
    """
    raise NotImplementedError


if __name__ == "__main__":
    raise SystemExit(main())
