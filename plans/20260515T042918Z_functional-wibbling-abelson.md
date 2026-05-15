# Yearly Ledger Freeze — Phase 0 + 1 (two-repo model)

## Context

README には既に「archive system ではない / active window は 1〜3 年 / 年単位 archive リポジトリへ移動」という思想が日本語で書かれているが (README.md:182-192)、それを担保する**構造・運用・workflow は存在しない**。

放置すれば「GitHub = 永久アーカイブ」へ流れる。本変更は **境界固定** が目的。制度化や taxonomy 設計ではない。

守りたいのは 3 つだけ:
1. 運用リポジトリ(active)を永久アーカイブ化しない
2. 観測履歴を overwrite しない
3. 後から forensic に復元できる

## 設計判断: forensic discipline を operational repo に背負わせない

このリポは operational workspace として運用する(history reset を許容する mutable な repo)。forensic 保存は **単一の archive repo** に分離する(年ごとに repo を増やさない)。

| Repository | 性質 | 役割 |
|-----------|------|------|
| `market-observation` (この repo / active) | mutable / operational | ingest / 最新 active window / 分析 |
| `market-observation-archive` (単一) | append-only (by policy) / forensic | 凍結された観測履歴を `ledger/YYYY-rN/` 配下に蓄積 |

archive repo は **元の hive partition layout を維持** する(parquet を tar/zst で固めない)。

**GitHub は技術的 immutability を保証しない**(force-push / repo 削除は権限があれば可能)。よって archive repo の append-only 性は **運用規律(policy)** に依存する。git 自身は append-only boundary を *enforce* しないことを前提に扱う。

範囲は Phase 0 + 1 のみ:
- **Phase 0**: README に二リポジトリ構成と archive policy を明文化
- **Phase 1**: 仕様 (`docs/archive/`) + preview-only workflow(dry-run / manual / 列挙と manifest 生成のみ / push なし / delete なし)
- **Phase 2 (将来)**: archive repo への実 push と restore 検証
- **Phase 3 (将来)**: active repo からの partition 削除 / 必要なら history reset

---

## 設計の核 (境界条件のみ)

1. archive 本体は別 repo。この repo の tree には置かない
2. workflow は `data/` / `logs/` に一切触れない(Phase 1)
3. archive repo への push は Phase 1 では行わない
4. revision で append-only lineage(archive repo を `-r2` で別途作成 / 旧 repo の削除禁止)
5. parquet を変換しない(tar/zst なし、原 hive layout 維持)
6. GitHub Release を cold storage と誤読しない

意味論バージョニング / freeze reason taxonomy / provider inventory / observation epoch などは入れない。実運用で壊れ方を観測してから追加する。

---

## 変更対象ファイル

### 1. `README.md` (修正)

既存 182-192 行はそのまま温存。直後に `## Archive Policy` セクションを新規追加。

```markdown
## Archive Policy

このリポジトリは **active observation ledger** として
運用される operational workspace であり、永続的な cold
archive ではない。

古いパーティションは年次で単一の **archive repo**
(`market-observation-archive`) に移送され、この repo
からは除外される。archive repo は元の hive partition
layout を維持したまま `ledger/YYYY-rN/` 配下に保存する
(parquet を tar/zst で固めない / 年ごとに repo を増やさない)。

### 二リポジトリ構成

| Repository | 性質 | 役割 |
|-----------|------|------|
| `market-observation` (この repo) | mutable / operational | ingest / 最新 active window / 分析 |
| `market-observation-archive` (単一) | append-only (by policy) / forensic | `ledger/YYYY-rN/` 配下に凍結された観測履歴 |

active repo は operational workspace なので、サイズ管理の
ため一定周期で git history をリセットすることがある。これは
operational practice であり、archive repo の append-only
規律とは独立。

### append-only の定義と限界

archive repo は overwrite せず lineage を append する
規律で運用する。瑕疵発覚時は旧ディレクトリを残し、
`ledger/YYYY-rN/` の `rN` を増加させた新ディレクトリを
追加する。旧 revision の削除・改変は禁止。

**ただし GitHub は技術的 immutability を保証しない**:
force-push / repo 削除 / branch protection の解除は
権限があれば可能。よって append-only は **policy** で
あって **enforcement** ではない。真正な cold storage が
必要な場合は別途確保すること。

### active repo からの削除条件

active repo の年次 partition を削除してよいのは、以下が
**全て** 満たされたとき:

1. 該当 `(year, revision)` が archive repo の
   `ledger/YYYY-rN/` に push 済み
2. archive 側 `manifest.json` が `manifest.schema.json`
   検証に通る
3. restore 検証成功(archive 側 parquet が active 側
   `frozen_from_commit` で得たものと一致)
4. archive 側 commit hash が active 側 delete commit
   message に記録されている

いずれか欠ければ削除しない。Phase 1 では削除しないので
本条件は Phase 2-3 用の前提として記録するに留まる。

### 運用フェーズ

| Phase | 状態 | 内容 |
|-------|------|------|
| 0 | 完了 | 思想の明文化(本セクション) |
| 1 | 完了 | yearly-ledger-freeze workflow 骨組み(dry-run / manual / 対象ファイル列挙 + manifest 生成 + archive-repo-plan 出力 / push なし / delete なし) |
| 2 | 未着手 | archive repo への実 push と restore 検証 |
| 3 | 未着手 | 上記削除条件を全て満たした年次 partition の active repo からの削除 / 必要なら history reset |
```

