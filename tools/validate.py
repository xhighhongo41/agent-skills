#!/usr/bin/env python3
"""Validate the skill definitions in this repository against the project conventions.

Run from the repository root::

    python tools/validate.py
    python tools/validate.py --min-skills 4

The exit code is 0 when every check passes and 1 when at least one violation is
found.  Every violation is reported before exiting so that a whole batch can be
fixed in a single pass instead of one error per run.

The conventions enforced here come from ``開発資料/v0.1実装計画.md`` section 4-5.
Each check has a stable identifier (``V01``..``V12``) so that a report line can be
traced back to the requirement it protects.
"""

from __future__ import annotations

import argparse
import dataclasses
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml

# --------------------------------------------------------------------------- #
# Conventions
# --------------------------------------------------------------------------- #

#: Directory (relative to the repository root) that holds the skill folders.
SKILLS_DIRNAME = "skills"

#: Entry point file that every skill folder must provide.
SKILL_FILENAME = "SKILL.md"

#: Codex-specific UI metadata bundled with every skill.
OPENAI_YAML_RELPATH = Path("agents") / "openai.yaml"

#: The complete set of frontmatter keys allowed by the Agent Skills standard.
#: Writing any other key breaks portability, so anything outside this set is an
#: error rather than a warning.
ALLOWED_FRONTMATTER_KEYS = frozenset(
    {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
)

#: Frontmatter keys this project always requires.
REQUIRED_FRONTMATTER_KEYS = ("name", "description", "license")

#: Skill names are lowercase alphanumerics joined by single hyphens.
NAME_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

MAX_NAME_CHARS = 64

#: The standard allows 1024 characters, but the limit may be applied in bytes.
#: Japanese text costs three bytes per character in UTF-8, so a stricter
#: character budget keeps the description safe under either interpretation.
MAX_DESCRIPTION_CHARS = 300

#: Semantic version string, always quoted in YAML so it stays a string.
VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")

MAX_BODY_LINES = 500

#: The machine-readable version marker that must appear in the skill body and
#: agree with ``metadata.version``.
VERSION_MARKER_PATTERN = re.compile(r"^>\s*\*\*skill version\*\*:\s*(\S+)\s*$")

REQUIRED_LICENSE = "MIT"

#: Upper bound for the Codex UI blurb.  The Codex reference also suggests a lower
#: bound of 25 characters, but that guidance is written for English text; the
#: Japanese blurbs used here carry the same information in fewer characters, so
#: only the upper bound (which protects against UI truncation) is enforced.
MAX_SHORT_DESCRIPTION_CHARS = 64

#: Substrings that must not appear anywhere in a skill.  These are the
#: agent-specific proper nouns, private project names and personal paths that the
#: generalisation policy removes.  ``CLAUDE.md`` and ``AGENTS.md`` are
#: deliberately absent: skills name both files on purpose.
DENYLIST: tuple[str, ...] = (
    # Subagent proper names.
    "code-implementer",
    "test-runner",
    "doc-reader",
    "web-researcher",
    "Explore",
    # Agent-specific configuration paths.
    ".claude/agents",
    # Private project names and personal identifiers.
    "/Users/",
    # Product-flavoured usage declarations left over from the per-agent copies.
    "OpenCode用",
    "Claude用",
    "Codex用",
)


# --------------------------------------------------------------------------- #
# Data structures
# --------------------------------------------------------------------------- #


@dataclasses.dataclass(frozen=True)
class Violation:
    """A single convention breach found in the repository.

    Attributes:
        path: Repository-relative path of the offending file.
        line: 1-indexed line number, or ``None`` when the violation is about the
            file as a whole rather than one line.
        check: Stable check identifier such as ``"V04"``.
        message: Human-readable description of what is wrong.
    """

    path: str
    line: int | None
    check: str
    message: str


@dataclasses.dataclass(frozen=True)
class SkillDoc:
    """A parsed ``SKILL.md`` together with the context needed to report on it.

    Attributes:
        directory: The skill folder, whose basename must equal the frontmatter name.
        skill_path: Path of the ``SKILL.md`` file itself.
        rel_path: Repository-relative path of ``SKILL.md``, used in reports.
        frontmatter: Frontmatter parsed with ``yaml.safe_load``.
        frontmatter_lines: Raw lines between the two ``---`` markers, so that
            checks can inspect the source text (for example quoting) and not only
            the parsed value.
        frontmatter_start_line: 1-indexed line number of the opening ``---``.
        body_lines: Lines after the closing ``---``.
        body_start_line: 1-indexed line number of the first body line.
    """

    directory: Path
    skill_path: Path
    rel_path: str
    frontmatter: dict[str, Any]
    frontmatter_lines: list[str]
    frontmatter_start_line: int
    body_lines: list[str]
    body_start_line: int

    @property
    def body(self) -> str:
        """Return the body as a single string."""
        raise NotImplementedError

    @property
    def declared_name(self) -> str | None:
        """Return the frontmatter ``name`` when it is a string, otherwise ``None``."""
        raise NotImplementedError

    @property
    def declared_version(self) -> str | None:
        """Return ``metadata.version`` when it is a string, otherwise ``None``."""
        raise NotImplementedError


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #


def discover_skill_dirs(skills_root: Path) -> list[Path]:
    """Return the skill folders under ``skills_root``, sorted by name.

    Only directories are returned; loose files such as a generated manifest are
    ignored so that they can live alongside the skill folders.

    Args:
        skills_root: The ``skills/`` directory.

    Returns:
        Sorted list of skill folder paths. Empty when ``skills_root`` is missing.
    """
    raise NotImplementedError


def split_frontmatter(text: str) -> tuple[list[str], list[str], int] | None:
    """Split a ``SKILL.md`` source into frontmatter lines and body lines.

    The frontmatter is only recognised when the very first line of the file is
    ``---``; agents treat a file without that opening marker as pure content, so
    this function mirrors that behaviour.

    Args:
        text: Full file contents.

    Returns:
        A tuple of (frontmatter lines, body lines, 1-indexed body start line), or
        ``None`` when the file has no well-formed frontmatter block.
    """
    raise NotImplementedError


def load_skill(skill_dir: Path, repo_root: Path) -> tuple[SkillDoc | None, list[Violation]]:
    """Read and parse one skill folder.

    Implements checks ``V01`` (``SKILL.md`` exists) and ``V02`` (the frontmatter
    is present and parses into a mapping). When either fails the returned document
    is ``None`` and the remaining checks are skipped for that skill.

    Args:
        skill_dir: The skill folder.
        repo_root: Repository root, used to build relative paths for reports.

    Returns:
        A tuple of (parsed document or ``None``, violations found while parsing).
    """
    raise NotImplementedError


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #


def check_frontmatter_keys(doc: SkillDoc) -> list[Violation]:
    """V03: every frontmatter key belongs to the Agent Skills standard set."""
    raise NotImplementedError


def check_name(doc: SkillDoc) -> list[Violation]:
    """V04: ``name`` is present, well formed, short enough and matches the folder."""
    raise NotImplementedError


def check_description(doc: SkillDoc) -> list[Violation]:
    """V05: ``description`` is present, within budget and double-quoted in source."""
    raise NotImplementedError


def check_license(doc: SkillDoc) -> list[Violation]:
    """V06: ``license`` is ``MIT``."""
    raise NotImplementedError


def check_version(doc: SkillDoc) -> list[Violation]:
    """V07: ``metadata.version`` is a semver string."""
    raise NotImplementedError


def check_body_length(doc: SkillDoc) -> list[Violation]:
    """V08: the body stays within the recommended line budget."""
    raise NotImplementedError


def check_version_marker(doc: SkillDoc) -> list[Violation]:
    """V09: exactly one version marker exists and agrees with ``metadata.version``."""
    raise NotImplementedError


def check_usage_declaration(doc: SkillDoc) -> list[Violation]:
    """V10: the body instructs the agent to announce the skill name and version."""
    raise NotImplementedError


def check_denylist(doc: SkillDoc) -> list[Violation]:
    """V11: no agent-specific proper noun, private name or personal path remains."""
    raise NotImplementedError


def check_openai_yaml(doc: SkillDoc, repo_root: Path) -> list[Violation]:
    """V12: the Codex UI metadata file exists and carries usable interface fields."""
    raise NotImplementedError


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #


def validate_skill(skill_dir: Path, repo_root: Path) -> list[Violation]:
    """Run every check against one skill folder.

    Args:
        skill_dir: The skill folder.
        repo_root: Repository root, used to build relative paths for reports.

    Returns:
        All violations found, in check order.
    """
    raise NotImplementedError


def validate_repository(repo_root: Path, min_skills: int = 1) -> list[Violation]:
    """Validate every skill in the repository.

    Args:
        repo_root: Repository root (the directory containing ``skills/``).
        min_skills: Minimum number of skill folders that must be present. The
            release gate raises this to the number of skills the version ships.

    Returns:
        All violations found across the repository.
    """
    raise NotImplementedError


def format_violation(violation: Violation) -> str:
    """Render a violation as ``path:line: CHECK: message``."""
    raise NotImplementedError


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point.

    Args:
        argv: Command-line arguments, defaulting to ``sys.argv[1:]``.

    Returns:
        0 when no violation was found, 1 otherwise.
    """
    raise NotImplementedError


if __name__ == "__main__":
    raise SystemExit(main())
