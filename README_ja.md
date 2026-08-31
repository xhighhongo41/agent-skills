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
| `test-driven-development` | テストを先に書く手順。シグネチャ → テスト → **失敗することの確認** → 実装の順に進めます。新規実装・更新実装・バグ修正の3つの進め方と、テスト自体が誤っていると思われるときの判定手順を含みます。 |
| `version-start` | 新バージョンの開始手順。プロジェクト文書の読解、コードベースの把握、ブランチ作成、実装計画書のドラフト作成まで行い、実装には入りません。 |
| `version-implement` | 確定した計画を完了まで進めます。入場ゲートでの回答照合、タスクごとの実装サイクル、自走と中断を判定する決定木で動きます。 |
| `version-release` | リリース手順のチェックリスト。バージョン上げ忘れの検出、文書追随の確認、検証、PR、マージ、タグ付与、リリースノートのドラフト作成まで。 |
| `skill-sync` | 自マシンのどのエージェントにどのスキルのどの版が入っているかを一覧し、本リポジトリと比較して、選んだものだけを更新します。ただし**書き込むのは自エージェントのコンフィグディレクトリの中だけ**です。 |

`version-*` の3つは `PROJECT.md` という進行文書を軸にした一連のワークフロー（開始 → 実装 → リリース）です。
言語やスタックを前提にせず、対象プロジェクトの文書から必要な情報を読み取って動きます。

`test-driven-development` は、あえてこのワークフローから独立させています。
テストを先に書くかどうかは利用者の方針であってスキルが決めることではないため、
`version-implement` は実装の進め方をグローバル指示書に委ねています。
したがって、グローバル指示書には「テストを先に書いて開発する」という方針だけを書き、
**手続きそのものは本スキルに任せる**という使い方ができます。

## インストール

配置されるファイルはどの方法でも同じです。前半2つはどのエージェントでも使え、
後半3つは各エージェント自身の導入機構です。

| 方法 | 対象 | 更新の届き方 | 呼び出し名 |
|---|---|---|---|
| **A** エージェント自身にやらせる | 全部 | もう一度頼む | `/python-coding` |
| **B** 手動でコピー | 全部 | もう一度コピー、または `skill-sync` | `/python-coding` |
| **C** Codex の `skill-installer` | OpenAI Codex | 入れ直す（バージョン比較機構が無い） | `/python-coding` |
| **D** OpenCode の `skills.urls` | OpenCode | 自動（版が変わったとき） | `/python-coding` |
| **E** Claude Code の marketplace | Claude Code | `/plugin marketplace update` | `/agent-skills:python-coding` |

**D** と **E** は全スキルが一度に入ります。**A**・**B**・**C** は1スキルずつです。

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

### 方法D — OpenCode の `skills.urls` を使う

OpenCode は本リポジトリから直接スキルを取得し、最新に保つことができます。
`opencode.json` にマニフェストのあるディレクトリを指定してください。

```json
{
  "skills": {
    "urls": ["https://raw.githubusercontent.com/xhighhongo41/agent-skills/main/skills"]
  }
}
```

OpenCode v2（別バイナリの `opencode2`）はフラットな配列で書きます。

```json
{
  "skills": ["https://raw.githubusercontent.com/xhighhongo41/agent-skills/main/skills"]
}
```

OpenCode はそのディレクトリの `index.json` を読み、そこに載っているスキルをすべて
ダウンロードします。スキルの版が変わると自動的に取り直します。

最新を追いかけるのではなく特定のリリースに固定したい場合は、URL の `main` を
`v1.0.0` のようなタグに置き換えてください。

### 方法E — Claude Code の plugin marketplace を使う

```
/plugin marketplace add xhighhongo41/agent-skills
/plugin install agent-skills@xhighhongo41-agent-skills
```

全スキルが1つのプラグインとして入ります。他の方法と違う点が2つあります。

- **スキル名に名前空間が付きます。** `/python-coding` ではなく
  `/agent-skills:python-coding` で呼び出します。素の名前で呼びたい場合は方法A・Bを
  使ってください。プラグイン版と個人コピーは互いを上書きせず共存できます。
- **サードパーティのマーケットプレイスは自動更新が既定でオフ**です。最新にしたいときは
  `/plugin marketplace update xhighhongo41-agent-skills` を実行するか、
  `/plugin` → Marketplaces で自動更新を有効にしてください。

特定のリリースに固定したい場合はタグを付けます。
`/plugin marketplace add xhighhongo41/agent-skills@v1.0.0`