### 2. `docs/archive/README.md` (新規・短い)

```markdown
# Archive Specification

archive は単一 git repository (`market-observation-archive`)
の `ledger/YYYY-rN/` 配下に保存する。parquet を tar/zst で
固めず、元の hive partition layout をそのまま維持する。

## archive repo 構造

```
market-observation-archive/
├── README.md
└── ledger/
    ├── 2024-r1/
    │   ├── manifest.json   # manifest.schema.json 準拠
    │   └── data/source=*/dataset=*/year=2024/month=MM/day=DD/data.parquet
    ├── 2024-r2/
    │   └── ... (瑕疵修正版 — 旧 r1 は残す)
    └── 2025-r1/
        └── ...
```

## append-only の規律(by policy, not by enforcement)

- 既存 `ledger/YYYY-rN/` を上書きしない(瑕疵時は `rN` を増やす)
- 旧 revision の削除禁止
- archive repo の git history rewrite 禁止
- 上記は **運用規律** であり、GitHub の技術的 enforcement
  に依存しない(force-push 禁止運用が前提)
- Phase 1 では active repo の `data/` / `logs/` を一切変更しない
- Phase 1 では archive repo への自動 push を行わない

## active repo からの削除条件 (Phase 3 で適用)

active repo の年次 partition を削除してよいのは、以下が
**全て** 満たされたとき:

1. 該当 `(year, revision)` が archive repo の
   `ledger/YYYY-rN/` に push 済み
2. archive 側 `manifest.json` が `manifest.schema.json`
   検証に通る
3. restore 検証成功
4. archive 側 commit hash が active 側 delete commit
   message に記録されている

## schema 進化

v1 は意図的に最小に保つ。実運用で壊れ方を観測してから
schema v2 で必要なフィールドを追加する(schema_version は
append-only で破壊変更しない)。
```

### 3. `docs/archive/manifest.schema.json` (新規・最小)

JSON Schema draft-07, v1:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "https://github.com/.../docs/archive/manifest.schema.json",
  "title": "Yearly Ledger Freeze Manifest",
  "type": "object",
  "required": [
    "schema_version", "year", "revision",
    "source_repo", "frozen_from_commit", "frozen_at",
    "content", "tool"
  ],
  "additionalProperties": false,
  "properties": {
    "schema_version":     { "type": "integer", "const": 1 },
    "year":               { "type": "integer", "minimum": 2020, "maximum": 2100 },
    "revision":           { "type": "integer", "minimum": 1 },
    "source_repo":        { "type": "string", "minLength": 1 },
    "frozen_from_commit": { "type": "string", "pattern": "^[0-9a-f]{7,40}$" },
    "frozen_at":          { "type": "string", "format": "date-time" },
    "supersedes": {
      "type": "object",
      "required": ["year", "revision"],
      "additionalProperties": false,
      "description": "Optional. v1 workflow does NOT populate this automatically. Manual edit only.",
      "properties": {
        "year":     { "type": "integer" },
        "revision": { "type": "integer", "minimum": 1 }
      }
    },
    "content": {
      "type": "object",
      "required": ["file_count", "date_range"],
      "additionalProperties": false,
      "properties": {
        "file_count": { "type": "integer", "minimum": 0 },
        "date_range": {
          "type": "object",
          "required": ["start", "end"],
          "additionalProperties": false,
          "properties": {
            "start": { "type": "string", "format": "date" },
            "end":   { "type": "string", "format": "date" }
          }
        }
      }
    },
    "tool": { "type": "string" }
  }
}
```

> 設計メモ:
> - tar/zst を作らないので `archive.{filename, sha256, size_bytes}` は schema に含めない。Phase 2 で push 形態が確定してから検討。
> - `source_repo` と `frozen_from_commit` で「どの repo のどの commit から凍結したか」が保存される。これが forensic 再現の最小成分。

### 4. `.github/workflows/yearly-ledger-freeze.yml` (新規・preview only)

```yaml
name: yearly-ledger-freeze

