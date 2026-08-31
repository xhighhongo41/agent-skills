#!/usr/bin/env python3
"""Catch skill edits that were released without a version bump.

Run from the repository root::

    python tools/version_guard.py
    python tools/version_guard.py --ref v1.0.0

Two release rules are enforced, both comparing the committed tree against the
most recent release tag:

* a skill whose folder changed must no longer declare the ``metadata.version`` it
  declared at the tag;
* any change under ``skills/`` must be accompanied by a change to ``VERSION``,
  because plugin users only receive an update when the project version rises.

Whether the new number is *greater* is not judged here: ``validate.py`` already
pins the format, and a deliberate re-numbering is a human decision.

The rules live here rather than in ``tools/validate.py`` on purpose: ``validate``
only ever looks at the files on disk, and mixing git history into it would break
that guarantee for every caller that validates a bare tree of skill folders.

The exit code is 0 when no omission is found and 1 when at least one is. When the
history needed for the comparison is not available -- no git executable, no git
checkout, or a clone without tags -- the check is *skipped* and the exit code is
still 0: a build that fails because it cannot run a check is worse than a build
that reports the check was not run.
"""

from __future__ import annotations

import argparse
import dataclasses
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import manifests
import validate
import yaml

# --------------------------------------------------------------------------- #
# Conventions
# --------------------------------------------------------------------------- #

#: Revision holding the content to compare against the baseline tag. The working
#: tree is deliberately not used: an uncommitted edit is still being written, and
#: only what is committed can reach a user.
HEAD_REF = "HEAD"

#: Upper bound for a single git invocation. A hung git would otherwise stall CI
#: for as long as the job allows.
GIT_TIMEOUT_SECONDS = 30

#: Checkout inspected when ``--repo-root`` is not given: the repository this
#: script is part of.
DEFAULT_REPO_ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------- #
# Data structures
# --------------------------------------------------------------------------- #


@dataclasses.dataclass(frozen=True)
class Finding:
    """A single version-bump omission (or a warning about one).

    Attributes:
        subject: What the finding is about, as a repository-relative path such as
            ``"skills/skill-sync"`` or ``"VERSION"``.
        message: Human-readable description of what changed and what was not
            bumped.
        is_warning: ``True`` when the check could not be completed rather than
            failed. Warnings are reported but do not fail the build.
    """

    subject: str
    message: str
    is_warning: bool = False


# --------------------------------------------------------------------------- #
# git access
# --------------------------------------------------------------------------- #


def _run_git(repo_root: Path, args: Sequence[str]) -> tuple[int, bytes]:
    """Run one git command inside ``repo_root``.

    ``-C`` is used rather than a working directory so that the result does not
    depend on where the process was started from.

    Args:
        repo_root: Directory to run git in.
        args: Arguments after the ``git`` executable.

    Returns:
        A tuple of (exit status, captured stdout). A missing, unusable or hung
        git is reported as a non-zero status with empty output, because every
        caller treats "git could not answer" the same way as "git said no".
    """
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo_root), *args],
            capture_output=True,
            check=False,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return 1, b""
    return completed.returncode, completed.stdout


