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

| Skill | Works with | What it does |
|---|---|---|
| `python-coding` | any | Applies a consistent Python style — PEP 8/257/484, type hints, docstrings, single responsibility — when writing or reviewing Python. |
| `test-driven-development` | any | Writes the tests first: signature, tests, a run that confirms they fail, then the implementation. Covers new work, updates and bug fixes, and says what to do when a test itself looks wrong. |
| `version-start` | any | Opens a new version: reads the project docs, surveys the codebase, creates the branch, and drafts an implementation plan. Stops before implementing. |
| `version-implement` | any | Drives a fixed plan to completion: gated entry, a cycle per task, a decision tree that says when to keep going and when to stop and ask. |
| `version-release` | any | Runs the release checklist: version-bump detection, doc sync, verification, PR, merge, tag, draft release notes. |
| `skill-sync` | any | Lists which agent on your machine holds which version of each skill, compares against this repository, and updates the ones you pick — inside its own agent's config directory only. A skill that names a target agent is checked against the agent running it, so one meant for somewhere else stays out of the candidates. |
| `council` | **OpenCode only** | Runs a round table: several *different* models think about the same question independently, review each other's write-ups, and the main session merges them into one decision. |
| `plugin-update-check` | **OpenCode only** | Checks the npm plugins named in your config against the registry and reports which ones have a newer version. Judgement only — it changes nothing. |
| `plugin-update-apply` | **OpenCode only** | Applies a plugin update by clearing its cache so the next start installs the new version. Asks for explicit approval before deleting anything. |

The three `version-*` skills form a workflow (start → implement → release) built around a
`PROJECT.md` progress document. They make no assumption about your language or
stack — everything is discovered from your project's own documents.

`test-driven-development` deliberately stands apart from that workflow. Whether
you work test-first is your policy, not a skill's, so `version-implement` leaves
the implementation style to your global instructions. That means you can write
"we develop test-first" there and leave the procedure itself to this skill.

`council` is the one skill that names an agent. It needs subagents backed by
*different* models to be worth running, and OpenCode is where you can point
subagents at several providers. Skills like this declare their target in the
`compatibility` field of their frontmatter and say so at the start of their
`description`, so your agent can see what a skill is for before loading it.

## Installation

Five routes, all installing the same files. The first two work with any agent;
the other three are each agent's own mechanism.

| Route | For | How updates arrive | Invoked as |
|---|---|---|---|
| **A** Ask your agent | any | re-run the request | `/python-coding` |
| **B** Copy it yourself | any | re-run, or use `skill-sync` | `/python-coding` |
| **C** Codex `skill-installer` | OpenAI Codex | reinstall; it has no version check | `/python-coding` |
| **D** OpenCode `skills.urls` | OpenCode | automatic, when a version changes | `/python-coding` |
| **E** Claude Code marketplace | Claude Code | `/plugin marketplace update` | `/agent-skills:python-coding` |

Routes **D** and **E** install every skill at once. **A**, **B** and **C** take one skill at a time.

> **Agent-specific skills and the install routes.** Route **D** reads
> `skills/index.json`, which lists only the skills that work with OpenCode, so
> nothing agent-specific arrives that OpenCode cannot use. Route **E** is a plugin
> and always scans the whole `skills/` folder, so `council` is installed into
> Claude Code too — it is inert there, and you can ignore it. With **A**, **B** and
> **C** you pick each skill yourself; check the **Works with** column first.

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

### Option D — OpenCode `skills.urls`

OpenCode can fetch skills from this repository itself and keep them current.
Point it at the manifest directory in your `opencode.json`:

```json
{
  "skills": {
    "urls": ["https://raw.githubusercontent.com/xhighhongo41/agent-skills/main/skills"]
  }
}
```

OpenCode v2 — the separate `opencode2` binary — takes a flat array instead:

```json
{
  "skills": ["https://raw.githubusercontent.com/xhighhongo41/agent-skills/main/skills"]
}
```

OpenCode reads `index.json` from that directory, downloads every skill listed in
it, and refetches a skill whenever its version string changes.

To pin a release rather than track the latest, replace `main` in the URL with a
tag such as `v1.0.0`.

### Option E — Claude Code plugin marketplace

```
/plugin marketplace add xhighhongo41/agent-skills
/plugin install agent-skills@xhighhongo41-agent-skills
```

That installs every skill as a single plugin. Two things differ from the other routes:

- **The skills are namespaced.** Invoke them as `/agent-skills:python-coding`,
  not `/python-coding`. If you want the bare name, use Option A or B — a plugin
  copy and a personal copy can coexist without overriding each other.
- **Auto-update is off by default** for third-party marketplaces. Run
  `/plugin marketplace update xhighhongo41-agent-skills` when you want the
  latest, or switch auto-update on under `/plugin` → Marketplaces.

