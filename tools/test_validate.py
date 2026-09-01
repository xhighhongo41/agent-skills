"""Tests for ``tools/validate.py``.

Stage 2 of the project's TDD cycle: these tests are written against the
*intended* behaviour of ``validate.py`` before the check bodies are
implemented (they currently all raise ``NotImplementedError``). Every test
below builds a synthetic skill folder under ``tmp_path`` and drives it
through :func:`validate.validate_skill`, so no test touches the network,
the user's home directory or this repository's real ``skills/`` folder.

Each test name embeds the check identifier (``V00``..``V16``) it protects,
per the convention documented in ``validate.py``. For every check there is
at least one "valid" test (a fully compliant skill does not trigger the
check) and at least one "violation" test (breaking exactly one aspect of an
otherwise compliant skill triggers the check).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import manifests
import pytest
import validate
from validate import (
    MAX_BODY_LINES,
    MAX_DESCRIPTION_CHARS,
    MAX_NAME_CHARS,
    MAX_SHORT_DESCRIPTION_CHARS,
    Violation,
    check_manifest_drift,
    check_marketplace_shape,
    validate_repository,
    validate_skill,
)

DEFAULT_NAME = "sample-skill"
DEFAULT_VERSION = "1.0.0"
DEFAULT_DESCRIPTION = "サンプルスキルの検証用説明。"

#: Project version written to ``VERSION`` by the manifest fixtures. Kept
#: different from :data:`DEFAULT_VERSION` so that a test comparing the plugin
#: entry against ``VERSION`` cannot pass by accidentally matching a skill version.
DEFAULT_PROJECT_VERSION = "0.1.0"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _check_ids(violations: list[Violation]) -> set[str]:
    """Return the set of check identifiers present in ``violations``."""
    return {violation.check for violation in violations}


def _harness_description(*display_names: str) -> str:
    """Return a description opening with the preamble that V16 requires.

    Args:
        *display_names: Harness display names, in the order the skill's
            ``compatibility`` value lists the matching keys.

    Returns:
        ``"<A / B> 専用。"`` followed by the default description text.
    """
    return f"{' / '.join(display_names)} 専用。{DEFAULT_DESCRIPTION}"


def _build_frontmatter(
    *,
    name: str | None,
    description: str | None,
    quote_description: bool,
    license_value: str | None,
    compatibility: str | None,
    compatibility_raw: str | None,
    version: str | None,
    quote_version: bool,
    metadata_raw: str | None,
    extra_lines: Sequence[str],
) -> str:
    """Assemble a ``SKILL.md`` frontmatter block, including the ``---`` markers."""
    lines = ["---"]
    if name is not None:
        lines.append(f"name: {name}")
    if description is not None:
        if quote_description:
            lines.append(f'description: "{description}"')
        else:
            lines.append(f"description: {description}")
    if license_value is not None:
        lines.append(f"license: {license_value}")
    if compatibility_raw is not None:
        lines.append(compatibility_raw)
    elif compatibility is not None:
        lines.append(f'compatibility: "{compatibility}"')
    if metadata_raw is not None:
        lines.append(metadata_raw)
    elif version is not None:
        lines.append("metadata:")
        if quote_version:
            lines.append(f'  version: "{version}"')
        else:
            lines.append(f"  version: {version}")
    lines.extend(extra_lines)
    lines.append("---")
    return "\n".join(lines) + "\n"


def _build_body(
    *,
    name: str,
    version: str | None,
    marker_version: str | None,
    marker_count: int,
    declare_usage: bool,
    declare_version: str | None,
    denylist_word: str | None,
    extra_lines: Sequence[str],
) -> str:
    """Assemble a skill body with a version marker and a usage declaration."""
    lines = [f"# {name}: サンプルスキル", ""]
    marker_value = marker_version if marker_version is not None else version
    for _ in range(marker_count):
        lines.append(f"> **skill version**: {marker_value}")
        lines.append("")
    if declare_usage:
        decl_value = declare_version if declare_version is not None else version
        lines.append("セッション内で初めて本スキルを使用するときは")
        lines.append(f"「{name} スキル v{decl_value} を使用します」と宣言する。")
        lines.append("")
    lines.append("## 手順")
    lines.append("")
    lines.append("本文がここに続く。")
    if denylist_word is not None:
        lines.append("")
        lines.append(denylist_word)
    lines.extend(extra_lines)
    return "\n".join(lines) + "\n"


def _default_openai_yaml(name: str) -> str:
    """Return a well-formed ``agents/openai.yaml`` body for ``name``."""
    return (
        "interface:\n"
        '  display_name: "サンプル"\n'
        '  short_description: "検証用の短い説明"\n'
        f'  default_prompt: "${name} を使ってください。"\n'
    )


def write_skill(
    tmp_path: Path,
    *,
    name: str | None = DEFAULT_NAME,
    dir_name: str | None = None,
    description: str | None = DEFAULT_DESCRIPTION,
    quote_description: bool = True,
    license_value: str | None = "MIT",
    compatibility: str | None = None,
    compatibility_raw: str | None = None,
    version: str | None = DEFAULT_VERSION,
    quote_version: bool = True,
    metadata_raw: str | None = None,
    extra_frontmatter_lines: Sequence[str] = (),
    body: str | None = None,
    marker_version: str | None = None,
    marker_count: int = 1,
    declare_usage: bool = True,
    declare_version: str | None = None,
    denylist_word: str | None = None,
    extra_body_lines: Sequence[str] = (),
    raw_content: str | None = None,
    write_openai_yaml: bool = True,
    openai_yaml_content: str | None = None,
) -> Path:
    """Materialise one skill folder under ``tmp_path/skills`` for a test.

    Every keyword argument defaults to a value that produces a fully
    compliant skill (see the module-level ``DEFAULT_*`` constants). Callers
    should override exactly the aspect under test, so that each test reads
    as "break one thing in an otherwise valid skill".

    Args:
        tmp_path: Pytest's per-test temporary directory.
        name: Frontmatter ``name`` value. ``None`` omits the key entirely.
        dir_name: Folder name to use, defaulting to ``name`` (or
            :data:`DEFAULT_NAME` when ``name`` is ``None``). Set this to a
            different value to simulate a name/folder mismatch.
        description: Frontmatter ``description`` value. ``None`` omits it.
        quote_description: Whether the raw source line double-quotes the
            description value.
        license_value: Frontmatter ``license`` value. ``None`` omits it.
        compatibility: Frontmatter ``compatibility`` value, written as a
            double-quoted string. ``None`` omits the key, which is what a
            skill common to every harness looks like; the defaults therefore
            keep producing exactly the frontmatter they produced before this
            argument existed.
        compatibility_raw: When given, used verbatim as the ``compatibility``
            line(s) instead of the quoted string (for example to make the
            value a YAML list rather than a string).
        version: ``metadata.version`` value, and the default source for the
            body's version marker and usage declaration. ``None`` omits the
            ``metadata`` key entirely.
        quote_version: Whether the raw source quotes the version value.
        metadata_raw: When given, used verbatim as the ``metadata`` line(s)
            instead of the usual nested mapping (for example to make
            ``metadata`` a plain string rather than a mapping).
        extra_frontmatter_lines: Additional raw lines appended to the
            frontmatter block, useful for injecting keys outside the
            standard set.
        body: Full body text override. When given, all other body-shaping
            arguments below are ignored.
        marker_version: Version embedded in the body's version marker line,
            defaulting to ``version``. Use this to simulate a mismatch.
        marker_count: Number of version marker lines to emit.
        declare_usage: Whether to emit the usage-declaration sentence.
        declare_version: Version embedded in the usage declaration,
            defaulting to ``version``. Use this to simulate a mismatch.
        denylist_word: Extra line appended to the body, typically a string
            drawn from (or excluded from) the denylist.
        extra_body_lines: Additional raw lines appended to the body.
        raw_content: When given, used verbatim as the whole ``SKILL.md``
            content, bypassing every other frontmatter/body argument. Used
            for malformed-frontmatter scenarios that the structured
            arguments cannot express.
        write_openai_yaml: Whether to write ``agents/openai.yaml`` at all.
        openai_yaml_content: Raw content for ``agents/openai.yaml``,
            defaulting to a well-formed stub referencing ``name``.

    Returns:
        The skill directory path, suitable for :func:`validate.validate_skill`.
    """
    effective_name = name if name is not None else DEFAULT_NAME
    skill_dir = tmp_path / "skills" / (dir_name if dir_name is not None else effective_name)
    skill_dir.mkdir(parents=True, exist_ok=True)

    if raw_content is not None:
        content = raw_content
    else:
        frontmatter = _build_frontmatter(
            name=name,
            description=description,
            quote_description=quote_description,
            license_value=license_value,
            compatibility=compatibility,
            compatibility_raw=compatibility_raw,
            version=version,
            quote_version=quote_version,
            metadata_raw=metadata_raw,
            extra_lines=extra_frontmatter_lines,
        )
        body_text = (
            body
            if body is not None
            else _build_body(
                name=effective_name,
                version=version,
                marker_version=marker_version,
                marker_count=marker_count,
                declare_usage=declare_usage,
                declare_version=declare_version,
                denylist_word=denylist_word,
                extra_lines=extra_body_lines,
            )
        )
        content = frontmatter + "\n" + body_text

    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")

    if write_openai_yaml:
        agents_dir = skill_dir / "agents"
        agents_dir.mkdir(parents=True, exist_ok=True)
        yaml_text = (
            openai_yaml_content
            if openai_yaml_content is not None
            else _default_openai_yaml(effective_name)
        )
        (agents_dir / "openai.yaml").write_text(yaml_text, encoding="utf-8")

    return skill_dir


# --------------------------------------------------------------------------- #
# V01: SKILL.md exists
# --------------------------------------------------------------------------- #


def test_v01_skill_md_present_is_valid(tmp_path: Path) -> None:
    """A skill folder that has a ``SKILL.md`` does not trigger V01."""
    skill_dir = write_skill(tmp_path)
    violations = validate_skill(skill_dir, tmp_path)
    assert "V01" not in _check_ids(violations)


def test_v01_skill_md_missing_is_violation(tmp_path: Path) -> None:
    """An empty skill folder (no ``SKILL.md``) is reported as V01."""
    skill_dir = tmp_path / "skills" / DEFAULT_NAME
    skill_dir.mkdir(parents=True)
    violations = validate_skill(skill_dir, tmp_path)
    assert "V01" in _check_ids(violations)


# --------------------------------------------------------------------------- #
# V02: frontmatter starts with "---" and parses to a mapping
# --------------------------------------------------------------------------- #


def test_v02_frontmatter_parses_as_mapping_is_valid(tmp_path: Path) -> None:
    """A well-formed frontmatter block does not trigger V02."""
    skill_dir = write_skill(tmp_path)
    violations = validate_skill(skill_dir, tmp_path)
    assert "V02" not in _check_ids(violations)


def test_v02_missing_leading_marker_is_violation(tmp_path: Path) -> None:
    """A file whose first line is not ``---`` has no recognised frontmatter."""
    skill_dir = write_skill(
        tmp_path,
        raw_content="# タイトル\n\n本文だけのファイルで、フロントマターがない。\n",
    )
    violations = validate_skill(skill_dir, tmp_path)
    assert "V02" in _check_ids(violations)


def test_v02_malformed_yaml_is_violation(tmp_path: Path) -> None:
    """A frontmatter block that is not valid YAML is reported as V02."""
    skill_dir = write_skill(
        tmp_path,
        raw_content="---\nname: [unclosed\n---\n\n# 本文\n",
    )
    violations = validate_skill(skill_dir, tmp_path)
    assert "V02" in _check_ids(violations)


def test_v02_frontmatter_not_a_mapping_is_violation(tmp_path: Path) -> None:
    """A frontmatter block that parses to a list rather than a mapping is V02."""
    skill_dir = write_skill(
        tmp_path,
        raw_content=("---\n- name: sample-skill\n- description: not-a-mapping\n---\n\n# 本文\n"),
    )
    violations = validate_skill(skill_dir, tmp_path)
    assert "V02" in _check_ids(violations)


# --------------------------------------------------------------------------- #
# V03: frontmatter keys are a subset of ALLOWED_FRONTMATTER_KEYS
# --------------------------------------------------------------------------- #


def test_v03_frontmatter_keys_allowed_is_valid(tmp_path: Path) -> None:
    """A frontmatter using only the standard keys does not trigger V03."""
    skill_dir = write_skill(tmp_path)
    violations = validate_skill(skill_dir, tmp_path)
    assert "V03" not in _check_ids(violations)


def test_v03_unknown_top_level_key_is_violation(tmp_path: Path) -> None:
    """A stray top-level key (e.g. ``version:``) outside the standard set is V03."""
    skill_dir = write_skill(tmp_path, extra_frontmatter_lines=['version: "1.0.0"'])
    violations = validate_skill(skill_dir, tmp_path)
    assert "V03" in _check_ids(violations)


# --------------------------------------------------------------------------- #
# V04: name is required, well formed, short enough and matches the folder
# --------------------------------------------------------------------------- #


def test_v04_name_matches_directory_is_valid(tmp_path: Path) -> None:
    """A name that matches the folder and the naming pattern does not trigger V04."""
    skill_dir = write_skill(tmp_path)
    violations = validate_skill(skill_dir, tmp_path)
    assert "V04" not in _check_ids(violations)


def test_v04_name_missing_is_violation(tmp_path: Path) -> None:
    """A frontmatter without a ``name`` key is reported as V04."""
    skill_dir = write_skill(tmp_path, name=None, dir_name=DEFAULT_NAME)
    violations = validate_skill(skill_dir, tmp_path)
    assert "V04" in _check_ids(violations)


@pytest.mark.parametrize(
    "bad_name",
    ["Sample-Skill", "sample_skill", "sample--skill"],
    ids=["uppercase", "underscore", "double-hyphen"],
)
def test_v04_name_pattern_violation(tmp_path: Path, bad_name: str) -> None:
    """Names must be lowercase alphanumerics joined by single hyphens."""
    skill_dir = write_skill(tmp_path, name=bad_name, dir_name=bad_name)
    violations = validate_skill(skill_dir, tmp_path)
    assert "V04" in _check_ids(violations)


def test_v04_name_too_long_is_violation(tmp_path: Path) -> None:
    """A pattern-valid name longer than MAX_NAME_CHARS is reported as V04."""
    long_name = "a" * (MAX_NAME_CHARS + 1)
    skill_dir = write_skill(tmp_path, name=long_name, dir_name=long_name)
    violations = validate_skill(skill_dir, tmp_path)
    assert "V04" in _check_ids(violations)


def test_v04_name_mismatched_directory_is_violation(tmp_path: Path) -> None:
    """A name that does not match its parent directory is reported as V04."""
    skill_dir = write_skill(tmp_path, name="sample-skill", dir_name="different-folder")
    violations = validate_skill(skill_dir, tmp_path)
    assert "V04" in _check_ids(violations)


# --------------------------------------------------------------------------- #
# V05: description is required, within budget and double-quoted in source
# --------------------------------------------------------------------------- #


def test_v05_description_within_budget_is_valid(tmp_path: Path) -> None:
    """A short, quoted description does not trigger V05."""
    skill_dir = write_skill(tmp_path)
    violations = validate_skill(skill_dir, tmp_path)
    assert "V05" not in _check_ids(violations)


def test_v05_description_missing_is_violation(tmp_path: Path) -> None:
    """A frontmatter without a ``description`` key is reported as V05."""
    skill_dir = write_skill(tmp_path, description=None)
    violations = validate_skill(skill_dir, tmp_path)
    assert "V05" in _check_ids(violations)


def test_v05_description_empty_is_violation(tmp_path: Path) -> None:
    """An empty ``description`` string is reported as V05."""
    skill_dir = write_skill(tmp_path, description="")
    violations = validate_skill(skill_dir, tmp_path)
    assert "V05" in _check_ids(violations)


def test_v05_description_too_long_is_violation(tmp_path: Path) -> None:
    """A description longer than MAX_DESCRIPTION_CHARS is reported as V05."""
    long_description = "あ" * (MAX_DESCRIPTION_CHARS + 1)
    skill_dir = write_skill(tmp_path, description=long_description)
    violations = validate_skill(skill_dir, tmp_path)
    assert "V05" in _check_ids(violations)


def test_v05_description_not_quoted_in_source_is_violation(tmp_path: Path) -> None:
    """A description whose raw source line is unquoted is reported as V05."""
    skill_dir = write_skill(tmp_path, quote_description=False)
    violations = validate_skill(skill_dir, tmp_path)
    assert "V05" in _check_ids(violations)


# --------------------------------------------------------------------------- #
# V06: license must be MIT
# --------------------------------------------------------------------------- #


def test_v06_license_mit_is_valid(tmp_path: Path) -> None:
    """``license: MIT`` does not trigger V06."""
    skill_dir = write_skill(tmp_path)
    violations = validate_skill(skill_dir, tmp_path)
    assert "V06" not in _check_ids(violations)


def test_v06_license_missing_is_violation(tmp_path: Path) -> None:
    """A frontmatter without a ``license`` key is reported as V06."""
    skill_dir = write_skill(tmp_path, license_value=None)
    violations = validate_skill(skill_dir, tmp_path)
    assert "V06" in _check_ids(violations)


def test_v06_license_wrong_value_is_violation(tmp_path: Path) -> None:
    """A license other than MIT is reported as V06."""
    skill_dir = write_skill(tmp_path, license_value="Apache-2.0")
    violations = validate_skill(skill_dir, tmp_path)
    assert "V06" in _check_ids(violations)


# --------------------------------------------------------------------------- #
# V07: metadata.version is required, a string, and matches VERSION_PATTERN
# --------------------------------------------------------------------------- #


def test_v07_version_valid(tmp_path: Path) -> None:
    """A quoted semver string in ``metadata.version`` does not trigger V07."""
    skill_dir = write_skill(tmp_path)
    violations = validate_skill(skill_dir, tmp_path)
    assert "V07" not in _check_ids(violations)


def test_v07_metadata_missing_is_violation(tmp_path: Path) -> None:
    """A frontmatter without a ``metadata`` key is reported as V07."""
    skill_dir = write_skill(tmp_path, version=None, marker_count=0, declare_usage=False)
    violations = validate_skill(skill_dir, tmp_path)
    assert "V07" in _check_ids(violations)


def test_v07_metadata_not_a_mapping_is_violation(tmp_path: Path) -> None:
    """A ``metadata`` value that is not a mapping is reported as V07."""
    skill_dir = write_skill(tmp_path, metadata_raw='metadata: "1.0.0"', version=DEFAULT_VERSION)
    violations = validate_skill(skill_dir, tmp_path)
    assert "V07" in _check_ids(violations)


def test_v07_version_not_a_string_is_violation(tmp_path: Path) -> None:
    """An unquoted version (parsed as a float by YAML) is reported as V07."""
    skill_dir = write_skill(tmp_path, version="1.0", quote_version=False)
    violations = validate_skill(skill_dir, tmp_path)
    assert "V07" in _check_ids(violations)


def test_v07_version_pattern_mismatch_is_violation(tmp_path: Path) -> None:
    """A quoted version string that is not ``x.y.z`` is reported as V07."""
    skill_dir = write_skill(tmp_path, version="1.0", quote_version=True)
    violations = validate_skill(skill_dir, tmp_path)
    assert "V07" in _check_ids(violations)


# --------------------------------------------------------------------------- #
# V08: body stays within MAX_BODY_LINES
# --------------------------------------------------------------------------- #


def test_v08_body_within_line_budget_is_valid(tmp_path: Path) -> None:
    """A short body does not trigger V08."""
    skill_dir = write_skill(tmp_path)
    violations = validate_skill(skill_dir, tmp_path)
    assert "V08" not in _check_ids(violations)


def test_v08_body_exceeds_line_budget_is_violation(tmp_path: Path) -> None:
    """A body longer than MAX_BODY_LINES lines is reported as V08."""
    filler = "\n".join(f"本文行 {i}。" for i in range(MAX_BODY_LINES + 10))
    body = (
        f"> **skill version**: {DEFAULT_VERSION}\n"
        "\n"
        "セッション内で初めて本スキルを使用するときは\n"
        f"「{DEFAULT_NAME} スキル v{DEFAULT_VERSION} を使用します」と宣言する。\n"
        "\n"
        f"{filler}\n"
    )
    skill_dir = write_skill(tmp_path, body=body)
    violations = validate_skill(skill_dir, tmp_path)
    assert "V08" in _check_ids(violations)


# --------------------------------------------------------------------------- #
# V09: exactly one version marker, agreeing with metadata.version
# --------------------------------------------------------------------------- #


def test_v09_single_matching_marker_is_valid(tmp_path: Path) -> None:
    """Exactly one marker matching ``metadata.version`` does not trigger V09."""
    skill_dir = write_skill(tmp_path)
    violations = validate_skill(skill_dir, tmp_path)
    assert "V09" not in _check_ids(violations)


def test_v09_missing_marker_is_violation(tmp_path: Path) -> None:
    """A body without any version marker is reported as V09."""
    skill_dir = write_skill(tmp_path, marker_count=0)
    violations = validate_skill(skill_dir, tmp_path)
    assert "V09" in _check_ids(violations)


def test_v09_duplicate_marker_is_violation(tmp_path: Path) -> None:
    """A body with two version marker lines is reported as V09."""
    skill_dir = write_skill(tmp_path, marker_count=2)
    violations = validate_skill(skill_dir, tmp_path)
    assert "V09" in _check_ids(violations)


def test_v09_marker_version_mismatch_is_violation(tmp_path: Path) -> None:
    """A marker whose value disagrees with ``metadata.version`` is V09."""
    skill_dir = write_skill(tmp_path, marker_version="9.9.9")
    violations = validate_skill(skill_dir, tmp_path)
    assert "V09" in _check_ids(violations)


# --------------------------------------------------------------------------- #
# V10: usage declaration names the skill and its version
# --------------------------------------------------------------------------- #


def test_v10_usage_declaration_present_is_valid(tmp_path: Path) -> None:
    """A correct usage declaration does not trigger V10."""
    skill_dir = write_skill(tmp_path)
    violations = validate_skill(skill_dir, tmp_path)
    assert "V10" not in _check_ids(violations)


def test_v10_usage_declaration_missing_is_violation(tmp_path: Path) -> None:
    """A body without any usage declaration is reported as V10."""
    skill_dir = write_skill(tmp_path, declare_usage=False)
    violations = validate_skill(skill_dir, tmp_path)
    assert "V10" in _check_ids(violations)


def test_v10_usage_declaration_version_mismatch_is_violation(tmp_path: Path) -> None:
    """A declared version that disagrees with ``metadata.version`` is V10."""
    skill_dir = write_skill(tmp_path, declare_version="2.0.0")
    violations = validate_skill(skill_dir, tmp_path)
    assert "V10" in _check_ids(violations)


# --------------------------------------------------------------------------- #
# V11: no denylisted proper noun, path or product-flavoured phrase remains
# --------------------------------------------------------------------------- #


def test_v11_body_without_denylisted_terms_is_valid(tmp_path: Path) -> None:
    """A body free of denylisted terms does not trigger V11."""
    skill_dir = write_skill(tmp_path)
    violations = validate_skill(skill_dir, tmp_path)
    assert "V11" not in _check_ids(violations)


def test_v11_subagent_name_is_violation(tmp_path: Path) -> None:
    """A subagent proper name (e.g. ``code-implementer``) is reported as V11."""
    skill_dir = write_skill(tmp_path, denylist_word="code-implementer サブエージェントを呼び出す。")
    violations = validate_skill(skill_dir, tmp_path)
    assert "V11" in _check_ids(violations)


def test_v11_claude_agents_config_path_is_violation(tmp_path: Path) -> None:
    """The agent-specific config path ``.claude/agents`` is reported as V11."""
    skill_dir = write_skill(tmp_path, denylist_word=".claude/agents 配下の定義を参照する。")
    violations = validate_skill(skill_dir, tmp_path)
    assert "V11" in _check_ids(violations)


def test_v11_personal_home_path_is_violation(tmp_path: Path) -> None:
    """A personal filesystem path (``/Users/``) is reported as V11."""
    skill_dir = write_skill(tmp_path, denylist_word="/Users/example/project を参照する。")
    violations = validate_skill(skill_dir, tmp_path)
    assert "V11" in _check_ids(violations)


def test_v11_product_flavoured_declaration_is_violation(tmp_path: Path) -> None:
    """A leftover product-flavoured phrase (``OpenCode用``) is reported as V11."""
    skill_dir = write_skill(tmp_path, denylist_word="OpenCode用の指示に従う。")
    violations = validate_skill(skill_dir, tmp_path)
    assert "V11" in _check_ids(violations)


def test_v11_claude_md_and_agents_md_are_not_denylisted(tmp_path: Path) -> None:
    """Regression guard: CLAUDE.md and AGENTS.md are deliberately allowed."""
    skill_dir = write_skill(
        tmp_path,
        denylist_word="CLAUDE.md と AGENTS.md の両方を参照して整合させる。",
    )
    violations = validate_skill(skill_dir, tmp_path)
    assert "V11" not in _check_ids(violations)


def test_v11_harness_denylist_terms_are_drawn_from_the_denylist() -> None:
    """The exemptible phrases are exactly the denylisted ones, one per harness."""
    assert validate.HARNESS_DENYLIST_TERMS == {
        "claude-code": "Claude用",
        "codex": "Codex用",
        "opencode": "OpenCode用",
    }
    assert set(validate.HARNESS_DENYLIST_TERMS) == set(validate.KNOWN_HARNESSES)
    assert all(term in validate.DENYLIST for term in validate.HARNESS_DENYLIST_TERMS.values())


def test_v11_declared_harness_phrase_is_valid(tmp_path: Path) -> None:
    """A skill that declares a harness may use that harness's phrase in its body."""
    skill_dir = write_skill(
        tmp_path,
        compatibility="opencode",
        description=_harness_description("OpenCode"),
        denylist_word="OpenCode用の手順に従う。",
    )
    violations = validate_skill(skill_dir, tmp_path)
    assert "V11" not in _check_ids(violations)


