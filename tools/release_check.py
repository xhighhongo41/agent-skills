#!/usr/bin/env python3
"""Gather the evidence needed before releasing a version, without changing anything.

Run from the repository root (``tools/release-check.sh`` does that for you)::

    python tools/release_check.py 3.1.0
    python tools/release_check.py 3.1.0 --fetch --base main

The report is a table of ``Section | Result | Detail`` rows followed by a
one-line summary:

* ``G0 evidence`` (always INFO): the release branch ``vX.Y`` and tag ``vX.Y.Z``,
  the latest release tag, and whether the release branch is merged into the base
  branch.
* ``Account``: whether the active ``gh`` account owns the ``origin`` repository.
* ``G1 versions``: ``VERSION``, the marketplace entry, the skill versions in
  ``skills/index.json`` and the changelog rows of both READMEs.
* ``G2 manifests``: whether the committed manifests match the generated ones.
* ``G4 checks``: the result of ``bash tools/check.sh``.
* ``G5 clean``: an empty ``git status`` and a branch level with its upstream.

The script never writes to the repository or to the remote, and never switches
the ``gh`` account. The only command that changes anything is
``git fetch --tags origin``, and it runs only when ``--fetch`` is given.

The exit code is 1 when any row is FAIL, 2 when the arguments are invalid, and 0
otherwise. A row that could not be verified is reported as WARN (or INFO in
``G0``) with the word "unverified", never as FAIL.
"""

from __future__ import annotations

import argparse
import dataclasses
import enum
import json
import os
import re
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import manifests
import validate
import version_guard

# --------------------------------------------------------------------------- #
# Conventions
# --------------------------------------------------------------------------- #

#: Checkout inspected when no other root is passed to :func:`main`: the
#: repository this script is part of.
DEFAULT_REPO_ROOT = Path(__file__).resolve().parent.parent

#: Version accepted on the command line. Pre-release suffixes are not used here.
VERSION_ARGUMENT_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")

#: Tags that count as releases when looking for the latest one.
RELEASE_TAG_PATTERN = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")

#: Base branch names accepted by ``--base``. Deliberately narrower than what git
#: allows, and never starting with ``-`` so that it cannot be read as an option.
BRANCH_NAME_PATTERN = re.compile(r"^[A-Za-z0-9._][A-Za-z0-9._/-]*$")

#: Remote whose refs and owner are inspected.
REMOTE_NAME = "origin"

#: Base branch used when ``--base`` is not given.
DEFAULT_BASE_BRANCH = "main"

#: Upper bound for one local git or gh call.
COMMAND_TIMEOUT_SECONDS = 30.0

#: Upper bound for ``git fetch``, which talks to the remote.
FETCH_TIMEOUT_SECONDS = 120.0

#: Default upper bound for ``bash tools/check.sh``.
DEFAULT_CHECK_TIMEOUT_SECONDS = 900.0

#: The full verification suite run by ``G4``, relative to the repository root.
CHECK_COMMAND = ("bash", "tools/check.sh")

#: How many trailing output lines of a failed ``tools/check.sh`` are reported.
CHECK_OUTPUT_TAIL_LINES = 20

#: How many paths of a dirty working tree are listed before the rest is counted.
MAX_LISTED_PATHS = 20

#: Returned by :func:`run` when the command did not finish in time. Real exit
#: codes are 0-255 on POSIX and signals are reported as -1..-64, so this value
#: cannot be confused with either.
TIMEOUT_RETURNCODE = -1001

#: Returned by :func:`run` when the executable could not be started at all.
NOT_FOUND_RETURNCODE = -1002

#: READMEs whose changelog table must carry a row for the released version.
README_FILENAMES = ("README.md", "README_ja.md")

#: Line in ``gh auth status`` that marks the account above it as the active one.
ACTIVE_ACCOUNT_MARKER = "Active account: true"

#: Account line in ``gh auth status``, e.g. "Logged in to github.com account alice".
GH_ACCOUNT_LINE_PATTERN = re.compile(r"Logged in to \S+ account (\S+)")

