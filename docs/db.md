# SQLite への射影(PYN 拡張・実験的)

> このファイルは docs/db.pyn から生成されています(手で編集しないこと)。 生成: `python -m tools.build_docs`

## <a id="db"></a>SQLite への射影(pyn.db)

<sub>`spec` `db`</sub>

- `implementation`: `pyn/db.py`
- `since`: `1.4.0`
- `status`: `experimental`

複数の `.pyn` を、ノード・属性・散文・辺の表に落とした SQLite の DB にする。検索の段階 2 である
(段階 1 は `pyn.query`。`docs/query.md`)。SQL で JOIN や再帰 CTE が書け、FTS5(trigram)が
使える環境では全文索引も張る。標準ライブラリの `sqlite3` だけで動く。

**DB は正本ではなく生成物である。** 読むときは読み取り専用で開き、書き込む文は拒否する。
文書を直すのは今までどおり `pyn patch` で、そのあと DB を作り直す。DB はコミットしない
(SQLite のファイルはバイト列が安定しないので、ほかの生成物と違って追従をテストで見張れない。
`*.sqlite` は .gitignore にある)。

### <a id="db-usage"></a>使い方

<sub>`section` `db-usage`</sub>

```bash
python -m pyn.db build --in Handover.pyn docs -o pyn.sqlite          # 作る(既定の出力は pyn.sqlite)
python -m pyn.db sql pyn.sqlite "SELECT id, title FROM nodes WHERE type='decision'"
python -m pyn.db sql pyn.sqlite "SELECT ..." --for human -o r.html   # 人向けは HTML の表
python -m pyn.db restore pyn.sqlite --doc Handover.pyn               # DB → .pyn(往復の確認)
python -m pyn.db verify pyn.sqlite                                   # 作ったあとで文書が変わっていないか
python -m pyn.query find 承認 --db pyn.sqlite                        # 段階 1 の検索を DB から
```

| 操作 | すること | 終了コード |
|---|---|---|
| `build` | 文書を読んで DB を作る。一時ファイルに書いてから置き換える | 0 / 2 |
| `sql` | 読むだけの SQL を 1 文実行し、結果を PYN 文書(AI 向け)か HTML の表(人向け)で出す | 0 / 2 |
| `restore` | DB から 1 つの文書を正準形に戻す | 0 / 2 |
| `verify` | DB に記録した正準形のダイジェストと、今の文書を比べる | 0 = 追従 / 1 = 古い / 2 |

`build` の `--in` / `--root` / `--max-files` は `pyn.query find` と同じ意味で、読めない文書は
`skipped` 表に理由の符号を残して飛ばす。出力先にすでにファイルがあるときは、`pyn.db` の DB で
なければ上書きしない(`--force` で上書き)。

### <a id="db-tables"></a>表

<sub>`section` `db-tables`</sub>

パスはすべて `--root` からの相対で、`doc` 列に入る(`'Handover.pyn'` のように書けば引ける)。

| 表 | 列 | 中身 |
|---|---|---|
| `docs` | `path` `digest` `nodes` | 文書。`digest` は正準形の SHA-256 |
| `nodes` | `doc` `id` `type` `title` `parent` `ord` `depth` | ノード。`ord` は文書の中の出現順(行きがけ順)、`parent` は親の ID(ルートなら NULL) |
| `attrs` | `doc` `node` `key` `idx` `kind` `value` | 属性。配列は 1 要素 1 行で `idx` が 0 から、スカラーは `idx` が NULL。`kind` は `str` / `int` / `float` / `bool` / `null` / `empty_list` |
| `prose` | `doc` `node` `idx` `text` | 散文ブロック |
| `edges` | `doc` `src` `kind` `via` `dst_doc` `dst` | 辺(下の表)。どれも散文と属性からの派生値 |
| `node_text` / `node_fts` | `doc` `node` `text` | 照合用の文字列(タイトル・属性・散文を `pyn.query` と同じく NFKC + casefold で正規化)と、その全文索引 |
| `skipped` | `path` `reason` | 読めなかった文書 |
| `meta` | `key` `value` | `format` / `schema_version` / `pyn_version` / `fts`(`trigram` か `none`) |

| `via` | `kind` | 取り出し方 |
|---|---|---|
| `prose` | `ref` | 散文の `@id` / `§id`(正準形の `refs` と同じ) |
| `prose` | `xref` | 散文の文書間参照 `@{path#id}`。`dst_doc` は root からの相対に直す |
| `attr` | 属性のキー(`depends` / `cites` など) | 属性の値が、同じ文書のノードの ID と一致するもの |
| `relation` | relation の `kind`(`supports` など) | `pyn.knowledge` の `relation` ノード。`source` から `target` への辺 |

属性の値が「たまたま ID と同じ文字列」でも辺になる。誤りを避けたいなら `kind` で絞って使う。

### <a id="db-examples"></a>問い合わせの例

<sub>`section` `db-examples`</sub>

`status` が `candidate` の着手候補:

```sql
SELECT n.id, n.title FROM nodes n
JOIN attrs a ON a.doc = n.doc AND a.node = n.id AND a.key = 'status'
WHERE n.doc = 'Handover.pyn' AND a.value = 'candidate'
```

t-24 に推移的に依存しているタスク(再帰 CTE):

```sql
WITH RECURSIVE dep(id) AS (
  SELECT src FROM edges WHERE doc = 'Handover.pyn' AND kind = 'depends' AND dst = 't-24'
  UNION SELECT e.src FROM edges e JOIN dep ON e.dst = dep.id
  WHERE e.doc = 'Handover.pyn' AND e.kind = 'depends')
SELECT id FROM dep
```

d-03 を参照している決定とタスク(文書をまたいで):

```sql
SELECT e.doc, n.id, n.type, n.title FROM edges e
JOIN nodes n ON n.doc = e.doc AND n.id = e.src
WHERE e.dst = 'd-03' AND n.type IN ('decision', 'task')
```

全文索引(3 文字以上の語。照合用の文字列は正規化してあるので、語も小文字・半角で書く):

```sql
SELECT doc, node FROM node_text
WHERE rowid IN (SELECT rowid FROM node_fts WHERE node_fts MATCH '"承認され"')
```

### <a id="db-roundtrip"></a>射影であること(往復)

<sub>`section` `db-roundtrip`</sub>

順序の列(`nodes.ord`・`attrs.idx`・`prose.idx`)と値の型(`attrs.kind`)を持たせてあるので、
DB から元の正準形へ戻せる。`restore(build(x)) == x` を、往復テストと同じ乱数木 60 本と、
このリポジトリの文書すべてで確かめている(`tests/test_db.py`)。これが成り立つので、
この DB は索引ではなく、`.pyn` や JSON と同じ射影である(cost-2 と同じ考え方)。

辺と照合用の文字列は派生値で、往復には使わない(正準形の `refs` と同じ扱い)。
`#` コメントは正準形に無いので DB にも入らない。`verify` がコメントだけの変更を「変更なし」と
見なすのはこのためである。

### <a id="db-sql"></a>読むだけの SQL

<sub>`section` `db-sql`</sub>

`sql` は、エージェントが組み立てた SQL を流すことを想定して守りを重ねている。

- DB を読み取り専用(`mode=ro`・`query_only`)で開く
- さらに authorizer で、SELECT と読み取り以外の操作をすべて拒否する(書き込み・DDL・ATTACH・PRAGMA・
  `load_extension` など)
- 文は 1 つだけ。再帰 CTE の暴走は 5 秒で打ち切る。行は既定で 1000 行まで(`--max-rows`)
- 結果は `pyn.query` と同じく PYN 文書(`sql` → `row`)。列名は属性キーの規則に合わせて直し、
  元の列名は `columns` に残す
- `--for ai`(既定)では、安全検査に当たったセルの値を伏せ(null)、行に `withheld`(伏せた列)と
  `findings`(規則名)を残す。`--for human` は HTML の表で、当たった行に色を付ける。
  `--for` は自己申告であって認証ではない(`docs/query.md` と同じ)

- 1 つの値は 8 MB まで、結果ぜんたいは 32 MB まで。時間の上限だけでは、`zeroblob(1000000000)` のように一瞬で巨大な値を作る式を止められない
- DB は `trusted_schema` を切って開く(スキーマに仕込まれたビューやトリガーから、副作用のありうる関数を呼ばせない)
- 有限でない数(`1e999` など)は文字列にして返す。正準形は有限の数しか持てない
- `verify` は、DB に書かれたパスが root の外を指すとき、ファイルがあるかどうかを調べずに `missing` とする(DB 越しに、任意のパスの有無を確かめさせない)

### <a id="db-search"></a>段階 1 の検索との関係

<sub>`section` `db-search`</sub>

`python -m pyn.query find 検索語 --db pyn.sqlite` は、文書の代わりに DB から検索する。
全文索引で候補のノードを絞ってから、段階 1 と同じ照合をかけるので、**結果は文書から検索した
ときと同じになる**(テストで確かめている)。3 文字以上の語は FTS5(trigram)の索引で引き、
2 文字以下の語(「承認」など)は trigram で引けないので `instr` の全件走査になる。
FTS5 の無い SQLite では、すべて全件走査になる(結果は同じ)。

`--db` を付けたときは `--in` / `--root` / `--max-files` は使えない(付けると終了コード 2)。
DB が古くなっていないかは `verify` で確かめる。

### <a id="db-limits"></a>限界

<sub>`section` `db-limits`</sub>

- `--db` の検索は、絞ったあとも結果を作るために文書をすべて DB から戻している。文書が数千本を
  超えるなら、戻す範囲も絞る必要がある
- 増分の更新はしない。`build` は毎回作り直す
- FTS5 の有無と trigram の対応は、Python に入っている SQLite しだい(3.34 以上で trigram)
- 安全検査は字面の検出であり、通ったことは安全の証明ではない