def test_v11_every_declared_harness_phrase_is_valid(tmp_path: Path) -> None:
    """Declaring two harnesses exempts the phrase belonging to each of them."""
    skill_dir = write_skill(
        tmp_path,
        compatibility="codex, opencode",
        description=_harness_description("Codex", "OpenCode"),
        denylist_word="Codex用 と OpenCode用 の手順に従う。",
    )
    violations = validate_skill(skill_dir, tmp_path)
    assert "V11" not in _check_ids(violations)


def test_v11_undeclared_harness_phrase_is_violation(tmp_path: Path) -> None:
    """The exemption is per harness: an undeclared harness's phrase still fails."""
    skill_dir = write_skill(
        tmp_path,
        compatibility="opencode",
        description=_harness_description("OpenCode"),
        denylist_word="Claude用の手順に従う。",
    )
    violations = validate_skill(skill_dir, tmp_path)
    assert "V11" in _check_ids(violations)


def test_v11_harness_phrase_without_compatibility_is_violation(tmp_path: Path) -> None:
    """Regression guard: a skill declaring nothing keeps the original strict behaviour."""
    skill_dir = write_skill(tmp_path, denylist_word="OpenCode用の手順に従う。")
    violations = validate_skill(skill_dir, tmp_path)
    assert "V11" in _check_ids(violations)


