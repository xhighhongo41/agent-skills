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
                "File does not start with a '---' frontmatter block, or the "
                "block is never closed.",
            )
        ]

    frontmatter_lines, body_lines, body_start_line = split
    frontmatter_source = "\n".join(frontmatter_lines)
    try:
        # ``yaml.safe_load`` (never ``yaml.load``) avoids constructing arbitrary
        # Python objects from untrusted skill content.
        frontmatter = yaml.safe_load(frontmatter_source)
    except yaml.YAMLError as exc:
        return None, [
            Violation(rel_path, None, "V02", f"Frontmatter is not valid YAML: {exc}")
        ]

    if not isinstance(frontmatter, dict):
        return None, [
            Violation(rel_path, None, "V02", "Frontmatter must parse to a mapping.")
        ]

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
    """V11: no agent-specific proper noun, private name or personal path remains."""
    violations = []
    for term in DENYLIST:
        line = None
        for offset, source_line in enumerate(doc.frontmatter_lines):
            if term in source_line:
                line = doc.frontmatter_start_line + 1 + offset
                break
        if line is None:
            for offset, source_line in enumerate(doc.body_lines):
                if term in source_line:
                    line = doc.body_start_line + offset
                    break
        if line is not None:
            violations.append(
                Violation(doc.rel_path, line, "V11", f"Denylisted term {term!r} found.")
            )
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
        return [
            Violation(rel_path, None, "V12", f"{OPENAI_YAML_RELPATH} is not valid YAML: {exc}")
        ]

    if not isinstance(data, dict):
        return [
            Violation(rel_path, None, "V12", f"{OPENAI_YAML_RELPATH} must parse to a mapping.")
        ]

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
    violations.extend(check_openai_yaml(doc, repo_root))
    return violations


def validate_repository(repo_root: Path, min_skills: int = 1) -> list[Violation]:
    """Validate every skill in the repository.

    Implements check ``V00``: ``skills/`` exists and holds at least ``min_skills``
    skill folders. The release gate raises ``min_skills`` to the number of skills
    the version ships, which turns "all expected skills are present" into a
    machine-checked requirement rather than a manual count.

    Args:
        repo_root: Repository root (the directory containing ``skills/``).
        min_skills: Minimum number of skill folders that must be present.

    Returns:
        All violations found across the repository, with any ``V00`` violation
        first, followed by per-skill violations in skill-name order.
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
    violations = validate_repository(repo_root, min_skills=args.min_skills)

    for violation in violations:
        print(format_violation(violation))

    if violations:
        return 1

    print("All skills passed validation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