def _decode(data: bytes) -> str | None:
    """Decode git output as UTF-8, or return ``None`` when it is not text."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _commit_exists(repo_root: Path, ref: str) -> bool:
    """Report whether ``ref`` resolves to a commit in ``repo_root``."""
    status, _ = _run_git(repo_root, ["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"])
    return status == 0


def find_latest_tag(repo_root: Path) -> str | None:
    """Return the most recent tag reachable from ``HEAD``.

    Args:
        repo_root: Directory expected to be the root of a git checkout.

    Returns:
        The tag name, or ``None`` when no comparison baseline can be determined
        (``repo_root`` is not the root of a checkout, no tag is reachable, or git
        is unavailable).
    """
    status, stdout = _run_git(repo_root, ["rev-parse", "--show-toplevel"])
    if status != 0:
        return None
    toplevel = _decode(stdout)
    if toplevel is None:
        return None
    # A directory that merely sits inside some *other* checkout (a temporary
    # directory under a workspace, say) must not be measured against that
    # checkout's tags, so the root git reports has to be the directory asked about.
    try:
        if Path(toplevel.strip()).resolve() != repo_root.resolve():
            return None
    except OSError:
        return None

    # Releases here carry annotated tags, which ``describe`` would find anyway;
    # ``--tags`` widens it to lightweight ones so a hand-made tag still serves as
    # a baseline. ``--abbrev=0`` prints the bare tag name instead of a
    # <tag>-<n>-g<sha> description.
    status, stdout = _run_git(repo_root, ["describe", "--tags", "--abbrev=0"])
    if status != 0:
        return None
    tag = (_decode(stdout) or "").strip()
    return tag or None


def changed_paths(repo_root: Path, ref: str) -> list[str]:
    """List the files that differ between ``ref`` and the committed ``HEAD``.

    Args:
        repo_root: Root of the git checkout.
        ref: Baseline revision, normally a release tag.

    Returns:
        Repository-relative paths with ``/`` separators. Empty when nothing
        changed and also when git cannot answer the question.
    """
    # ``core.quotepath=off`` keeps non-ASCII paths readable and, more
    # importantly, unescaped, so that prefix matching on them is reliable.
    status, stdout = _run_git(
        repo_root, ["-c", "core.quotepath=off", "diff", "--name-only", ref, HEAD_REF]
    )
    if status != 0:
        return []
    text = _decode(stdout)
    if text is None:
        return []
    return [line for line in text.splitlines() if line]


def read_blob(repo_root: Path, ref: str, rel_path: str) -> str | None:
    """Read one file as it exists at ``ref``.

    Args:
        repo_root: Root of the git checkout.
        ref: Revision to read from.
        rel_path: Repository-relative path with ``/`` separators.

    Returns:
        The file contents, or ``None`` when the path does not exist at ``ref``,
        is not decodable as UTF-8 text, or git cannot be run.
    """
    # In ``<rev>:<path>`` the path is taken relative to the top of the tree as
    # long as it does not start with ``./``, which is exactly what the callers
    # pass in.
    status, stdout = _run_git(repo_root, ["show", f"{ref}:{rel_path}"])
    if status != 0:
        return None
    return _decode(stdout)


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #


def declared_version(skill_text: str) -> str | None:
    """Extract ``metadata.version`` from the full text of a ``SKILL.md``.

    Args:
        skill_text: Complete file contents, typically read out of git.

    Returns:
        The declared version, or ``None`` when the frontmatter is missing, is not
        valid YAML, or does not carry ``metadata.version`` as a string.
    """
    split = validate.split_frontmatter(skill_text)
    if split is None:
        return None
    frontmatter_lines, _body_lines, _body_start_line = split
    try:
        # ``yaml.safe_load`` (never ``yaml.load``) avoids constructing arbitrary
        # Python objects from skill content.
        frontmatter = yaml.safe_load("\n".join(frontmatter_lines))
    except yaml.YAMLError:
        return None
    if not isinstance(frontmatter, dict):
        return None
    metadata = frontmatter.get("metadata")
    if not isinstance(metadata, dict):
        return None
    version = metadata.get("version")
    return version if isinstance(version, str) else None


def _summarise(paths: Sequence[str], limit: int = 3) -> str:
    """Render a changed-file list short enough to sit on one report line."""
    shown = ", ".join(paths[:limit])
    remaining = len(paths) - limit
    return f"{shown}, and {remaining} more" if remaining > 0 else shown


def _check_skill(repo_root: Path, ref: str, name: str, changed: Sequence[str]) -> list[Finding]:
    """Compare one changed skill's ``metadata.version`` between ``ref`` and ``HEAD``."""
    subject = f"{validate.SKILLS_DIRNAME}/{name}"
    rel_path = f"{subject}/{validate.SKILL_FILENAME}"

    baseline_text = read_blob(repo_root, ref, rel_path)
    if baseline_text is None:
        # The skill did not exist at ``ref``: it is new, so there is no previous
        # version it could have failed to move past.
        return []
    head_text = read_blob(repo_root, HEAD_REF, rel_path)
    if head_text is None:
        # The skill was removed; nothing about it ships any more.
        return []

    baseline_version = declared_version(baseline_text)
    head_version = declared_version(head_text)
    if baseline_version is None or head_version is None:
        unreadable = [
            revision
            for revision, version in ((ref, baseline_version), (HEAD_REF, head_version))
            if version is None
        ]
        return [
            Finding(
                subject,
                f"metadata.version cannot be read at {' and '.join(unreadable)}, so the bump "
                f"could not be checked. validate.py reports the malformed frontmatter itself.",
                is_warning=True,
            )
        ]

    if baseline_version != head_version:
        return []
    return [
        Finding(
            subject,
            f"{len(changed)} file(s) changed since {ref} ({_summarise(changed)}) but "
            f'metadata.version is still "{head_version}". Bump it in {rel_path}.',
        )
    ]


