# PYN

## <a id="rd-overview"></a>概要

<sub>`section` `rd-overview`</sub>

PYN(`.pyn`)は、**JSON と HTML の良いとこ取り**をねらった、Python 風の文書形式です。
仕様書・設計メモ・引継ぎ文書のように、人間が読み、AI が書き換える文書のために作りました。

中心にある主張はひとつだけです。

> 真実は「型と ID を持つノードの集合と、その間の辺」である。
> `.pyn` も JSON も HTML も Markdown も、すべて再生成可能な射影にすぎない。

*PYN is a Python-like document format where the source of truth is a graph of typed,
ID-addressed nodes. The indented `.pyn` text, canonical JSON, HTML and Markdown are all
regenerable projections of it. Documentation is currently Japanese only.*

## <a id="rd-example"></a>見た目

<sub>`section` `rd-example`</sub>

```
spec auth "認証システム仕様":
  version: 1.2
  status: draft

  decision d-01 "なぜ PKCE か":
    chosen: "OAuth2 + PKCE"
    rejected: ["Implicit Flow", "独自トークン"]

  task auth-01 "認可エンドポイント実装":
    est: 3d
    depends: [d-01]
```

構文は 4 つだけです。

- `type id "title":` — ノード宣言。子はインデントで持つ
- `key: value` — 属性(スカラーか、スカラーの配列)
- 三連引用符の行で囲んだ散文ブロック — 中身は Markdown。`@id` と書くと他のノードへの**参照(グラフの辺)**になり、存在しない ID を指すと検証で落ちる
- `# ...` — コメント(作業メモ。正準形には保存されない)

実物は `samples/auth-spec.pyn` にあります。

## <a id="rd-why"></a>何がうれしいか

<sub>`section` `rd-why`</sub>

- **構造はインデントではなく ID が持つ。** インデントは見た目の手がかりで、壊れても ID から検出・修復候補の提示ができる。親子を `^parent` だけで表すフラット形(投影B)もあり、行番号に依存しない
- **AI には ID 宛のパッチで書き換えさせる。** 文字列置換ではないので、行がずれても当たり、失敗したら文書は元のまま(全か無か)
- **生成物と正本を取り違えない。** HTML / Markdown / JSON はいつでも作り直せる射影なので、手で直す理由がない。追従はテストと CI が強制する
- **散文中の参照は検証される。** `@id` のリンク切れは `pyn check` と CI で落ちる
- **本体は Python の標準ライブラリだけ。** 外部依存はない(テストに pytest を使うのみ)

## <a id="rd-start"></a>使い方

<sub>`section` `rd-start`</sub>

Python 3.11 以上が要ります。いまは PyPI に置いていないので、clone して使います。

```bash
git clone https://github.com/dataclock-jp/pyn.git
cd pyn
python -m pyn check samples/auth-spec.pyn    # 往復 + 参照 + schema の検証
```

```bash
python -m pyn json  doc.pyn          # 正準形 JSON へ
python -m pyn a     doc.pyn          # 投影A(インデント形。人間が読む)へ
python -m pyn b     doc.pyn          # 投影B(フラット形。行番号に依存しない)へ
python -m pyn html  doc.pyn -o out.html
python -m pyn md    doc.pyn -o out.md
python -m pyn repair broken.pyn      # インデント破損の診断(自動修復はしない)
python -m pyn id    draft.pyn        # ID の無い宣言に採番する
```

入力は `.pyn` でも `.json` でも構いません(同じ文書の別の射影なので)。

## <a id="rd-patch"></a>AI に書き換えさせる

<sub>`section` `rd-patch`</sub>

宛先は行番号ではなく ID です。操作はファイルで渡します。

```bash
cat > ops.json <<'JSON'
[
  {"op": "set_attr", "id": "auth-01", "key": "status", "value": "done"},
  {"op": "add_prose", "id": "auth-01", "prose": "完了。次は auth-02。"}
]
JSON
python -m pyn patch doc.pyn --ops ops.json --dry-run   # 当ててみるだけ
python -m pyn patch doc.pyn --ops ops.json             # 適用(既定で上書き)
```

使える操作(`set_attr` / `add` / `move` / `rename` ほか)と、エージェントに守らせる規律は `AGENTS.md` にまとめてあります。Claude Code や Codex CLI に作業させるなら、まずこれを読ませてください。

## <a id="rd-docs"></a>ドキュメント

<sub>`section` `rd-docs`</sub>

| 文書 | 内容 |
|---|---|
| `AGENTS.md` | エージェント向けの操作ガイド。正本と生成物の対応、パッチの操作一覧、書くときの制約 |
| `docs/prose-dialect.md` | 散文ブロックに書いてよい Markdown の範囲 |
| `Handover.md` | 設計に至った経緯と、受け入れた代償。判断の理由はすべてここにある |
| `samples/auth-spec.pyn` | 実物のサンプル |
| `editors/vscode/` | VS Code 用のシンタックスハイライト |

この README も含め、`.md` / `.html` の大半は `.pyn` からの生成物です(正本は `docs/*.pyn` と `Handover.pyn`)。PYN 自身の文書を PYN で書いています。

## <a id="rd-status"></a>状態

<sub>`section` `rd-status`</sub>

v1.0。仕様と実装はひととおり揃っていますが、運用実績はまだ少なく、実験的なプロジェクトです。
未着手の候補(pip で入れられるようにする、など)は `Handover.md` の「着手候補」にあります。

## <a id="rd-license"></a>ライセンス

<sub>`section` `rd-license`</sub>

MIT。`LICENSE` を参照してください。