#: GitHub user and organisation names: alphanumerics and single inner hyphens.
#: Underscores and dots are tolerated because some hosts allow them.
OWNER_PATTERN = r"[A-Za-z0-9][A-Za-z0-9._-]*"

#: Remote URL forms the owner is read from. Each captures the owner as group 1.
OWNER_URL_PATTERNS = (
    # https://github.com/OWNER/REPO(.git), optionally with user info.
    re.compile(rf"^https?://(?:[^@/\s]+@)?[^/\s]+/({OWNER_PATTERN})/[^/\s]+?/?$"),
    # ssh://git@host(:port)/OWNER/REPO(.git)
    re.compile(rf"^ssh://(?:[^@/\s]+@)?[^/\s]+/({OWNER_PATTERN})/[^/\s]+?/?$"),
    # git@host:OWNER/REPO(.git), where host may be an SSH alias.
    re.compile(rf"^[^@/:\s]+@[^/:\s]+:({OWNER_PATTERN})/[^/\s]+?$"),
)

#: Section labels, in report order.
SECTION_EVIDENCE = "G0 evidence"
SECTION_ACCOUNT = "Account"
SECTION_VERSIONS = "G1 versions"
SECTION_MANIFESTS = "G2 manifests"
SECTION_CHECKS = "G4 checks"
SECTION_CLEAN = "G5 clean"


# --------------------------------------------------------------------------- #
# Data structures
# --------------------------------------------------------------------------- #


class Status(enum.StrEnum):
    """Outcome of one report row."""

    PASS = "PASS"
    FAIL = "FAIL"
    WARN = "WARN"
    INFO = "INFO"


@dataclasses.dataclass(frozen=True)
class Result:
    """One row of the report.

    Attributes:
        section: Section label, one of the ``SECTION_*`` constants.
        status: Outcome of the row.
        detail: Human-readable explanation. May span several lines.
    """

    section: str
    status: Status
    detail: str


#: Executes one external command: ``(args, cwd, timeout) -> (returncode, stdout,
#: stderr)``. Every check takes one so that tests can substitute it.
Runner = Callable[[Sequence[str], Path, float], tuple[int, str, str]]


# --------------------------------------------------------------------------- #
# Running external commands
# --------------------------------------------------------------------------- #