def test_v11_subagent_name_with_compatibility_is_violation(tmp_path: Path) -> None:
    """``compatibility`` never exempts a subagent proper name."""
    skill_dir = write_skill(
        tmp_path,
        compatibility="opencode",
        description=_harness_description("OpenCode"),
        denylist_word="code-implementer サブエージェントを呼び出す。",
    )
    violations = validate_skill(skill_dir, tmp_path)
    assert "V11" in _check_ids(violations)


def test_v11_personal_home_path_with_compatibility_is_violation(tmp_path: Path) -> None:
    """``compatibility`` never exempts a personal filesystem path."""
    skill_dir = write_skill(
        tmp_path,
        compatibility="opencode",
        description=_harness_description("OpenCode"),
        denylist_word="/Users/example/project を参照する。",
    )
    violations = validate_skill(skill_dir, tmp_path)
    assert "V11" in _check_ids(violations)


# --------------------------------------------------------------------------- #
# V12: agents/openai.yaml exists and carries usable interface fields
# --------------------------------------------------------------------------- #


def test_v12_openai_yaml_valid(tmp_path: Path) -> None:
    """A well-formed ``agents/openai.yaml`` does not trigger V12."""
    skill_dir = write_skill(tmp_path)
    violations = validate_skill(skill_dir, tmp_path)
    assert "V12" not in _check_ids(violations)


