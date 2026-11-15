# 知識の取り込み審査(PYN 拡張・実験的)

> このファイルは docs/knowledge-intake.pyn から生成されています(手で編集しないこと)。 生成: `python -m tools.build_docs`

## <a id="intake"></a>知識の取り込み審査(pyn.intake)

<sub>`spec` `intake`</sub>

- `implementation`: `pyn/intake.py`
- `since`: `1.2.0`
- `status`: `experimental`

AI が新しい知識をそのまま書き足すと、出典の無い主張や、既存の知識と食い違う主張が
黙って積み上がる。`pyn.intake` は「記憶する」を 3 段に分け、**記憶する前に**出典・信頼性・
既存知識との矛盾を評価し、**承認されたものだけ**を知識体系へ反映する。

構文は増やさない。知識体系も提案も普通の `.pyn` で、約束事はすべてノードと属性で書く。
反映は `pyn patch` と同じ ID 宛のパッチで、全か無かで行う。

### <a id="in-flow"></a>流れ

<sub>`section` `in-flow`</sub>

```bash
# 1. 評価する(何も書き換えない)。終了コード 0=approvable / 3=review / 4=reject
python -m pyn.intake evaluate samples/intake/knowledge.pyn \
  samples/intake/proposal-conflict.pyn -o report.json

# 2. 人が評価結果を読み、承認記録を作る(review なら --accept-review が要る)
python -m pyn.intake approve report.json --by human:owner --accept-review -o approval.json

# 3. 承認記録が今の提案・知識体系と一致することを確かめてから反映する
python -m pyn.intake admit samples/intake/knowledge.pyn \
  samples/intake/proposal-conflict.pyn --approval approval.json --dry-run
```

| 段 | 何をするか | 書き換えるもの |
|---|---|---|
| `evaluate` | 提案を知識体系に重ねて調べ、判定と所見を出す | なし |
| `approve` | 評価結果に承認者と時刻を付け、承認記録を作る | 承認記録(新規ファイルのみ。上書きしない) |
| `admit` | もう一度評価し、承認記録と一致すれば反映する | 知識体系(既定で上書き。`-o` / `--dry-run` あり) |

判定は 3 つ。`reject` は承認できない。`review` は人の確認が要る。`approvable` は方針の
範囲内だが、それでも反映には承認記録が要る(評価は提案にすぎず、`actions_authorized` は常に false)。

### <a id="in-kb"></a>知識体系の側の約束事

<sub>`section` `in-kb`</sub>

- **出典台帳**: `source` ノード。`reliability`(0〜1)と `status`(`trusted` / `unverified` /
  `blocked`)を持つ。信頼性を決めるのは人で、`pyn patch` で直す
- **出典の示し方**: 主張は `cites: <source の ID>` で出典を示す(複数なら配列)。
  relation の `source` 属性は辺の始点なので、出典には使わない
- **方針**: `policy` ノード(1 つまで)。`min_reliability`(これ未満は review。既定 0.7)、
  `unverified_reliability`(台帳に無い出典・`unverified` の出典の扱い。既定 0.0)、
  `ledger`(取り込み記録を置く親ノードの ID。無ければルート)
- **構造化した主張**: `subject` / `predicate` / `value` を持つ主張は、同じ主語・述語で値の
  違う既存の主張と突き合わせる。`valid_from` / `valid_until` の期間が重ならなければ矛盾としない
- **もう有効でない知識**: `status` が `retracted` / `superseded` / `rejected` か、既存の
  `supersedes` 関係で置き換えられたノード。矛盾の相手にはしないが、それに依拠する提案は review

### <a id="in-proposal"></a>提案の書き方

<sub>`section` `in-proposal`</sub>

ルートに `proposal` 型のノードを 1 つだけ置き、その子に足したいノードを書く。

```
proposal p-2026-11-15-01 "標高 3000m での沸点の観測":
  into: kb-claims
  proposed_by: "agent:example"
  recorded_at: "2026-11-15T10:00:00+09:00"

  observation o-lab-02 "標高 3000m で水は約 90℃ で沸騰した":
    cites: src-lab
    confidence: 0.85
```