def run(args: Sequence[str], cwd: Path, timeout: float) -> tuple[int, str, str]:
    """Run one external command and capture its output.

    This is the only place where the script starts a process.

    Args:
        args: Executable and arguments. Never passed through a shell.
        cwd: Working directory for the command.
        timeout: Seconds to wait before the command is killed.

    Returns:
        A tuple of (exit status, stdout, stderr). The status is
        :data:`TIMEOUT_RETURNCODE` when the command did not finish in time and
        :data:`NOT_FOUND_RETURNCODE` when it could not be started.
    """
    # Prompts would hang the check until the timeout, so both tools are told
    # to fail instead of asking.
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GH_PROMPT_DISABLED": "1"}
    try:
        completed = subprocess.run(
            list(args),
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return TIMEOUT_RETURNCODE, "", ""
    except OSError as exc:
        return NOT_FOUND_RETURNCODE, "", str(exc)
    return completed.returncode, completed.stdout, completed.stderr


def _git(
    runner: Runner,
    repo_root: Path,
    *args: str,
    timeout: float = COMMAND_TIMEOUT_SECONDS,
) -> tuple[int, str, str]:
    """Run a git command in ``repo_root`` through ``runner``.

    ``--no-optional-locks`` stops ``git status`` from refreshing the index on
    disk, which would otherwise be a write.
    """
    return runner(["git", "--no-optional-locks", *args], repo_root, timeout)


def _resolve_commit(runner: Runner, repo_root: Path, ref: str) -> str | None:
    """Return the commit id ``ref`` points at, or ``None`` when it does not exist."""
    status, stdout, _ = _git(
        runner, repo_root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"
    )
    if status != 0:
        return None
    return stdout.strip() or None


# --------------------------------------------------------------------------- #
# Parsing helpers
# --------------------------------------------------------------------------- #


def release_names(version: str) -> tuple[str, str]:
    """Return the release branch and tag names for ``version``.

    Args:
        version: A version of the form ``X.Y.Z``.

    Returns:
        A tuple of (branch ``vX.Y``, tag ``vX.Y.Z``).
    """
    major, minor, _patch = version.split(".")
    return f"v{major}.{minor}", f"v{version}"


def parse_owner(url: str) -> str | None:
    """Extract the repository owner from a GitHub-style remote URL.

    Args:
        url: Output of ``git remote get-url``. Surrounding whitespace is ignored.

    Returns:
        The owner, or ``None`` when the URL has none of the supported forms
        (HTTPS, ``ssh://``, or ``user@host:OWNER/REPO`` with any host alias).
    """
    text = url.strip()
    for pattern in OWNER_URL_PATTERNS:
        match = pattern.match(text)
        if match:
            return match.group(1)
    return None


def parse_active_account(text: str) -> str | None:
    """Find the active account in the output of ``gh auth status``.

    Args:
        text: Combined stdout and stderr of ``gh auth status``.

    Returns:
        The account named on the last account line before the first
        "Active account: true" line, or ``None`` when there is no such pair.
    """
    account: str | None = None
    for line in text.splitlines():
        match = GH_ACCOUNT_LINE_PATTERN.search(line)
        if match:
            account = match.group(1)
        elif ACTIVE_ACCOUNT_MARKER in line and account is not None:
            return account
    return None


def _version_type(value: str) -> str:
    """argparse type for the version argument."""
    if not VERSION_ARGUMENT_PATTERN.match(value):
        raise argparse.ArgumentTypeError(f"{value!r} is not a version of the form X.Y.Z")
    return value


def _branch_type(value: str) -> str:
    """argparse type for ``--base``."""
    if not BRANCH_NAME_PATTERN.match(value) or ".." in value:
        raise argparse.ArgumentTypeError(f"{value!r} is not an accepted branch name")
    return value


def _positive_seconds(value: str) -> float:
    """argparse type for ``--check-timeout``."""
    try:
        seconds = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not a number of seconds") from None
    # ``not seconds > 0`` also rejects NaN.
    if not seconds > 0 or seconds == float("inf"):
        raise argparse.ArgumentTypeError(f"{value!r} must be a positive number of seconds")
    return seconds


# --------------------------------------------------------------------------- #
# G0 evidence
# --------------------------------------------------------------------------- #


def _latest_release_tag(runner: Runner, repo_root: Path) -> str | None:
    """Return the highest ``vX.Y.Z`` tag, compared numerically."""
    status, stdout, _ = _git(
        runner, repo_root, "for-each-ref", "--format=%(refname:strip=2)", "refs/tags/v*"
    )
    if status != 0:
        return None
    versions: list[tuple[tuple[int, int, int], str]] = []
    for line in stdout.splitlines():
        match = RELEASE_TAG_PATTERN.match(line.strip())
        if match:
            major, minor, patch = (int(part) for part in match.groups())
            versions.append(((major, minor, patch), line.strip()))
    if not versions:
        return None
    return max(versions)[1]


def _merge_state(
    runner: Runner, repo_root: Path, branch: str, work_ref: str, base_ref: str, base_label: str
) -> str:
    """Describe whether ``work_ref`` has been merged into ``base_ref``.

    A branch counts as merged only when it has commits of its own and its tip is
    an ancestor of the base. "Commits of its own" are the commits after the point
    where the branch left the base's first-parent history: a tip that is itself
    on that history (a branch created from the base and never committed to) has
    none, even though it is trivially an ancestor of the base. A fast-forward
    merge is indistinguishable from that case and is reported the same way.
    """
    status, _, _ = _git(runner, repo_root, "merge-base", "--is-ancestor", work_ref, base_ref)
    if status == 1:
        return f"{branch} is not merged into {base_label}"
    if status != 0:
        return f"merge state of {branch}: unverified (git merge-base failed)"

    work_commit = _resolve_commit(runner, repo_root, work_ref)
    status, stdout, _ = _git(runner, repo_root, "rev-list", "--first-parent", base_ref)
    if work_commit is None or status != 0:
        return f"merge state of {branch}: unverified (git rev-list failed)"
    if work_commit in stdout.split():
        return f"{branch} is not merged into {base_label} (no commits of its own)"
    return f"{branch} is merged into {base_label}"


def check_evidence(
    repo_root: Path,
    version: str,
    *,
    base: str = DEFAULT_BASE_BRANCH,
    fetch: bool = False,
    runner: Runner = run,
) -> list[Result]:
    """Collect the release branch, tag and merge facts (G0, all INFO).

    Args:
        repo_root: Root of the git checkout.
        version: Version being released, ``X.Y.Z``.
        base: Base branch the release branch is merged into.
        fetch: Whether to run ``git fetch --tags origin`` first.
        runner: Executes external commands.

    Returns:
        INFO rows, starting with how fresh the refs are.
    """
    branch, tag = release_names(version)
    results: list[Result] = []

    def info(detail: str) -> None:
        results.append(Result(SECTION_EVIDENCE, Status.INFO, detail))

    if fetch:
        status, _, stderr = _git(
            runner, repo_root, "fetch", "--tags", REMOTE_NAME, timeout=FETCH_TIMEOUT_SECONDS
        )
        if status == 0:
            info(f"fetched branches and tags from {REMOTE_NAME}")
        else:
            reason = "timed out" if status == TIMEOUT_RETURNCODE else _last_line(stderr)
            info(f"fetch failed ({reason}); judged from local refs")
    else:
        info("judged from local refs; pass --fetch to refresh")

    local_ref = f"refs/heads/{branch}"
    remote_ref = f"refs/remotes/{REMOTE_NAME}/{branch}"
    has_local = _resolve_commit(runner, repo_root, local_ref) is not None
    has_remote = _resolve_commit(runner, repo_root, remote_ref) is not None
    info(f"local branch {branch}: {'exists' if has_local else 'missing'}")
    info(f"remote-tracking branch {REMOTE_NAME}/{branch}: {'exists' if has_remote else 'missing'}")

    has_tag = _resolve_commit(runner, repo_root, f"refs/tags/{tag}") is not None
    info(f"tag {tag}: {'exists' if has_tag else 'missing'}")
    info(f"latest release tag: {_latest_release_tag(runner, repo_root) or 'none'}")

    # The remote's view of the base wins: a merge that only exists locally has
    # not been released.
    remote_base_ref = f"refs/remotes/{REMOTE_NAME}/{base}"
    local_base_ref = f"refs/heads/{base}"
    if _resolve_commit(runner, repo_root, remote_base_ref) is not None:
        base_ref, base_label = remote_base_ref, f"{REMOTE_NAME}/{base}"
    elif _resolve_commit(runner, repo_root, local_base_ref) is not None:
        base_ref, base_label = local_base_ref, base
    else:
        base_ref, base_label = None, base

    # The local branch is the one being worked on; the remote-tracking one is
    # used when the branch was never checked out here.
    work_ref = local_ref if has_local else remote_ref if has_remote else None
    if work_ref is None:
        info(f"merge state of {branch}: unverified (branch not found)")
    elif base_ref is None:
        info(f"merge state of {branch}: unverified (base branch {base} not found)")
    else:
        info(_merge_state(runner, repo_root, branch, work_ref, base_ref, base_label))
    return results


def _last_line(text: str) -> str:
    """Return the last non-empty line of ``text``, or a placeholder."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else "no message"


# --------------------------------------------------------------------------- #
# Account
# --------------------------------------------------------------------------- #


def _active_gh_account(runner: Runner, repo_root: Path) -> tuple[str | None, str]:
    """Return the active gh login, or ``None`` with the reason it is unknown."""
    status, stdout, _ = runner(
        ["gh", "api", "user", "--jq", ".login"], repo_root, COMMAND_TIMEOUT_SECONDS
    )
    login = stdout.strip()
    if status == 0 and re.fullmatch(OWNER_PATTERN, login):
        return login, ""
    if status == NOT_FOUND_RETURNCODE:
        return None, "gh is not installed"

    # ``gh api`` also fails without network access; ``gh auth status`` still
    # names the account from the local configuration. Older gh versions print
    # it on stderr, newer ones on stdout.
    status, stdout, stderr = runner(["gh", "auth", "status"], repo_root, COMMAND_TIMEOUT_SECONDS)
    if status == NOT_FOUND_RETURNCODE:
        return None, "gh is not installed"
    account = parse_active_account(stdout + "\n" + stderr)
    if account is None:
        return None, "no active gh account found (not logged in?)"
    return account, ""


def check_account(repo_root: Path, *, runner: Runner = run) -> list[Result]:
    """Compare the owner of ``origin`` with the active gh account.

    The account is never switched; a mismatch is only reported.

    Args:
        repo_root: Root of the git checkout.
        runner: Executes external commands.

    Returns:
        One row: PASS on a match, WARN on a mismatch, WARN marked "unverified"
        when either side cannot be determined.
    """

    def unverified(reason: str) -> list[Result]:
        return [Result(SECTION_ACCOUNT, Status.WARN, f"unverified: {reason}")]

    status, stdout, _ = _git(runner, repo_root, "remote", "get-url", REMOTE_NAME)
    if status != 0:
        return unverified(f"no {REMOTE_NAME} remote")
    owner = parse_owner(stdout)
    if owner is None:
        # The URL itself is not echoed: it may carry credentials.
        return unverified(f"cannot read the owner from the {REMOTE_NAME} URL")

    login, reason = _active_gh_account(runner, repo_root)
    if login is None:
        return unverified(reason)
    # GitHub treats user names case-insensitively.
    if login.casefold() == owner.casefold():
        return [
            Result(
                SECTION_ACCOUNT,
                Status.PASS,
                f"{REMOTE_NAME} owner {owner} matches the active gh account",
            )
        ]
    return [
        Result(
            SECTION_ACCOUNT,
            Status.WARN,
            f"{REMOTE_NAME} owner is {owner} but the active gh account is {login}; "
            "switch only with the user's approval",
        )
    ]


# --------------------------------------------------------------------------- #
# G1 version declarations
# --------------------------------------------------------------------------- #


def _load_json(path: Path) -> Any:
    """Read a JSON file, raising ``ValueError`` with a short reason on failure."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError("file is missing") from None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot be read as JSON ({exc})") from None


