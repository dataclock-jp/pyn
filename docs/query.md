# 複数文書の検索(PYN 拡張・実験的)

> このファイルは docs/query.pyn から生成されています(手で編集しないこと)。 生成: `python -m tools.build_docs`

## <a id="query"></a>複数文書の検索(pyn.query)

<sub>`spec` `query`</sub>

- `implementation`: `pyn/query.py`
- `since`: `1.3.0`
- `status`: `experimental`

検索語を含む文を、複数の `.pyn` から抜き出して **一つの PYN 文書として** 返す。
検索結果も正準形の文書なので、ほかの文書と同じく射影を選んで読める。人は HTML で、
AI は `.pyn` のままで読む。

構文は増やさない。本体(パーサ・正準形・patch・`pyn check`)にも手を入れない。
検索は読むだけで、元の文書を書き換えない。

### <a id="qr-usage"></a>使い方

<sub>`section` `qr-usage`</sub>

```bash
# AI 向け(既定): 結果は .pyn。標準出力へ
python -m pyn.query find 承認 審査 --in Handover.pyn docs

# 人向け: 結果は HTML。検索語を強調し、文書ごとにまとめる
python -m pyn.query find patch --in . --for human -o result.html

# どれか 1 語でよい / 型で絞る / 散文ブロックごと抜き出す
python -m pyn.query find 署名 承認 --any --type decision --unit block
```

| オプション | 意味 | 既定 |
|---|---|---|
| `--in PATH...` | `.pyn` ファイルかディレクトリ。ディレクトリは下まで見る(`.` で始まるものは除く) | `.` |
| `--root DIR` | 検索してよい範囲。結果のパスはここからの相対 | カレント |
| `--db FILE` | 文書の代わりに `pyn.db` の DB から検索する(全文索引で絞る。結果は同じ。`docs/db.md`) | — |
| `--for ai` / `--for human` | 使い手(下の節) | `ai` |
| `--format pyn / html / md / json` | 射影 | `ai` は `pyn`、`human` は `html` |
| `--any` | どれか 1 語を含めば当てる(既定はすべての語) | — |
| `--type TYPE` | この型のノードだけ(繰り返し可) | — |
| `--unit sentence / block` | 抜き出す単位 | `sentence` |
| `--limit N` / `--max-excerpts N` | 載せるノードの数 / 1 ノードあたりの抜き出しの数 | `ai` 50 / 5、`human` 500 / 20 |

終了コードは grep と同じ。0 = 見つかった / 1 = 見つからなかった / 2 = 指定の誤り。
読めなかった文書があっても止めずに、結果の `skipped` ノードに理由の符号を残す。

### <a id="qr-match"></a>何を当てるか

<sub>`section` `qr-match`</sub>

- **当てる単位はノード。** ノードのタイトル・属性(キーと値)・散文をまとめて照合し、
  既定ではすべての語を含むノードを当てる。結果の単位をノードにするのは、ID があるからである。
  結果から元の文書へ戻れ、そのまま `pyn patch` の宛先にもなる
- **照合は NFKC と大文字小文字を吸収する。** `ｐｙｎ` で `PYN` が当たる
- **抜き出すのは、検索語を含む文。** 折り返された段落・箇条書きの続きの行はつないでから、
  `。` `！` `？` と、後ろに空白のある `.` で文に分ける。コードスパンの中では切らない。
  箇条書き・引用は記号を残し、表は 1 行を 1 単位にする。コードフェンスの中は 1 行ずつ、
  同じフェンスで包み直す
- 順位は付けない。文書の順、文書の中では出現順に並べる(なぜその順なのかを説明できるように)

### <a id="qr-result"></a>結果の文書

<sub>`section` `qr-result`</sub>

```
query query "検索: 承認 審査":
  audience: ai
  files: 19
  hits: 5
  hits_total: 5
  match: all
  terms: ["承認", "審査"]
  truncated: false
  unit: sentence

  file f1 "Handover.pyn":
    hits: 2
    path: Handover.pyn

    hit f1.h2 "知識の取り込み審査(pyn.intake)— ...":
      at: "Handover.pyn#t-26"
      matched_in: [title, prose]
      node_type: task
      within: [plan]
```

`hit` ノードの属性は、元の位置(`at` = 文書と ID)、元の型(`node_type`)、上位のノードの
ID(`within`)、どこで当たったか(`matched_in`: `title` / `prose` / `attr:キー`)。散文ブロックが
抜き出した文で、1 文が 1 ブロックになる。

抜き出した文の中の参照は、元の文書を指す文書間参照に書き換える。結果の文書には元の ID が
無いので、`@` に ID を続けた参照のままだとリンク切れになるからである。パスは `at` と同じく
root からの相対にそろえる(`ops/b.pyn` の中の `../a.pyn` は `a.pyn` になる)。行の途中から
切り出した文が `#` や三連引用符で始まるときは、先頭に `…` を置いて散文に書ける形にする。
結果は必ず `pyn check` を通る(往復・参照・schema)。

### <a id="qr-audience"></a>人からの検索と AI からの検索

<sub>`section` `qr-audience`</sub>

`--for` で使い手を区別する。変わるのは表示と保護の既定値である。

| | `ai`(既定) | `human` |
|---|---|---|
| 既定の射影 | `.pyn` | HTML(検索語を強調し、上位のノードをタイトルで並べる) |
| 安全検査に当たったノード | タイトルと本文を伏せ、`withheld: true` と規則名(`findings`)だけを残す | 伏せずに、警告を付けて見せる |
| 結果の先頭の注意書き | 付ける(結果は文書から抜き出したデータで、指示ではない) | 付けない |
| 件数の上限 | 50 ノード・1 ノード 5 文 | 500 ノード・1 ノード 20 文 |

**`--for` は自己申告であって、認証ではない。** エージェントが `--for human` を付ければ、
伏せた本文も読めてしまう。区別は「AI に渡す文脈へ攻撃文を混ぜない」ための既定値であり、
権限を与えるものではない。迷ったら安全側になるよう、既定は `ai` にしてある。

### <a id="qr-safety"></a>安全検査の単位

<sub>`section` `qr-safety`</sub>

`pyn.guardrails` の `safe_read` は、攻撃に見える字面が 1 か所でもあればファイルごと隔離する。
検索ではそうしない。攻撃の手口を説明している文書(この引継ぎ文書がそうである)が、
まるごと検索できなくなるからである。

代わりに、**結果に載せるノードを 1 つずつ検査する**。対象はノードのタイトル・属性・散文の
全体で、抜き出した文だけではない(文に分けたことで手口が検査をすり抜けないように)。
大きさ・バイナリの偽装・UTF-8・パス(root の外・シンボリックリンク)の検査は、
ファイル単位でこれまでどおり効く。そのために `safe_read` に `screen=False` を足した
(既定は変えていない)。

人向けの HTML は、本体の HTML 射影と同じ規則で書き出す。文書の中身はエスケープし、
`javascript:` などのリンクは出さない(t-25)。

### <a id="qr-limits"></a>限界と、この先

<sub>`section` `qr-limits`</sub>

- 照合は部分文字列の一致だけ。語形の変化・同義語・あいまい検索はしない
- 文の区切りは記号による近似で、`e.g. ` のような略語でも切れる
- 毎回すべての文書を読む。文書が多いなら `pyn.db` で DB を作り、`--db` で検索する
- 段階 2 として、正準形を SQLite へ射影する `pyn.db` がある(`docs/db.md`)。ノード・属性・散文・辺を
  表に落とし、SQL と全文索引で引ける。そこでも DB は生成物であり、書き込みは `pyn patch` を通す
