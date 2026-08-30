"""Tests for ``tools/validate.py``.

Stage 2 of the project's TDD cycle: these tests are written against the
*intended* behaviour of ``validate.py`` before the check bodies are
implemented (they currently all raise ``NotImplementedError``). Every test
below builds a synthetic skill folder under ``tmp_path`` and drives it
through :func:`validate.validate_skill`, so no test touches the network,
the user's home directory or this repository's real ``skills/`` folder.

Each test name embeds the check identifier (``V01``..``V12``) it protects,
per the convention documented in ``validate.py``. For every check there is
at least one "valid" test (a fully compliant skill does not trigger the
check) and at least one "violation" test (breaking exactly one aspect of an
otherwise compliant skill triggers the check).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest
from validate import (
    MAX_BODY_LINES,
    MAX_DESCRIPTION_CHARS,
    MAX_NAME_CHARS,
    MAX_SHORT_DESCRIPTION_CHARS,
    Violation,
    validate_repository,
    validate_skill,
)

DEFAULT_NAME = "sample-skill"
DEFAULT_VERSION = "1.0.0"
DEFAULT_DESCRIPTION = "サンプルスキルの検証用説明。"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _check_ids(violations: list[Violation]) -> set[str]:
    """Return the set of check identifiers present in ``violations``."""
    return {violation.check for violation in violations}


def _build_frontmatter(
    *,
    name: str | None,
    description: str | None,
    quote_description: bool,
    license_value: str | None,
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
