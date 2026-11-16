"""Tests for ``tools/release_check.py``.

Written before the implementation (test-driven). The git-facing checks run
against synthetic repositories built under ``tmp_path``: a bare repository plays
``origin`` and a working clone pushes to it, so remote-tracking branches and
upstreams behave as they do in a real checkout without touching the network or
this repository's history. ``gh`` and ``bash tools/check.sh`` are never run for
real; a fake runner answers for them.

Every synthetic repository gets its git identity from its own local config, so
the tests neither depend on nor modify the global git configuration.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

import manifests
import pytest
import release_check
from release_check import (
    NOT_FOUND_RETURNCODE,
    SECTION_ACCOUNT,
    SECTION_CHECKS,
    SECTION_CLEAN,
    SECTION_EVIDENCE,
    SECTION_MANIFESTS,
    SECTION_VERSIONS,
    TIMEOUT_RETURNCODE,
    Result,
    Status,
    check_account,
    check_clean,
    check_evidence,
    check_generated_manifests,
    check_suite,
    check_version_declarations,
    format_report,
    main,
    parse_active_account,
    parse_owner,
    release_names,
    run,
)

VERSION = "1.1.0"
PREVIOUS_VERSION = "1.0.0"
BRANCH = "v1.1"
TAG = "v1.1.0"
SKILL_NAME = "sample-skill"

requires_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")

#: Runner signature used by the module: ``(args, cwd, timeout) -> (rc, stdout, stderr)``.
Runner = Callable[[Sequence[str], Path, float], tuple[int, str, str]]


# --------------------------------------------------------------------------- #
# Helpers: synthetic git repositories
# --------------------------------------------------------------------------- #


def _git(repo: Path, *args: str) -> str:
    """Run a git command inside ``repo`` and return its standard output."""
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return result.stdout


def _configure(repo: Path) -> None:
    """Give ``repo`` a local identity and disable signing for its commits and tags."""
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "config", "tag.gpgsign", "false")


def _commit(repo: Path, message: str, *, filename: str = "notes.txt") -> None:
    """Append a line to ``filename`` and record it as one commit."""
    path = repo / filename
    previous = path.read_text(encoding="utf-8") if path.exists() else ""
    path.write_text(previous + message + "\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "--no-verify", "--quiet", "-m", message)


def _push(repo: Path, *refs: str) -> None:
    """Push ``refs`` to ``origin`` without running hooks."""
    _git(repo, "push", "--no-verify", "--quiet", "origin", *refs)


def _make_repo(tmp_path: Path) -> Path:
    """Create a bare ``origin`` and a clone of it with one commit on ``main``.

    The ``main`` branch is pushed with an upstream, and ``v1.0.0`` is tagged on
    that first commit.

    Returns:
        Path of the working clone.
    """
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--quiet", "--bare", "-b", "main", str(origin)],
        check=True,
        timeout=60,
    )
    repo = tmp_path / "work"
    repo.mkdir()
    _git(repo, "init", "--quiet", "-b", "main")
    _configure(repo)
    _git(repo, "remote", "add", "origin", str(origin))
    _commit(repo, "initial")
    _git(repo, "tag", f"v{PREVIOUS_VERSION}")
    _push(repo, "-u", "main", "--tags")
    return repo


def _clone(tmp_path: Path, name: str) -> Path:
    """Clone the synthetic ``origin`` into a second working copy."""
    target = tmp_path / name
    subprocess.run(
        ["git", "clone", "--quiet", str(tmp_path / "origin.git"), str(target)],
        check=True,
        timeout=60,
    )
    _configure(target)
    return target


def _find(results: Sequence[Result], needle: str) -> Result:
    """Return the only result whose detail contains ``needle``."""
    matches = [result for result in results if needle in result.detail]
    assert len(matches) == 1, f"expected one result containing {needle!r}, got {results!r}"
    return matches[0]


def _starting(results: Sequence[Result], prefix: str) -> Result:
    """Return the only result whose detail starts with ``prefix``."""
    matches = [result for result in results if result.detail.startswith(prefix)]
    assert len(matches) == 1, f"expected one result starting with {prefix!r}, got {results!r}"
    return matches[0]


# --------------------------------------------------------------------------- #
# Helpers: release files
# --------------------------------------------------------------------------- #


def _skill_text(name: str, version: str) -> str:
    """Build a minimal ``SKILL.md`` declaring ``version``."""
    return (
        "---\n"
        f"name: {name}\n"
        'description: "A sample skill for the tests."\n'
        "license: MIT\n"
        "metadata:\n"
        f'  version: "{version}"\n'
        "---\n"
        "\n"
        f"# {name}\n"
        "\n"
        f"> **skill version**: {version}\n"
    )


def _readme_text(version: str) -> str:
    """Build a README whose changelog table has a row for ``version``."""
    return (
        "# sample\n\n"
        "| Version | Changes |\n"
        "|---|---|\n"
        f"| **{version}** | Something new. |\n"
        f"| **{PREVIOUS_VERSION}** | First release. |\n"
    )


def _write_release_files(root: Path, version: str = VERSION) -> None:
    """Write a consistent set of release declarations for ``version``.

    ``VERSION``, one skill, both READMEs, and the two generated manifests (written
    by :mod:`manifests` itself so that they are exactly what it expects).
    """
    (root / "VERSION").write_text(version + "\n", encoding="utf-8")
    skill_dir = root / "skills" / SKILL_NAME
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(_skill_text(SKILL_NAME, "2.0.0"), encoding="utf-8")
    (root / "README.md").write_text(_readme_text(version), encoding="utf-8")
    (root / "README_ja.md").write_text(_readme_text(version), encoding="utf-8")
    manifests.write_manifests(root)


def _release_ready_repo(tmp_path: Path) -> Path:
    """Create a synthetic checkout in which every release check passes.

    The release files are committed on ``main`` and pushed, so the working tree is
    clean and ``main`` matches its upstream.
    """
    repo = _make_repo(tmp_path)
    _write_release_files(repo)
    _git(repo, "add", "-A")
    _git(repo, "commit", "--no-verify", "--quiet", "-m", "release files")
    _push(repo, "main")
    return repo


# --------------------------------------------------------------------------- #
# Helpers: fake and recording runners
# --------------------------------------------------------------------------- #


def _fake_runner(
    answers: dict[tuple[str, ...], tuple[int, str, str]],
    *,
    delegate: Runner | None = None,
    calls: list[list[str]] | None = None,
) -> Runner:
    """Build a runner that answers from ``answers`` by the leading arguments.

    Args:
        answers: Maps an argument prefix (e.g. ``("gh", "api")``) to the result to
            return. The longest matching prefix wins.
        delegate: Runner used for commands that no prefix matches. When ``None``,
            such commands fail the test.
        calls: When given, every call's arguments are appended here.
    """

    def runner(args: Sequence[str], cwd: Path, timeout: float) -> tuple[int, str, str]:
        if calls is not None:
            calls.append(list(args))
        matching = [prefix for prefix in answers if tuple(args[: len(prefix)]) == prefix]
        if matching:
            return answers[max(matching, key=len)]
        if delegate is None:
            raise AssertionError(f"unexpected command: {list(args)!r}")
        return delegate(args, cwd, timeout)

    return runner


def _git_subcommand(args: Sequence[str]) -> list[str]:
    """Return a git command line from its subcommand on, skipping global options."""
    assert args[0] == "git"
    index = 1
    while index < len(args):
        token = args[index]
        if token in ("-C", "-c"):
            index += 2
        elif token.startswith("-"):
            index += 1
        else:
            return list(args[index:])
    raise AssertionError(f"git command without a subcommand: {list(args)!r}")


#: Git subcommands that only read. Anything else reaching the runner is a bug.
READ_ONLY_GIT_SUBCOMMANDS = frozenset(
    {"rev-parse", "for-each-ref", "merge-base", "rev-list", "status", "symbolic-ref", "remote"}
)

#: Subcommands that write to the repository or the remote, listed explicitly so
#: that the intent of the test survives a change to the allow-list above.
WRITING_GIT_SUBCOMMANDS = frozenset(
    {
        "commit",
        "push",
        "tag",
        "checkout",
        "switch",
        "reset",
        "branch",
        "merge",
        "rebase",
        "pull",
        "stash",
        "add",
        "rm",
        "mv",
        "restore",
        "clean",
        "update-ref",
        "config",
        "gc",
    }
)


def _assert_read_only(calls: Sequence[Sequence[str]], *, allow_fetch: bool) -> None:
    """Fail when a recorded call could write to the repository, the remote or gh."""
    for args in calls:
        if args[0] == "git":
            command = _git_subcommand(args)
            name = command[0]
            assert name not in WRITING_GIT_SUBCOMMANDS, args
            if name == "fetch":
                assert allow_fetch, args
                assert command == ["fetch", "--tags", "origin"], args
                continue
            assert name in READ_ONLY_GIT_SUBCOMMANDS, args
            if name == "remote":
                assert command[:2] == ["remote", "get-url"], args
        elif args[0] == "gh":
            assert list(args[1:3]) in (["api", "user"], ["auth", "status"]), args
        else:
            assert list(args) == ["bash", "tools/check.sh"], args


def _snapshot(repo: Path) -> tuple[str, str, str, str]:
    """Capture refs, HEAD, working tree state and local config of ``repo``."""
    return (
        _git(repo, "for-each-ref"),
        _git(repo, "rev-parse", "HEAD"),
        _git(repo, "status", "--porcelain"),
        _git(repo, "config", "--local", "--list"),
    )


# --------------------------------------------------------------------------- #
# run
# --------------------------------------------------------------------------- #


def test_run_returns_status_and_output(tmp_path: Path) -> None:
    code = "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)"
    returncode, stdout, stderr = run([sys.executable, "-c", code], tmp_path, 30)
    assert returncode == 3
    assert stdout.strip() == "out"
    assert stderr.strip() == "err"


def test_run_reports_a_timeout(tmp_path: Path) -> None:
    returncode, _, _ = run([sys.executable, "-c", "import time; time.sleep(10)"], tmp_path, 0.5)
    assert returncode == TIMEOUT_RETURNCODE


def test_run_reports_a_missing_executable(tmp_path: Path) -> None:
    returncode, _, _ = run(["definitely-not-an-installed-command-xyz"], tmp_path, 30)
    assert returncode == NOT_FOUND_RETURNCODE


def test_special_returncodes_cannot_be_process_exit_codes() -> None:
    for value in (TIMEOUT_RETURNCODE, NOT_FOUND_RETURNCODE):
        assert not 0 <= value <= 255
        assert value < -64  # negative values down to -64 are signal numbers
    assert TIMEOUT_RETURNCODE != NOT_FOUND_RETURNCODE


# --------------------------------------------------------------------------- #
# Names derived from the version
# --------------------------------------------------------------------------- #


def test_release_names() -> None:
    assert release_names("3.0.0") == ("v3.0", "v3.0.0")
    assert release_names("10.12.3") == ("v10.12", "v10.12.3")


# --------------------------------------------------------------------------- #
# G0 evidence
# --------------------------------------------------------------------------- #


@requires_git
def test_evidence_is_all_info_and_says_refs_are_local_without_fetch(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    results = check_evidence(repo, VERSION)
    assert results
    assert all(result.section == SECTION_EVIDENCE for result in results)
    assert all(result.status is Status.INFO for result in results)
    _find(results, "judged from local refs; pass --fetch to refresh")


@requires_git
def test_evidence_branch_without_commits_is_not_merged(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _git(repo, "branch", BRANCH)
    _push(repo, BRANCH)

    results = check_evidence(repo, VERSION)

    assert "exists" in _find(results, f"local branch {BRANCH}").detail
    assert "exists" in _find(results, f"origin/{BRANCH}").detail
    merge = _find(results, "merged into")
    assert merge.detail == f"{BRANCH} is not merged into origin/main (no commits of its own)"


@requires_git
def test_evidence_branch_with_commits_merged_into_base(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _git(repo, "switch", "--quiet", "-c", BRANCH)
    _commit(repo, "work on the release")
    _push(repo, BRANCH)
    _git(repo, "switch", "--quiet", "main")
    _git(repo, "merge", "--no-ff", "--no-verify", "--quiet", "-m", "merge", BRANCH)
    _push(repo, "main")

    merge = _find(check_evidence(repo, VERSION), "merged into")
    assert merge.detail == f"{BRANCH} is merged into origin/main"


@requires_git
def test_evidence_branch_with_commits_not_merged(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _git(repo, "switch", "--quiet", "-c", BRANCH)
    _commit(repo, "work on the release")

    results = check_evidence(repo, VERSION)

    assert "missing" in _find(results, f"origin/{BRANCH}").detail
    merge = _find(results, "merged into")
    assert merge.detail == f"{BRANCH} is not merged into origin/main"


@requires_git
def test_evidence_prefers_the_remote_tracking_base(tmp_path: Path) -> None:
    """A merge that exists only in the local ``main`` is not yet a merge on origin."""
    repo = _make_repo(tmp_path)
    _git(repo, "switch", "--quiet", "-c", BRANCH)
    _commit(repo, "work on the release")
    _git(repo, "switch", "--quiet", "main")
    _git(repo, "merge", "--no-ff", "--no-verify", "--quiet", "-m", "merge", BRANCH)

    merge = _find(check_evidence(repo, VERSION), "merged into")
    assert merge.detail == f"{BRANCH} is not merged into origin/main"


@requires_git
def test_evidence_falls_back_to_the_local_base(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _git(repo, "branch", "trunk")
    _git(repo, "switch", "--quiet", "-c", BRANCH)
    _commit(repo, "work on the release")
    _git(repo, "switch", "--quiet", "trunk")
    _git(repo, "merge", "--no-ff", "--no-verify", "--quiet", "-m", "merge", BRANCH)

    merge = _find(check_evidence(repo, VERSION, base="trunk"), "merged into")
    assert merge.detail == f"{BRANCH} is merged into trunk"


@requires_git
def test_evidence_missing_work_branch_leaves_merge_unverified(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    results = check_evidence(repo, VERSION)
    assert "missing" in _find(results, f"local branch {BRANCH}").detail
    assert "missing" in _find(results, f"origin/{BRANCH}").detail
    assert "unverified" in _find(results, "merge state").detail


@requires_git
def test_evidence_missing_base_leaves_merge_unverified(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _git(repo, "branch", BRANCH)
    assert "unverified" in _find(check_evidence(repo, VERSION, base="nope"), "merge state").detail


@requires_git
def test_evidence_tag_present_and_absent(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    assert "missing" in _find(check_evidence(repo, VERSION), f"tag {TAG}").detail
    _git(repo, "tag", "-a", "-m", "release", TAG)
    assert "exists" in _find(check_evidence(repo, VERSION), f"tag {TAG}").detail


@requires_git
def test_evidence_latest_tag_uses_semver_order_and_ignores_other_tags(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    for tag in ("v1.9.0", "v1.10.0", "v2.0.0-rc1", "vnext", "release-9.9.9"):
        _git(repo, "tag", tag)
    latest = _find(check_evidence(repo, VERSION), "latest release tag")
    assert latest.detail == "latest release tag: v1.10.0"


@requires_git
def test_evidence_latest_tag_none(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _git(repo, "tag", "-d", f"v{PREVIOUS_VERSION}")
    latest = _find(check_evidence(repo, VERSION), "latest release tag")
    assert latest.detail == "latest release tag: none"


@requires_git
def test_evidence_fetch_refreshes_tags_and_branches(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    other = _clone(tmp_path, "other")
    _git(other, "switch", "--quiet", "-c", BRANCH)
    _git(other, "tag", TAG)
    _push(other, BRANCH, "--tags")

    stale = check_evidence(repo, VERSION)
    assert "missing" in _find(stale, f"tag {TAG}").detail
    assert "missing" in _find(stale, f"origin/{BRANCH}").detail

    fresh = check_evidence(repo, VERSION, fetch=True)
    assert "exists" in _find(fresh, f"tag {TAG}").detail
    assert "exists" in _find(fresh, f"origin/{BRANCH}").detail
    assert not [result for result in fresh if "pass --fetch" in result.detail]
    assert all(result.status is Status.INFO for result in fresh)


def test_evidence_reports_a_failed_fetch_as_info(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    runner = _fake_runner(
        {("git",): (1, "", "fatal: no")},
        calls=calls,
    )
    results = check_evidence(tmp_path, VERSION, fetch=True, runner=runner)
    assert all(result.status is Status.INFO for result in results)
    assert "fetch failed" in _find(results, "fetch failed").detail
    assert any("fetch" in _git_subcommand(args) for args in calls)


# --------------------------------------------------------------------------- #
# Account: owner parsing
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("url", "owner"),
    [
        pytest.param("https://github.com/alice/agent-skills.git", "alice", id="https-git"),
        pytest.param("https://github.com/alice/agent-skills", "alice", id="https"),
        pytest.param("git@github.com:alice/agent-skills.git", "alice", id="scp"),
        pytest.param("git@github-alice:alice/agent-skills.git", "alice", id="ssh-alias"),
        pytest.param("ssh://git@github.com/alice/agent-skills.git", "alice", id="ssh-url"),
        pytest.param("ssh://git@github.com:22/alice/agent-skills.git", "alice", id="ssh-port"),
        pytest.param("https://github.com/alice/agent-skills.git\n", "alice", id="trailing-nl"),
    ],
)
def test_parse_owner(url: str, owner: str) -> None:
    assert parse_owner(url) == owner


@pytest.mark.parametrize(
    "url",
    [
        pytest.param("", id="empty"),
        pytest.param("/tmp/origin.git", id="local-path"),
        pytest.param("file:///tmp/origin.git", id="file-url"),
        pytest.param("https://github.com/alice", id="no-repo"),
        pytest.param("not a url", id="garbage"),
    ],
)
def test_parse_owner_unreadable(url: str) -> None:
    assert parse_owner(url) is None


def test_parse_active_account_picks_the_active_one() -> None:
    text = (
        "github.com\n"
        "  ✓ Logged in to github.com account bob (keyring)\n"
        "  - Active account: false\n"
        "  - Git operations protocol: ssh\n"
        "\n"
        "  ✓ Logged in to github.com account alice (keyring)\n"
        "  - Active account: true\n"
        "  - Git operations protocol: ssh\n"
    )
    assert parse_active_account(text) == "alice"


def test_parse_active_account_none_without_an_active_line() -> None:
    assert parse_active_account("You are not logged into any GitHub hosts.\n") is None


# --------------------------------------------------------------------------- #
# Account: the check
# --------------------------------------------------------------------------- #


def _account_runner(
    url: str, gh_api: tuple[int, str, str], gh_status: tuple[int, str, str] | None = None
) -> Runner:
    """Answer ``git remote get-url`` and the two gh queries."""
    answers: dict[tuple[str, ...], tuple[int, str, str]] = {
        ("git",): (0, url + "\n", ""),
        ("gh", "api", "user"): gh_api,
    }
    answers[("gh", "auth", "status")] = gh_status if gh_status is not None else (1, "", "")
    return _fake_runner(answers)


def test_account_matches() -> None:
    runner = _account_runner("git@github.com:alice/repo.git", (0, "alice\n", ""))
    (result,) = check_account(Path("."), runner=runner)
    assert result.section == SECTION_ACCOUNT
    assert result.status is Status.PASS
    assert "alice" in result.detail


def test_account_match_ignores_case() -> None:
    runner = _account_runner("git@github.com:Alice/repo.git", (0, "alice\n", ""))
    (result,) = check_account(Path("."), runner=runner)
    assert result.status is Status.PASS


def test_account_mismatch_warns_and_never_switches() -> None:
    calls: list[list[str]] = []
    runner = _fake_runner(
        {
            ("git",): (0, "git@github.com:alice/repo.git\n", ""),
            ("gh", "api", "user"): (0, "bob\n", ""),
        },
        calls=calls,
    )
    (result,) = check_account(Path("."), runner=runner)
    assert result.status is Status.WARN
    assert "switch only with the user's approval" in result.detail
    assert "alice" in result.detail and "bob" in result.detail
    _assert_read_only(calls, allow_fetch=False)


def test_account_falls_back_to_gh_auth_status() -> None:
    status_text = (
        "github.com\n"
        "  ✓ Logged in to github.com account alice (keyring)\n"
        "  - Active account: true\n"
    )
    runner = _account_runner(
        "https://github.com/alice/repo.git", (1, "", "HTTP 401"), (0, "", status_text)
    )
    (result,) = check_account(Path("."), runner=runner)
    assert result.status is Status.PASS


def test_account_without_gh_is_unverified() -> None:
    runner = _account_runner(
        "git@github.com:alice/repo.git",
        (NOT_FOUND_RETURNCODE, "", ""),
        (NOT_FOUND_RETURNCODE, "", ""),
    )
    (result,) = check_account(Path("."), runner=runner)
    assert result.status is Status.WARN
    assert "unverified" in result.detail


def test_account_not_logged_in_is_unverified() -> None:
    runner = _account_runner(
        "git@github.com:alice/repo.git",
        (1, "", "not logged in"),
        (1, "", "You are not logged into any GitHub hosts."),
    )
    (result,) = check_account(Path("."), runner=runner)
    assert result.status is Status.WARN
    assert "unverified" in result.detail


def test_account_unreadable_owner_is_unverified_and_hides_the_url() -> None:
    secret_url = "https://user:secret-token@example.com/only-one-part"
    runner = _account_runner(secret_url, (0, "alice\n", ""))
    (result,) = check_account(Path("."), runner=runner)
    assert result.status is Status.WARN
    assert "unverified" in result.detail
    assert "secret-token" not in result.detail


def test_account_without_origin_is_unverified() -> None:
    runner = _fake_runner({("git",): (2, "", "error: No such remote 'origin'")})
    (result,) = check_account(Path("."), runner=runner)
    assert result.status is Status.WARN
    assert "unverified" in result.detail


# --------------------------------------------------------------------------- #
# G1 version declarations
# --------------------------------------------------------------------------- #


def test_versions_all_consistent(tmp_path: Path) -> None:
    _write_release_files(tmp_path)
    results = check_version_declarations(tmp_path, VERSION)
    assert all(result.section == SECTION_VERSIONS for result in results)
    assert [result.status for result in results] == [Status.PASS] * 5
    for prefix in ("VERSION", ".claude-plugin/marketplace.json", "skills/index.json"):
        _starting(results, prefix)
    _starting(results, "README.md")
    _starting(results, "README_ja.md")


def test_versions_version_file_mismatch(tmp_path: Path) -> None:
    _write_release_files(tmp_path)
    (tmp_path / "VERSION").write_text("1.2.0\n", encoding="utf-8")
    results = check_version_declarations(tmp_path, VERSION)
    assert _starting(results, "VERSION").status is Status.FAIL
    assert _starting(results, "README.md").status is Status.PASS


def test_versions_version_file_missing(tmp_path: Path) -> None:
    _write_release_files(tmp_path)
    (tmp_path / "VERSION").unlink()
    assert _starting(check_version_declarations(tmp_path, VERSION), "VERSION").status is (
        Status.FAIL
    )


def test_versions_marketplace_mismatch(tmp_path: Path) -> None:
    _write_release_files(tmp_path)
    path = tmp_path / ".claude-plugin" / "marketplace.json"
    path.write_text(path.read_text(encoding="utf-8").replace(VERSION, "0.9.0"), encoding="utf-8")
    results = check_version_declarations(tmp_path, VERSION)
    result = _starting(results, ".claude-plugin/marketplace.json")
    assert result.status is Status.FAIL
    assert "0.9.0" in result.detail
    assert _starting(results, "VERSION").status is Status.PASS


@pytest.mark.parametrize(
    "content",
    [
        pytest.param("{not json", id="invalid-json"),
        pytest.param('{"plugins": []}', id="no-plugin"),
        pytest.param("[1, 2]", id="not-an-object"),
    ],
)
def test_versions_marketplace_unreadable(tmp_path: Path, content: str) -> None:
    _write_release_files(tmp_path)
    (tmp_path / ".claude-plugin" / "marketplace.json").write_text(content, encoding="utf-8")
    result = _starting(
        check_version_declarations(tmp_path, VERSION), ".claude-plugin/marketplace.json"
    )
    assert result.status is Status.FAIL


def test_versions_index_disagrees_with_skill_md(tmp_path: Path) -> None:
    _write_release_files(tmp_path)
    skill_md = tmp_path / "skills" / SKILL_NAME / "SKILL.md"
    skill_md.write_text(_skill_text(SKILL_NAME, "2.0.1"), encoding="utf-8")
    result = _starting(check_version_declarations(tmp_path, VERSION), "skills/index.json")
    assert result.status is Status.FAIL
    assert SKILL_NAME in result.detail
    assert "2.0.0" in result.detail and "2.0.1" in result.detail


def test_versions_index_names_a_missing_skill(tmp_path: Path) -> None:
    _write_release_files(tmp_path)
    shutil.rmtree(tmp_path / "skills" / SKILL_NAME)
    result = _starting(check_version_declarations(tmp_path, VERSION), "skills/index.json")
    assert result.status is Status.FAIL
    assert SKILL_NAME in result.detail


def test_versions_index_rejects_a_path_like_name(tmp_path: Path) -> None:
    _write_release_files(tmp_path)
    (tmp_path / "skills" / "index.json").write_text(
        '{"skills": [{"name": "../escape", "version": "1.0.0"}]}', encoding="utf-8"
    )
    result = _starting(check_version_declarations(tmp_path, VERSION), "skills/index.json")
    assert result.status is Status.FAIL


@pytest.mark.parametrize("readme", ["README.md", "README_ja.md"])
def test_versions_one_readme_lacks_the_changelog_row(tmp_path: Path, readme: str) -> None:
    _write_release_files(tmp_path)
    (tmp_path / readme).write_text(_readme_text(PREVIOUS_VERSION), encoding="utf-8")
    results = check_version_declarations(tmp_path, VERSION)
    other = "README_ja.md" if readme == "README.md" else "README.md"
    assert _starting(results, readme).status is Status.FAIL
    assert _starting(results, other).status is Status.PASS


def test_versions_changelog_row_must_start_the_line(tmp_path: Path) -> None:
    """A mention of the version elsewhere in the table does not count as its row."""
    _write_release_files(tmp_path)
    (tmp_path / "README.md").write_text(
        f"| **{PREVIOUS_VERSION}** | Prepares **{VERSION}**. |\n"
        f"Text mentioning | **{VERSION}** | inline.\n",
        encoding="utf-8",
    )
    assert _starting(check_version_declarations(tmp_path, VERSION), "README.md").status is (
        Status.FAIL
    )


def test_versions_missing_readme(tmp_path: Path) -> None:
    _write_release_files(tmp_path)
    (tmp_path / "README_ja.md").unlink()
    assert _starting(check_version_declarations(tmp_path, VERSION), "README_ja.md").status is (
        Status.FAIL
    )


# --------------------------------------------------------------------------- #
# G2 manifests
# --------------------------------------------------------------------------- #


def test_manifests_in_sync(tmp_path: Path) -> None:
    _write_release_files(tmp_path)
    (result,) = check_generated_manifests(tmp_path)
    assert result.section == SECTION_MANIFESTS
    assert result.status is Status.PASS


def test_manifests_drift_names_the_file(tmp_path: Path) -> None:
    _write_release_files(tmp_path)
    (tmp_path / "skills" / SKILL_NAME / "extra.md").write_text("new\n", encoding="utf-8")
    results = check_generated_manifests(tmp_path)
    assert [result.status for result in results] == [Status.FAIL]
    assert "skills/index.json" in results[0].detail


def test_manifests_that_cannot_be_generated_fail(tmp_path: Path) -> None:
    _write_release_files(tmp_path)
    (tmp_path / "VERSION").write_text("garbage\n", encoding="utf-8")
    results = check_generated_manifests(tmp_path)
    assert [result.status for result in results] == [Status.FAIL]


# --------------------------------------------------------------------------- #
# G4 checks
# --------------------------------------------------------------------------- #


def test_suite_success(tmp_path: Path) -> None:
    seen: list[tuple[list[str], Path, float]] = []

    def runner(args: Sequence[str], cwd: Path, timeout: float) -> tuple[int, str, str]:
        seen.append((list(args), cwd, timeout))
        return 0, "check.sh: all checks passed.\n", ""

    (result,) = check_suite(tmp_path, timeout=123, runner=runner)
    assert result.section == SECTION_CHECKS
    assert result.status is Status.PASS
    assert seen == [(["bash", "tools/check.sh"], tmp_path, 123)]


def test_suite_failure_shows_the_last_twenty_lines(tmp_path: Path) -> None:
    stdout = "".join(f"out-{index:02d}\n" for index in range(1, 26))
    runner = _fake_runner({("bash",): (1, stdout, "boom-stderr\n")})
    (result,) = check_suite(tmp_path, runner=runner)
    assert result.status is Status.FAIL
    lines = result.detail.splitlines()
    assert "boom-stderr" in lines
    assert "out-25" in lines
    assert "out-07" in lines
    assert "out-06" not in lines
    assert len([line for line in lines if line.startswith("out-") or line == "boom-stderr"]) == 20


def test_suite_timeout_is_unverified(tmp_path: Path) -> None:
    runner = _fake_runner({("bash",): (TIMEOUT_RETURNCODE, "", "")})
    (result,) = check_suite(tmp_path, timeout=5, runner=runner)
    assert result.status is Status.WARN
    assert "unverified" in result.detail
    assert "run tools/check.sh separately" in result.detail


def test_suite_skipped(tmp_path: Path) -> None:
    (result,) = check_suite(tmp_path, skip=True, runner=_fake_runner({}))
    assert result.status is Status.INFO
    assert "skipped" in result.detail


# --------------------------------------------------------------------------- #
# G5 clean
# --------------------------------------------------------------------------- #


@requires_git
def test_clean_and_in_sync(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    results = check_clean(repo)
    assert all(result.section == SECTION_CLEAN for result in results)
    assert [result.status for result in results] == [Status.PASS, Status.PASS]
    assert "origin/main" in _starting(results, "main").detail


@requires_git
def test_clean_reports_uncommitted_files(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    (repo / "notes.txt").write_text("edited\n", encoding="utf-8")
    (repo / "new-file.txt").write_text("new\n", encoding="utf-8")
    result = _starting(check_clean(repo), "working tree")
    assert result.status is Status.FAIL
    assert "notes.txt" in result.detail
    assert "new-file.txt" in result.detail


@requires_git
def test_clean_branch_without_upstream(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _git(repo, "switch", "--quiet", "-c", BRANCH)
    result = _starting(check_clean(repo), BRANCH)
    assert result.status is Status.FAIL
    assert "no upstream" in result.detail


@requires_git
def test_clean_branch_ahead_of_upstream(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _commit(repo, "not pushed")
    results = check_clean(repo)
    assert _starting(results, "working tree").status is Status.PASS
    result = _starting(results, "main")
    assert result.status is Status.FAIL
    assert "ahead" in result.detail and "1 commit" in result.detail


@requires_git
def test_clean_branch_behind_upstream(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    other = _clone(tmp_path, "other")
    _commit(other, "someone else's work")
    _push(other, "main")
    _git(repo, "fetch", "--quiet", "origin")
    result = _starting(check_clean(repo), "main")
    assert result.status is Status.FAIL
    assert "behind" in result.detail


@requires_git
def test_clean_branch_diverged_from_upstream(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    other = _clone(tmp_path, "other")
    _commit(other, "someone else's work")
    _push(other, "main")
    _git(repo, "fetch", "--quiet", "origin")
    _commit(repo, "my work", filename="mine.txt")
    result = _starting(check_clean(repo), "main")
    assert result.status is Status.FAIL
    assert "ahead" in result.detail and "behind" in result.detail


@requires_git
def test_clean_detached_head(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _git(repo, "switch", "--quiet", "--detach")
    results = check_clean(repo)
    assert any(result.status is Status.FAIL and "detached" in result.detail for result in results)


# --------------------------------------------------------------------------- #
# Report formatting
# --------------------------------------------------------------------------- #


def test_format_report_has_a_table_and_a_summary() -> None:
    results = [
        Result(SECTION_EVIDENCE, Status.INFO, "latest release tag: v1.0.0"),
        Result(SECTION_VERSIONS, Status.FAIL, "VERSION is 1.0.0, expected 1.1.0"),
        Result(SECTION_ACCOUNT, Status.WARN, "unverified: gh is not installed"),
        Result(SECTION_CHECKS, Status.FAIL, "tools/check.sh failed:\nline-a\nline-b"),
    ]
    report = format_report(results)
    lines = report.splitlines()
    header = lines[0]
    assert "Section" in header and "Result" in header and "Detail" in header
    assert lines[-1] == "Summary: 2 FAIL, 1 WARN"
    detail_column = header.index("Detail")
    continuation = next(line for line in lines if line.rstrip().endswith("line-a"))
    assert continuation.index("line-a") == detail_column


# --------------------------------------------------------------------------- #
# main: exit codes, arguments and the no-write guarantee
# --------------------------------------------------------------------------- #


def _release_runner(owner: str = "alice", calls: list[list[str]] | None = None) -> Runner:
    """Delegate git to the real runner and fake gh and ``bash tools/check.sh``."""
    return _fake_runner(
        {
            ("git", "--no-optional-locks", "remote", "get-url"): (
                0,
                f"git@github.com:{owner}/repo.git\n",
                "",
            ),
            ("gh", "api", "user"): (0, f"{owner}\n", ""),
            ("gh", "auth", "status"): (1, "", ""),
            ("bash", "tools/check.sh"): (0, "check.sh: all checks passed.\n", ""),
        },
        delegate=run,
        calls=calls,
    )


@pytest.mark.parametrize(
    "argv",
    [
        pytest.param([], id="missing-version"),
        pytest.param(["3.0"], id="two-parts"),
        pytest.param(["v3.0.0"], id="leading-v"),
        pytest.param(["3.0.0.1"], id="four-parts"),
        pytest.param(["3.0.0-rc1"], id="prerelease"),
        pytest.param(["3.0.0", "--base", "-x"], id="option-like-base"),
        pytest.param(["3.0.0", "--base", "a b"], id="base-with-space"),
        pytest.param(["3.0.0", "--check-timeout", "0"], id="zero-timeout"),
        pytest.param(["3.0.0", "--check-timeout", "abc"], id="bad-timeout"),
        pytest.param(["3.0.0", "--unknown"], id="unknown-option"),
    ],
)
def test_main_bad_arguments_exit_2(
    tmp_path: Path, argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(argv, runner=_fake_runner({}), repo_root=tmp_path)
    assert excinfo.value.code == 2
    assert "usage:" in capsys.readouterr().err


@requires_git
def test_main_exit_0_when_nothing_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = _release_ready_repo(tmp_path)
    assert main([VERSION], runner=_release_runner(), repo_root=repo) == 0
    output = capsys.readouterr().out
    assert output.splitlines()[-1] == "Summary: 0 FAIL, 0 WARN"
    for section in (
        SECTION_EVIDENCE,
        SECTION_ACCOUNT,
        SECTION_VERSIONS,
        SECTION_MANIFESTS,
        SECTION_CHECKS,
        SECTION_CLEAN,
    ):
        assert section in output


@requires_git
def test_main_warnings_alone_exit_0(tmp_path: Path) -> None:
    repo = _release_ready_repo(tmp_path)
    assert main([VERSION, "--skip-checks"], runner=_release_runner(), repo_root=repo) == 0
    mismatched = _fake_runner({("gh", "api", "user"): (0, "bob\n", "")}, delegate=_release_runner())
    assert main([VERSION, "--skip-checks"], runner=mismatched, repo_root=repo) == 0


@requires_git
def test_main_exit_1_on_a_failure(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = _release_ready_repo(tmp_path)
    assert main(["9.9.9", "--skip-checks"], runner=_release_runner(), repo_root=repo) == 1
    last = capsys.readouterr().out.splitlines()[-1]
    assert last.startswith("Summary: ") and not last.startswith("Summary: 0 FAIL")


@requires_git
def test_main_exit_1_when_check_sh_fails(tmp_path: Path) -> None:
    repo = _release_ready_repo(tmp_path)
    failing = _fake_runner(
        {("bash", "tools/check.sh"): (1, "ruff: error\n", "")}, delegate=_release_runner()
    )
    assert main([VERSION], runner=failing, repo_root=repo) == 1


@requires_git
def test_main_passes_the_check_timeout(tmp_path: Path) -> None:
    repo = _release_ready_repo(tmp_path)
    timeouts: list[float] = []

    def runner(args: Sequence[str], cwd: Path, timeout: float) -> tuple[int, str, str]:
        if list(args) == ["bash", "tools/check.sh"]:
            timeouts.append(timeout)
        return _release_runner()(args, cwd, timeout)

    main([VERSION, "--check-timeout", "42"], runner=runner, repo_root=repo)
    assert timeouts == [42]


@requires_git
def test_main_writes_nothing_without_fetch(tmp_path: Path) -> None:
    repo = _release_ready_repo(tmp_path)
    _git(repo, "branch", BRANCH)
    before = _snapshot(repo)
    calls: list[list[str]] = []

    main([VERSION], runner=_release_runner(calls=calls), repo_root=repo)

    assert calls
    _assert_read_only(calls, allow_fetch=False)
    assert not [args for args in calls if args[0] == "git" and "fetch" in _git_subcommand(args)]
    assert _snapshot(repo) == before


@requires_git
def test_main_with_fetch_only_adds_a_tag_fetch(tmp_path: Path) -> None:
    repo = _release_ready_repo(tmp_path)
    calls: list[list[str]] = []

    main([VERSION, "--fetch", "--skip-checks"], runner=_release_runner(calls=calls), repo_root=repo)

    _assert_read_only(calls, allow_fetch=True)
    fetches = [args for args in calls if args[0] == "git" and _git_subcommand(args)[0] == "fetch"]
    assert len(fetches) == 1


def test_module_exposes_a_single_runner_entry_point() -> None:
    """Every external command goes through ``run``; nothing else spawns processes."""
    source = Path(release_check.__file__).read_text(encoding="utf-8")
    assert source.count("subprocess.run(") == 1
    assert "os.system" not in source
    assert "Popen" not in source