def test_v12_openai_yaml_missing_is_violation(tmp_path: Path) -> None:
    """A skill without ``agents/openai.yaml`` is reported as V12."""
    skill_dir = write_skill(tmp_path, write_openai_yaml=False)
    violations = validate_skill(skill_dir, tmp_path)
    assert "V12" in _check_ids(violations)


def test_v12_openai_yaml_missing_interface_key_is_violation(tmp_path: Path) -> None:
    """Interface fields written outside the ``interface:`` mapping are V12."""
    yaml_text = (
        'display_name: "サンプル"\n'
        'short_description: "検証用の短い説明"\n'
        f'default_prompt: "${DEFAULT_NAME} を使ってください。"\n'
    )
    skill_dir = write_skill(tmp_path, openai_yaml_content=yaml_text)
    violations = validate_skill(skill_dir, tmp_path)
    assert "V12" in _check_ids(violations)


def test_v12_default_prompt_missing_name_token_is_violation(tmp_path: Path) -> None:
    """A ``default_prompt`` without ``$<name>`` is reported as V12."""
    yaml_text = (
        "interface:\n"
        '  display_name: "サンプル"\n'
        '  short_description: "検証用の短い説明"\n'
        '  default_prompt: "スキルを使ってください。"\n'
    )
    skill_dir = write_skill(tmp_path, openai_yaml_content=yaml_text)
    violations = validate_skill(skill_dir, tmp_path)
    assert "V12" in _check_ids(violations)


