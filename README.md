# agent-skills

Personally developed [Agent Skills](https://agentskills.io) for coding agents,
kept in one place so they stay identical across every agent and machine.

日本語の説明は **[README_ja.md](README_ja.md)** をご覧ください。

> **The skills themselves are written in Japanese.** Only this README is in English.
> The author edits these skills regularly and works faster in Japanese, so the skill
> bodies stay in Japanese on purpose. The structure and metadata follow the
> Agent Skills standard, so they load correctly in any supported agent.

## Supported agents

These skills follow the [Agent Skills](https://agentskills.io) open standard and are
tested with:

| Agent | Skill directory |
|---|---|
| [Claude Code](https://code.claude.com) | `~/.claude/skills/` (Windows: `%USERPROFILE%\.claude\skills\`) |
| [OpenAI Codex](https://learn.chatgpt.com/docs/build-skills) | `~/.codex/skills/` (or `$CODEX_HOME/skills/`) |
| [OpenCode](https://opencode.ai) | `~/.config/opencode/skills/` (same path on Windows) |

Any other tool that reads the Agent Skills format should work too.

## What's inside

| Skill | What it does |
|---|---|
| `python-coding` | Applies a consistent Python style — PEP 8/257/484, type hints, docstrings, single responsibility — when writing or reviewing Python. |
| `version-start` | Opens a new version: reads the project docs, surveys the codebase, creates the branch, and drafts an implementation plan. Stops before implementing. |
| `version-implement` | Drives a fixed plan to completion: gated entry, TDD cycles per task, a decision tree that says when to keep going and when to stop and ask. |
| `version-release` | Runs the release checklist: version-bump detection, doc sync, verification, PR, merge, tag, draft release notes. |

The last three form a workflow (start → implement → release) built around a
`PROJECT.md` progress document. They make no assumption about your language or
stack — everything is discovered from your project's own documents.

## Installation

Every route below installs the same files. Pick whichever suits you.

### Option A — Ask your agent to do it (easiest)

Paste this into your agent, replacing the skill name:

```
Download the `skills/python-coding` folder from
https://github.com/xhighhongo41/agent-skills
and place it in your user skills directory.
```

If your agent doesn't know where its skills live, give it the path from the
**Supported agents** table above.

### Option B — Copy it yourself

```bash
git clone https://github.com/xhighhongo41/agent-skills.git
cd agent-skills

# Claude Code
cp -r skills/python-coding ~/.claude/skills/

# OpenAI Codex
cp -r skills/python-coding ~/.codex/skills/

# OpenCode
cp -r skills/python-coding ~/.config/opencode/skills/
```

Copy the whole folder, not just `SKILL.md` — each skill ships a small
`agents/openai.yaml` that gives Codex a display name and an example prompt.
Other agents simply ignore it.

### Option C — Codex `skill-installer`

Codex ships a `skill-installer` skill that can install straight from a GitHub URL:

```
Use skill-installer to install
https://github.com/xhighhongo41/agent-skills/tree/main/skills/python-coding
```

### Verifying the install

Start a new session and run `/python-coding` (or `/version-start`, and so on).
Each skill announces itself on first use, for example:

```
python-coding スキル v1.0.0 を使用します
```

That line tells you the skill really loaded and which version you have.

## Recommended: give your agent a role-to-subagent mapping

These skills delegate work to subagents **by role**, never by name — for example
"解析専任サブエージェント" (the analysis specialist) or "読解役サブエージェント"
(the document reader). They have to: a distributed skill cannot know which
subagents exist on your machine.

If you have defined subagents, adding a short mapping to your **global
instructions** removes the guesswork. Put it in the file your agent already reads:

| Agent | Global instructions file |
|---|---|
| Claude Code | `~/.claude/CLAUDE.md` |
| OpenAI Codex | `~/.codex/AGENTS.md` |
| OpenCode | `~/.config/opencode/AGENTS.md` |

Something like this is enough:

```markdown
## Subagent roles

When a skill refers to a subagent by role, use these:

- 実装担当サブエージェント (implementation) → <your implementation agent>
- 解析専任サブエージェント (test/lint failure analysis) → <your analysis agent>
- 読解役サブエージェント (reading documents) → <your reader agent>
- 探索役サブエージェント (searching the codebase) → <your search agent>
- Web調査役サブエージェント (web research) → <your research agent>
```

This is optional. Without it the agent falls back to whatever general-purpose
subagent it has, which still works — it just picks less precisely.

## If you use both Claude Code and OpenCode

OpenCode reads Claude Code's skill directory natively, so **anything you put in
`~/.claude/skills/` is already visible to OpenCode**. You do not need to install
it twice. To turn that off, set `OPENCODE_DISABLE_CLAUDE_CODE_SKILLS=1`.

## Updating

Re-run whichever installation route you used; it overwrites the previous copy.
To check what you have, compare the `metadata.version` in your installed
`SKILL.md` with the one in this repository.

> A `skill-sync` skill that reports installed versions and available updates,
> plus native install manifests for OpenCode and Claude Code, are planned for v1.0.

## Versioning

Each skill carries its own version in its frontmatter, independent of this
repository's release tags:

```yaml
metadata:
  version: "1.0.0"
```

Versions follow semantic versioning: a breaking change to a procedure bumps the
major, a new step bumps the minor, wording fixes bump the patch.

## License

[MIT](LICENSE)
