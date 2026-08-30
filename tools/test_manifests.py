"""Tests for ``tools/manifests.py``.

Stage 2 of the project's TDD cycle: these tests are written against the
*intended* behaviour of ``manifests.py`` before the function bodies are
implemented (they currently all raise ``NotImplementedError``). Every test
below builds a synthetic repository under ``tmp_path`` — a ``VERSION`` file
and/or a ``skills/`` tree of minimal skill folders — and drives it through
the public functions of :mod:`manifests`, so no test touches the network,
the user's home directory or this repository's real ``skills/`` folder.
"""

from __future__ import annotations

import json
from pathlib import Path

import manifests
import pytest
from manifests import (
    INDEX_RELPATH,
    JSON_INDENT,
    MARKETPLACE_NAME,
    MARKETPLACE_OWNER,
    MARKETPLACE_RELPATH,
    PLUGIN_JSON_RELPATH,
    PLUGIN_SOURCE,
    ManifestError,
    build_index,
    build_marketplace,
    check_manifests,
    collect_skill_files,
    main,
    manifest_targets,
    read_project_version,
    render,
    write_manifests,
)

DEFAULT_VERSION = "1.0.0"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def write_version(repo_root: Path, content: str = DEFAULT_VERSION + "\n") -> None:
    """Write the top-level ``VERSION`` file used by ``build_marketplace``."""
    (repo_root / "VERSION").write_text(content, encoding="utf-8")


def write_skill(
    repo_root: Path,
    name: str,
    *,
    version: str | None = DEFAULT_VERSION,
    extra_files: dict[str, str] | None = None,
    raw_content: str | None = None,
) -> Path:
    """Materialise a minimal skill folder under ``repo_root/skills``.

    Unlike ``validate.py``'s fixtures, ``manifests.py`` only needs a name and
    a ``metadata.version`` to build a manifest entry, so the ``SKILL.md``
    produced here carries no description, license or body conventions.

    Args:
        repo_root: The synthetic repository root.
        name: Skill name, used for both the frontmatter and the folder name.
        version: ``metadata.version`` value. ``None`` omits the whole
            ``metadata`` mapping, simulating a skill with no declared version.
        extra_files: Mapping of skill-relative path to file content, written
            in addition to ``SKILL.md``.
        raw_content: When given, used verbatim as ``SKILL.md``'s content,
            bypassing ``version`` entirely (used for malformed-frontmatter
            scenarios).

    Returns:
        The skill directory path.
    """
    skill_dir = repo_root / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    extra_files = extra_files if extra_files is not None else {}

    if raw_content is not None:
        content = raw_content
    else:
        lines = ["---", f"name: {name}"]
        if version is not None:
            lines.append("metadata:")
            lines.append(f'  version: "{version}"')
        lines.append("---")
        lines.append("")
        lines.append(f"# {name}")
        lines.append("")
        content = "\n".join(lines) + "\n"
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")

    for rel_path, file_content in extra_files.items():
        target = skill_dir / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(file_content, encoding="utf-8")

    return skill_dir


def build_repo(tmp_path: Path, skill_names: tuple[str, ...] = ("sample-skill",)) -> Path:
    """Build a minimal, fully valid synthetic repository.

    Returns:
        The repository root, holding a ``VERSION`` file and one skill folder
        per name in ``skill_names``, each with an ``agents/openai.yaml``
        companion file so ``collect_skill_files`` has more than one entry.
    """
    write_version(tmp_path)
    for name in skill_names:
        write_skill(
            tmp_path,
            name,
            extra_files={"agents/openai.yaml": "interface:\n  display_name: x\n"},
        )
    return tmp_path


# --------------------------------------------------------------------------- #
# read_project_version
# --------------------------------------------------------------------------- #


def test_read_project_version_returns_declared_version(tmp_path: Path) -> None:
    """A ``VERSION`` file holding ``x.y.z`` is returned verbatim."""
    write_version(tmp_path, "1.2.3\n")
    assert read_project_version(tmp_path) == "1.2.3"