def test_v12_short_description_too_long_is_violation(tmp_path: Path) -> None:
    """A ``short_description`` longer than MAX_SHORT_DESCRIPTION_CHARS is V12."""
    long_short_description = "あ" * (MAX_SHORT_DESCRIPTION_CHARS + 1)
    yaml_text = (
        "interface:\n"
        '  display_name: "サンプル"\n'
        f'  short_description: "{long_short_description}"\n'
        f'  default_prompt: "${DEFAULT_NAME} を使ってください。"\n'
    )
    skill_dir = write_skill(tmp_path, openai_yaml_content=yaml_text)
    violations = validate_skill(skill_dir, tmp_path)
    assert "V12" in _check_ids(violations)


# --------------------------------------------------------------------------- #
# V15: compatibility lists known harnesses, each exactly once
# --------------------------------------------------------------------------- #


def test_v15_known_harnesses_map_each_key_to_its_display_name() -> None:
    """The harness table is the single source of accepted keys and their spelling."""
    assert validate.KNOWN_HARNESSES == {
        "claude-code": "Claude Code",
        "codex": "Codex",
        "opencode": "OpenCode",
    }


def test_v15_no_compatibility_key_is_valid(tmp_path: Path) -> None:
    """A skill that omits ``compatibility`` is common to every harness, not a violation."""
    skill_dir = write_skill(tmp_path)
    violations = validate_skill(skill_dir, tmp_path)
    assert "V15" not in _check_ids(violations)


@pytest.mark.parametrize(
    ("harness", "display_name"),
    [("claude-code", "Claude Code"), ("codex", "Codex"), ("opencode", "OpenCode")],
    ids=["claude-code", "codex", "opencode"],
)
def test_v15_single_known_harness_is_valid(tmp_path: Path, harness: str, display_name: str) -> None:
    """Every key of the harness table is accepted, and such a skill is otherwise clean."""
    skill_dir = write_skill(
        tmp_path,
        compatibility=harness,
        description=_harness_description(display_name),
    )
    violations = validate_skill(skill_dir, tmp_path)
    assert violations == []


def test_v15_two_known_harnesses_is_valid(tmp_path: Path) -> None:
    """A comma-separated pair of known keys is accepted."""
    skill_dir = write_skill(
        tmp_path,
        compatibility="codex, opencode",
        description=_harness_description("Codex", "OpenCode"),
    )
    violations = validate_skill(skill_dir, tmp_path)
    assert "V15" not in _check_ids(violations)


def test_v15_extra_whitespace_around_keys_is_valid(tmp_path: Path) -> None:
    """Elements are stripped before being matched, so the spacing is free."""
    skill_dir = write_skill(
        tmp_path,
        compatibility="  codex ,  opencode  ",
        description=_harness_description("Codex", "OpenCode"),
    )
    violations = validate_skill(skill_dir, tmp_path)
    assert "V15" not in _check_ids(violations)


@pytest.mark.parametrize(
    "unknown_value",
    ["OpenCode", "opencode-cli", "unknown-harness", "opencode, claude"],
    ids=["display-name", "suffixed", "unrelated", "unknown-among-known"],
)
def test_v15_unknown_harness_is_violation(tmp_path: Path, unknown_value: str) -> None:
    """A word outside the harness table is not a declaration an installer can act on."""
    skill_dir = write_skill(tmp_path, compatibility=unknown_value)
    violations = validate_skill(skill_dir, tmp_path)
    assert "V15" in _check_ids(violations)


@pytest.mark.parametrize(
    "empty_element_value",
    ["opencode, ", ",opencode", "opencode,,codex", ""],
    ids=["trailing-comma", "leading-comma", "double-comma", "empty-string"],
)
def test_v15_empty_element_is_violation(tmp_path: Path, empty_element_value: str) -> None:
    """An empty element means the list itself is malformed."""
    skill_dir = write_skill(tmp_path, compatibility=empty_element_value)
    violations = validate_skill(skill_dir, tmp_path)
    assert "V15" in _check_ids(violations)


