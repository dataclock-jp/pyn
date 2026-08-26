# PYN — VS Code シンタックスハイライト (t-09)

`.pyn`(PYN 投影A / 投影B)のシンタックスハイライトだけを提供する最小の拡張です。
言語サーバも整形機能も持ちません。整形・検証は CLI 側にあります:

```bash
python -m pyn a doc.pyn -o doc.pyn   # 投影A へ整形
python -m pyn check doc.pyn          # 往復・参照・schema の検証
```

## 入れ方

拡張マーケットプレースには出していないので、ディレクトリごと拡張フォルダへ置きます。

| OS | 置き場所 |
|---|---|
| Windows | `%USERPROFILE%\.vscode\extensions\pyn` |
| macOS / Linux | `~/.vscode/extensions/pyn` |

```bash
# 例(Windows / PowerShell)
Copy-Item -Recurse editors\vscode "$env:USERPROFILE\.vscode\extensions\pyn"

# 例(macOS / Linux)
cp -r editors/vscode ~/.vscode/extensions/pyn
```

VS Code を再起動すると `.pyn` に色が付きます。開発中は `code --extensionDevelopmentPath`
でも読み込めます。

## 何に色が付くか

| 対象 | スコープ | 意図 |
|---|---|---|
| ノード宣言の型 | `storage.type.node.pyn` | 型は第一級(HTML の class 名と違う) |
| ID | `entity.name.tag.pyn` | 構造の実体。ここが真実(s-01) |
| タイトル | `string.quoted.double.title.pyn` | 表示名 |
| `^parent`(投影B) | `keyword.operator.parent.pyn` | 親子関係の明示 |
| 属性キー / 値 | `variable.other.property.pyn` ほか | `true` / `false` / `null` と数値だけを定数扱い |
| 散文ブロック | `string.quoted.triple.pyn` | 中身は Markdown 素通し |
| `@id` / `§id` | `markup.underline.link.pyn` | 装飾ではなくグラフの辺(s-02) |
| コメント | `comment.line.number-sign.pyn` | **作業メモ。再生成で消える**(t-14) |

`no` / `yes` / `on` / `off` に色が付かないのは仕様です。PYN は YAML の暗黙型変換を
採用していないので、これらはただの文字列です。

コードフェンス内の `@id` は参照として扱われないため、色も付きません(s-02)。

## テスト

TextMate エンジンは動かせないので、`tests/test_grammar.py` が
「文法の各パターンが実物の `samples/auth-spec.pyn` の行を想定どおり拾うか」を検証します。
文法を触ったらこれを走らせてください。

```bash
python -m pytest tests/test_grammar.py -q
```