def test_read_project_version_strips_surrounding_whitespace(tmp_path: Path) -> None:
    """Leading/trailing whitespace and a trailing newline are stripped."""
    write_version(tmp_path, "  1.2.3  \n\n")
    assert read_project_version(tmp_path) == "1.2.3"


def test_read_project_version_missing_file_is_error(tmp_path: Path) -> None:
    """A repository with no ``VERSION`` file raises ManifestError."""
    with pytest.raises(ManifestError):
        read_project_version(tmp_path)


def test_read_project_version_empty_file_is_error(tmp_path: Path) -> None:
    """An empty ``VERSION`` file raises ManifestError."""
    write_version(tmp_path, "")
    with pytest.raises(ManifestError):
        read_project_version(tmp_path)


def test_read_project_version_blank_file_is_error(tmp_path: Path) -> None:
    """A ``VERSION`` file holding only whitespace raises ManifestError."""
    write_version(tmp_path, "   \n\n  \n")
    with pytest.raises(ManifestError):
        read_project_version(tmp_path)


def test_read_project_version_multiple_non_blank_lines_is_error(tmp_path: Path) -> None:
    """Two non-empty lines cannot be a single unambiguous version."""
    write_version(tmp_path, "1.0.0\n2.0.0\n")
    with pytest.raises(ManifestError):
        read_project_version(tmp_path)


@pytest.mark.parametrize("bad_version", ["1.0", "v1.0.0", "1.0.0-rc1", "one.zero.zero"])
def test_read_project_version_malformed_is_error(tmp_path: Path, bad_version: str) -> None:
    """A version string that does not look like ``x.y.z`` raises ManifestError."""
    write_version(tmp_path, f"{bad_version}\n")
    with pytest.raises(ManifestError):
        read_project_version(tmp_path)


# --------------------------------------------------------------------------- #
# collect_skill_files
# --------------------------------------------------------------------------- #


def test_collect_skill_files_recurses_into_subdirectories(tmp_path: Path) -> None:
    """Files nested under subdirectories are collected, not just top-level ones."""
    skill_dir = write_skill(
        tmp_path,
        "sample-skill",
        extra_files={"agents/openai.yaml": "x: 1\n", "docs/notes/detail.md": "detail\n"},
    )
    files = collect_skill_files(skill_dir)
    assert "agents/openai.yaml" in files
    assert "docs/notes/detail.md" in files


def test_collect_skill_files_uses_slash_separated_relative_paths(tmp_path: Path) -> None:
    """Every returned path is relative to the skill folder and slash-separated."""
    skill_dir = write_skill(tmp_path, "sample-skill", extra_files={"a/b/c.txt": "x\n"})
    files = collect_skill_files(skill_dir)
    assert all("\\" not in f for f in files)
    assert "a/b/c.txt" in files
    assert "SKILL.md" in files


def test_collect_skill_files_is_sorted(tmp_path: Path) -> None:
    """The returned list is sorted regardless of filesystem creation order."""
    skill_dir = write_skill(
        tmp_path,
        "sample-skill",
        extra_files={"z_last.txt": "z\n", "a_first.txt": "a\n", "m_mid/n.txt": "n\n"},
    )
    files = collect_skill_files(skill_dir)
    assert files == sorted(files)


def test_collect_skill_files_excludes_hidden_files(tmp_path: Path) -> None:
    """Top-level dotfiles are not distributable and are excluded."""
    skill_dir = write_skill(tmp_path, "sample-skill", extra_files={".DS_Store": "junk"})
    files = collect_skill_files(skill_dir)
    assert ".DS_Store" not in files


def test_collect_skill_files_excludes_hidden_directories(tmp_path: Path) -> None:
    """Files under a dot-prefixed directory are excluded entirely."""
    skill_dir = write_skill(tmp_path, "sample-skill", extra_files={".git/config": "junk"})
    files = collect_skill_files(skill_dir)
    assert all(not f.startswith(".git/") for f in files)
    assert ".git/config" not in files


def test_collect_skill_files_excludes_pycache(tmp_path: Path) -> None:
    """Files under any ``__pycache__`` directory are local artefacts, excluded."""
    skill_dir = write_skill(
        tmp_path, "sample-skill", extra_files={"scripts/__pycache__/cache.pyc": "junk"}
    )
    files = collect_skill_files(skill_dir)
    assert all("__pycache__" not in f for f in files)


