"""Tests for ``tools/version_guard.py``.

Stage 2 of the project's TDD cycle: these tests are written against the
*intended* behaviour of ``version_guard.py`` before the function bodies are
implemented (they currently all raise ``NotImplementedError``).

Every test builds a synthetic checkout under ``tmp_path`` -- ``git init``, a
couple of commits and a tag written by the test itself -- so that no test depends
on the history of this repository. A test that read the real history would start
failing the moment a commit or a tag is added to it.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from version_guard import (
    Finding,
    changed_paths,
    check_version_bumps,
    declared_version,
    find_latest_tag,
    main,
    read_blob,
)

BASE_TAG = "v1.0.0"
BASE_VERSION = "1.0.0"
NEXT_VERSION = "1.1.0"
SKILL_NAME = "sample-skill"

#: Identity and commit options forced onto every synthetic commit. The identity is
#: deliberately generic so that the fixtures carry no personal information, and it
#: keeps the tests working on machines where git has no global identity. Signing
#: and hooks are disabled so that a contributor's global configuration cannot make
#: these commits fail.
GIT_COMMIT_OPTIONS = (
    "-c",
    "user.email=test@example.com",
    "-c",
    "user.name=Test",
    "-c",
    "commit.gpgsign=false",
)

#: Without git there is nothing to compare, and ``version_guard`` is a no-op by
#: design; the fixtures below cannot be built either.
requires_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


# --------------------------------------------------------------------------- #
# Helpers
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


def _commit(repo: Path, message: str) -> None:
    """Stage everything under ``repo`` and record it as one commit."""
    _git(repo, "add", "-A")
    _git(repo, *GIT_COMMIT_OPTIONS, "commit", "--no-verify", "-m", message)


def _skill_text(name: str, version: str, body: str = "") -> str:
    """Build a minimal but well-formed ``SKILL.md``."""
    return (
        "---\n"
        f"name: {name}\n"
        'description: "テスト用のサンプルスキル。"\n'
        "license: MIT\n"
        "metadata:\n"
        f'  version: "{version}"\n'
        "---\n"
        "\n"
        f"# {name}\n"
        "\n"
        f"> **skill version**: {version}\n" + body
    )


def _write_skill(repo: Path, name: str, version: str, body: str = "") -> None:
    """Write (or overwrite) one skill folder in the working tree."""
    skill_dir = repo / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(_skill_text(name, version, body), encoding="utf-8")


def _write_project_version(repo: Path, version: str) -> None:
    """Write the top-level ``VERSION`` file."""
    (repo / "VERSION").write_text(f"{version}\n", encoding="utf-8")


def _init_repo(tmp_path: Path, *, with_version: bool = True, tag: str | None = BASE_TAG) -> Path:
    """Create a checkout holding one skill, commit it and tag the commit.

    Args:
        tmp_path: pytest-provided temporary directory.
        with_version: Whether the baseline commit contains a ``VERSION`` file.
        tag: Tag to place on the baseline commit, or ``None`` to leave it untagged.

    Returns:
        Path of the new checkout.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "--quiet", "-b", "main")
    (repo / "README.md").write_text("# sample\n", encoding="utf-8")
    _write_skill(repo, SKILL_NAME, BASE_VERSION)
    if with_version:
        _write_project_version(repo, BASE_VERSION)
    _commit(repo, "initial")
    if tag is not None:
        _git(repo, "tag", tag)
    return repo


def _subjects(findings: list[Finding]) -> set[str]:
    """Return the subjects of the findings that fail the build."""
    return {finding.subject for finding in findings if not finding.is_warning}


# --------------------------------------------------------------------------- #
# Case 1: a changed skill with a bumped version is accepted
# --------------------------------------------------------------------------- #