- `proposed_by` と `recorded_at`(タイムゾーン付き)は必須。`into` は足す先の親ノード
- 提案の散文には「なぜ足すか」を書く。反映されると取り込み記録にそのまま残る
- 子ノードの属性(`cites` / `evidence_id` / relation の端点)は、知識体系の ID を指してよい
- 散文で知識体系のノードに触れるときは、提案単体でも `pyn check` が通るようにバッククォートで書く
- 提案は**ひとまとまり**で評価・反映する。一部だけ取り込みたいなら提案を分ける

### <a id="in-checks"></a>評価の中身

<sub>`section` `in-checks`</sub>

| 所見 | 判定 | 意味 |
|---|---|---|
| `malformed_proposal` `missing_proposed_by` `invalid_recorded_at` `unknown_into` `empty_proposal` | reject | 提案の形が違う |
| `unsafe_content:*` | reject | `pyn.guardrails` の安全検査に当たった(本文は所見に写さない) |
| `id_exists` | reject | 既にある ID を使った。置き換えるなら新しい ID と `supersedes` で |
| `forbidden_type` | reject | `policy` / `intake` / `proposal` は提案から足せない |
| `unresolved_reference` `invalid_knowledge` | reject | 重ねると参照切れ、または `pyn.knowledge` の検証に落ちる |
| `missing_source` | reject | 出典(`cites`)の無い主張。関係と出典そのものは除く |
| `blocked_source` | reject | `blocked` の出典を引用している |
| `new_source` | review | 提案の中で新しい出典を登録しようとしている |
| `low_reliability` | review | 引用した出典の信頼性(最大値)が `min_reliability` 未満 |
| `overconfident` | review | `confidence` が出典の信頼性を超えている |
| `conflicts_with` `internal_conflict` | review | 同じ主語・述語で値が違う(既存と / 提案の中で) |
| `contradicts_existing` `supersedes_existing` | review | 既存の知識を反証する・置き換える関係を張る |
| `relies_on_inactive` | review | もう有効でない知識に依拠している |
| `corroborates` `self_assessed_reliability_ignored` | info | 既存と同じ値の裏づけ / 自己申告の信頼性は使わなかった |

**提案者は出典の信頼性を自己申告できない。** 提案の中の `source` に `reliability` を書いても
使わず、`unverified_reliability` とみなす。台帳に載せるかどうかと信頼性は人が決める。

### <a id="in-admit"></a>承認と反映

<sub>`section` `in-admit`</sub>

承認記録は、評価したときの提案と知識体系の SHA-256 ダイジェストを持つ。`admit` は反映の
直前にもう一度評価し、ダイジェストが一致しなければ断る。承認のあとに提案が書き換えられた
場合も、知識体系が先に変わって評価の前提が崩れた場合も、評価からやり直しになる。
反映が済めば知識体系が変わるので、同じ承認記録は二度と使えない。

反映すると次が残る。

- 足したノード(トップレベル)に `admitted_via: <提案の ID>`
- `intake` ノード(ID は提案の ID): `proposed_by` / `proposed_at` / `approved_by` /
  `approved_at` / `decision` / `admitted` / `proposal_digest` / `accepted_findings` と、提案の散文

反映したあとで出典の信頼性が変わったときは、`pyn.impact` が `cites` を依存として追うので、
その出典を引用する主張が再検討の候補に上がる。

### <a id="in-boundary"></a>防御境界と限界

<sub>`section` `in-boundary`</sub>

- **ここが決めるのは「承認に回してよいか」まで。** 主張が真かどうかは判定しない
- **承認者が本当に人かどうかは保証できない。** 承認記録は署名ではない。エージェントに
  `approve` を実行させない・承認記録の置き場所を守る、といった線引きはホスト側で行う
- 矛盾の検出は構造化した主張(`subject` / `predicate` / `value`)と明示的な関係に限る。
  散文の意味が食い違っていても、構造に表れていなければ見つけられない
- 安全検査は字面の検出であり、通ったことは安全の証明にならない(`docs/impact-security.md`)
- 信頼性は出典ごとの 1 つの数値で、主張ごとの校正や裏づけの数による加点はしていない