def test_collect_skill_files_no_distributable_file_is_error(tmp_path: Path) -> None:
    """A folder holding only hidden files and ``__pycache__`` output is an error."""
    skill_dir = tmp_path / "skills" / "empty-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / ".hidden").write_text("junk", encoding="utf-8")
    pycache_dir = skill_dir / "__pycache__"
    pycache_dir.mkdir()
    (pycache_dir / "cache.pyc").write_text("junk", encoding="utf-8")
    with pytest.raises(ManifestError):
        collect_skill_files(skill_dir)


# --------------------------------------------------------------------------- #
# build_index
# --------------------------------------------------------------------------- #


def test_build_index_orders_skills_by_name(tmp_path: Path) -> None:
    """Skills appear sorted by name regardless of on-disk creation order."""
    build_repo(tmp_path, skill_names=("zebra-skill", "alpha-skill", "mid-skill"))
    document = build_index(tmp_path)
    names = [entry["name"] for entry in document["skills"]]
    assert names == sorted(names)
    assert names == ["alpha-skill", "mid-skill", "zebra-skill"]


def test_build_index_entry_has_name_version_and_files(tmp_path: Path) -> None:
    """Each entry exposes exactly the fields a downloader needs."""
    build_repo(tmp_path, skill_names=("sample-skill",))
    document = build_index(tmp_path)
    entry = document["skills"][0]
    assert entry["name"] == "sample-skill"
    assert entry["version"] == DEFAULT_VERSION
    assert "SKILL.md" in entry["files"]


def test_build_index_files_match_collect_skill_files(tmp_path: Path) -> None:
    """The ``files`` list agrees with :func:`collect_skill_files` for the skill."""
    skill_dir = write_skill(tmp_path, "sample-skill", extra_files={"agents/openai.yaml": "x: 1\n"})
    write_version(tmp_path)
    document = build_index(tmp_path)
    entry = document["skills"][0]
    assert entry["files"] == collect_skill_files(skill_dir)


def test_build_index_top_level_key_is_only_skills(tmp_path: Path) -> None:
    """The document has no key besides ``skills``."""
    build_repo(tmp_path)
    document = build_index(tmp_path)
    assert list(document.keys()) == ["skills"]


def test_build_index_missing_version_is_error(tmp_path: Path) -> None:
    """A skill with no ``metadata.version`` cannot produce a manifest entry."""
    write_skill(tmp_path, "sample-skill", version=None)
    with pytest.raises(ManifestError):
        build_index(tmp_path)


def test_build_index_broken_skill_md_is_error(tmp_path: Path) -> None:
    """A ``SKILL.md`` with no frontmatter block cannot be parsed."""
    write_skill(tmp_path, "sample-skill", raw_content="# no frontmatter here\n")
    with pytest.raises(ManifestError):
        build_index(tmp_path)


def test_build_index_no_skills_directory_is_empty_list(tmp_path: Path) -> None:
    """A repository without a ``skills/`` directory yields an empty manifest."""
    assert build_index(tmp_path) == {"skills": []}


def test_build_index_empty_skills_directory_is_empty_list(tmp_path: Path) -> None:
    """A ``skills/`` directory with no skill folders yields an empty manifest."""
    (tmp_path / "skills").mkdir()
    assert build_index(tmp_path) == {"skills": []}


# --------------------------------------------------------------------------- #
# build_marketplace
# --------------------------------------------------------------------------- #


def test_build_marketplace_has_name_owner_and_plugins(tmp_path: Path) -> None:
    """The document exposes the marketplace identity fields."""
    write_version(tmp_path, "2.5.0\n")
    document = build_marketplace(tmp_path)
    assert document["name"] == MARKETPLACE_NAME
    assert isinstance(document["plugins"], list)


def test_build_marketplace_owner_is_a_mapping_carrying_the_name(tmp_path: Path) -> None:
    """``owner`` is an object, not a bare string.

    The marketplace schema requires a mapping whose ``name`` is mandatory. A
    string here parses as JSON but is the wrong shape, so the check is on the
    structure rather than only on the value being present.
    """
    write_version(tmp_path, "2.5.0\n")
    owner = build_marketplace(tmp_path)["owner"]
    assert isinstance(owner, dict)
    assert owner["name"] == MARKETPLACE_OWNER