on:
  workflow_dispatch:
    inputs:
      year:
        description: 'Target year (e.g., 2024)'
        required: true
        type: string
      revision:
        description: 'Revision number (1 = initial)'
        required: false
        type: string
        default: "1"
      dry_run:
        description: 'Dry-run only (Phase 1: always effectively true; no push, no delete)'
        required: false
        type: boolean
        default: true

env:
  LC_ALL: C
  TZ: UTC

jobs:
  preview:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 1 }

      - name: Validate inputs
        run: |
          [[ "${{ inputs.year }}" =~ ^[0-9]{4}$ ]] || { echo "invalid year"; exit 1; }
          [[ "${{ inputs.revision }}" =~ ^[1-9][0-9]*$ ]] || { echo "invalid revision"; exit 1; }
          current=$(date -u +%Y)
          [ "${{ inputs.year }}" -lt "$current" ] || { echo "year must be past"; exit 1; }

      - name: Locate files
        id: locate
        run: |
          mkdir -p build
          find data -type f -path "*year=${{ inputs.year }}*/data.parquet" \
            | LC_ALL=C sort > build/file_list.txt
          count=$(wc -l < build/file_list.txt)
          [ "$count" -gt 0 ] || { echo "no parquet for year=${{ inputs.year }}"; exit 1; }
          echo "count=$count" >> "$GITHUB_OUTPUT"

      - name: Compute date range
        id: range
        run: |
          dates=$(awk -F'/' '{
            y=""; m=""; d=""
            for (i=1; i<=NF; i++) {
              if ($i ~ /^year=/)  y = substr($i, 6)
              if ($i ~ /^month=/) m = substr($i, 7)
              if ($i ~ /^day=/)   d = substr($i, 5)
            }
            if (y && m && d) print y "-" m "-" d
          }' build/file_list.txt | LC_ALL=C sort -u)
          echo "start=$(echo "$dates" | head -n1)" >> "$GITHUB_OUTPUT"
          echo "end=$(echo   "$dates" | tail -n1)" >> "$GITHUB_OUTPUT"

      - name: Generate manifest
        run: |
          cat > build/manifest.json <<EOF
          {
            "schema_version": 1,
            "year": ${{ inputs.year }},
            "revision": ${{ inputs.revision }},
            "source_repo": "${{ github.repository }}",
            "frozen_from_commit": "${{ github.sha }}",
            "frozen_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
            "content": {
              "file_count": ${{ steps.locate.outputs.count }},
              "date_range": {
                "start": "${{ steps.range.outputs.start }}",
                "end":   "${{ steps.range.outputs.end }}"
              }
            },
            "tool": "yearly-ledger-freeze@v1"
          }
          EOF

      - name: Generate archive-repo plan
        id: plan
        run: |
          basename="${GITHUB_REPOSITORY##*/}"
          archive_repo="${basename}-archive"
          target_subdir="ledger/${{ inputs.year }}-r${{ inputs.revision }}"
          echo "archive_repo=${archive_repo}"   >> "$GITHUB_OUTPUT"
          echo "target_subdir=${target_subdir}" >> "$GITHUB_OUTPUT"
          cat > build/archive-repo-plan.json <<EOF
          {
            "archive_repo": "${archive_repo}",
            "target_subdir_in_archive_repo": "${target_subdir}",
            "source_repo": "${{ github.repository }}",
            "source_commit": "${{ github.sha }}",
            "year": ${{ inputs.year }},
            "revision": ${{ inputs.revision }},
            "target_file_count": ${{ steps.locate.outputs.count }},
            "target_files_reference": "file_list.txt",
            "phase1_actions": {
              "rsync_to_archive_repo": false,
              "git_push_to_archive_repo": false,
              "delete_from_active_repo": false
            }
          }
          EOF

      - name: Upload preview artifact
        uses: actions/upload-artifact@v4
        with:
          name: ledger-freeze-${{ inputs.year }}-r${{ inputs.revision }}-preview
          path: build/

      - name: Summary
        run: |
          {
            echo "### Yearly Ledger Freeze (preview)"
            echo ""
            echo "- target archive repo: \`${{ steps.plan.outputs.archive_repo }}\`"
            echo "- target subdir: \`${{ steps.plan.outputs.target_subdir }}\`"
            echo "- year: ${{ inputs.year }}"
            echo "- revision: ${{ inputs.revision }}"
            echo "- file_count: ${{ steps.locate.outputs.count }}"
            echo "- date_range: ${{ steps.range.outputs.start }} → ${{ steps.range.outputs.end }}"
            echo "- source_repo: ${{ github.repository }}"
            echo "- source_commit: ${{ github.sha }}"
            echo "- dry_run: ${{ inputs.dry_run }}"
            echo ""
            echo "**Phase 1**: no push / no delete / no rsync。file_list.txt と manifest.json と archive-repo-plan.json を Artifact に出力するのみ。"
          } >> "$GITHUB_STEP_SUMMARY"