@requires_git
def test_changed_skill_with_version_bump_has_no_finding(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _write_skill(repo, SKILL_NAME, NEXT_VERSION, body="\n追記した本文。\n")
    _write_project_version(repo, NEXT_VERSION)
    _commit(repo, "edit the skill and bump both versions")

    assert check_version_bumps(repo, BASE_TAG) == []


@requires_git
def test_changed_skill_with_version_bump_but_stale_project_version(tmp_path: Path) -> None:
    """The skill rule passes on its own even when the project version is stale."""
    repo = _init_repo(tmp_path)
    _write_skill(repo, SKILL_NAME, NEXT_VERSION, body="\n追記した本文。\n")
    _commit(repo, "edit the skill and bump only its version")

    findings = check_version_bumps(repo, BASE_TAG)

    assert f"skills/{SKILL_NAME}" not in _subjects(findings)


# --------------------------------------------------------------------------- #
# Case 2: a changed skill with an unchanged version is a violation
# --------------------------------------------------------------------------- #


@requires_git
def test_changed_skill_without_version_bump_is_a_violation(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _write_skill(repo, SKILL_NAME, BASE_VERSION, body="\n版を据え置いたまま追記した本文。\n")
    # Bumped so that only the per-skill rule can produce a finding.
    _write_project_version(repo, NEXT_VERSION)
    _commit(repo, "edit the skill without bumping its version")

    findings = check_version_bumps(repo, BASE_TAG)

    assert _subjects(findings) == {f"skills/{SKILL_NAME}"}
    assert BASE_VERSION in findings[0].message
    assert findings[0].is_warning is False


@requires_git
def test_changed_skill_without_version_bump_exits_one(tmp_path: Path, capsys) -> None:
    repo = _init_repo(tmp_path)
    _write_skill(repo, SKILL_NAME, BASE_VERSION, body="\n版を据え置いたまま追記した本文。\n")
    _write_project_version(repo, NEXT_VERSION)
    _commit(repo, "edit the skill without bumping its version")

    exit_code = main(["--repo-root", str(repo), "--ref", BASE_TAG])

    assert exit_code == 1
    assert f"skills/{SKILL_NAME}" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# Case 3: an unrelated change is accepted
# --------------------------------------------------------------------------- #


@requires_git
def test_change_outside_skills_has_no_finding(tmp_path: Path, capsys) -> None:
    repo = _init_repo(tmp_path)
    (repo / "README.md").write_text("# sample\n\n説明を追記。\n", encoding="utf-8")
    _commit(repo, "edit the readme only")

    assert check_version_bumps(repo, BASE_TAG) == []
    assert main(["--repo-root", str(repo), "--ref", BASE_TAG]) == 0
    assert capsys.readouterr().err == ""


@requires_git
def test_uncommitted_changes_are_ignored(tmp_path: Path) -> None:
    """Only committed content can reach a user, so the working tree is not read."""
    repo = _init_repo(tmp_path)
    _write_skill(repo, SKILL_NAME, BASE_VERSION, body="\nまだコミットしていない追記。\n")

    assert check_version_bumps(repo, BASE_TAG) == []


# --------------------------------------------------------------------------- #
# Case 4: a skill added after the tag is accepted
# --------------------------------------------------------------------------- #


@requires_git
def test_new_skill_is_not_a_violation(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _write_skill(repo, "brand-new-skill", "0.1.0")
    _write_project_version(repo, NEXT_VERSION)
    _commit(repo, "add a skill")

    assert check_version_bumps(repo, BASE_TAG) == []


@requires_git
def test_removed_skill_is_not_a_violation(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _git(repo, "rm", "--quiet", "-r", f"skills/{SKILL_NAME}")
    _write_project_version(repo, NEXT_VERSION)
    _commit(repo, "drop a skill")

    assert check_version_bumps(repo, BASE_TAG) == []


# --------------------------------------------------------------------------- #
# Case 5: a change under skills/ with a stale VERSION is a violation
# --------------------------------------------------------------------------- #


@requires_git
def test_stale_project_version_is_a_violation(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _write_skill(repo, SKILL_NAME, NEXT_VERSION, body="\n追記した本文。\n")
    _commit(repo, "edit the skill without bumping the project version")

    findings = check_version_bumps(repo, BASE_TAG)

    assert _subjects(findings) == {"VERSION"}
    assert main(["--repo-root", str(repo), "--ref", BASE_TAG]) == 1


@requires_git
def test_loose_file_under_skills_still_requires_a_project_bump(tmp_path: Path) -> None:
    """A generated manifest such as ``skills/index.json`` belongs to no skill."""
    repo = _init_repo(tmp_path)
    (repo / "skills" / "index.json").write_text("{}\n", encoding="utf-8")
    _commit(repo, "regenerate the skill index")

    findings = check_version_bumps(repo, BASE_TAG)

    assert _subjects(findings) == {"VERSION"}


# --------------------------------------------------------------------------- #
# Case 6: a change under skills/ with a bumped VERSION is accepted
# --------------------------------------------------------------------------- #


@requires_git
def test_skills_change_with_project_bump_has_no_finding(tmp_path: Path, capsys) -> None:
    repo = _init_repo(tmp_path)
    _write_skill(repo, SKILL_NAME, NEXT_VERSION, body="\n追記した本文。\n")
    _write_project_version(repo, NEXT_VERSION)
    _commit(repo, "edit the skill and bump both versions")

    assert check_version_bumps(repo, BASE_TAG) == []
    assert main(["--repo-root", str(repo), "--ref", BASE_TAG]) == 0
    assert capsys.readouterr().err == ""


@requires_git
def test_whitespace_only_project_version_change_is_still_a_violation(tmp_path: Path) -> None:
    """Re-writing ``VERSION`` with the same number is not a bump."""
    repo = _init_repo(tmp_path)
    _write_skill(repo, SKILL_NAME, NEXT_VERSION, body="\n追記した本文。\n")
    (repo / "VERSION").write_text(f"{BASE_VERSION}\n\n", encoding="utf-8")
    _commit(repo, "edit the skill and reformat VERSION")

    assert _subjects(check_version_bumps(repo, BASE_TAG)) == {"VERSION"}


# --------------------------------------------------------------------------- #
# Case 7: a checkout without tags is skipped
# --------------------------------------------------------------------------- #


@requires_git
def test_repository_without_tags_is_skipped(tmp_path: Path, capsys) -> None:
    repo = _init_repo(tmp_path, tag=None)
    _write_skill(repo, SKILL_NAME, BASE_VERSION, body="\n版を据え置いたまま追記した本文。\n")
    _commit(repo, "edit the skill without bumping anything")

    exit_code = main(["--repo-root", str(repo)])
    captured = capsys.readouterr()

    assert find_latest_tag(repo) is None
    assert exit_code == 0
    assert "skip" in captured.out.lower()
    assert captured.err == ""


@requires_git
def test_unknown_reference_is_skipped(tmp_path: Path, capsys) -> None:
    repo = _init_repo(tmp_path)

    exit_code = main(["--repo-root", str(repo), "--ref", "v9.9.9"])

    assert exit_code == 0
    assert "skip" in capsys.readouterr().out.lower()


@requires_git
def test_find_latest_tag_returns_the_most_recent_tag(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _write_project_version(repo, NEXT_VERSION)
    _commit(repo, "release the next version")
    _git(repo, "tag", "v1.1.0")

    assert find_latest_tag(repo) == "v1.1.0"


# --------------------------------------------------------------------------- #
# Case 8: a directory that is not a checkout is skipped
# --------------------------------------------------------------------------- #


def test_non_git_directory_is_skipped(tmp_path: Path, capsys) -> None:
    plain = tmp_path / "plain"
    (plain / "skills" / SKILL_NAME).mkdir(parents=True)
    (plain / "skills" / SKILL_NAME / "SKILL.md").write_text(
        _skill_text(SKILL_NAME, BASE_VERSION), encoding="utf-8"
    )

    exit_code = main(["--repo-root", str(plain)])
    captured = capsys.readouterr()

    assert find_latest_tag(plain) is None
    assert exit_code == 0
    assert "skip" in captured.out.lower()
    assert captured.err == ""


def test_git_helpers_tolerate_a_non_git_directory(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()

    assert changed_paths(plain, BASE_TAG) == []
    assert read_blob(plain, BASE_TAG, "VERSION") is None
    assert check_version_bumps(plain, BASE_TAG) == []


# --------------------------------------------------------------------------- #
# Case 9: a VERSION file absent at the tag is accepted
# --------------------------------------------------------------------------- #


@requires_git
def test_project_version_absent_at_the_tag_is_not_a_violation(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path, with_version=False)
    _write_skill(repo, SKILL_NAME, NEXT_VERSION, body="\n追記した本文。\n")
    _commit(repo, "edit the skill before VERSION existed")

    assert check_version_bumps(repo, BASE_TAG) == []


@requires_git
def test_project_version_added_after_the_tag_is_not_a_violation(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path, with_version=False)
    _write_skill(repo, SKILL_NAME, NEXT_VERSION, body="\n追記した本文。\n")
    _write_project_version(repo, NEXT_VERSION)
    _commit(repo, "edit the skill and introduce VERSION")

    assert check_version_bumps(repo, BASE_TAG) == []


# --------------------------------------------------------------------------- #
# Case 10: an unreadable version is a warning, not a violation
# --------------------------------------------------------------------------- #


@requires_git
def test_broken_frontmatter_is_reported_as_a_warning(tmp_path: Path, capsys) -> None:
    repo = _init_repo(tmp_path)
    broken = '---\nname: sample-skill\nmetadata:\n  version: "1.1.0"\n\n# 閉じ忘れ\n'
    (repo / "skills" / SKILL_NAME / "SKILL.md").write_text(broken, encoding="utf-8")
    _write_project_version(repo, NEXT_VERSION)
    _commit(repo, "break the frontmatter")

    findings = check_version_bumps(repo, BASE_TAG)
    exit_code = main(["--repo-root", str(repo), "--ref", BASE_TAG])

    assert [finding.subject for finding in findings] == [f"skills/{SKILL_NAME}"]
    assert findings[0].is_warning is True
    assert exit_code == 0
    assert f"skills/{SKILL_NAME}" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# Unit tests for the individual helpers
# --------------------------------------------------------------------------- #


@requires_git
def test_changed_paths_lists_committed_differences(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _write_skill(repo, SKILL_NAME, NEXT_VERSION, body="\n追記した本文。\n")
    _commit(repo, "edit the skill")

    assert changed_paths(repo, BASE_TAG) == [f"skills/{SKILL_NAME}/SKILL.md"]


@requires_git
def test_read_blob_reads_the_tagged_content(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _write_skill(repo, SKILL_NAME, NEXT_VERSION)
    _commit(repo, "bump the skill version")

    tagged = read_blob(repo, BASE_TAG, f"skills/{SKILL_NAME}/SKILL.md")
    head = read_blob(repo, "HEAD", f"skills/{SKILL_NAME}/SKILL.md")

    assert tagged is not None
    assert head is not None
    assert declared_version(tagged) == BASE_VERSION
    assert declared_version(head) == NEXT_VERSION


@requires_git
def test_read_blob_returns_none_for_a_missing_path(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)

    assert read_blob(repo, BASE_TAG, "skills/absent-skill/SKILL.md") is None
    assert read_blob(repo, "v0.0.1", "VERSION") is None


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("", id="empty"),
        pytest.param("# 本文だけ\n", id="no-frontmatter"),
        pytest.param("---\nname: sample-skill\n\n# 閉じ忘れ\n", id="unterminated"),
        pytest.param("---\nname: sample-skill\n---\n", id="no-metadata"),
        pytest.param("---\nmetadata: 1.0.0\n---\n", id="metadata-not-a-mapping"),
        pytest.param('---\nmetadata:\n  name: "x"\n---\n', id="no-version-key"),
        pytest.param("---\nmetadata:\n  version: 1.0\n---\n", id="version-not-a-string"),
        pytest.param("---\nname: [unclosed\n---\n", id="invalid-yaml"),
    ],
)
def test_declared_version_returns_none_when_unreadable(text: str) -> None:
    assert declared_version(text) is None


def test_declared_version_reads_a_well_formed_skill() -> None:
    assert declared_version(_skill_text(SKILL_NAME, "2.3.4")) == "2.3.4"
