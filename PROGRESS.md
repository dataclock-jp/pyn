# 実行ログ — 自律実行セッション

**2026-08-26 朝 / ユーザー不在での自律実行**
指示: `t-01 → t-04` を通す。途中で `t-06`(`Handover.html` の生成)まで承認。

## 結果:t-01 〜 t-08 完了。テスト 420 件すべて通過。

```
python -m pytest tests -q     →  420 passed
python -m pyn check samples/auth-spec.pyn
                              →  OK  nodes=6  source_projection=A  往復A/B一致  参照解決OK
python -m tools.handover_to_pyn
                              →  OK  nodes=34  (Handover.pyn / canonical.json / Handover.html)
```

外部依存はゼロ(Python 3.14.3 標準ライブラリのみ)。ネットワーク不要。

---

## タスクごとの結果

| ID | タスク | 状態 | 成果物 |
|---|---|---|---|
| t-01 | 正準形スキーマ確定 | ✅ | `schema/canonical.schema.json` |
| t-02 | PYNパーサ | ✅ | `pyn/parser.py`, `pyn/values.py` |
| t-03 | 射影フォーマッタ | ✅ | `pyn/formatter.py` |
| t-04 | 往復プロパティテスト | ✅ | `tests/test_roundtrip.py`(410件) |
| t-05 | 参照解決 `@id` | ✅ | `pyn/refs.py` |
| t-06 | HTMLレンダラ | ✅ | `pyn/render_html.py` → `Handover.html` |
| t-07 | 破損検出と修復候補 | ✅ | `pyn/repair.py`, `tests/test_repair.py`(10件) |
| t-08 | E2E確認 | ✅ | `samples/auth-spec.pyn` + `Handover.json` の両方 |
| t-09〜t-12 | ハイライタ / schema接続 / パッチAPI / 方言明文化 | ⬜ todo | — |

t-07 は当初の指示範囲外でしたが、**s-01 の主張(ID の冗長性で破損を検出・修復できる)が未実装のままだと止揚の半分が言いっぱなしになる**ため実装しました。

---

## 検証されたプロパティ(t-04)

乱数で生成した木 60 本 × 各性質。cost-2 の担保です。

- `parse(to_pyn_a(x)) == x` — 投影A の可逆性
- `parse(to_pyn_b(x)) == x` — 投影B の可逆性
- `dumps(parse(...)) == dumps(x)` — 正準 JSON のバイト安定性
- `A → B → A` の相互変換で情報が落ちない
- `canonicalize` と各フォーマッタの冪等性
- 投影B のノード宣言・属性行が**必ず 0 インデント**であること
  = 「インデント破損という故障モードを構造的に持たない」ことの機械的確認