def test_build_marketplace_has_exactly_one_plugin_entry(tmp_path: Path) -> None:
    """This repository ships a single plugin bundling every skill."""
    write_version(tmp_path, "2.5.0\n")
    document = build_marketplace(tmp_path)
    assert len(document["plugins"]) == 1


def test_build_marketplace_plugin_source_is_marketplace_root(tmp_path: Path) -> None:
    """The plugin's source is the marketplace root, so it scans ``skills/``."""
    write_version(tmp_path, "2.5.0\n")
    document = build_marketplace(tmp_path)
    assert document["plugins"][0]["source"] == PLUGIN_SOURCE

    # This value must actually be the documented constant, not an accidental
    # coincidence with some other default.
    assert PLUGIN_SOURCE == "./"


def test_build_marketplace_plugin_entry_has_no_skills_key(tmp_path: Path) -> None:
    """The entry must not list ``skills`` explicitly, or it replaces the scan."""
    write_version(tmp_path, "2.5.0\n")
    document = build_marketplace(tmp_path)
    assert "skills" not in document["plugins"][0]
    assert "skills" not in document


def test_build_marketplace_plugin_version_matches_version_file(tmp_path: Path) -> None:
    """The plugin's version tracks the project version, not a skill's version."""
    write_version(tmp_path, "3.4.5\n")
    document = build_marketplace(tmp_path)
    assert document["plugins"][0]["version"] == "3.4.5"


def test_build_marketplace_missing_version_file_is_error(tmp_path: Path) -> None:
    """Without ``VERSION`` the marketplace entry has no version to publish."""
    with pytest.raises(ManifestError):
        build_marketplace(tmp_path)


# --------------------------------------------------------------------------- #
# render
# --------------------------------------------------------------------------- #


def test_render_uses_two_space_indent() -> None:
    """Nested structures are indented by two spaces per level."""
    text = render({"a": [1, 2]})
    assert "\n" + " " * JSON_INDENT + '"a"' in text
    assert "\n" + " " * (JSON_INDENT * 2) + "1" in text


def test_render_ends_with_a_single_trailing_newline() -> None:
    """The output ends with exactly one newline, suitable for a text file."""
    text = render({"a": 1})
    assert text.endswith("\n")
    assert not text.endswith("\n\n")


def test_render_keeps_non_ascii_characters_verbatim() -> None:
    """Non-ASCII text is not escaped as ``\\uXXXX``."""
    text = render({"a": "あいうえお"})
    assert "あいうえお" in text
    assert "\\u" not in text


def test_render_preserves_key_insertion_order() -> None:
    """Keys appear in the order the mapping defines, not sorted alphabetically."""
    document = {"zeta": 1, "alpha": 2}
    text = render(document)
    assert text.index('"zeta"') < text.index('"alpha"')


def test_render_round_trips_through_json() -> None:
    """Parsing the rendered text back reproduces the original document."""
    document = {"skills": [{"name": "sample-skill", "version": "1.0.0", "files": ["SKILL.md"]}]}
    assert json.loads(render(document)) == document


# --------------------------------------------------------------------------- #
# manifest_targets
# --------------------------------------------------------------------------- #


def test_manifest_targets_returns_exactly_two_entries(tmp_path: Path) -> None:
    """Only the two generated manifests are targets, nothing else."""
    build_repo(tmp_path)
    targets = manifest_targets(tmp_path)
    assert len(targets) == 2


def test_manifest_targets_paths_point_to_the_two_manifests(tmp_path: Path) -> None:
    """The two paths are the OpenCode index and the Claude Code marketplace file."""
    build_repo(tmp_path)
    targets = manifest_targets(tmp_path)
    rel_paths = {path.relative_to(tmp_path) for path, _ in targets}
    assert rel_paths == {INDEX_RELPATH, MARKETPLACE_RELPATH}