def _check_version_file(repo_root: Path, version: str) -> Result:
    """Compare ``VERSION`` with the released version."""
    label = manifests.VERSION_FILENAME
    try:
        declared = manifests.read_project_version(repo_root)
    except manifests.ManifestError as exc:
        return Result(SECTION_VERSIONS, Status.FAIL, f"{label}: {exc}")
    except (OSError, UnicodeDecodeError) as exc:
        return Result(SECTION_VERSIONS, Status.FAIL, f"{label}: cannot be read ({exc})")
    if declared != version:
        return Result(SECTION_VERSIONS, Status.FAIL, f"{label} is {declared}, expected {version}")
    return Result(SECTION_VERSIONS, Status.PASS, f"{label} is {version}")


def _check_marketplace(repo_root: Path, version: str) -> Result:
    """Compare the plugin version in the marketplace manifest with ``version``."""
    label = manifests.MARKETPLACE_RELPATH.as_posix()

    def fail(reason: str) -> Result:
        return Result(SECTION_VERSIONS, Status.FAIL, f"{label}: {reason}")

    try:
        document = _load_json(repo_root / manifests.MARKETPLACE_RELPATH)
    except ValueError as exc:
        return fail(str(exc))
    plugins = document.get("plugins") if isinstance(document, dict) else None
    if not isinstance(plugins, list):
        return fail("has no 'plugins' list")
    entries = [
        entry
        for entry in plugins
        if isinstance(entry, dict) and entry.get("name") == manifests.PLUGIN_NAME
    ]
    if len(entries) != 1:
        return fail(f"expected one plugin named {manifests.PLUGIN_NAME!r}, found {len(entries)}")
    declared = entries[0].get("version")
    if declared != version:
        return fail(f"plugin version is {declared}, expected {version}")
    return Result(SECTION_VERSIONS, Status.PASS, f"{label} plugin version is {version}")