def test_v15_duplicate_harness_is_violation(tmp_path: Path) -> None:
    """Naming the same harness twice is a copy/paste slip rather than a declaration."""
    skill_dir = write_skill(tmp_path, compatibility="opencode, opencode")
    violations = validate_skill(skill_dir, tmp_path)
    assert "V15" in _check_ids(violations)


@pytest.mark.parametrize(
    "raw_block",
    [
        "compatibility:\n  - opencode",
        "compatibility: 3",
        "compatibility:\n  opencode: true",
    ],
    ids=["list", "number", "mapping"],
)
def test_v15_non_string_value_is_violation(tmp_path: Path, raw_block: str) -> None:
    """``compatibility`` carries a comma-separated string, never a YAML structure."""
    skill_dir = write_skill(tmp_path, compatibility_raw=raw_block)
    violations = validate_skill(skill_dir, tmp_path)
    assert "V15" in _check_ids(violations)


# --------------------------------------------------------------------------- #
# V16: description opens with the preamble naming the declared harnesses
# --------------------------------------------------------------------------- #


def test_v16_no_compatibility_key_is_valid(tmp_path: Path) -> None:
    """Without a ``compatibility`` declaration there is no preamble to require."""
    skill_dir = write_skill(tmp_path)
    violations = validate_skill(skill_dir, tmp_path)
    assert "V16" not in _check_ids(violations)


def test_v16_single_harness_preamble_is_valid(tmp_path: Path) -> None:
    """One declared harness asks for its display name followed by ``専用。``."""
    skill_dir = write_skill(
        tmp_path,
        compatibility="opencode",
        description=_harness_description("OpenCode"),
    )
    violations = validate_skill(skill_dir, tmp_path)
    assert "V16" not in _check_ids(violations)


def test_v16_two_harness_preamble_is_valid(tmp_path: Path) -> None:
    """Two declared harnesses are joined with ``' / '`` in the preamble."""
    skill_dir = write_skill(
        tmp_path,
        compatibility="codex, opencode",
        description=_harness_description("Codex", "OpenCode"),
    )
    violations = validate_skill(skill_dir, tmp_path)
    assert "V16" not in _check_ids(violations)


def test_v16_preamble_in_the_declared_order_is_valid(tmp_path: Path) -> None:
    """The display names follow the order the ``compatibility`` value lists them in."""
    skill_dir = write_skill(
        tmp_path,
        compatibility="opencode, codex",
        description=_harness_description("OpenCode", "Codex"),
    )
    violations = validate_skill(skill_dir, tmp_path)
    assert "V16" not in _check_ids(violations)


def test_v16_missing_preamble_is_violation(tmp_path: Path) -> None:
    """A harness-specific skill whose description never says so is reported as V16."""
    skill_dir = write_skill(tmp_path, compatibility="opencode")
    violations = validate_skill(skill_dir, tmp_path)
    assert "V16" in _check_ids(violations)


def test_v16_preamble_naming_another_harness_is_violation(tmp_path: Path) -> None:
    """The preamble must name the declared harness, not a different one."""
    skill_dir = write_skill(
        tmp_path,
        compatibility="opencode",
        description=_harness_description("Codex"),
    )
    violations = validate_skill(skill_dir, tmp_path)
    assert "V16" in _check_ids(violations)


def test_v16_preamble_with_a_missing_display_name_is_violation(tmp_path: Path) -> None:
    """Every declared harness has to appear in the preamble, not just the first one."""
    skill_dir = write_skill(
        tmp_path,
        compatibility="codex, opencode",
        description=_harness_description("Codex"),
    )
    violations = validate_skill(skill_dir, tmp_path)
    assert "V16" in _check_ids(violations)


def test_v16_preamble_in_the_wrong_order_is_violation(tmp_path: Path) -> None:
    """Display names in an order the ``compatibility`` value does not use is V16."""
    skill_dir = write_skill(
        tmp_path,
        compatibility="codex, opencode",
        description=_harness_description("OpenCode", "Codex"),
    )
    violations = validate_skill(skill_dir, tmp_path)
    assert "V16" in _check_ids(violations)


def test_v16_preamble_not_at_the_start_is_violation(tmp_path: Path) -> None:
    """The preamble opens the description so a truncated listing still shows it."""
    skill_dir = write_skill(
        tmp_path,
        compatibility="opencode",
        description=f"サンプル。{_harness_description('OpenCode')}",
    )
    violations = validate_skill(skill_dir, tmp_path)
    assert "V16" in _check_ids(violations)


def test_v16_misspelled_display_name_is_violation(tmp_path: Path) -> None:
    """The display name is compared verbatim, so a casing slip is a violation."""
    skill_dir = write_skill(
        tmp_path,
        compatibility="opencode",
        description=f"Opencode 専用。{DEFAULT_DESCRIPTION}",
    )
    violations = validate_skill(skill_dir, tmp_path)
    assert "V16" in _check_ids(violations)


def test_v16_preamble_without_the_separator_spaces_is_violation(tmp_path: Path) -> None:
    """The join is ``' / '``, so ``'Codex/OpenCode 専用。'`` does not match."""
    skill_dir = write_skill(
        tmp_path,
        compatibility="codex, opencode",
        description=f"Codex/OpenCode 専用。{DEFAULT_DESCRIPTION}",
    )
    violations = validate_skill(skill_dir, tmp_path)
    assert "V16" in _check_ids(violations)


@pytest.mark.parametrize(
    "invalid_value",
    ["OpenCode", "opencode-cli", "opencode, ", "opencode,,codex", "opencode, opencode"],
    ids=["display-name", "suffixed", "trailing-comma", "double-comma", "duplicate"],
)
def test_v16_invalid_compatibility_is_not_double_reported(
    tmp_path: Path, invalid_value: str
) -> None:
    """A value V15 already rejects has no expected preamble, so V16 stays silent."""
    skill_dir = write_skill(tmp_path, compatibility=invalid_value)
    check_ids = _check_ids(validate_skill(skill_dir, tmp_path))
    assert "V15" in check_ids
    assert "V16" not in check_ids


def test_v16_non_string_compatibility_is_not_double_reported(tmp_path: Path) -> None:
    """A non-string value is reported once, by V15 alone."""
    skill_dir = write_skill(tmp_path, compatibility_raw="compatibility:\n  - opencode")
    check_ids = _check_ids(validate_skill(skill_dir, tmp_path))
    assert "V15" in check_ids
    assert "V16" not in check_ids


# --------------------------------------------------------------------------- #
# V00: the repository holds at least the expected number of skills
# --------------------------------------------------------------------------- #


def test_v00_repository_with_enough_skills_is_valid(tmp_path: Path) -> None:
    """A repository holding at least ``min_skills`` compliant skills is clean."""
    write_skill(tmp_path, name="alpha-skill")
    write_skill(tmp_path, name="beta-skill")
    violations = validate_repository(tmp_path, min_skills=2)
    assert violations == []


