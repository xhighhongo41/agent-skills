# agent-skills

コーディングエージェント向けの個人用 [Agent Skills](https://agentskills.io) を
1つのリポジトリにまとめ、複数のエージェント・複数のマシンで同一の内容を保つためのものです。

English: **[README.md](README.md)**

> **スキル本文は日本語で書いています。** 英語なのはこの README だけです。
> 作者自身がスキルを頻繁に編集して改良するため、日本語の方が作業が速いという理由による意図的な選択です。
> 構造とメタデータは Agent Skills 標準に従っているので、対応エージェントでは問題なく読み込まれます。

## 対応エージェント

[Agent Skills](https://agentskills.io) オープン標準に準拠しており、以下で動作を確認しています。

| エージェント | スキルの配置先 |
|---|---|
| [Claude Code](https://code.claude.com) | `~/.claude/skills/`（Windows: `%USERPROFILE%\.claude\skills\`） |
| [OpenAI Codex](https://learn.chatgpt.com/docs/build-skills) | `~/.codex/skills/`（`$CODEX_HOME` 設定時はその配下） |
| [OpenCode](https://opencode.ai) | `~/.config/opencode/skills/`（Windows も同じパス） |

Agent Skills 形式を読む他のツールでも動作するはずです。

## 収録スキル

| スキル | 内容 |
|---|---|
| `python-coding` | Python の実装・修正・レビュー時に、PEP 8/257/484、型ヒント、docstring、単一責任を重視した一貫した方針を適用します。 |
| `version-start` | 新バージョンの開始手順。プロジェクト文書の読解、コードベースの把握、ブランチ作成、実装計画書のドラフト作成まで行い、実装には入りません。 |
| `version-implement` | 確定した計画を完了まで進めます。入場ゲートでの回答照合、タスクごとの TDD サイクル、自走と中断を判定する決定木で動きます。 |
| `version-release` | リリース手順のチェックリスト。バージョン上げ忘れの検出、文書追随の確認、検証、PR、マージ、タグ付与、リリースノートのドラフト作成まで。 |

後半3つは `PROJECT.md` という進行文書を軸にした一連のワークフロー（開始 → 実装 → リリース）です。
言語やスタックを前提にせず、対象プロジェクトの文書から必要な情報を読み取って動きます。

## インストール

どの方法でも配置されるファイルは同じです。好きな方法を選んでください。

### 方法A — エージェント自身にやらせる（いちばん簡単）

以下をエージェントに貼り付けてください。スキル名は適宜置き換えます。

```
https://github.com/xhighhongo41/agent-skills の
`skills/python-coding` フォルダをダウンロードして、
あなたのユーザースキルディレクトリに配置してください。
```

エージェントが自分のスキルディレクトリを知らない場合は、上の「対応エージェント」表の
パスを併せて伝えてください。

### 方法B — 手動でコピーする

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

`SKILL.md` だけでなく**フォルダごとコピー**してください。各スキルには
`agents/openai.yaml` が同梱されており、Codex ではこれが表示名や例示プロンプトとして
使われます。他のエージェントは単に無視するだけなので、あっても害はありません。

### 方法C — Codex の `skill-installer` を使う

Codex には GitHub の URL から直接インストールできる `skill-installer` が同梱されています。

```
skill-installer を使って
https://github.com/xhighhongo41/agent-skills/tree/main/skills/python-coding
のスキルを入れて
```

### インストールできたかの確認

新しいセッションを開始して `/python-coding`（あるいは `/version-start` など）を実行します。
各スキルは初回使用時に次のように宣言します。

```
python-coding スキル v1.0.0 を使用します
```

この行が出れば、スキルが実際に読み込まれていること、そしてどのバージョンが入っているかが分かります。

## 推奨: グローバル指示書に「役割 → サブエージェント」の対応を書く

本リポジトリのスキルは、サブエージェントへの委譲を**役割名**で書いています
（例:「解析専任サブエージェント」「読解役サブエージェント」）。実名を書いていないのは、
配布されるスキルはどの環境にインストールされるか分からず、
**どんなサブエージェントが定義されているかを知りようがない**からです。

サブエージェントを定義している場合は、**グローバル指示書**に短い対応表を書いておくと
エージェントが迷いません。指示書のファイルはエージェントごとに異なります。

| エージェント | グローバル指示書 |
|---|---|
| Claude Code | `~/.claude/CLAUDE.md` |
| OpenAI Codex | `~/.codex/AGENTS.md` |
| OpenCode | `~/.config/opencode/AGENTS.md` |

次の程度で十分です。

```markdown
## サブエージェントの役割対応

スキルが役割名でサブエージェントに言及したときは、以下を使う。

- 実装担当サブエージェント → <実装用エージェント名>
- 解析専任サブエージェント（テスト・lint 失敗の要点抽出） → <解析用エージェント名>
- 読解役サブエージェント（資料・ログの読解） → <読解用エージェント名>
- 探索役サブエージェント（コード探索） → <探索用エージェント名>
- Web調査役サブエージェント → <調査用エージェント名>
```

これは任意です。書かなくても、エージェントは手持ちの汎用サブエージェントで代替するので
動作はします。選択がやや大雑把になるだけです。

## Claude Code と OpenCode を両方使っている場合

OpenCode は Claude Code のスキルディレクトリをそのまま読みます。つまり
**`~/.claude/skills/` に入れたスキルは OpenCode からも見えます**。二重に入れる必要はありません。
無効にしたい場合は `OPENCODE_DISABLE_CLAUDE_CODE_SKILLS=1` を設定してください。

## 更新

インストールに使った方法をもう一度実行すれば上書きされます。
今どのバージョンが入っているかは、インストール済みの `SKILL.md` の `metadata.version` と
本リポジトリのものを比べれば分かります。

> 導入済みバージョンの一覧と更新有無を調べる `skill-sync` スキル、および
> OpenCode / Claude Code のネイティブな導入機構への対応は v1.0 で予定しています。

## バージョンについて

各スキルは、リポジトリのリリースタグとは別に、独自のバージョンを frontmatter に持ちます。

```yaml
metadata:
  version: "1.0.0"
```

セマンティックバージョニングに従い、手順の破壊的変更でメジャー、手順の追加でマイナー、
文言の修正でパッチを上げます。

## ライセンス

[MIT](LICENSE)