def _check_index(repo_root: Path) -> Result:
    """Compare every skill version in ``skills/index.json`` with its ``SKILL.md``."""
    label = manifests.INDEX_RELPATH.as_posix()
    try:
        document = _load_json(repo_root / manifests.INDEX_RELPATH)
    except ValueError as exc:
        return Result(SECTION_VERSIONS, Status.FAIL, f"{label}: {exc}")
    skills = document.get("skills") if isinstance(document, dict) else None
    if not isinstance(skills, list):
        return Result(SECTION_VERSIONS, Status.FAIL, f"{label}: has no 'skills' list")

    problems: list[str] = []
    for entry in skills:
        name = entry.get("name") if isinstance(entry, dict) else None
        # The name becomes part of a path, so anything but a plain skill name is
        # rejected before it is used.
        if not isinstance(name, str) or not validate.NAME_PATTERN.match(name):
            problems.append(f"invalid entry name {name!r}")
            continue
        indexed = entry.get("version")
        skill_path = repo_root / validate.SKILLS_DIRNAME / name / validate.SKILL_FILENAME
        try:
            declared = version_guard.declared_version(skill_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            problems.append(f"{name}: {validate.SKILL_FILENAME} cannot be read")
            continue
        if declared != indexed:
            problems.append(f"{name}: index {indexed}, {validate.SKILL_FILENAME} {declared}")

    if problems:
        detail = f"{label} disagrees with the skills:\n" + "\n".join(problems)
        return Result(SECTION_VERSIONS, Status.FAIL, detail)
    return Result(
        SECTION_VERSIONS,
        Status.PASS,
        f"{label} matches every {validate.SKILL_FILENAME} ({len(skills)} skills)",
    )


def _check_changelog(repo_root: Path, filename: str, version: str) -> Result:
    """Look for the changelog row of ``version`` in one README."""
    row_prefix = f"| **{version}** |"
    try:
        lines = (repo_root / filename).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return Result(SECTION_VERSIONS, Status.FAIL, f"{filename} is missing")
    except (OSError, UnicodeDecodeError) as exc:
        return Result(SECTION_VERSIONS, Status.FAIL, f"{filename} cannot be read ({exc})")
    if any(line.startswith(row_prefix) for line in lines):
        return Result(
            SECTION_VERSIONS, Status.PASS, f"{filename} has a changelog row for {version}"
        )
    return Result(
        SECTION_VERSIONS,
        Status.FAIL,
        f"{filename} has no changelog row starting with {row_prefix}",
    )


def check_version_declarations(repo_root: Path, version: str) -> list[Result]:
    """Check that every place declaring the version agrees (G1).

    Args:
        repo_root: Repository root.
        version: Version being released, ``X.Y.Z``.

    Returns:
        One row each for ``VERSION``, the marketplace manifest, the skill index
        and each README, in that order. Each detail starts with the file name.
    """
    return [
        _check_version_file(repo_root, version),
        _check_marketplace(repo_root, version),
        _check_index(repo_root),
        *(_check_changelog(repo_root, filename, version) for filename in README_FILENAMES),
    ]


# --------------------------------------------------------------------------- #
# G2 manifests
# --------------------------------------------------------------------------- #


def check_generated_manifests(repo_root: Path) -> list[Result]:
    """Check that the committed manifests are what the generator produces (G2).

    Args:
        repo_root: Repository root.

    Returns:
        One row: PASS, or FAIL listing each manifest that differs.
    """
    try:
        mismatches = manifests.check_manifests(repo_root)
    except manifests.ManifestError as exc:
        return [Result(SECTION_MANIFESTS, Status.FAIL, f"cannot generate the manifests: {exc}")]
    if not mismatches:
        return [Result(SECTION_MANIFESTS, Status.PASS, "manifests match the generated ones")]
    lines = [f"{mismatch.rel_path}: {mismatch.message}" for mismatch in mismatches]
    detail = "manifests differ (run python tools/manifests.py --write):\n" + "\n".join(lines)
    return [Result(SECTION_MANIFESTS, Status.FAIL, detail)]


# --------------------------------------------------------------------------- #
# G4 checks
# --------------------------------------------------------------------------- #


def check_suite(
    repo_root: Path,
    *,
    timeout: float = DEFAULT_CHECK_TIMEOUT_SECONDS,
    skip: bool = False,
    runner: Runner = run,
) -> list[Result]:
    """Run ``bash tools/check.sh`` and report its outcome (G4).

    Args:
        repo_root: Repository root, used as the working directory.
        timeout: Seconds to wait for the suite.
        skip: Report the step as skipped instead of running it.
        runner: Executes external commands.

    Returns:
        One row: PASS, FAIL with the tail of the output, WARN marked "unverified"
        when the suite did not finish or could not start, or INFO when skipped.
    """
    if skip:
        return [Result(SECTION_CHECKS, Status.INFO, "skipped (--skip-checks)")]
    status, stdout, stderr = runner(list(CHECK_COMMAND), repo_root, timeout)
    if status == 0:
        return [Result(SECTION_CHECKS, Status.PASS, "tools/check.sh passed")]
    if status == TIMEOUT_RETURNCODE:
        return [
            Result(
                SECTION_CHECKS,
                Status.WARN,
                f"unverified: tools/check.sh did not finish within {timeout:g} s; "
                "run tools/check.sh separately",
            )
        ]
    if status == NOT_FOUND_RETURNCODE:
        return [
            Result(
                SECTION_CHECKS,
                Status.WARN,
                "unverified: bash could not be started; run tools/check.sh separately",
            )
        ]
    # stdout and stderr are captured apart, so their interleaving is lost; the
    # errors usually come last, which is why stderr is appended after stdout.
    output_lines = [line for line in (stdout + "\n" + stderr).splitlines() if line.strip()]
    tail = output_lines[-CHECK_OUTPUT_TAIL_LINES:]
    detail = f"tools/check.sh exited with {status}; last lines:\n" + "\n".join(tail)
    return [Result(SECTION_CHECKS, Status.FAIL, detail)]


# --------------------------------------------------------------------------- #
# G5 clean
# --------------------------------------------------------------------------- #


def _check_working_tree(runner: Runner, repo_root: Path) -> Result:
    """Report uncommitted and untracked files."""
    status, stdout, _ = _git(runner, repo_root, "-c", "core.quotepath=off", "status", "--porcelain")
    if status != 0:
        return Result(SECTION_CLEAN, Status.WARN, "working tree: unverified (git status failed)")
    entries = [line for line in stdout.splitlines() if line.strip()]
    if not entries:
        return Result(SECTION_CLEAN, Status.PASS, "working tree is clean")
    listed = entries[:MAX_LISTED_PATHS]
    if len(entries) > MAX_LISTED_PATHS:
        listed.append(f"... and {len(entries) - MAX_LISTED_PATHS} more")
    detail = "working tree has uncommitted changes:\n" + "\n".join(listed)
    return Result(SECTION_CLEAN, Status.FAIL, detail)


def _check_upstream(runner: Runner, repo_root: Path) -> Result:
    """Report whether the current branch is level with its upstream."""
    status, stdout, _ = _git(runner, repo_root, "symbolic-ref", "--quiet", "--short", "HEAD")
    branch = stdout.strip()
    if status != 0 or not branch:
        return Result(SECTION_CLEAN, Status.FAIL, "HEAD is detached; no branch to compare")

    status, stdout, _ = _git(
        runner, repo_root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"
    )
    upstream = stdout.strip()
    if status != 0 or not upstream:
        return Result(SECTION_CLEAN, Status.FAIL, f"{branch} has no upstream")

    status, stdout, _ = _git(
        runner, repo_root, "rev-list", "--left-right", "--count", "HEAD...@{u}"
    )
    counts = stdout.split()
    if status != 0 or len(counts) != 2 or not all(count.isdigit() for count in counts):
        return Result(
            SECTION_CLEAN, Status.WARN, f"{branch}: unverified (cannot compare with {upstream})"
        )
    ahead, behind = (int(count) for count in counts)
    if ahead and behind:
        detail = (
            f"{branch} has diverged from {upstream}: "
            f"{ahead} commit(s) ahead, {behind} commit(s) behind"
        )
    elif ahead:
        detail = f"{branch} is {ahead} commit(s) ahead of {upstream} (not pushed)"
    elif behind:
        detail = f"{branch} is {behind} commit(s) behind {upstream}"
    else:
        return Result(SECTION_CLEAN, Status.PASS, f"{branch} matches its upstream {upstream}")
    return Result(SECTION_CLEAN, Status.FAIL, detail)


def check_clean(repo_root: Path, *, runner: Runner = run) -> list[Result]:
    """Check the working tree and the current branch against its upstream (G5).

    The upstream is compared as recorded in the local remote-tracking ref; pass
    ``--fetch`` to :func:`main` to refresh it first.

    Args:
        repo_root: Root of the git checkout.
        runner: Executes external commands.

    Returns:
        Two rows: the working tree (detail starting with "working tree") and the
        upstream comparison (detail starting with the branch name).
    """
    return [_check_working_tree(runner, repo_root), _check_upstream(runner, repo_root)]


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #


def format_report(results: Sequence[Result]) -> str:
    """Render the results as a fixed-width table followed by a summary line.

    Args:
        results: Rows in report order.

    Returns:
        The report. Continuation lines of a multi-line detail are aligned with
        the detail column. The last line reads ``Summary: N FAIL, M WARN``.
    """
    headers = ("Section", "Result", "Detail")
    section_width = max([len(headers[0]), *(len(result.section) for result in results)])
    status_width = max([len(headers[1]), *(len(result.status) for result in results)])

    def row(section: str, status: str, detail: str) -> str:
        return f"{section:<{section_width}} | {status:<{status_width}} | {detail}".rstrip()

    lines = [
        row(*headers),
        f"{'-' * section_width}-+-{'-' * status_width}-+-{'-' * len(headers[2])}",
    ]
    for result in results:
        detail_lines = result.detail.splitlines() or [""]
        lines.append(row(result.section, result.status, detail_lines[0]))
        lines.extend(row("", "", continuation) for continuation in detail_lines[1:])

    failures = sum(result.status is Status.FAIL for result in results)
    warnings = sum(result.status is Status.WARN for result in results)
    lines.append("")
    lines.append(f"Summary: {failures} FAIL, {warnings} WARN")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def main(
    argv: Sequence[str] | None = None,
    *,
    runner: Runner = run,
    repo_root: Path = DEFAULT_REPO_ROOT,
) -> int:
    """Entry point.

    Args:
        argv: Command-line arguments, defaulting to ``sys.argv[1:]``.
        runner: Executes external commands; replaced in tests.
        repo_root: Checkout to inspect; replaced in tests.

    Returns:
        1 when any row is FAIL, 0 otherwise. Invalid arguments make argparse
        print the usage and exit with 2.
    """
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("version", type=_version_type, help="Version to release, as X.Y.Z.")
    parser.add_argument(
        "--fetch",
        action="store_true",
        help=f"Run 'git fetch --tags {REMOTE_NAME}' first (the only command that updates refs).",
    )
    parser.add_argument(
        "--base",
        type=_branch_type,
        default=DEFAULT_BASE_BRANCH,
        help=f"Base branch the release branch is merged into (default: {DEFAULT_BASE_BRANCH}).",
    )
    parser.add_argument(
        "--check-timeout",
        type=_positive_seconds,
        default=DEFAULT_CHECK_TIMEOUT_SECONDS,
        help=f"Seconds allowed for tools/check.sh (default: {DEFAULT_CHECK_TIMEOUT_SECONDS:g}).",
    )
    parser.add_argument("--skip-checks", action="store_true", help="Do not run tools/check.sh.")
    args = parser.parse_args(argv)

    evidence = check_evidence(
        repo_root, args.version, base=args.base, fetch=args.fetch, runner=runner
    )
    account = check_account(repo_root, runner=runner)
    versions = check_version_declarations(repo_root, args.version)
    generated = check_generated_manifests(repo_root)
    # The tree is inspected before tools/check.sh runs, so that anything the
    # suite leaves behind cannot be mistaken for an uncommitted change.
    clean = check_clean(repo_root, runner=runner)
    suite = check_suite(repo_root, timeout=args.check_timeout, skip=args.skip_checks, runner=runner)

    results = [*evidence, *account, *versions, *generated, *suite, *clean]
    print(format_report(results))
    return 1 if any(result.status is Status.FAIL for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