def test_manifest_targets_text_matches_render_of_build_functions(tmp_path: Path) -> None:
    """Each target's text is exactly ``render`` of the matching ``build_*`` call."""
    build_repo(tmp_path)
    targets = {path.relative_to(tmp_path): text for path, text in manifest_targets(tmp_path)}
    assert targets[INDEX_RELPATH] == render(build_index(tmp_path))
    assert targets[MARKETPLACE_RELPATH] == render(build_marketplace(tmp_path))


# --------------------------------------------------------------------------- #
# write_manifests
# --------------------------------------------------------------------------- #


def test_write_manifests_creates_claude_plugin_directory(tmp_path: Path) -> None:
    """The ``.claude-plugin/`` directory is created if it does not exist yet."""
    build_repo(tmp_path)
    assert not (tmp_path / ".claude-plugin").exists()
    write_manifests(tmp_path)
    assert (tmp_path / MARKETPLACE_RELPATH).is_file()


def test_write_manifests_content_matches_manifest_targets(tmp_path: Path) -> None:
    """The bytes written to disk are exactly what ``manifest_targets`` expects."""
    build_repo(tmp_path)
    expected = dict(manifest_targets(tmp_path))
    write_manifests(tmp_path)
    for path, text in expected.items():
        assert path.read_text(encoding="utf-8") == text


def test_write_manifests_is_idempotent(tmp_path: Path) -> None:
    """Writing twice in a row leaves the files byte-identical."""
    build_repo(tmp_path)
    write_manifests(tmp_path)
    first_pass = {path: path.read_text(encoding="utf-8") for path, _ in manifest_targets(tmp_path)}
    write_manifests(tmp_path)
    second_pass = {path: path.read_text(encoding="utf-8") for path, _ in manifest_targets(tmp_path)}
    assert first_pass == second_pass


def test_write_manifests_returns_the_written_paths(tmp_path: Path) -> None:
    """The return value tells the caller exactly what was written."""
    build_repo(tmp_path)
    expected_paths = {path for path, _ in manifest_targets(tmp_path)}
    written = write_manifests(tmp_path)
    assert set(written) == expected_paths


# --------------------------------------------------------------------------- #
# check_manifests
# --------------------------------------------------------------------------- #


def test_check_manifests_freshly_generated_repository_has_no_mismatch(
    tmp_path: Path,
) -> None:
    """Right after ``write_manifests``, the committed files match exactly."""
    build_repo(tmp_path)
    write_manifests(tmp_path)
    assert check_manifests(tmp_path) == []


def test_check_manifests_detects_a_modified_manifest(tmp_path: Path) -> None:
    """A one-byte edit to a committed manifest is reported against that path."""
    build_repo(tmp_path)
    write_manifests(tmp_path)
    index_path = tmp_path / INDEX_RELPATH
    index_path.write_text(index_path.read_text(encoding="utf-8") + " ", encoding="utf-8")

    mismatches = check_manifests(tmp_path)
    assert len(mismatches) == 1
    assert mismatches[0].rel_path == str(INDEX_RELPATH)


def test_check_manifests_detects_changed_line_endings(tmp_path: Path) -> None:
    """A manifest rewritten with CRLF is stale even though its text is equal.

    The agents fetch these files verbatim, so the committed bytes are what
    matters; a text-mode comparison would translate the line endings away and
    accept the file.
    """
    build_repo(tmp_path)
    write_manifests(tmp_path)
    index_path = tmp_path / INDEX_RELPATH
    index_path.write_bytes(index_path.read_bytes().replace(b"\n", b"\r\n"))

    mismatches = check_manifests(tmp_path)
    assert any(m.rel_path == str(INDEX_RELPATH) for m in mismatches)


def test_check_manifests_detects_a_missing_manifest_file(tmp_path: Path) -> None:
    """A committed manifest that was deleted is reported, not silently skipped."""
    build_repo(tmp_path)
    write_manifests(tmp_path)
    (tmp_path / MARKETPLACE_RELPATH).unlink()

    mismatches = check_manifests(tmp_path)
    assert any(m.rel_path == str(MARKETPLACE_RELPATH) for m in mismatches)