def _check_project_version(repo_root: Path, ref: str, changed: Sequence[str]) -> list[Finding]:
    """Compare the top-level ``VERSION`` between ``ref`` and ``HEAD``."""
    version_file = manifests.VERSION_FILENAME
    baseline_text = read_blob(repo_root, ref, version_file)
    if baseline_text is None:
        # No project version existed at ``ref`` (true of the first release), so
        # there is nothing to compare against.
        return []
    head_text = read_blob(repo_root, HEAD_REF, version_file)
    if head_text is None:
        # The file was deleted; validate.py and manifests.py report that.
        return []

    # Compared stripped: re-writing the same number with different surrounding
    # whitespace is not a release.
    baseline_version = baseline_text.strip()
    if baseline_version != head_text.strip():
        return []
    return [
        Finding(
            version_file,
            f"{len(changed)} file(s) under {validate.SKILLS_DIRNAME}/ changed since {ref} "
            f"({_summarise(changed)}) but {version_file} is still {baseline_version!r}. "
            f"Plugin users only receive an update when the project version rises.",
        )
    ]


def check_version_bumps(repo_root: Path, ref: str) -> list[Finding]:
    """Report every skill (and the project version) that changed without a bump.

    Args:
        repo_root: Root of the git checkout.
        ref: Baseline revision, normally the most recent release tag.

    Returns:
        Findings in a stable order: per-skill findings by skill name, then the
        project-level ``VERSION`` finding. Empty when nothing is wrong.
    """
    skills_prefix = f"{validate.SKILLS_DIRNAME}/"
    changed_under_skills = [
        path for path in changed_paths(repo_root, ref) if path.startswith(skills_prefix)
    ]
    if not changed_under_skills:
        return []

    changed_by_skill: dict[str, list[str]] = {}
    for path in changed_under_skills:
        parts = path.split("/")
        # Only ``skills/<name>/<file>`` belongs to a skill; anything shallower is
        # a loose file such as the generated ``skills/index.json``, which still
        # counts as a change under ``skills/`` for the project-version rule.
        if len(parts) >= 3:
            changed_by_skill.setdefault(parts[1], []).append(path)

    findings: list[Finding] = []
    for name in sorted(changed_by_skill):
        findings.extend(_check_skill(repo_root, ref, name, changed_by_skill[name]))
    findings.extend(_check_project_version(repo_root, ref, changed_under_skills))
    return findings


def format_finding(finding: Finding) -> str:
    """Render a finding as ``subject: level: message``."""
    level = "warning" if finding.is_warning else "error"
    return f"{finding.subject}: {level}: {finding.message}"


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point.

    Args:
        argv: Command-line arguments, defaulting to ``sys.argv[1:]``.

    Returns:
        0 when no omission was found or the check had to be skipped, 1 otherwise.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ref",
        default=None,
        help="Baseline revision to compare against. Defaults to the most recent tag.",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=DEFAULT_REPO_ROOT,
        help="Checkout to inspect. Defaults to the repository this script belongs to.",
    )
    args = parser.parse_args(argv)

    repo_root: Path = args.repo_root
    ref = args.ref or find_latest_tag(repo_root)
    if ref is None:
        print(
            "version_guard: no release tag is reachable (not a git checkout, no tags fetched, "
            "or no git executable); skipping the version-bump check."
        )
        return 0
    if not _commit_exists(repo_root, ref):
        print(
            f"version_guard: reference {ref!r} does not resolve in this checkout; "
            "skipping the version-bump check."
        )
        return 0

    findings = check_version_bumps(repo_root, ref)
    for finding in findings:
        print(format_finding(finding), file=sys.stderr)

    if any(not finding.is_warning for finding in findings):
        return 1

    print(f"version_guard: every change under {validate.SKILLS_DIRNAME}/ since {ref} is versioned.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