def test_v00_too_few_skills_is_violation(tmp_path: Path) -> None:
    """Fewer skill folders than ``min_skills`` is reported as V00."""
    write_skill(tmp_path, name="alpha-skill")
    violations = validate_repository(tmp_path, min_skills=2)
    assert "V00" in _check_ids(violations)


def test_v00_missing_skills_directory_is_violation(tmp_path: Path) -> None:
    """A repository without a ``skills/`` directory at all is reported as V00."""
    violations = validate_repository(tmp_path, min_skills=1)
    assert "V00" in _check_ids(violations)


def test_validate_repository_reports_violations_from_every_skill(tmp_path: Path) -> None:
    """Per-skill violations from more than one skill are all collected."""
    write_skill(tmp_path, name="alpha-skill", license_value="Apache-2.0")
    write_skill(tmp_path, name="beta-skill", declare_usage=False)
    violations = validate_repository(tmp_path, min_skills=2)
    paths = {violation.path for violation in violations}
    assert any("alpha-skill" in path for path in paths)
    assert any("beta-skill" in path for path in paths)


def test_validate_repository_ignores_loose_files_in_skills_dir(tmp_path: Path) -> None:
    """A stray file such as a generated manifest is not treated as a skill."""
    write_skill(tmp_path, name="alpha-skill")
    (tmp_path / "skills" / "index.json").write_text("{}\n", encoding="utf-8")
    violations = validate_repository(tmp_path, min_skills=1)
    assert violations == []


# --------------------------------------------------------------------------- #
# Manifest helpers (V13/V14)
# --------------------------------------------------------------------------- #


def write_project_version(repo_root: Path, version: str = DEFAULT_PROJECT_VERSION) -> None:
    """Write the top-level ``VERSION`` file that the manifests are built from."""
    (repo_root / "VERSION").write_text(f"{version}\n", encoding="utf-8")


def build_manifest_repo(
    tmp_path: Path,
    *,
    project_version: str = DEFAULT_PROJECT_VERSION,
    skill_names: Sequence[str] = ("alpha-skill",),
) -> Path:
    """Materialise a synthetic repository whose manifests are freshly generated.

    The result is what a correctly maintained checkout looks like: compliant
    skills, a ``VERSION`` file and the two manifests exactly as the generator
    writes them. Tests then break one aspect of it.

    Args:
        tmp_path: Pytest's per-test temporary directory, used as the repository
            root so that no test touches a real checkout.
        project_version: Content of the ``VERSION`` file.
        skill_names: Names of the skill folders to create.

    Returns:
        The repository root (``tmp_path``), for readability at the call site.
    """
    for skill_name in skill_names:
        write_skill(tmp_path, name=skill_name)
    write_project_version(tmp_path, project_version)
    manifests.write_manifests(tmp_path)
    return tmp_path


def read_marketplace(repo_root: Path) -> dict[str, Any]:
    """Return the committed marketplace manifest as a Python mapping."""
    text = (repo_root / manifests.MARKETPLACE_RELPATH).read_text(encoding="utf-8")
    return json.loads(text)


def write_marketplace(repo_root: Path, document: Any) -> None:
    """Overwrite the committed marketplace manifest with ``document``."""
    path = repo_root / manifests.MARKETPLACE_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- #
# V13: skills/index.json matches the generator's output
# --------------------------------------------------------------------------- #


def test_v13_freshly_generated_manifests_are_valid(tmp_path: Path) -> None:
    """A repository whose manifests were just generated reports no violation."""
    build_manifest_repo(tmp_path, skill_names=("alpha-skill", "beta-skill"))
    violations = validate_repository(tmp_path, min_skills=2, verify_manifests=True)
    assert violations == []


def test_v13_edited_index_json_is_violation(tmp_path: Path) -> None:
    """A hand-edited ``skills/index.json`` is reported as V13."""
    build_manifest_repo(tmp_path)
    index_path = tmp_path / manifests.INDEX_RELPATH
    index_path.write_text('{"skills": []}\n', encoding="utf-8")
    violations = validate_repository(tmp_path, min_skills=1, verify_manifests=True)
    assert "V13" in _check_ids(violations)


def test_v13_index_json_whitespace_only_change_is_violation(tmp_path: Path) -> None:
    """Byte comparison catches a reformatting that leaves the JSON equivalent."""
    build_manifest_repo(tmp_path)
    index_path = tmp_path / manifests.INDEX_RELPATH
    document = json.loads(index_path.read_text(encoding="utf-8"))
    index_path.write_text(json.dumps(document, indent=4) + "\n", encoding="utf-8")
    violations = check_manifest_drift(tmp_path)
    assert [violation.check for violation in violations] == ["V13"]


def test_v13_missing_index_json_is_violation(tmp_path: Path) -> None:
    """A repository with no ``skills/index.json`` at all is reported as V13."""
    build_manifest_repo(tmp_path)
    (tmp_path / manifests.INDEX_RELPATH).unlink()
    violations = validate_repository(tmp_path, min_skills=1, verify_manifests=True)
    assert "V13" in _check_ids(violations)


def test_v13_reports_the_manifest_relative_path(tmp_path: Path) -> None:
    """The reported path is the manifest's own repository-relative path."""
    build_manifest_repo(tmp_path)
    (tmp_path / manifests.INDEX_RELPATH).unlink()
    violations = check_manifest_drift(tmp_path)
    assert [violation.path for violation in violations] == [str(manifests.INDEX_RELPATH)]
    assert violations[0].line is None


def test_v13_ungeneratable_manifests_are_a_single_violation(tmp_path: Path) -> None:
    """When generation itself fails, one V13 violation carries the reason."""
    build_manifest_repo(tmp_path)
    (tmp_path / "VERSION").unlink()
    violations = check_manifest_drift(tmp_path)
    assert [violation.check for violation in violations] == ["V13"]
    assert "VERSION" in violations[0].message


def test_v13_ungeneratable_manifests_surface_through_validate_repository(tmp_path: Path) -> None:
    """The generation failure also reaches the repository-level entry point."""
    build_manifest_repo(tmp_path)
    (tmp_path / "VERSION").unlink()
    violations = validate_repository(tmp_path, min_skills=1, verify_manifests=True)
    assert "V13" in _check_ids(violations)


# --------------------------------------------------------------------------- #
# V14: .claude-plugin/marketplace.json matches and keeps its shape
# --------------------------------------------------------------------------- #


def test_v14_generated_marketplace_is_valid(tmp_path: Path) -> None:
    """The generated marketplace manifest passes the structural checks."""
    build_manifest_repo(tmp_path)
    assert check_marketplace_shape(tmp_path) == []