```

要点:
- `permissions: contents: read` のみ(write は一切要らない)
- 単一 job(tar / Release が無いので build/release-draft 分離も不要)
- output は `file_list.txt` + `manifest.json` + `archive-repo-plan.json` を Artifact に置くだけ
- `archive-repo-plan.json` は二リポジトリ思想を Phase 1 から実体化する: 「どの archive repo に / どの commit から / どのファイルを切り出すか」を明示し、Phase 2 で実 push が乗る土台を作る。`phase1_actions.*` を全て `false` で書き出すことで、Phase 1 が *何をしない* かを成果物自体に刻む

---

## 触らないもの

- `data/` 配下
- `logs/` 配下
- 既存 `.github/workflows/daily-ingest.yml`, `weekly-backfill.yml`
- `scripts/` 配下
- 既存 README の他のセクション

---

## 入れないもの (意図的)

- tar.zst / sha256 / 圧縮(parquet を変換しないので不要)
- GitHub Release(Phase 1 は push しない / archive 配布は別 repo)
- archive repo の作成 / push(Phase 2 以降)
- active repo からの削除(Phase 3 以降)
- freeze_reason / observation_semantics_version / provider_inventory / dataset_universe_hash / config-workflow commit 分離(speculative abstraction)
- workflow の `supersedes_revision` input(schema は optional フィールドを残すが workflow は populate しない)

---

## 検証

1. **構文検証**
   - `python -c "import json, jsonschema; jsonschema.Draft7Validator.check_schema(json.load(open('docs/archive/manifest.schema.json')))"`
   - `python -c "import yaml; yaml.safe_load(open('.github/workflows/yearly-ledger-freeze.yml'))"`

2. **workflow dry-run**(GitHub 上)
   - UI から `year=2025`(または存在する過去年), `revision=1`, `dry_run=true` で手動実行
   - 期待: `preview` job が成功
   - 期待: Artifact `ledger-freeze-YYYY-r1-preview` が `file_list.txt` / `manifest.json` / `archive-repo-plan.json` を含む
   - 期待: `archive-repo-plan.json` の `archive_repo` が `market-observation-archive`(年で repo を増やさない)
   - 期待: `archive-repo-plan.json` の `target_subdir_in_archive_repo` が `ledger/YYYY-r1`
   - 期待: `archive-repo-plan.json` の `phase1_actions.*` が全て `false`
   - 期待: `git log` 上で本 workflow に起因する commit が**発生していない**
   - 期待: 他リポジトリへの push が**発生していない**
   - 期待: Step Summary に target archive repo / target subdir / file_count / date_range / source_commit / dry_run / "no push / no delete / no rsync" が出る

3. **manifest schema 整合性**
   - Artifact の `manifest.json` を `jsonschema.validate` で検証成功

4. **invariant 確認**
   - `git diff HEAD~N -- data/ logs/` が空

---

## 死守すべき不変条件

- workflow は **どのモードでも `data/` / `logs/` を変更しない**
- workflow は **どのモードでも他リポジトリに push しない**(Phase 1)
- archive repo は parquet を tar/zst で固めない(hive layout 維持)
- archive repo は **単一**(`market-observation-archive`)。年ごとに repo を増やさない
- archive repo 内の `ledger/YYYY-rN/` を上書きしない(瑕疵時は `rN` を増やす)
- 旧 `ledger/YYYY-rN/` を削除しない
- archive repo の append-only 性は **policy** であり、GitHub の technical enforcement に依存しない(README で明示し続ける)
- active partition を削除するのは「削除条件 4 項目」が全て満たされたときのみ(Phase 3 で適用)
- schema_version は append-only(v1 を破壊変更しない)
- 「GitHub Release = cold storage」と誤読されないよう README で明示し続ける
- forensic discipline を operational repo に背負わせない(active と archive を分離し続ける)
- **schema を実運用前に拡張しない**(壊れ方を観測してから増やす)

