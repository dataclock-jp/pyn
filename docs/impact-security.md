# 変更の影響分析と取り込み前の安全検査(PYN 拡張・実験的)

> このファイルは docs/impact-security.pyn から生成されています(手で編集しないこと)。 生成: `python -m tools.build_docs`

## <a id="impact-security"></a>PYN知識更新の影響分析と安全検査（実験版v0.3）

<sub>`spec` `impact-security`</sub>

- `compatibility`: `"PYN 1.2.0"`
- `implementation`: `pyn/impact.py`, `pyn/guardrails.py`
- `status`: `experimental`

この拡張は既存の PYN 書式・正準形式に新しいフィールドを強制しない。
ノード ID による依存関係を追跡し、変更時の再検討候補を *提案* する。
文書の本文はAIへの権限指示ではなく、信頼できないデータとして扱う。

### <a id="change-steps"></a>変更の検出と再検討

<sub>`section` `change-steps`</sub>

```bash
# 変更前の状態を保存（JSONには本文ではなくハッシュ・参照のみが入る）
python -m pyn.impact snapshot --root samples/impact --output before.json

# 文書の変更後に再評価候補を出力（元文書は書き換えない）
python -m pyn.impact plan --root samples/impact --baseline before.json --output review.json
```

- PYNファイル内の全ノードの構造・属性・散文をSHA-256で比較する
- 同一文書の参照、別文書の参照、`depends`、`evidence_id` を追跡する
- `relation`型の `supports`、`contradicts`等も再検討の手がかりにする
- 追加・変更・削除と、そこから最大2ホップの再検討候補を求める
- 変更前後の辺を併用するので、消えたノードへの古い参照も追跡できる
- `review.json` は提案であり、AIの自動実行や真偽判定ではない

### <a id="safety-screen"></a>悪意ある入力のスクリーニング

<sub>`section` `safety-screen`</sub>

```bash
python -m pyn.guardrails scan samples/impact/observations.pyn --root samples/impact
python -m pyn.guardrails scan-tree samples/impact
```

- 拡張子 `.pyn`、厳密なUTF-8、単一ファイルのサイズ上限を検査する
- 実行形式、圧縮ファイル、PDF、画像などのバイナリマジックを拒否する
- NUL・双方向制御文字・不可視文字などを拒否する
- 代表的な偽の権限命令や安全検査回避指示を検出する
- 一部のBase64に隠れた命令やバイナリを解析する（復号した内容は実行しない）
- ディレクトリ逸脱、シンボリックリンク、非通常ファイルを拒否する
- 検査に失敗した文書はそのまま隔離扱いにし、勝手に削除・修復しない

規則の多くは 1 行の中を見るが、検査は**段落の中で折り返された行をつないだ形**にもかける。手口を 2 行に折り返して書いても当たる。空行と散文の区切り(三連引用符の行)は段落の終わりとしてつながない(無関係なブロックどうしをつなぐと、誤検出が増えるだけである)。報告する行番号は、元の文面のものである。

### <a id="boundary"></a>防御境界と限界

<sub>`section` `boundary`</sub>

**字面の検出だけでは、自然言語で書かれた攻撃を完全に判別できない。**
言い換え、他言語、符号化、正常な文章への埋め込みなどをすり抜けられる可能性がある。
逆に正当な研究文書が疑わしいと判定される可能性もある。

- PYN文書の内容は常に *untrusted data* であり、システム/開発者/人間の命令と混ぜない
- 文書からエージェントのツール利用権限を付与しない
- データ処理層は任意の副作用を拒否する。実行権限の決定は信頼できるホスト側で行う
- メール送信、ファイル削除、外部送信、コード実行、Git pushには別途利用者の明示的許可が必要
- 出力先・ツール引数・外部通信をツールの実行直前に再検査し、最小権限を適用する
- 現段階ではコアの `pyn check` 等に全面統合していない。安全な取り込みには
  `pyn.guardrails`、`pyn.impact`、安全検査を組み込んだ `pyn.xrefs` を使用する
- 攻撃の見逃し、誤検出、TOCTOU、ファイル名の偽装、LLM側の実行などは残る課題
- SHA-256ハッシュは変化検出用であり、スナップショットの作成者や真正性を保証しない

### <a id="next"></a>次に試すべき検証

<sub>`section` `next`</sub>

- 同じ正常文書群で誤検知率を測る
- 攻撃を含む文書・偽ロール・Base64・バイナリの回帰テストを増やす
- 権限を持たないAIが閲覧しても、外部送信等の副作用が起きないことを検証する
- 計測結果を見て、全CLIへ取り込みゲートを統合するかを決める

本体の全テストと合わせた統合テストは、取り込み時(t-24)に実施済み。
