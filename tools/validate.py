#!/usr/bin/env python3
"""Validate the skill definitions in this repository against the project conventions.

Run from the repository root::

    python tools/validate.py
    python tools/validate.py --min-skills 4

The exit code is 0 when every check passes and 1 when at least one violation is
found.  Every violation is reported before exiting so that a whole batch can be
fixed in a single pass instead of one error per run.

The conventions enforced here come from ``開発資料/v0.1実装計画.md`` section 4-5.
Each check has a stable identifier (``V00``..``V19``) so that a report line can be
traced back to the requirement it protects.

``V17`` guards the ``external.json`` declaration a skill may ship: the repository
holds only the declaration, never the fetched ``external/`` content.

``V18`` keeps the passages several skills share word for word (see
:data:`SHARED_TEXT_GROUPS`) identical across their copies.  Like ``V13``/``V14``
it reads named files of the real repository, so it is opt-in for library callers
and always on from the CLI.

``V19`` applies the ``V11`` denylist to the rest of a skill's text files
(references, scripts, ``agents/openai.yaml``), with the same ``compatibility``
exemption.

``V13``/``V14`` cover the generated install manifests rather than a single skill.
They need the whole repository (a ``VERSION`` file and both manifests) to be
present, so they are opt-in for library callers and always on from the CLI.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

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
    # Absolute home directory paths, which would leak a local account name.
    # The path prefixes are matched rather than any specific user name, so this
    # list itself stays free of personal information.
    "/Users/",
    "/home/",
    "C:\\Users\\",
    # Product-flavoured usage declarations left over from the per-agent copies.
    "OpenCode用",
    "Claude用",
    "Codex用",
)

#: Harness keys accepted in the ``compatibility`` frontmatter value, mapped to
#: the display name that names the harness in prose.  A skill omits the key when
#: it works everywhere, so this table only lists what a *restricted* skill may
#: declare.
KNOWN_HARNESSES: dict[str, str] = {
    "claude-code": "Claude Code",
    "codex": "Codex",
    "opencode": "OpenCode",
}

#: The denylisted phrase each harness owns.  Naming a harness the skill does not
#: target is exactly what the generalisation policy removes, but a skill that
#: declares the harness in ``compatibility`` is *about* that harness, so its own
#: phrase stops being a leftover.  The phrases stay in :data:`DENYLIST`; V11
#: drops them from the scan per skill instead, which keeps the exemption tied to
#: the declaration rather than weakening the list for everyone.
HARNESS_DENYLIST_TERMS: dict[str, str] = {
    "claude-code": "Claude用",
    "codex": "Codex用",
    "opencode": "OpenCode用",
}

#: Separator between display names in the description preamble V16 requires.
HARNESS_DISPLAY_SEPARATOR = " / "

#: Text that closes the preamble, after the joined display names.
HARNESS_PREAMBLE_SUFFIX = " 専用。"

#: Declaration file a skill may place at its folder root to name external
#: repositories whose material is fetched on the user's machine.
EXTERNAL_DECLARATION_FILENAME = "external.json"

#: Folder the fetched material lands in.  It exists only on a user's machine and
#: must never be committed, so any entry with this name in a skill is an error.
EXTERNAL_CONTENT_DIRNAME = "external"

#: The only value of the declaration's ``format`` key that this tool understands.
EXTERNAL_DECLARATION_FORMAT = 1

#: Extensions (compared in lower case) of the auxiliary files V19 scans.  Other
#: files, images and archives for instance, are not text the agent reads.
AUXILIARY_TEXT_SUFFIXES = frozenset({".md", ".json", ".yaml", ".yml", ".txt", ".py", ".sh"})

#: Folders V19 never descends into, wherever they sit in a skill.  They hold
#: local tool caches, which are not committed but may record absolute paths.
AUXILIARY_SKIPPED_DIRNAMES = frozenset(
    {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
)

#: A Markdown ATX heading line: one to six ``#`` then a space, a tab or the end.
HEADING_PATTERN = re.compile(r"^(#{1,6})(?:[ \t]|$)")

#: The opening line of a fenced code block, up to three spaces of indentation.
CODE_FENCE_PATTERN = re.compile(r"^ {0,3}(`{3,}|~{3,})")


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
        return "\n".join(self.body_lines)

    @property
    def declared_name(self) -> str | None:
        """Return the frontmatter ``name`` when it is a string, otherwise ``None``."""
        name = self.frontmatter.get("name")
        return name if isinstance(name, str) else None

    @property
    def declared_version(self) -> str | None:
        """Return ``metadata.version`` when it is a string, otherwise ``None``."""
        metadata = self.frontmatter.get("metadata")
        if not isinstance(metadata, dict):
            return None
        version = metadata.get("version")
        return version if isinstance(version, str) else None

    @property
    def declared_compatibility(self) -> str | None:
        """Return the frontmatter ``compatibility`` when it is a string, else ``None``.

        ``None`` therefore means "no usable value", which covers both a missing
        key and a value of the wrong type; callers that must tell those apart
        test ``"compatibility" in frontmatter`` as well.
        """
        compatibility = self.frontmatter.get("compatibility")
        return compatibility if isinstance(compatibility, str) else None


@dataclasses.dataclass(frozen=True)
class SharedTextTarget:
    """One copy of a passage that V18 compares with the other copies.

    Attributes:
        path: Path of the file, relative to ``skills/`` and written with ``/``.
        heading: The full heading line (``"## ..."``) that opens the section, or
            ``None`` to compare the whole file.

    Raises:
        ValueError: When ``path`` is empty, absolute or contains ``..`` or a
            backslash, or when ``heading`` is not a Markdown heading line.
    """

    path: str
    heading: str | None = None

    def __post_init__(self) -> None:
        """Refuse a target that could never be located."""
        problems = _relative_path_problems(self.path)
        if problems:
            raise ValueError(f"SharedTextTarget path {problems[0]}")
        if self.heading is not None and not HEADING_PATTERN.match(self.heading):
            raise ValueError(
                f"SharedTextTarget heading {self.heading!r} must be a Markdown heading "
                "line such as '## Title'."
            )

    @property
    def label(self) -> str:
        """Return the repository-relative path of the file, as used in reports."""
        return f"{SKILLS_DIRNAME}/{self.path}"


@dataclasses.dataclass(frozen=True)
class SharedTextGroup:
    """Passages that claim to be the same text and must stay identical (V18).

    Attributes:
        name: Identifier quoted in reports, unique within the table.
        targets: The copies, two or more.  The first one is the reference the
            others are compared with.

    Raises:
        ValueError: When the group has fewer than two targets.
    """

    name: str
    targets: tuple[SharedTextTarget, ...]

    def __post_init__(self) -> None:
        """Refuse a group with nothing to compare."""
        if len(self.targets) < 2:
            raise ValueError(
                f"SharedTextGroup {self.name!r} needs at least two targets, "
                f"found {len(self.targets)}."
            )

    @classmethod
    def files(cls, name: str, *paths: str) -> SharedTextGroup:
        """Build a group that compares whole files.

        Args:
            name: Identifier quoted in reports.
            *paths: The files, relative to ``skills/``.

        Returns:
            The group.
        """
        return cls(name, tuple(SharedTextTarget(path) for path in paths))

    @classmethod
    def sections(cls, name: str, heading: str, *paths: str) -> SharedTextGroup:
        """Build a group that compares the section under one heading in several files.

        Args:
            name: Identifier quoted in reports.
            heading: The full heading line, for example ``"## Title"``.
            *paths: The files, relative to ``skills/``.

        Returns:
            The group.
        """
        return cls(name, tuple(SharedTextTarget(path, heading) for path in paths))


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
    if not skills_root.is_dir():
        return []
    return sorted(
        (entry for entry in skills_root.iterdir() if entry.is_dir()),
        key=lambda entry: entry.name,
    )


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
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        return None

    closing_index = None
    for index in range(1, len(lines)):
        if lines[index] == "---":
            closing_index = index
            break
    if closing_index is None:
        return None

    frontmatter_lines = lines[1:closing_index]
    body_lines = lines[closing_index + 1 :]
    # ``closing_index`` is the 0-indexed position of the closing marker, so the
    # first body line sits two lines further along in 1-indexed terms.
    body_start_line = closing_index + 2
    return frontmatter_lines, body_lines, body_start_line


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
    skill_path = skill_dir / SKILL_FILENAME
    rel_path = str(skill_path.relative_to(repo_root))

    if not skill_path.is_file():
        return None, [Violation(rel_path, None, "V01", f"{SKILL_FILENAME} is missing.")]

    text = skill_path.read_text(encoding="utf-8")
    split = split_frontmatter(text)
    if split is None:
        return None, [
            Violation(
                rel_path,
                None,
                "V02",
                "File does not start with a '---' frontmatter block, or the block is never closed.",
            )
        ]

    frontmatter_lines, body_lines, body_start_line = split
    frontmatter_source = "\n".join(frontmatter_lines)
    try:
        # ``yaml.safe_load`` (never ``yaml.load``) avoids constructing arbitrary
        # Python objects from untrusted skill content.
        frontmatter = yaml.safe_load(frontmatter_source)
    except yaml.YAMLError as exc:
        return None, [Violation(rel_path, None, "V02", f"Frontmatter is not valid YAML: {exc}")]

    if not isinstance(frontmatter, dict):
        return None, [Violation(rel_path, None, "V02", "Frontmatter must parse to a mapping.")]

    doc = SkillDoc(
        directory=skill_dir,
        skill_path=skill_path,
        rel_path=rel_path,
        frontmatter=frontmatter,
        frontmatter_lines=frontmatter_lines,
        frontmatter_start_line=1,
        body_lines=body_lines,
        body_start_line=body_start_line,
    )
    return doc, []


def parse_compatibility(value: str) -> list[str]:
    """Split a ``compatibility`` value into its comma-separated elements.

    Surrounding whitespace is removed from each element, so ``"a , b"`` and
    ``"a,b"`` describe the same pair.  Nothing else is normalised: the elements
    are returned as written (case included) and in the declared order, because
    both matter to the checks that consume them.

    Args:
        value: The raw ``compatibility`` string.

    Returns:
        The stripped elements, in declaration order.  A malformed value keeps
        its empty elements (``"a,"`` yields ``["a", ""]``) so that the caller
        can report them rather than silently dropping them.
    """
    return [element.strip() for element in value.split(",")]


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #


def _find_frontmatter_line(doc: SkillDoc, prefix: str) -> int | None:
    """Return the 1-indexed line of the first frontmatter line starting with ``prefix``.

    This is a best-effort lookup used to make violation reports more precise; it
    is not required for the checks themselves to be correct, so it returns
    ``None`` (rather than raising) when no matching line is found.
    """
    for offset, line in enumerate(doc.frontmatter_lines):
        if line.strip().startswith(prefix):
            return doc.frontmatter_start_line + 1 + offset
    return None


def _is_description_quoted(doc: SkillDoc) -> bool:
    """Return whether the raw ``description:`` line double-quotes its value."""
    for line in doc.frontmatter_lines:
        stripped = line.strip()
        if stripped.startswith("description:"):
            value = stripped[len("description:") :].strip()
            return len(value) >= 2 and value.startswith('"') and value.endswith('"')
    return False


def _declared_harnesses(doc: SkillDoc) -> list[str] | None:
    """Return the harness keys a skill declares, or ``None`` when there are none usable.

    ``None`` covers both "no ``compatibility`` key" and "a value V15 rejects":
    in neither case is there a declaration another check may act on.  Keeping
    that decision in one place is what stops V16 from restating a V15 failure
    and stops a malformed value from exempting a denylisted phrase.
    """
    if "compatibility" not in doc.frontmatter:
        return None
    value = doc.declared_compatibility
    if value is None:
        return None
    harnesses = parse_compatibility(value)
    # An empty element is not a key, so the unknown-key test rejects it too.
    if any(harness not in KNOWN_HARNESSES for harness in harnesses):
        return None
    if len(set(harnesses)) != len(harnesses):
        return None
    return harnesses


def _scanned_denylist_terms(doc: SkillDoc) -> tuple[str, ...]:
    """Return the denylisted terms to look for in ``doc``'s skill (V11 and V19).

    The phrase owned by each harness the skill declares in ``compatibility`` is
    dropped; every other term of :data:`DENYLIST` is kept.
    """
    declared = _declared_harnesses(doc) or []
    exempt = {HARNESS_DENYLIST_TERMS[harness] for harness in declared}
    return tuple(term for term in DENYLIST if term not in exempt)


def _first_line_containing(term: str, lines: Sequence[str]) -> int | None:
    """Return the 0-indexed position of the first line containing ``term``, if any."""
    for offset, line in enumerate(lines):
        if term in line:
            return offset
    return None


def _denylist_violation(rel_path: str, line: int, check: str, term: str) -> Violation:
    """Build the report V11 and V19 share for one denylisted term."""
    return Violation(rel_path, line, check, f"Denylisted term {term!r} found.")


def _text_lines(text: str) -> list[str]:
    """Split ``text`` into lines after turning CRLF and lone CR into LF.

    ``str.splitlines`` is not used because it also breaks on form feeds and
    Unicode line separators, which would shift line numbers away from what an
    editor shows.  A final newline does not produce an extra empty line.
    """
    normalised = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalised.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def check_frontmatter_keys(doc: SkillDoc) -> list[Violation]:
    """V03: every frontmatter key belongs to the Agent Skills standard set."""
    violations = []
    for key in doc.frontmatter:
        if key not in ALLOWED_FRONTMATTER_KEYS:
            line = _find_frontmatter_line(doc, f"{key}:")
            violations.append(
                Violation(
                    doc.rel_path,
                    line,
                    "V03",
                    f"Frontmatter key {key!r} is outside the allowed set "
                    f"{sorted(ALLOWED_FRONTMATTER_KEYS)}.",
                )
            )
    return violations


def check_name(doc: SkillDoc) -> list[Violation]:
    """V04: ``name`` is present, well formed, short enough and matches the folder."""
    line = _find_frontmatter_line(doc, "name:")
    name = doc.frontmatter.get("name")
    if not isinstance(name, str) or not name:
        return [Violation(doc.rel_path, line, "V04", "Frontmatter is missing a 'name' string.")]

    violations = []
    if not NAME_PATTERN.match(name):
        violations.append(
            Violation(
                doc.rel_path,
                line,
                "V04",
                f"Name {name!r} must be lowercase alphanumerics joined by single hyphens.",
            )
        )
    if len(name) > MAX_NAME_CHARS:
        violations.append(
            Violation(
                doc.rel_path,
                line,
                "V04",
                f"Name is {len(name)} characters, exceeding the {MAX_NAME_CHARS}-character limit.",
            )
        )
    if name != doc.directory.name:
        violations.append(
            Violation(
                doc.rel_path,
                line,
                "V04",
                f"Name {name!r} does not match the folder name {doc.directory.name!r}.",
            )
        )
    return violations


def check_description(doc: SkillDoc) -> list[Violation]:
    """V05: ``description`` is present, within budget and double-quoted in source."""
    line = _find_frontmatter_line(doc, "description:")
    description = doc.frontmatter.get("description")
    if not isinstance(description, str) or not description:
        return [
            Violation(
                doc.rel_path,
                line,
                "V05",
                "Frontmatter is missing a non-empty 'description' string.",
            )
        ]

    violations = []
    if len(description) > MAX_DESCRIPTION_CHARS:
        violations.append(
            Violation(
                doc.rel_path,
                line,
                "V05",
                f"Description is {len(description)} characters, exceeding the "
                f"{MAX_DESCRIPTION_CHARS}-character limit.",
            )
        )
    if not _is_description_quoted(doc):
        violations.append(
            Violation(
                doc.rel_path,
                line,
                "V05",
                "Description must be double-quoted in the frontmatter source.",
            )
        )
    return violations


def check_license(doc: SkillDoc) -> list[Violation]:
    """V06: ``license`` is ``MIT``."""
    license_value = doc.frontmatter.get("license")
    if license_value == REQUIRED_LICENSE:
        return []
    line = _find_frontmatter_line(doc, "license:")
    return [
        Violation(
            doc.rel_path,
            line,
            "V06",
            f"License must be {REQUIRED_LICENSE!r}, found {license_value!r}.",
        )
    ]


def check_version(doc: SkillDoc) -> list[Violation]:
    """V07: ``metadata.version`` is a semver string."""
    line = _find_frontmatter_line(doc, "metadata:")
    metadata = doc.frontmatter.get("metadata")
    if metadata is None:
        return [
            Violation(doc.rel_path, line, "V07", "Frontmatter is missing a 'metadata' mapping.")
        ]
    if not isinstance(metadata, dict):
        return [Violation(doc.rel_path, line, "V07", "'metadata' must be a mapping.")]
    if "version" not in metadata:
        return [Violation(doc.rel_path, line, "V07", "'metadata' is missing a 'version' key.")]

    version = metadata["version"]
    if not isinstance(version, str):
        return [
            Violation(
                doc.rel_path,
                line,
                "V07",
                f"'metadata.version' must be a string, found {type(version).__name__}.",
            )
        ]
    if not VERSION_PATTERN.match(version):
        return [
            Violation(
                doc.rel_path,
                line,
                "V07",
                f"'metadata.version' {version!r} does not match x.y.z.",
            )
        ]
    return []


def check_body_length(doc: SkillDoc) -> list[Violation]:
    """V08: the body stays within the recommended line budget."""
    if len(doc.body_lines) <= MAX_BODY_LINES:
        return []
    return [
        Violation(
            doc.rel_path,
            None,
            "V08",
            f"Body has {len(doc.body_lines)} lines, exceeding the {MAX_BODY_LINES}-line budget.",
        )
    ]


def check_version_marker(doc: SkillDoc) -> list[Violation]:
    """V09: exactly one version marker exists and agrees with ``metadata.version``."""
    matches: list[tuple[int, str]] = []
    for offset, line in enumerate(doc.body_lines):
        match = VERSION_MARKER_PATTERN.match(line)
        if match:
            matches.append((doc.body_start_line + offset, match.group(1)))

    if len(matches) != 1:
        return [
            Violation(
                doc.rel_path,
                None,
                "V09",
                f"Expected exactly one '**skill version**' marker, found {len(matches)}.",
            )
        ]

    marker_line, marker_version = matches[0]
    declared_version = doc.declared_version
    if declared_version is not None and marker_version != declared_version:
        return [
            Violation(
                doc.rel_path,
                marker_line,
                "V09",
                f"Version marker {marker_version!r} does not match "
                f"metadata.version {declared_version!r}.",
            )
        ]
    return []


def check_usage_declaration(doc: SkillDoc) -> list[Violation]:
    """V10: the body instructs the agent to announce the skill name and version."""
    name = doc.declared_name
    version = doc.declared_version
    if name is None or version is None:
        return [
            Violation(
                doc.rel_path,
                None,
                "V10",
                "Cannot verify the usage declaration without a valid name and version.",
            )
        ]

    expected = f"{name} スキル v{version} を使用します"
    if expected in doc.body:
        return []
    return [
        Violation(
            doc.rel_path,
            None,
            "V10",
            f"Body must declare {expected!r} on first use.",
        )
    ]


def check_denylist(doc: SkillDoc) -> list[Violation]:
    """V11: no agent-specific proper noun, private name or personal path remains.

    A skill that declares ``compatibility`` is written for the harnesses it
    names, so naming them is documentation rather than a leftover: the phrase
    owned by each declared harness is removed from the scanned terms for that
    skill alone.  Every other denylisted term — subagent names, personal paths,
    the phrase of an undeclared harness — is still scanned, and a declaration
    V15 rejects exempts nothing at all.
    """
    violations = []
    for term in _scanned_denylist_terms(doc):
        line = None
        offset = _first_line_containing(term, doc.frontmatter_lines)
        if offset is not None:
            line = doc.frontmatter_start_line + 1 + offset
        else:
            offset = _first_line_containing(term, doc.body_lines)
            if offset is not None:
                line = doc.body_start_line + offset
        if line is not None:
            violations.append(_denylist_violation(doc.rel_path, line, "V11", term))
    return violations


def _auxiliary_text_files(skill_dir: Path) -> list[Path]:
    """Return the auxiliary text files V19 scans in ``skill_dir``, sorted by path.

    These are the files with an extension in :data:`AUXILIARY_TEXT_SUFFIXES`,
    at any depth, except the top-level ``SKILL.md`` (V11 covers it), anything
    under the top-level ``external/`` (V17 forbids it outright) and anything
    under a folder named in :data:`AUXILIARY_SKIPPED_DIRNAMES`.  Symlinked
    folders are not followed, so a link cannot pull in text from elsewhere.
    """
    found: list[Path] = []
    for current, dirnames, filenames in os.walk(skill_dir):
        current_path = Path(current)
        # Pruning ``dirnames`` in place is how ``os.walk`` is told not to descend.
        dirnames[:] = [
            dirname
            for dirname in dirnames
            if dirname not in AUXILIARY_SKIPPED_DIRNAMES
            and not (current_path == skill_dir and dirname == EXTERNAL_CONTENT_DIRNAME)
        ]
        for filename in filenames:
            path = current_path / filename
            if current_path == skill_dir and filename == SKILL_FILENAME:
                continue
            if path.suffix.lower() not in AUXILIARY_TEXT_SUFFIXES or not path.is_file():
                continue
            found.append(path)
    return sorted(found)


def check_auxiliary_denylist(doc: SkillDoc, repo_root: Path) -> list[Violation]:
    """V19: the V11 denylist also holds for a skill's other text files.

    Every file :func:`_auxiliary_text_files` returns is scanned with the terms
    V11 uses for the same skill, so the ``compatibility`` declared in
    ``SKILL.md`` exempts the same harness phrases here.  A file that is not
    valid UTF-8 is treated as binary and skipped.

    Args:
        doc: The parsed ``SKILL.md`` of the skill, which supplies the folder and
            the declared harnesses.
        repo_root: Repository root, used to build relative paths for reports.

    Returns:
        One violation per file and term, at the first line the term is on,
        ordered by file path and then by :data:`DENYLIST` order.
    """
    terms = _scanned_denylist_terms(doc)
    violations = []
    for path in _auxiliary_text_files(doc.directory):
        try:
            text = path.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            continue
        lines = _text_lines(text)
        rel_path = str(path.relative_to(repo_root))
        for term in terms:
            offset = _first_line_containing(term, lines)
            if offset is not None:
                violations.append(_denylist_violation(rel_path, offset + 1, "V19", term))
    return violations


def check_openai_yaml(doc: SkillDoc, repo_root: Path) -> list[Violation]:
    """V12: the Codex UI metadata file exists and carries usable interface fields."""
    yaml_path = doc.directory / OPENAI_YAML_RELPATH
    rel_path = str(yaml_path.relative_to(repo_root))

    if not yaml_path.is_file():
        return [Violation(rel_path, None, "V12", f"{OPENAI_YAML_RELPATH} is missing.")]

    try:
        # ``yaml.safe_load`` (never ``yaml.load``) avoids constructing arbitrary
        # Python objects from untrusted skill content.
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        return [Violation(rel_path, None, "V12", f"{OPENAI_YAML_RELPATH} is not valid YAML: {exc}")]

    if not isinstance(data, dict):
        return [Violation(rel_path, None, "V12", f"{OPENAI_YAML_RELPATH} must parse to a mapping.")]

    interface = data.get("interface")
    if not isinstance(interface, dict):
        return [Violation(rel_path, None, "V12", "'interface' must be a mapping.")]

    violations = []
    for key in ("display_name", "short_description", "default_prompt"):
        if key not in interface:
            violations.append(Violation(rel_path, None, "V12", f"'interface.{key}' is missing."))
    if violations:
        return violations

    name = doc.declared_name
    default_prompt = interface["default_prompt"]
    if not isinstance(default_prompt, str) or (
        name is not None and f"${name}" not in default_prompt
    ):
        violations.append(
            Violation(
                rel_path,
                None,
                "V12",
                f"'interface.default_prompt' must reference the skill name as '${name}'.",
            )
        )

    short_description = interface["short_description"]
    too_long = (
        not isinstance(short_description, str)
        or len(short_description) > MAX_SHORT_DESCRIPTION_CHARS
    )
    if too_long:
        violations.append(
            Violation(
                rel_path,
                None,
                "V12",
                "'interface.short_description' is longer than "
                f"{MAX_SHORT_DESCRIPTION_CHARS} characters.",
            )
        )
    return violations


def check_compatibility(doc: SkillDoc) -> list[Violation]:
    """V15: ``compatibility`` names known harnesses, each exactly once.

    The key is optional: omitting it is how a skill says "every harness". When
    it is present it drives both the install manifests and the V11 exemption,
    so a value no installer can act on is an error rather than a hint.
    """
    if "compatibility" not in doc.frontmatter:
        return []

    line = _find_frontmatter_line(doc, "compatibility:")
    value = doc.declared_compatibility
    if value is None:
        found = doc.frontmatter["compatibility"]
        return [
            Violation(
                doc.rel_path,
                line,
                "V15",
                "'compatibility' must be a comma-separated string of harness keys, "
                f"found {type(found).__name__}.",
            )
        ]

    harnesses = parse_compatibility(value)
    violations = []
    if any(not harness for harness in harnesses):
        violations.append(
            Violation(
                doc.rel_path,
                line,
                "V15",
                f"'compatibility' {value!r} has an empty element; write the keys as "
                "'a, b' without a leading, trailing or doubled comma.",
            )
        )
    unknown = sorted(
        {harness for harness in harnesses if harness and harness not in KNOWN_HARNESSES}
    )
    if unknown:
        violations.append(
            Violation(
                doc.rel_path,
                line,
                "V15",
                f"'compatibility' names unknown harness(es) {unknown}; the accepted "
                f"keys are {sorted(KNOWN_HARNESSES)}.",
            )
        )
    duplicates = sorted(
        {harness for harness in harnesses if harness and harnesses.count(harness) > 1}
    )
    if duplicates:
        violations.append(
            Violation(
                doc.rel_path,
                line,
                "V15",
                f"'compatibility' names {duplicates} more than once.",
            )
        )
    return violations


def check_compatibility_preamble(doc: SkillDoc) -> list[Violation]:
    """V16: the description opens by naming the declared harnesses.

    A restricted skill is listed next to unrestricted ones, so the restriction
    has to be visible in the description itself — and at its start, because a
    listing that truncates the text keeps only the opening.

    Nothing is reported when the declaration is missing or already rejected by
    V15: without a usable list there is no preamble to expect, and repeating the
    same defect under a second identifier would only hide the real cause.
    """
    harnesses = _declared_harnesses(doc)
    if harnesses is None:
        return []

    display_names = [KNOWN_HARNESSES[harness] for harness in harnesses]
    expected = HARNESS_DISPLAY_SEPARATOR.join(display_names) + HARNESS_PREAMBLE_SUFFIX
    description = doc.frontmatter.get("description")
    if isinstance(description, str) and description.startswith(expected):
        return []
    return [
        Violation(
            doc.rel_path,
            _find_frontmatter_line(doc, "description:"),
            "V16",
            f"Description must start with {expected!r} because 'compatibility' "
            f"declares {harnesses}.",
        )
    ]


def _reject_json_constant(constant: str) -> Any:
    """Refuse ``NaN``, ``Infinity`` and ``-Infinity``, which are not JSON.

    ``json.loads`` accepts them by default; passing this as ``parse_constant``
    makes the parse strict.
    """
    raise ValueError(f"the constant {constant} is not allowed in JSON")


def _name_problems(value: Any) -> list[str]:
    """Return what is wrong with a declared ``name`` (empty when it is sound)."""
    if not isinstance(value, str):
        return [f"must be a string, found {type(value).__name__}."]
    problems = []
    if not NAME_PATTERN.match(value):
        problems.append(f"{value!r} must be lowercase alphanumerics joined by single hyphens.")
    if len(value) > MAX_NAME_CHARS:
        problems.append(
            f"is {len(value)} characters, exceeding the {MAX_NAME_CHARS}-character limit."
        )
    return problems


def _origin_problems(value: Any) -> list[str]:
    """Return what is wrong with a declared ``origin`` (empty when it is sound)."""
    if not isinstance(value, str):
        return [f"must be a string, found {type(value).__name__}."]
    if not value.startswith("https://"):
        return [f"{value!r} must start with 'https://'."]
    try:
        parts = urlsplit(value)
    except ValueError as exc:
        return [f"{value!r} is not a valid URL: {exc}."]

    problems = []
    if not parts.hostname:
        problems.append(f"{value!r} must name a host.")
    if "@" in parts.netloc:
        problems.append("must not embed credentials ('user@' or 'user:pass@').")
    return problems


def _relative_path_problems(value: Any) -> list[str]:
    """Return what is wrong with a relative path value (empty when it is sound).

    Shared by ``path`` and each ``include`` element.  ``.`` (the repository
    root) and a trailing ``/`` (the whole directory) are both sound.
    """
    if not isinstance(value, str):
        return [f"must be a string, found {type(value).__name__}."]
    if not value:
        return ["must not be empty."]

    problems = []
    if value.startswith("/"):
        problems.append(f"{value!r} must be relative, not start with '/'.")
    if "\\" in value:
        problems.append(f"{value!r} must not contain a backslash.")
    if ".." in value.split("/"):
        problems.append(f"{value!r} must not contain a '..' part.")
    return problems


def check_external_declaration(skill_dir: Path, repo_root: Path) -> list[Violation]:
    """V17: a skill ships an ``external.json`` declaration, never fetched ``external/``.

    The declaration names external repositories whose material is fetched into
    ``external/<name>/`` on the user's machine.  The repository must hold the
    declaration only, so an entry named ``external`` (directory, file or symlink)
    is a violation whether or not a declaration exists.  A skill with neither
    is not affected.

    The declaration must be strict UTF-8 JSON (``NaN`` and ``Infinity`` are
    rejected) whose top level is an object with ``format`` equal to the integer
    ``1`` and a non-empty ``externals`` array.  Each element is an object with
    ``name`` (a skill-style name, unique within the array), ``origin`` (an
    ``https://`` URL with a host and no credentials) and ``path`` (a non-empty
    relative path without ``..`` or backslashes; ``.`` is allowed), plus an
    optional non-empty ``ref`` string and an optional non-empty ``include``
    array of relative path strings (a trailing ``/`` is allowed).  Unknown keys
    are allowed at both levels.

    Every violation found is returned, except that a declaration that cannot be
    read as JSON or whose top level is not an object yields that one violation
    alone, because nothing below it can be inspected.

    Args:
        skill_dir: The skill folder.
        repo_root: Repository root, used to build relative paths for reports.

    Returns:
        The violations found, ``external/`` first and then the declaration's.
        Empty when the skill has neither the declaration nor ``external/``.
    """
    violations: list[Violation] = []

    # ``Path.exists`` follows symlinks, so a dangling link would be missed; the
    # lexical test sees every kind of entry, which is what "any entry named
    # ``external``" asks for.
    content_path = skill_dir / EXTERNAL_CONTENT_DIRNAME
    if os.path.lexists(content_path):
        violations.append(
            Violation(
                str(content_path.relative_to(repo_root)),
                None,
                "V17",
                f"{EXTERNAL_CONTENT_DIRNAME!r} must not be committed: the repository holds "
                f"the {EXTERNAL_DECLARATION_FILENAME} declaration only, and the content is "
                "fetched on the user's machine.",
            )
        )

    declaration_path = skill_dir / EXTERNAL_DECLARATION_FILENAME
    if not os.path.lexists(declaration_path):
        return violations

    rel_path = str(declaration_path.relative_to(repo_root))
    if not declaration_path.is_file():
        violations.append(
            Violation(rel_path, None, "V17", f"{EXTERNAL_DECLARATION_FILENAME} must be a file.")
        )
        return violations

    try:
        text = declaration_path.read_text(encoding="utf-8")
        document = json.loads(text, parse_constant=_reject_json_constant)
    except (OSError, ValueError, RecursionError) as exc:
        # ``UnicodeDecodeError`` and ``json.JSONDecodeError`` are both
        # ``ValueError`` subclasses, as is the error ``_reject_json_constant``
        # raises for ``NaN`` and ``Infinity``.
        violations.append(
            Violation(
                rel_path,
                None,
                "V17",
                f"{EXTERNAL_DECLARATION_FILENAME} is not valid UTF-8 JSON: {exc}",
            )
        )
        return violations

    if not isinstance(document, dict):
        violations.append(Violation(rel_path, None, "V17", "The top level must be a JSON object."))
        return violations

    def report(message: str) -> None:
        violations.append(Violation(rel_path, None, "V17", message))

    # ``True == 1`` in Python, so the type is compared exactly: ``true`` and
    # ``1.0`` are not the integer ``1``.
    declared_format = document.get("format")
    if type(declared_format) is not int or declared_format != EXTERNAL_DECLARATION_FORMAT:
        report(f"'format' must be the integer {EXTERNAL_DECLARATION_FORMAT}.")

    externals = document.get("externals")
    if not isinstance(externals, list) or not externals:
        report("'externals' must be a non-empty array.")
        return violations

    names: list[str] = []
    for index, entry in enumerate(externals):
        label = f"externals[{index}]"
        if not isinstance(entry, dict):
            report(f"{label} must be a JSON object.")
            continue
        for key in ("name", "origin", "path"):
            if key not in entry:
                report(f"{label} is missing the required key {key!r}.")

        if "name" in entry:
            for problem in _name_problems(entry["name"]):
                report(f"{label}.name {problem}")
            if isinstance(entry["name"], str):
                names.append(entry["name"])
        if "origin" in entry:
            for problem in _origin_problems(entry["origin"]):
                report(f"{label}.origin {problem}")
        if "path" in entry:
            for problem in _relative_path_problems(entry["path"]):
                report(f"{label}.path {problem}")
        if "ref" in entry and (not isinstance(entry["ref"], str) or not entry["ref"]):
            report(f"{label}.ref must be a non-empty string when present.")
        if "include" in entry:
            include = entry["include"]
            if not isinstance(include, list) or not include:
                report(f"{label}.include must be a non-empty array when present.")
            else:
                for position, item in enumerate(include):
                    for problem in _relative_path_problems(item):
                        report(f"{label}.include[{position}] {problem}")

    for name in sorted({name for name in names if names.count(name) > 1}):
        report(f"'name' {name!r} is used by more than one element of 'externals'.")
    return violations


#: Passages that several skills carry word for word, checked by V18.  Adding a
#: shared passage is one entry here: ``SharedTextGroup.sections`` for the
#: section under a heading, ``SharedTextGroup.files`` for whole files.  The
#: table sits here rather than with the other conventions because building it
#: validates each path with :func:`_relative_path_problems`.
SHARED_TEXT_GROUPS: tuple[SharedTextGroup, ...] = (
    SharedTextGroup.sections(
        "version-state-section",
        "## 版の状態の見分け方",
        "version-start/SKILL.md",
        "version-implement/SKILL.md",
        "version-release/SKILL.md",
    ),
    SharedTextGroup.sections(
        "handoff-section",
        "## 申し送りの扱い",
        "version-start/SKILL.md",
        "version-implement/SKILL.md",
        "version-release/SKILL.md",
    ),
    SharedTextGroup.files(
        "audit-reference",
        "skill-sync/references/audit.md",
        "register-external-skill/references/audit.md",
    ),
    SharedTextGroup.files(
        "contents-reference",
        "skill-sync/references/contents.md",
        "register-external-skill/references/contents.md",
    ),
)


@dataclasses.dataclass(frozen=True)
class _SharedTextExcerpt:
    """The text of one V18 target, ready for comparison.

    Attributes:
        lines: The lines compared, without trailing blank lines.
        first_line: 1-indexed line number of ``lines[0]`` in the file.
    """

    lines: list[str]
    first_line: int


def _section_bounds(lines: Sequence[str], heading: str) -> tuple[list[int], int | None]:
    """Locate the section that ``heading`` opens.

    A line is a heading only outside fenced code blocks.  The section runs from
    the heading line up to (not including) the next heading of the same or a
    higher level, or to the end of the file.

    Args:
        lines: The file's lines.
        heading: The full heading line; trailing whitespace on a file line is
            ignored when matching it.

    Returns:
        The 0-indexed positions of every line matching ``heading``, and the end
        (exclusive) of the section opened by the first of them, or ``None`` when
        there is no match.
    """
    level = len(heading) - len(heading.lstrip("#"))
    starts: list[int] = []
    end: int | None = None
    fence: str | None = None
    for index, line in enumerate(lines):
        if fence is not None:
            # A fence closes on a run of its own character, at least as long as
            # the opening run, with nothing but whitespace after it.
            closing = CODE_FENCE_PATTERN.match(line)
            if (
                closing
                and closing.group(1)[0] == fence[0]
                and len(closing.group(1)) >= len(fence)
                and not line[closing.end() :].strip()
            ):
                fence = None
            continue
        opening = CODE_FENCE_PATTERN.match(line)
        if opening:
            fence = opening.group(1)
            continue
        match = HEADING_PATTERN.match(line)
        if not match:
            continue
        if starts and end is None and len(match.group(1)) <= level:
            end = index
        if line.rstrip() == heading:
            starts.append(index)
    if not starts:
        return starts, None
    return starts, end if end is not None else len(lines)


def _extract_shared_text(
    repo_root: Path, group: SharedTextGroup, target: SharedTextTarget
) -> tuple[_SharedTextExcerpt | None, Violation | None]:
    """Read one V18 target, reporting why it cannot be compared when it cannot.

    Returns:
        The excerpt and ``None``, or ``None`` and the violation to report.
    """

    def problem(message: str) -> tuple[None, Violation]:
        return None, Violation(target.label, None, "V18", f"Group {group.name!r}: {message}")

    path = repo_root / SKILLS_DIRNAME / target.path
    if not path.is_file():
        return problem("the file is missing.")
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        return problem(f"the file is not valid UTF-8: {exc}")

    lines = _text_lines(text)
    first = 0
    end = len(lines)
    if target.heading is not None:
        starts, section_end = _section_bounds(lines, target.heading)
        if section_end is None:
            return problem(f"the heading {target.heading!r} is not found outside code blocks.")
        if len(starts) > 1:
            numbers = ", ".join(str(start + 1) for start in starts)
            return problem(
                f"the heading {target.heading!r} appears more than once (lines {numbers})."
            )
        first, end = starts[0], section_end

    excerpt = list(lines[first:end])
    while excerpt and not excerpt[-1].strip():
        excerpt.pop()
    return _SharedTextExcerpt(excerpt, first + 1), None


def _describe_line(excerpt: _SharedTextExcerpt, index: int) -> str:
    """Return the line number and the quoted content of ``excerpt`` at ``index``."""
    number = excerpt.first_line + index
    if index < len(excerpt.lines):
        return f"line {number} {excerpt.lines[index]!r}"
    return f"line {number} <end of text>"


def check_shared_text(
    repo_root: Path, groups: Sequence[SharedTextGroup] = SHARED_TEXT_GROUPS
) -> list[Violation]:
    """V18: passages that claim to be the same text are identical.

    For each group, every target is extracted (a whole file, or the section a
    heading opens) and compared line by line with the first target that could
    be extracted.  Line endings are normalised to LF and trailing blank lines
    are dropped before the comparison.  A missing file, a missing heading and a
    heading that appears twice are violations in their own right, so renaming
    a file or a heading cannot quietly take a passage out of the check.

    Args:
        repo_root: Repository root (the directory containing ``skills/``).
        groups: The groups to check, defaulting to :data:`SHARED_TEXT_GROUPS`.

    Returns:
        The violations in group order: for each group, the targets that could
        not be read, then one violation per target that differs from the
        reference, placed at its first differing line.
    """
    violations: list[Violation] = []
    for group in groups:
        excerpts: list[tuple[SharedTextTarget, _SharedTextExcerpt]] = []
        for target in group.targets:
            excerpt, violation = _extract_shared_text(repo_root, group, target)
            if violation is not None:
                violations.append(violation)
            elif excerpt is not None:
                excerpts.append((target, excerpt))
        if not excerpts:
            continue

        reference_target, reference = excerpts[0]
        for target, excerpt in excerpts[1:]:
            if excerpt.lines == reference.lines:
                continue
            index = next(
                (
                    position
                    for position, (left, right) in enumerate(
                        zip(reference.lines, excerpt.lines, strict=False)
                    )
                    if left != right
                ),
                min(len(reference.lines), len(excerpt.lines)),
            )
            violations.append(
                Violation(
                    target.label,
                    excerpt.first_line + index,
                    "V18",
                    f"Group {group.name!r}: {target.label} {_describe_line(excerpt, index)} "
                    f"differs from {reference_target.label} {_describe_line(reference, index)}.",
                )
            )
    return violations


def check_manifest_drift(repo_root: Path) -> list[Violation]:
    """V13/V14: the committed manifests match what the generator produces.

    Args:
        repo_root: Repository root (the directory containing ``skills/``).

    Returns:
        One violation per manifest that differs from the generated bytes, or a
        single violation when the manifests cannot be generated at all.
    """
    # Imported here rather than at module level because ``manifests`` imports
    # this module: a top-level import would close the cycle and fail. This
    # function only runs after ``validate`` is fully loaded, so the local import
    # resolves cleanly.
    import manifests

    index_rel_path = str(manifests.INDEX_RELPATH)

    try:
        mismatches = manifests.check_manifests(repo_root)
    except manifests.ManifestError as exc:
        # A generation failure (missing ``VERSION``, unparseable skill) blocks
        # both manifests, but it has a single cause, so it is reported once
        # instead of duplicated per manifest. The reason is carried in the
        # message because the path alone does not identify what went wrong.
        return [
            Violation(
                index_rel_path,
                None,
                "V13",
                f"Manifests cannot be generated, so the committed copies cannot be checked: {exc}",
            )
        ]

    violations: list[Violation] = []
    for mismatch in mismatches:
        # The manifest path decides the check identifier: the OpenCode index is
        # V13, everything else (the marketplace manifest and a plugin.json that
        # must not exist) belongs to the Claude Code manifest check, V14.
        check = "V13" if mismatch.rel_path == index_rel_path else "V14"
        violations.append(Violation(mismatch.rel_path, None, check, mismatch.message))
    return violations


def check_marketplace_shape(repo_root: Path) -> list[Violation]:
    """V14: the committed marketplace manifest keeps its structural invariants.

    This duplicates part of the byte comparison in :func:`check_manifest_drift`
    on purpose. The drift check only says "not what today's generator writes";
    this one pins the invariants themselves, so a future change to the generator
    that quietly breaks the install cannot pass by regenerating the file.

    Args:
        repo_root: Repository root (the directory containing ``.claude-plugin/``).

    Returns:
        One violation per broken invariant, empty when the manifest is sound.
    """
    # Same circular-import reason as in ``check_manifest_drift``.
    import manifests

    rel_path = str(manifests.MARKETPLACE_RELPATH)
    path = repo_root / manifests.MARKETPLACE_RELPATH

    if not path.is_file():
        return [Violation(rel_path, None, "V14", "File is missing.")]

    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return [Violation(rel_path, None, "V14", f"File is not valid JSON: {exc}")]

    if not isinstance(document, dict):
        return [Violation(rel_path, None, "V14", "Manifest must be a JSON object.")]

    violations: list[Violation] = []

    # ``owner`` as a bare string is valid JSON but invalid against the
    # marketplace schema, which requires a mapping carrying ``name``. The type
    # is therefore checked, not just the presence of the key.
    owner = document.get("owner")
    if not isinstance(owner, dict):
        violations.append(
            Violation(
                rel_path,
                None,
                "V14",
                f"'owner' must be a mapping with a 'name' key, found {type(owner).__name__}.",
            )
        )
    elif "name" not in owner:
        violations.append(Violation(rel_path, None, "V14", "'owner' is missing a 'name' key."))

    plugins = document.get("plugins")
    if not isinstance(plugins, list):
        violations.append(
            Violation(
                rel_path,
                None,
                "V14",
                f"'plugins' must be a list, found {type(plugins).__name__}.",
            )
        )
        return violations
    if len(plugins) != 1:
        # Every skill ships inside one plugin, so a second entry would change
        # the invocation names users type. Stop here: without exactly one entry
        # there is nothing well-defined to inspect below.
        violations.append(
            Violation(
                rel_path,
                None,
                "V14",
                f"'plugins' must hold exactly one entry, found {len(plugins)}.",
            )
        )
        return violations

    plugin = plugins[0]
    if not isinstance(plugin, dict):
        violations.append(
            Violation(
                rel_path,
                None,
                "V14",
                f"The plugin entry must be a mapping, found {type(plugin).__name__}.",
            )
        )
        return violations

    source = plugin.get("source")
    if source != manifests.PLUGIN_SOURCE:
        violations.append(
            Violation(
                rel_path,
                None,
                "V14",
                f"Plugin 'source' must be {manifests.PLUGIN_SOURCE!r}, found {source!r}.",
            )
        )

    if "skills" in plugin:
        # The invariant this whole function exists for. With a marketplace-root
        # ``source``, Claude Code scans ``skills/`` by default; a ``skills`` key
        # *replaces* that scan instead of adding to it, so every skill added or
        # removed later would silently disappear from the plugin until someone
        # remembered to edit this manifest by hand.
        violations.append(
            Violation(
                rel_path,
                None,
                "V14",
                "Plugin entry must not carry a 'skills' key: it would replace Claude Code's "
                "default 'skills/' scan, so the manifest would need editing for every skill "
                "added or removed.",
            )
        )

    try:
        project_version = manifests.read_project_version(repo_root)
    except manifests.ManifestError as exc:
        # Without a usable ``VERSION`` the comparison cannot be made at all,
        # which is itself a defect worth reporting rather than skipping.
        violations.append(
            Violation(
                rel_path,
                None,
                "V14",
                f"Plugin 'version' cannot be compared with {manifests.VERSION_FILENAME}: {exc}",
            )
        )
    else:
        plugin_version = plugin.get("version")
        if plugin_version != project_version:
            violations.append(
                Violation(
                    rel_path,
                    None,
                    "V14",
                    f"Plugin 'version' {plugin_version!r} does not match "
                    f"{manifests.VERSION_FILENAME} {project_version!r}.",
                )
            )

    return violations


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
    doc, violations = load_skill(skill_dir, repo_root)
    if doc is None:
        # V17 inspects the folder rather than ``SKILL.md``, so a broken skill
        # still gets it: a committed ``external/`` must not hide behind V01/V02.
        violations = list(violations)
        violations.extend(check_external_declaration(skill_dir, repo_root))
        return violations

    violations = list(violations)
    violations.extend(check_frontmatter_keys(doc))
    violations.extend(check_name(doc))
    violations.extend(check_description(doc))
    violations.extend(check_license(doc))
    violations.extend(check_version(doc))
    violations.extend(check_body_length(doc))
    violations.extend(check_version_marker(doc))
    violations.extend(check_usage_declaration(doc))
    violations.extend(check_denylist(doc))
    violations.extend(check_auxiliary_denylist(doc, repo_root))
    violations.extend(check_openai_yaml(doc, repo_root))
    violations.extend(check_compatibility(doc))
    violations.extend(check_compatibility_preamble(doc))
    violations.extend(check_external_declaration(skill_dir, repo_root))
    return violations


def validate_repository(
    repo_root: Path,
    min_skills: int = 1,
    verify_manifests: bool = False,
    verify_shared_text: bool = False,
) -> list[Violation]:
    """Validate every skill in the repository.

    Implements check ``V00``: ``skills/`` exists and holds at least ``min_skills``
    skill folders. The release gate raises ``min_skills`` to the number of skills
    the version ships, which turns "all expected skills are present" into a
    machine-checked requirement rather than a manual count.

    Args:
        repo_root: Repository root (the directory containing ``skills/``).
        min_skills: Minimum number of skill folders that must be present.
        verify_manifests: Whether to also run the manifest checks ``V13``/``V14``.
            Defaults to ``False`` because those checks need repository-level
            files that a caller validating a bare tree of skill folders (the
            test fixtures, for instance) has no reason to provide: neither a
            ``VERSION`` file nor either generated manifest. The CLI passes
            ``True``, so a real checkout is always fully checked.
        verify_shared_text: Whether to also run ``V18`` with
            :data:`SHARED_TEXT_GROUPS`.  Defaults to ``False`` for the same
            reason: the groups name files of the real repository, which a bare
            tree of skill folders does not have.  The CLI passes ``True``.

    Returns:
        All violations found across the repository, with any ``V00`` violation
        first, then per-skill violations in skill-name order, then any ``V18``
        violation, then any manifest violation.
    """
    skills_root = repo_root / SKILLS_DIRNAME
    skill_dirs = discover_skill_dirs(skills_root)

    violations: list[Violation] = []
    if len(skill_dirs) < min_skills:
        violations.append(
            Violation(
                SKILLS_DIRNAME,
                None,
                "V00",
                f"Expected at least {min_skills} skill folder(s) under "
                f"{SKILLS_DIRNAME!r}, found {len(skill_dirs)}.",
            )
        )

    # ``discover_skill_dirs`` already sorts by name, so skills are validated
    # (and their violations reported) in a stable, deterministic order.
    for skill_dir in skill_dirs:
        violations.extend(validate_skill(skill_dir, repo_root))

    # Shared passages span several skills, so they are compared once every
    # skill has been checked on its own.
    if verify_shared_text:
        violations.extend(check_shared_text(repo_root))

    # The manifests describe the repository as a whole, so they are checked
    # after every skill and their violations are appended last.
    if verify_manifests:
        violations.extend(check_manifest_drift(repo_root))
        violations.extend(check_marketplace_shape(repo_root))
    return violations


def format_violation(violation: Violation) -> str:
    """Render a violation as ``path:line: CHECK: message``."""
    line = "-" if violation.line is None else str(violation.line)
    return f"{violation.path}:{line}: {violation.check}: {violation.message}"


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point.

    Args:
        argv: Command-line arguments, defaulting to ``sys.argv[1:]``.

    Returns:
        0 when no violation was found, 1 otherwise.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--min-skills",
        type=int,
        default=1,
        help="Minimum number of skill folders required under skills/.",
    )
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parent.parent
    # Run from a real checkout, so the generated manifests and the shared
    # passages are always checked too: a stale manifest is served to users
    # exactly as committed, and a drifted copy is read by agents as written.
    violations = validate_repository(
        repo_root,
        min_skills=args.min_skills,
        verify_manifests=True,
        verify_shared_text=True,
    )

    for violation in violations:
        print(format_violation(violation))

    if violations:
        return 1

    print("All skills passed validation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