マーケットプレイス名が `agent-skills` ではなく `xhighhongo41-agent-skills` なのは、
Claude Code が素の `agent-skills` を Anthropic 公式マーケットプレイス用に予約しているためです。

### インストールできたかの確認

新しいセッションを開始して `/python-coding`（あるいは `/version-start` など）を実行します。
marketplace（方法E）で入れた場合の名前は `/agent-skills:python-coding` です。

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
- 判定役サブエージェント（テストと実装のどちらが誤っているかの判定） → <判定用エージェント名>
```

これは任意です。書かなくても、エージェントは手持ちの汎用サブエージェントで代替するので
動作はします。選択がやや大雑把になるだけです。

## Claude Code と OpenCode を両方使っている場合

OpenCode は Claude Code のスキルディレクトリをそのまま読みます。つまり
**`~/.claude/skills/` に入れたスキルは OpenCode からも見えます**。二重に入れる必要はありません。
無効にしたい場合は `OPENCODE_DISABLE_CLAUDE_CODE_SKILLS=1` を設定してください。

## 更新

| 方法 | すること |
|---|---|
| **A**・**B** | インストールをもう一度実行すれば上書きされます |
| **C** | `skill-installer` にもう一度頼みます。バージョン比較機構が無いので、入れ直すことが更新そのものです |
| **D** | 何もしなくて構いません。スキルの版が変わると OpenCode が取り直します |
| **E** | `/plugin marketplace update xhighhongo41-agent-skills` |

今どのバージョンが入っているかを調べるには、本リポジトリの **`skill-sync`** スキルを
使ってください。導入済みの版を本リポジトリの `index.json` と突き合わせて、
**承認を得たうえで**古いものだけを取得・配置します。スキル単位でも、まとめてでも指定できます。

**書き込むのは、そのスキルを実行しているエージェント自身のコンフィグディレクトリの中だけ**です。
他のエージェントのスキルを代わりに更新することはありません。別のエージェントを更新したいときは、
そのエージェント上で `skill-sync` を実行してください。

## バージョンについて

各スキルは、リポジトリのリリースタグとは別に、独自のバージョンを frontmatter に持ちます。

```yaml
metadata:
  version: "1.0.0"
```

セマンティックバージョニングに従い、手順の破壊的変更でメジャー、手順の追加でマイナー、
文言の修正でパッチを上げます。

リポジトリ自体のバージョンはこれとは別で、直下の `VERSION` ファイルとリリースタグで
表現します。Claude Code のプラグインが表示するのはこちらの番号です。

**スキルが1件でも変われば、リポジトリのバージョンを必ず上げます。**上げ幅はその回の変更のうち
最も大きいものに合わせます（どれかがメジャーならメジャー、最大がマイナーならマイナー、
パッチのみならパッチ）。プラグイン利用者にはこの番号が動いたときにしか更新が届かないため、
スキルだけが進んで番号が取り残されないようにしています。CI がこれを検査し、
`skills/` に変更があるのにバージョンが据え置きならビルドが落ちます。

## 変更履歴

| バージョン | 変更内容 |
|---|---|
| **1.2.0** | `test-driven-development` スキルを追加しました。シグネチャ → テスト → **失敗することの確認** → 実装の順に進め、新規実装・更新実装・バグ修正の3つを扱います。テストを実質的に無意味にしたまま通す抜け道については、それぞれに**見つけ方**を併記しました（禁止を書くだけでは止まらないためです）。併せて、収録スキル表の `version-implement` の説明が実態と違っていた点（スキルが強制していない TDD サイクルを謳っていた）を訂正しました。 |
| 1.1.0 | `skill-sync` が書き込むのは、それを実行しているエージェント自身のコンフィグディレクトリの中だけになりました。他のエージェントのスキルを代わりに更新することはありません（そのエージェント上で実行してください）。バージョン管理系3スキルは、ホスティングサービスの CLI が複数アカウントを保持している場合に、必要ならリポジトリの所有者へ一時的に切り替え、作業後に必ず元へ戻すようになりました。 |
| 1.0.0 | 公式導入経路のマニフェストを追加。OpenCode の `skills.urls` と Claude Code の plugin marketplace の両方を実機で検証済み（従来からの Codex `skill-installer` 経路と併せて3経路）。導入状況の一覧と更新を行う `skill-sync` スキルを追加。 |
| 0.1.0 | 最初の収集。4スキルを1つのリポジトリにまとめ、規約・バージョン・CI 検証を統一。プレリリースのため手動コピーのみ。 |

各リリースの詳細は
[リリースページ](https://github.com/xhighhongo41/agent-skills/releases)にあります。

## ライセンス

[MIT](LICENSE)