To pin a release, append the tag:
`/plugin marketplace add xhighhongo41/agent-skills@v1.0.0`.

The marketplace is called `xhighhongo41-agent-skills` rather than
`agent-skills` because Claude Code reserves the bare name for official Anthropic
marketplaces.

### Verifying the install

Start a new session and run `/python-coding` (or `/version-start`, and so on).
If you installed through the plugin marketplace (Option E), the name is
`/agent-skills:python-coding`.

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
- 判定役サブエージェント (ruling on whether a test or the implementation is wrong) → <your reviewer agent>
```

This is optional. Without it the agent falls back to whatever general-purpose
subagent it has, which still works — it just picks less precisely.

## If you use both Claude Code and OpenCode

OpenCode reads Claude Code's skill directory natively, so **anything you put in
`~/.claude/skills/` is already visible to OpenCode**. You do not need to install
it twice. To turn that off, set `OPENCODE_DISABLE_CLAUDE_CODE_SKILLS=1`.

## Updating

| Route | What to do |
|---|---|
| **A**, **B** | Re-run the install; it overwrites the previous copy |
| **C** | Ask `skill-installer` again — it has no version comparison, so reinstalling *is* the update |
| **D** | Nothing. OpenCode refetches a skill when its version changes |
| **E** | `/plugin marketplace update xhighhongo41-agent-skills` |

To find out what you actually have, use the **`skill-sync`** skill from this
repository. It reports the versions you have installed, compares them against
this repository's `index.json`, and — once you approve — fetches and places the
copies that are out of date. You can limit it to a single skill or let it cover
everything.

**It writes only inside the config directory of the agent you run it from.** It
will not update another agent's skills on your behalf; to update a different
agent, run `skill-sync` from that agent.

## Versioning

Each skill carries its own version in its frontmatter, independent of this
repository's release tags:

```yaml
metadata:
  version: "1.0.0"
```

Versions follow semantic versioning: a breaking change to a procedure bumps the
major, a new step bumps the minor, wording fixes bump the patch.

The repository itself is versioned separately, in the top-level `VERSION` file
and in the release tags. That is the number the Claude Code plugin reports.

**The repository version bumps whenever any skill changes — even just one**, and
it follows the largest change in the release: a skill's major bump makes it a
major release, a minor a minor, patches a patch. Plugin users only see an update
when this number moves, so it is never left behind while skills quietly drift
forward. CI enforces this: a change under `skills/` with no matching version bump
fails the build.

## Changelog

| Version | What changed |
|---|---|
| **1.4.0** | `skill-sync` now reads each skill's `compatibility`, so one written for a different agent is kept out of the update and install candidates instead of being offered silently. It also scans the cache that remotely distributed skills land in — a place it never looked before, where an old copy can sit unnoticed. `skills/index.json` now carries `compatibility` for the skills that declare one, and the manifest is cross-checked against the skill's own frontmatter before anything is written, so a stale manifest cannot smuggle in a skill for the wrong agent. Adds two OpenCode-only skills for npm plugin updates — `plugin-update-check` reports what is out of date, `plugin-update-apply` clears the cache after asking — bringing the collection to nine. `version-start` now settles options-shaped questions in conversation and leaves only what genuinely needs a written answer in the plan. |
| 1.3.0 | Adds `council`, an OpenCode-only skill that runs a round table: several different models write up the same question independently, review each other, and the main session merges the result into one decision. This is the first skill in the repository written for one agent, so agent-specific skills now declare their target in `compatibility`, CI checks that the declaration and the `description` agree, and `skills/index.json` — the manifest OpenCode installs from — carries only the skills that list OpenCode. |
| 1.2.0 | Adds the `test-driven-development` skill: signature, tests, a run that confirms they fail, then the implementation — covering new work, updates and bug fixes. Each way of making a test pass without meaning it lists how to spot it, since naming the rule alone does not stop it. Also corrects the `version-implement` entry in the skill table, which promised TDD cycles the skill never mandated. |
| 1.1.0 | `skill-sync` now writes only inside the config directory of the agent running it, and will not update another agent's skills on your behalf — run it from that agent instead. The three version-workflow skills handle a hosting CLI holding several accounts: they switch to the repository's owner when needed and always switch back. |
| 1.0.0 | Install manifests for the official routes — OpenCode's `skills.urls` and the Claude Code plugin marketplace — both verified on real installs, alongside the existing Codex `skill-installer` route. Adds the `skill-sync` skill, which reports what is installed where and updates what you pick. |
| 0.1.0 | First collection: four skills gathered into one repository with unified conventions, versions and CI validation. Pre-release; manual copy only. |

Each release's full notes are on the
[releases page](https://github.com/xhighhongo41/agent-skills/releases).

## License

[MIT](LICENSE)