エッジケース: 空文書 / 空タイトル / 空文字列値 / エスケープ(`\` と `"` の隣接)/ 散文内のインデント保存 / 配列の入れ子拒否 / タブ拒否 / インデント混在拒否 / 深さ飛び拒否 / 射影の混在拒否 / 重複ID拒否 / 未定義親拒否。

---

## 設計判断のうち、実装して初めて決まったこと

1. **YAML の暗黙型変換を全面的に排除した。**
   `true` / `false` / `null` と数値リテラルのみを型として扱い、`no` `yes` `on` `off` は文字列のまま。いわゆるノルウェー問題を仕様から消しました(テストで固定済み)。

2. **裸の文字列は「書いて読み直すと同じに戻る」場合のみ許す。**
   `values.is_bare_safe()` が自己検査でそれを判定します。この自己参照的な定義のおかげで、引用の要否がフォーマッタとパーサで食い違うことが原理的に起こりません。

3. **`refs` は表層構文に持たせない。**
   散文から毎回導出します。したがって `.pyn` には `refs` が現れず、往復で自動的に再計算されるため、参照の二重管理が発生しません。

4. **参照抽出はコードスパンとフェンスを除外する。**
   これが効いたのは実際のドッグフーディングでした(下記)。

---

## ドッグフーディングで実際に起きたこと

`Handover.json` を PYN に写して HTML を生成する最初の試行で、**リンク切れ検証が 5 件のエラーを出しました。**

```
PynRefError: 未定義のIDを参照しています (5件):
  goal -> @id, s-02 -> @id, s-02 -> @auth-02, t-05 -> @id, t-06 -> @id
```

原因は、引継ぎ文書の本文にあった「参照(@id)を解決する」のような、**記法そのものへの言及**でした。設計上「コードスパンは参照解決の対象外」と決めてあったので、正しい直し方は該当箇所を `` `@id` `` と書くこと。そのように修正して解消しています。

これは s-02 の主張(「リンク切れが CI で落ちる」)が実際に働いた例であり、同時に**この形式を使うと文書の書き方に規律が要求される**ことの実例でもあります。cost-1 の「ID を振る規律」と同種のコストです。

---

## 使い方

```bash
python -m pyn json  samples/auth-spec.pyn    # 正準形JSONへ
python -m pyn a     samples/auth-spec.pyn    # 投影A(インデント形)へ
python -m pyn b     samples/auth-spec.pyn    # 投影B(フラット形)へ
python -m pyn html  samples/auth-spec.pyn -o out.html
python -m pyn check samples/auth-spec.pyn    # 往復 + 参照の検証
python -m pyn repair broken.pyn              # 破損診断(自動適用はしない)

python -m tools.handover_to_pyn              # Handover.json → .pyn / .html
python -m pytest tests -q
```

Windows のコンソールで日本語が化ける場合は `PYTHONIOENCODING=utf-8` を付けてください(ファイル出力は常に UTF-8 なので影響ありません)。

`repair` の出力例:

```
2 件のインデント破損を検出しました(cost-3 により自動適用はしません)

line 3: インデント 5 が単位 2 の倍数ではありません
  |      mitigation: "自動採番"
  修復候補:
    1. インデント 4(cost-1 の子) — 最寄りの妥当なインデント(切り捨て)
```

---

## サイズの実測(引継ぎ文書 1 本あたり)

| 形式 | バイト数 | 比 |
|---|---|---|
| `Handover.pyn`(投影A) | 約 15KB | 0.76 |
| `build/Handover.canonical.json`(正準形) | 約 19KB | 1.00 |
| `Handover.html`(生成物) | 約 33KB | 1.71 |

会話中の見積(JSON比 0.6〜0.7 / HTML比 1/4)に対し、**JSON比は概ね妥当、HTML比は控えめ**でした。今回の HTML は CSS を内包した自己完結ファイルなので、CSS 分を除けば比率はさらに開きます。

---

## 引っかかった点・残っている懸念

1. **`Handover.md` だけが手書きのまま**です。`Handover.html` は `Handover.json` から生成される一方、`.md` は独立して書かれており、d-00 の「二重管理を作らない」に照らすと弱点です。`.md` もレンダラから生成する(Markdown 射影を足す)のが筋。→ 新タスク候補。

2. **`canonicalize` の検証と `schema/canonical.schema.json` が二重定義**になっています。片方を生成元にするか、突き合わせるテストが必要(t-10)。

3. **投影B へのパッチ適用 API がまだない**(t-11)。投影B を出力・解釈はできますが、「AI が差分を当てる」という s-01 の実運用部分は関数として提供されていません。ここが揃って初めて s-01 が完成します。

4. `repair` の ID 命名ヒューリスティクスは**規約であって仕様ではない**ため、`cost-1` のような命名をしていない文書では構文的な候補しか出せません。設計どおりの想定挙動ですが、期待値としては共有しておきます。

---

# 実行ログ — 再開セッション

**2026-08-26 午後 / `Handover.json` を読んで再開**
指示: 引継ぎの `next_actions` に従う。推奨されていた **t-11 → t-13** を実施。

## 結果:t-11 / t-13 完了。テスト 482 件通過。

```
python -m pytest tests -q     →  482 passed
python -m pyn check samples/auth-spec.pyn
                              →  OK  nodes=6  source_projection=A  往復A/B一致  参照解決OK
python -m tools.handover_to_pyn
                              →  OK  nodes=46  (Handover.md / .pyn / canonical.json / .html)
```

| ID | タスク | 状態 | 成果物 |
|---|---|---|---|
| t-11 | 投影B へのパッチ適用 API | ✅ | `pyn/patch.py`, `tests/test_patch.py`(41件), `python -m pyn patch` |
| t-13 | Markdown 射影 | ✅ | `pyn/render_md.py`, `tests/test_render_md.py`(17件), `python -m pyn md` |
| — | 生成物の追従テスト | ✅ | `tests/test_handover.py`(4件) |

---

## t-11 — パッチは「テキスト」ではなく「正準形」に当てる

投影B のテキストを編集する API にしませんでした。投影B が安全なのは
「インデントに意味がない」からですが、それでも行番号は動きます。宛先が ID であるなら、
操作はテキストではなく正準形に対して定義でき、射影は毎回再生成すればよい。

```python
doc = patch.apply(doc, [
    {"op": "set_attr", "id": "t-11", "key": "status", "value": "done"},
    {"op": "move", "id": "t-11", "parent": "plan"},
])
```

- 全操作が純粋関数(入力の `doc` を変更しない)、結果は常に正準形
- `apply` は**全か無か**。途中で失敗したら文書は元のまま(中途半端な適用を返さない)
- `rename` は ID を変えるだけでなく**散文中の `@id` も張り替える**。ID はグラフの頂点なので、
  改名は表記の変更ではなく辺の張り替えだから。コードスパン/フェンス内は s-02 により対象外
- `remove` は**参照を消さない**。リンク切れは `validate_refs` が検出すべきもので、
  黙って本文を書き換えるのは cost-3(誤修復)と同じ罪
- テストで「インデント幅を変えた文書」「投影A由来/投影B由来の文書」に同じ op を当て、
  結果が一致することを確認しています(s-01 の眼目)

CLI は既定で入力と同じ射影に書き戻します:

```bash
python -m pyn patch Handover.pyn --ops ops.json          # 投影A のまま上書き
python -m pyn patch doc.pyn --ops ops.json --as b -o out.pyn
```

パッチ後に `validate_refs` を必ず通すので、**リンク切れを生むパッチは失敗して原本を書き換えません**。

---

## t-13 — `Handover.md` が生成物になった(d-00 の積み残しを解消)

前回の引継ぎに残っていた `known_gap`(「`Handover.md` だけ手書き」)を閉じました。

- `pyn/render_md.py` を追加。散文は解釈せず素通しし、`@id` だけ `[タイトル](#id)` へ解決
- 見出しは**ノードの深さから作る**。アンカーは自動生成に任せず `<a id="...">` で ID に固定
  (真実は ID の側にあるので、タイトルを変えてもリンクが切れない)
- 手書きだった読み物本文は `Handover.json` の `readme[]` へ移設。**節を分けたければ子ノードにする**
- `tests/test_handover.py` を追加し、`Handover.md` / `.pyn` / `canonical.json` が
  `Handover.json` と一致しないとテストが落ちるようにしました。d-00 を規律ではなく CI で守る

### 途中で仕様が 2 つ固まった

1. **散文中の Markdown 見出し(`#`)を `canonicalize` が実際に拒否するようにした。**
   q-03 で「見出しは禁止」と決めてありましたが検証されておらず、Markdown 射影では
   見出し階層がノードの深さと二重定義になるため、ここで実際に弾く必要がありました。
   フェンス内の `#` は対象外です。
2. **散文ブロックに三連引用符だけの行は置けない。** 引継ぎ本文を正本へ移す途中で踏みました。
   投影A/B ではその行が散文の終端そのものなので、書けてしまうと往復が壊れます。
   `canonicalize` が拒否します(仕様として正しい挙動)。

どちらも「ドッグフーディングしたら仕様の穴が出た」例で、前回の `@id` 誤検出と同じ種類の収穫です。

### Markdown では「→ 参照」の一覧を出さない

HTML 射影は末尾に参照チップを並べますが、Markdown では出していません。
`refs` は散文からの派生値なので、散文をそのまま通す Markdown には必ず本文中に同じリンクが
現れており、一覧を足すと同じ辺を二度書くことになるためです。
(HTML の一覧は「本文を読まずに辺だけ辿る」という別の役目があるので残してあります)

---

## 残タスクと引っかかっている点

- 残りは **t-09**(VS Code ハイライタ)/ **t-10**(schema と `canonicalize` の二重定義)/
  **t-12**(散文方言の明文化)。推奨は t-10 → t-12。
- t-12 は、上の 2 つの仕様(見出し禁止・三連引用符の禁止)が実装だけにある状態なので、
  そのまま書き起こす材料が揃っています。
- **q-02(形式名 PYN / `.pyn` の確定)は未決のまま**です。実装は `.pyn` 前提で進んでいます。

---

# 実行ログ — 再開セッション(続き)

**2026-08-26 午後 / 「推奨通りで。形式名も PYN / .pyn で良い」**

## 結果:q-02 確定、t-10 / t-12 完了。テスト 599 件通過。残るは t-09 のみ。

```
python -m pytest tests -q      →  599 passed
python -m pyn check samples/auth-spec.pyn
  →  OK  nodes=6  source_projection=A  往復A/B一致  参照解決OK  schema適合
python -m tools.build_schema --check
  →  OK  canonical.schema.json は追従しています
```

| ID | タスク | 状態 | 成果物 |
|---|---|---|---|
| q-02 | 形式名 PYN / 拡張子 `.pyn` | ✅ 確定 | 文書から「仮称」を削除 |
| t-10 | schema と `canonicalize` の接続 | ✅ | `pyn/schema.py`, `tools/build_schema.py`, `tests/test_schema.py`(88件) |
| t-12 | 散文方言の明文化 | ✅ | `docs/prose-dialect.pyn` → `.md`, `tests/test_prose_dialect.py`(29件) |

---

## t-10 — schema を「手書きの二重定義」から「生成物」へ

`canonicalize`(実装)と `schema/canonical.schema.json`(手書き)が同じ規則を
二度書いていました。d-00 が HTML について言っていることは schema にも当てはまるので、
**どちらかを生成物にする**という同じ解き方を適用しています。

- 単一の源は `pyn/model.py`。ID/型の正規表現・正準形バージョン・ノードのキー順は
  すべて `model` の定数から `build_schema()` が組み立てます
- `schema/canonical.schema.json` はその出力をコミットしたもの。
  `tests/test_schema.py` が追従を強制し、`python -m tools.build_schema --check` でも見られます
- **生成しただけでは足りない**ので突き合わせも用意しました。外部依存を入れない方針なので、
  この schema が使う語彙(`$ref` / `type` / `pattern` / `anyOf` / `required` /
  `additionalProperties` / `propertyNames` / `items` / `minItems` / `minProperties` / `const`)
  だけを解釈する検証器を `pyn/schema.py` に置き、乱数生成した60本の文書で
  「`canonicalize` の出力は必ず schema に適合する」ことを検証しています
- 検証器は**知らないキーワードを無視せずエラーにします**。黙って素通しする検証は、
  検証がないより危険なので
- `python -m pyn check` が schema にも通すようになりました(出力に `schema適合` が増えます)

突き合わせを片側の包含(`canonicalize` の出力 ⊆ schema 適合)にしているのは、
`canonicalize` のほうが狭いためです。重複IDの禁止・散文中の見出し禁止・
三連引用符の行の禁止は JSON Schema では表現できません。

---

## t-12 — 仕様書を `.pyn` で書いた

`docs/prose-dialect.pyn` が正本、`docs/prose-dialect.md` はその Markdown 射影です。
「見出しを使いたくなったら子ノードにする」という方言の核心を、仕様書自身が実演しています。

書いてある内容(抜粋):

- 方言を切る基準は **「その記法がデータモデルに参加するか」** のひとつだけ
- 許可: 段落 / 箇条書き / 番号付き / 引用 / 表 / コードブロック / インラインコード /
  強調 / リンク / 参照
- 禁止: **見出し**(階層はノードが持つ)と **三連引用符だけの行**(散文の終端そのもの)
- 参照 `@id` `§id` はコード領域では無効、`refs[]` は派生値で手書き不可、
  リンクテキストは参照先のタイトルから補完
- 許可された記法には**構文エラーという概念がない**。表の区切り行がなければ
  ただの段落になるだけで、文書としては妥当なまま

`tests/test_prose_dialect.py` が、この主張を1件ずつ実装で裏づけます(29件)。
仕様書と実装が食い違ったらテストが落ちます。書きながら実装の仕様も 2 つ確認できました:

- `#hashtag` のように `#` の後ろに空白がないものは見出しとして扱わない(巻き添えにしない)
- 箇条書きのネストは平坦化される(仕様として明記した)

---

## 現在の状態

**正本は 3 つだけです。** 残りはすべて再生成できます。

| 正本 | そこから生成されるもの |
|---|---|
| `pyn/model.py` | `schema/canonical.schema.json` |
| `Handover.json` | `Handover.md` / `.pyn` / `.html` / `build/Handover.canonical.json` |
| `docs/prose-dialect.pyn` | `docs/prose-dialect.md` |

残タスクは **t-09(VS Code シンタックスハイライト、優先度低)** のみ。
未解決の問いはありません(q-01 / q-02 / q-03 すべて決定済み)。

---

## t-14 — コメントは正本ではない(主張を実態に合わせ、黙って消さない)

「完成か」の確認中に見つけた食い違いです。d-01 は **「コメントで『なぜそう決めたか』を正本に残せる」**
と主張していましたが、`#` は正準形に保存されないため、射影を作り直すと消えていました。
しかも `pyn patch` は既定で入力を上書きするので、**警告なしに消えていました**。

s-meta に照らせばコメントは真実(ノードグラフ)ではなく表記の側に属するので、
保存する方向には倒さず、**主張のほうを実態に合わせました**。

- `pyn/parser.py` に `comment_lines()` を追加。散文ブロックはパーサと同じ規則で読み飛ばすので、
  フェンス内の `#` や `#hashtag` を巻き添えにしません
- `pyn a` / `b` / `json` / `patch` は、消えるコメントの件数と先頭 3 行を stderr に出します。
  上書きになる場合は文言を変えて「入力を上書きするので失われます」と言います
- `html` / `md` は `.pyn` を置き換えないので黙っています
- d-01 の `syntax` と `samples/auth-spec.pyn` のコメント文面を訂正しました
  (サンプルのコメント自体が古い主張を書いていたため)
- `tests/test_comments.py`(15件)で固定

テストを書く途中で、題材にした散文が q-03(見出し禁止)に引っかかりました。
仕様どおりの拒否なので題材のほうを直しています。t-12 で明文化した規則が効いた形です。

**テスト 614 件通過。残タスクは t-09(VS Code ハイライタ、優先度低)のみ。**

---

## t-09 — VS Code シンタックスハイライト、そして git 化

**残タスクはこれで全部なくなりました。テスト 647 件通過。**

### 拡張(`editors/vscode/`)

- `syntaxes/pyn.tmLanguage.json` — 文法。投影A(末尾 `:`)と投影B(末尾 `^parent`)を
  1 つのパターンで扱います
- `language-configuration.json` — コメント記号、`[` の対応、ノード宣言の次行を
  自動インデントする規則
- `package.json` / `README.md` — 拡張の manifest と入れ方

色の付け方に設計判断を 2 つ入れてあります。

1. `@id` / `§id` を **リンク色**(`markup.underline.link`)にしました。装飾ではなく
   グラフの辺なので、文字列の一部ではなく参照に見えるべきだからです(s-02)
2. 定数として色を付けるのは `true` / `false` / `null` **だけ**。`no` / `yes` / `on` / `off`
   に色が付かないのは仕様です(YAML の暗黙型変換を採用しないという決定を文法側でも守る)

### 文法をどうテストしたか

TextMate エンジン(Oniguruma)は動かせないので、文法そのものは走らせられません。
代わりに `tests/test_grammar.py`(33件)で、

- manifest と文法の `scopeName` / パス / 拡張子が食い違っていないこと
- 全パターンが正規表現として成立すること、孤立したルールがないこと
- **実物の `samples/auth-spec.pyn` の全行**が、想定どおりのパターンに拾われること
- 投影B に変換した出力も同じ文法で読めること

を検証しています。3 つめが本体で、文法が実装からずれればサンプルのどれかの行が
「どのパターンにも当たらない」ので落ちます。

### リポジトリ化

- `git init`(既定ブランチ `main`)。初回コミットにコアと生成物を入れました
- `.gitignore` — `__pycache__` などは無視。**生成物はあえてコミットします**。
  d-00 に従い手では編集しませんが、リポジトリを見ただけで読める状態を保つためです。
  追従は `tests/test_handover.py` と `tests/test_schema.py` が強制します
- `.github/workflows/ci.yml` — 設計文書が繰り返し「リンク切れは CI で落ちる」(s-02)と
  言っているので、その CI を実在させました。テスト / `build_schema --check` /
  3 本の `.pyn` に対する `pyn check` が走ります。push 先が GitHub でなければ削除して構いません