def test_v14_edited_marketplace_json_is_violation(tmp_path: Path) -> None:
    """A hand-edited ``.claude-plugin/marketplace.json`` is reported as V14."""
    build_manifest_repo(tmp_path)
    document = read_marketplace(tmp_path)
    document["description"] = "hand written"
    write_marketplace(tmp_path, document)
    violations = validate_repository(tmp_path, min_skills=1, verify_manifests=True)
    assert "V14" in _check_ids(violations)


def test_v14_missing_marketplace_json_is_violation(tmp_path: Path) -> None:
    """A missing marketplace manifest is reported as V14 by the shape check too."""
    build_manifest_repo(tmp_path)
    (tmp_path / manifests.MARKETPLACE_RELPATH).unlink()
    violations = check_marketplace_shape(tmp_path)
    assert [violation.check for violation in violations] == ["V14"]


def test_v14_malformed_marketplace_json_is_violation(tmp_path: Path) -> None:
    """A marketplace manifest that is not valid JSON is reported as V14."""
    build_manifest_repo(tmp_path)
    (tmp_path / manifests.MARKETPLACE_RELPATH).write_text("{not json", encoding="utf-8")
    violations = check_marketplace_shape(tmp_path)
    assert [violation.check for violation in violations] == ["V14"]


def test_v14_plugin_json_present_is_violation(tmp_path: Path) -> None:
    """A committed ``.claude-plugin/plugin.json`` is reported as V14."""
    build_manifest_repo(tmp_path)
    plugin_json_path = tmp_path / manifests.PLUGIN_JSON_RELPATH
    plugin_json_path.write_text('{"name": "agent-skills"}\n', encoding="utf-8")
    violations = validate_repository(tmp_path, min_skills=1, verify_manifests=True)
    assert "V14" in _check_ids(violations)


def test_v14_owner_as_string_is_violation(tmp_path: Path) -> None:
    """A string ``owner`` parses as JSON but breaks the schema, so it is V14."""
    build_manifest_repo(tmp_path)
    document = read_marketplace(tmp_path)
    document["owner"] = manifests.MARKETPLACE_OWNER
    write_marketplace(tmp_path, document)
    violations = check_marketplace_shape(tmp_path)
    assert [violation.check for violation in violations] == ["V14"]


def test_v14_owner_without_name_is_violation(tmp_path: Path) -> None:
    """An ``owner`` mapping lacking the mandatory ``name`` key is V14."""
    build_manifest_repo(tmp_path)
    document = read_marketplace(tmp_path)
    document["owner"] = {"url": "https://example.invalid"}
    write_marketplace(tmp_path, document)
    violations = check_marketplace_shape(tmp_path)
    assert [violation.check for violation in violations] == ["V14"]


def test_v14_two_plugin_entries_is_violation(tmp_path: Path) -> None:
    """More than one plugin entry is V14: this repository ships exactly one."""
    build_manifest_repo(tmp_path)
    document = read_marketplace(tmp_path)
    document["plugins"] = [document["plugins"][0], dict(document["plugins"][0])]
    write_marketplace(tmp_path, document)
    violations = check_marketplace_shape(tmp_path)
    assert [violation.check for violation in violations] == ["V14"]


def test_v14_plugins_not_a_list_is_violation(tmp_path: Path) -> None:
    """A ``plugins`` value that is not a list is V14."""
    build_manifest_repo(tmp_path)
    document = read_marketplace(tmp_path)
    document["plugins"] = {"name": "agent-skills"}
    write_marketplace(tmp_path, document)
    violations = check_marketplace_shape(tmp_path)
    assert [violation.check for violation in violations] == ["V14"]


def test_v14_unexpected_plugin_source_is_violation(tmp_path: Path) -> None:
    """A ``source`` other than the marketplace root is V14."""
    build_manifest_repo(tmp_path)
    document = read_marketplace(tmp_path)
    document["plugins"][0]["source"] = "./plugins/agent-skills"
    write_marketplace(tmp_path, document)
    violations = check_marketplace_shape(tmp_path)
    assert [violation.check for violation in violations] == ["V14"]


def test_v14_plugin_skills_key_is_violation(tmp_path: Path) -> None:
    """A ``skills`` key replaces Claude Code's default scan, so it is V14."""
    build_manifest_repo(tmp_path)
    document = read_marketplace(tmp_path)
    document["plugins"][0]["skills"] = ["./skills/alpha-skill"]
    write_marketplace(tmp_path, document)
    violations = check_marketplace_shape(tmp_path)
    assert [violation.check for violation in violations] == ["V14"]
    assert "skills" in violations[0].message


def test_v14_plugin_skills_key_surfaces_through_validate_repository(tmp_path: Path) -> None:
    """The most important structural check also reaches the entry point."""
    build_manifest_repo(tmp_path)
    document = read_marketplace(tmp_path)
    document["plugins"][0]["skills"] = ["./skills/alpha-skill"]
    write_marketplace(tmp_path, document)
    violations = validate_repository(tmp_path, min_skills=1, verify_manifests=True)
    assert "V14" in _check_ids(violations)


def test_v14_plugin_version_mismatch_is_violation(tmp_path: Path) -> None:
    """A plugin ``version`` disagreeing with ``VERSION`` is V14."""
    build_manifest_repo(tmp_path)
    document = read_marketplace(tmp_path)
    document["plugins"][0]["version"] = "9.9.9"
    write_marketplace(tmp_path, document)
    violations = check_marketplace_shape(tmp_path)
    assert [violation.check for violation in violations] == ["V14"]


def test_v14_shape_check_survives_an_empty_plugins_list(tmp_path: Path) -> None:
    """An empty ``plugins`` list is reported rather than raising IndexError."""
    build_manifest_repo(tmp_path)
    document = read_marketplace(tmp_path)
    document["plugins"] = []
    write_marketplace(tmp_path, document)
    violations = check_marketplace_shape(tmp_path)
    assert [violation.check for violation in violations] == ["V14"]


# --------------------------------------------------------------------------- #
# validate_repository wiring for the manifest checks
# --------------------------------------------------------------------------- #


def test_validate_repository_skips_manifest_checks_by_default(tmp_path: Path) -> None:
    """Regression guard: a repository with no manifests at all stays clean.

    Every pre-existing test builds a synthetic repository that has neither a
    ``VERSION`` file nor a manifest, so the manifest checks must stay opt-in.
    """
    write_skill(tmp_path, name="alpha-skill")
    assert validate_repository(tmp_path, min_skills=1) == []


def test_validate_repository_reports_manifest_violations_last(tmp_path: Path) -> None:
    """Manifest violations are appended after V00 and the per-skill ones."""
    write_skill(tmp_path, name="alpha-skill", license_value="Apache-2.0")
    write_project_version(tmp_path)
    manifests.write_manifests(tmp_path)
    (tmp_path / manifests.INDEX_RELPATH).unlink()
    violations = validate_repository(tmp_path, min_skills=2, verify_manifests=True)
    checks = [violation.check for violation in violations]
    assert checks[0] == "V00"
    assert checks[-1] == "V13"
    assert checks.index("V06") < checks.index("V13")