def test_check_manifests_detects_a_committed_plugin_json(tmp_path: Path) -> None:
    """``.claude-plugin/plugin.json`` must not exist, even if everything else matches."""
    build_repo(tmp_path)
    write_manifests(tmp_path)
    plugin_json_path = tmp_path / PLUGIN_JSON_RELPATH
    plugin_json_path.parent.mkdir(parents=True, exist_ok=True)
    plugin_json_path.write_text('{"version": "0.0.1"}\n', encoding="utf-8")

    mismatches = check_manifests(tmp_path)
    assert any(m.rel_path == str(PLUGIN_JSON_RELPATH) for m in mismatches)


def test_check_manifests_detects_an_unregenerated_new_file(tmp_path: Path) -> None:
    """Adding a file to a skill without regenerating is a stale-manifest mismatch."""
    skill_dir = write_skill(tmp_path, "sample-skill")
    write_version(tmp_path)
    write_manifests(tmp_path)

    (skill_dir / "new_asset.txt").write_text("new\n", encoding="utf-8")

    mismatches = check_manifests(tmp_path)
    assert any(m.rel_path == str(INDEX_RELPATH) for m in mismatches)


def test_check_manifests_order_is_stable_and_by_manifest_path(tmp_path: Path) -> None:
    """When both manifests are stale, mismatches keep a fixed, repeatable order.

    ``manifest_targets`` is the function that enumerates "the manifest paths",
    so its order is used here as the reference for "manifest path order"
    rather than guessing which of the two paths sorts first.
    """
    build_repo(tmp_path)
    write_manifests(tmp_path)
    expected_order = [str(path.relative_to(tmp_path)) for path, _ in manifest_targets(tmp_path)]
    (tmp_path / INDEX_RELPATH).write_text("broken", encoding="utf-8")
    (tmp_path / MARKETPLACE_RELPATH).write_text("broken", encoding="utf-8")

    first = check_manifests(tmp_path)
    second = check_manifests(tmp_path)
    assert [m.rel_path for m in first] == [m.rel_path for m in second]
    assert [m.rel_path for m in first] == expected_order


# --------------------------------------------------------------------------- #
# main
#
# ``main`` resolves the repository root from its own module ``__file__``
# (mirroring ``validate.main``), so these tests point it at a synthetic
# repository by monkeypatching ``manifests.__file__`` rather than by passing
# a root explicitly, keeping the test independent of that implementation
# detail beyond the documented ``tools/manifests.py`` layout.
# --------------------------------------------------------------------------- #


def _point_main_at(monkeypatch: pytest.MonkeyPatch, repo_root: Path) -> None:
    """Make ``manifests.main`` treat ``repo_root`` as the repository root."""
    monkeypatch.setattr(manifests, "__file__", str(repo_root / "tools" / "manifests.py"))


def test_main_write_returns_zero_and_writes_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--write`` regenerates the manifests and reports success."""
    build_repo(tmp_path)
    _point_main_at(monkeypatch, tmp_path)

    exit_code = main(["--write"])

    assert exit_code == 0
    assert (tmp_path / INDEX_RELPATH).is_file()
    assert (tmp_path / MARKETPLACE_RELPATH).is_file()


def test_main_check_passes_right_after_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--check`` succeeds against manifests that were just regenerated."""
    build_repo(tmp_path)
    _point_main_at(monkeypatch, tmp_path)
    main(["--write"])

    assert main(["--check"]) == 0


def test_main_check_fails_after_manifest_is_broken(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--check`` reports failure once a committed manifest goes stale."""
    build_repo(tmp_path)
    _point_main_at(monkeypatch, tmp_path)
    main(["--write"])
    (tmp_path / INDEX_RELPATH).write_text("broken", encoding="utf-8")

    assert main(["--check"]) == 1


def test_main_default_behaves_like_check_and_does_not_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No arguments means ``--check``: a stale manifest fails and is left untouched."""
    build_repo(tmp_path)
    _point_main_at(monkeypatch, tmp_path)
    main(["--write"])
    (tmp_path / INDEX_RELPATH).write_text("broken", encoding="utf-8")

    exit_code = main([])

    assert exit_code == 1
    assert (tmp_path / INDEX_RELPATH).read_text(encoding="utf-8") == "broken"
