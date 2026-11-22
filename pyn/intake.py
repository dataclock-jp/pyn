"""知識の取り込み審査 — 記憶する前に評価し、承認されたものだけを知識体系へ反映する(t-26)。

AI が新しい知識をそのまま書き足すと、出典の無い主張や、既存の知識と食い違う主張が
黙って積み上がる。ここでは「記憶する」を 3 段に分ける。

1. ``evaluate`` — 提案(``.pyn``)を知識体系(``.pyn``)と突き合わせ、出典・信頼性・
   既存知識との矛盾を評価する。判定(``reject`` / ``review`` / ``approvable``)と
   所見の一覧を返すだけで、何も書き換えない
2. ``approve`` — 人(または信頼できるホスト)が判定を承認し、承認記録を作る。
   承認記録は提案と知識体系のダイジェストに結びつく
3. ``apply`` — 承認記録が「今の」提案・知識体系と一致することを確かめてから、
   ID 宛のパッチとして反映する(全か無か)。取り込みの記録(``intake`` ノード)も残す

知識体系の側の約束事(構文は増やさない。すべて普通のノードと属性):

- ``source`` ノード … 出典台帳。``reliability``(0〜1)と ``status``
  (``trusted`` / ``unverified`` / ``blocked``)を持つ
- ``policy`` ノード … 取り込みの方針。``min_reliability`` / ``unverified_reliability`` /
  ``ledger``(取り込み記録を置く親ノードの ID)
- 主張 … ``cites`` で出典を示す。``subject`` / ``predicate`` / ``value`` を持つ主張は、
  同じ主語・述語で値の違う既存の主張と突き合わせる(有効期間が重なるときだけ)

ここが決めるのは「承認に回してよいか」までで、主張が真かどうかは決めない。
承認記録の作成者が本当に人であることも、ここでは保証できない(ホスト側の責任)。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from .errors import PynError
from .model import canonicalize, dumps
from .patch import apply as apply_patch
from .refs import index_by_id, iter_nodes, validate_refs

__all__ = ["IntakeError", "evaluate", "approve", "admit", "DEFAULT_POLICY"]

REPORT_SCHEMA = "pyn-intake-report-v1"
APPROVAL_SCHEMA = "pyn-intake-approval-v1"

#: 知識体系に policy ノードが無いときの方針
DEFAULT_POLICY = {"min_reliability": 0.7, "unverified_reliability": 0.0}
#: 提案から足せない型。方針・取り込み記録・提案は、提案者が書けてはいけない
FORBIDDEN_TYPES = frozenset({"policy", "intake", "proposal"})
#: 出典(cites)を求めない型。関係は端点に従い、出典は台帳への登録そのもの
NO_CITES_TYPES = frozenset({"relation", "source"})
#: この status の既存ノードは「もう有効でない」とみなす
INACTIVE_STATUS = frozenset({"retracted", "superseded", "rejected"})

#: 所見の重さ。reject が 1 つでもあれば却下、review があれば人の確認が要る
REJECT, REVIEW, INFO = "reject", "review", "info"

#: evaluate の CLI 終了コード
EXIT = {"approvable": 0, "review": 3, "reject": 4}


class IntakeError(ValueError):
    """評価・承認・反映ができない(入力の形が違う、承認記録が今の状態と合わない など)。"""


# --------------------------------------------------------------------------
# 評価
# --------------------------------------------------------------------------

def _finding(code: str, severity: str, node: str | None = None,
             about: str | None = None, detail: str | None = None) -> dict:
    # 所見には提案の本文を写さない。ログや画面も、攻撃文の通り道になりうるため
    # (pyn.guardrails と同じ考え方)。ID と規則名だけを出す。
    item = {"code": code, "severity": severity}
    if node is not None:
        item["node"] = node
    if about is not None:
        item["about"] = about
    if detail is not None:
        item["detail"] = detail
    return item


def _digest(doc: dict) -> str:
    return hashlib.sha256(dumps(doc).encode("utf-8")).hexdigest()


def _as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _number01(value) -> bool:
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and 0 <= value <= 1)


def _policy(kb: dict, kb_index: dict) -> dict:
    nodes = [n for n in iter_nodes(kb) if n.get("type") == "policy"]
    if len(nodes) > 1:
        raise IntakeError("知識体系に policy ノードが 2 つ以上あります")
    policy = dict(DEFAULT_POLICY)
    if nodes:
        attrs = nodes[0].get("attrs", {})
        for key in ("min_reliability", "unverified_reliability"):
            if key in attrs:
                if not _number01(attrs[key]):
                    raise IntakeError(f"policy の {key} は 0〜1 の数値にしてください")
                policy[key] = attrs[key]
        if "ledger" in attrs:
            if not isinstance(attrs["ledger"], str) or attrs["ledger"] not in kb_index:
                raise IntakeError("policy の ledger が知識体系に無い ID を指しています")
            policy["ledger"] = attrs["ledger"]
    return policy


def _screen(node: dict) -> list[str]:
    """ノードの読める部分(タイトル・散文・属性)を安全検査に通し、規則名を返す。"""
    from .guardrails import scan_text

    readable = [str(node.get("title") or ""), *node.get("prose", [])]
    for value in (node.get("attrs") or {}).values():
        readable.extend(str(v) for v in _as_list(value))
    return sorted({f.code for text in readable for f in scan_text(text)})


def _add_ops(candidates: list[dict], into: str | None, *, mark: str | None = None) -> list[dict]:
    ops = []
    for node in candidates:
        node = canonicalize({"root": [node]})["root"][0]
        if mark is not None:
            node.setdefault("attrs", {})["admitted_via"] = mark
            # 反映するとき、新しい出典の自己申告(reliability / status)は台帳に写さない。
            # 写すと、次の提案がその出典を引いたときに自己申告の値がそのまま信頼性になる。
            # 未確認(unverified)として載せ、信頼性は人が台帳に書く。
            for new in iter_nodes({"root": [node]}):
                if new.get("type") == "source":
                    new_attrs = new.setdefault("attrs", {})
                    new_attrs.pop("reliability", None)
                    new_attrs["status"] = "unverified"
        op = {"op": "add", "node": node}
        if into is not None:
            op["parent"] = into
        ops.append(op)
    return ops


def _interval(node: dict):
    from .knowledge import _time

    attrs = node.get("attrs", {})
    start = _time(attrs["valid_from"], node["id"], "valid_from") if "valid_from" in attrs else None
    end = _time(attrs["valid_until"], node["id"], "valid_until") if "valid_until" in attrs else None
    return start, end


def _overlaps(a: dict, b: dict) -> bool:
    a_from, a_until = _interval(a)
    b_from, b_until = _interval(b)
    if a_until is not None and b_from is not None and a_until < b_from:
        return False
    if b_until is not None and a_from is not None and b_until < a_from:
        return False
    return True


def _claim_key(node: dict):
    attrs = node.get("attrs", {})
    if "subject" in attrs and "predicate" in attrs:
        return (json.dumps(attrs["subject"], ensure_ascii=False),
                json.dumps(attrs["predicate"], ensure_ascii=False))
    return None


def _report(proposal_id, proposal, kb, policy, findings, candidates) -> dict:
    severities = {f["severity"] for f in findings}
    decision = REJECT if REJECT in severities else REVIEW if REVIEW in severities else "approvable"
    return {
        "schema": REPORT_SCHEMA,
        "proposal": proposal_id,
        "decision": decision,
        "proposal_digest": _digest(proposal),
        "kb_digest": _digest(kb),
        "policy": policy,
        "candidates": candidates,
        "findings": sorted(findings, key=lambda f: (
            {REJECT: 0, REVIEW: 1, INFO: 2}[f["severity"]], f["code"],
            f.get("node") or "", f.get("about") or "")),
        # 評価は提案にすぎない。反映には承認記録が要る(approve → admit)
        "actions_authorized": False,
    }


def evaluate(kb: dict, proposal: dict) -> dict:
    """提案を知識体系と突き合わせて評価する。どちらの文書も変更しない。

    返り値は JSON にできる dict。``decision`` は次のいずれか:

    - ``reject`` … 取り込めない(出典が無い、遮断された出典、ID の衝突、
      参照切れ、意味の検証に落ちる、安全検査に当たる など)
    - ``review`` … 取り込む前に人の確認が要る(既存知識との矛盾、置き換え、
      新しい出典、信頼性の不足、出典を超える確信度 など)
    - ``approvable`` … 方針の範囲内。それでも反映には承認記録が要る
    """
    from .guardrails import SecurityError
    from .knowledge import KnowledgeError, typed_edges, validate_knowledge

    kb = canonicalize(kb)
    proposal = canonicalize(proposal)
    kb_index = index_by_id(kb)
    policy = _policy(kb, kb_index)
    findings: list[dict] = []

    root = proposal["root"]
    if len(root) != 1 or root[0].get("type") != "proposal":
        findings.append(_finding("malformed_proposal", REJECT,
                                 detail="ルートは proposal 型のノード 1 つだけにしてください"))
        return _report(None, proposal, kb, policy, findings, [])

    prop = root[0]
    pid = prop["id"]
    pattrs = prop.get("attrs", {})
    candidates = prop.get("children", [])

    # --- 1. 提案そのものの形 ---------------------------------------------
    if not isinstance(pattrs.get("proposed_by"), str) or not pattrs["proposed_by"].strip():
        findings.append(_finding("missing_proposed_by", REJECT, node=pid))
    try:
        from .knowledge import _time
        _time(pattrs.get("recorded_at"), pid, "recorded_at")
    except KnowledgeError:
        findings.append(_finding("invalid_recorded_at", REJECT, node=pid))
    into = pattrs.get("into")
    if into is not None and (not isinstance(into, str) or into not in kb_index):
        findings.append(_finding("unknown_into", REJECT, node=pid))
    if not candidates:
        findings.append(_finding("empty_proposal", REJECT, node=pid))
    if pid in kb_index:
        findings.append(_finding("id_exists", REJECT, node=pid))
    for code in _screen(prop):
        findings.append(_finding("unsafe_content:" + code, REJECT, node=pid))

    # --- 2. 候補ノード: ID・型・安全検査 -------------------------------------
    new_nodes = [n for n in iter_nodes({"root": candidates})]
    new_ids = {n["id"] for n in new_nodes}
    for node in new_nodes:
        if node["id"] in kb_index:
            findings.append(_finding("id_exists", REJECT, node=node["id"]))
        if node.get("type") in FORBIDDEN_TYPES:
            findings.append(_finding("forbidden_type", REJECT, node=node["id"],
                                     detail=str(node.get("type"))))
        for code in _screen(node):
            findings.append(_finding("unsafe_content:" + code, REJECT, node=node["id"]))

    summary = [{"id": n["id"], "type": n.get("type"),
                "cites": _as_list(n.get("attrs", {}).get("cites"))} for n in candidates]
    if any(f["severity"] == REJECT for f in findings):
        # 形が壊れた提案を知識体系に重ねて調べても意味がない
        return _report(pid, proposal, kb, policy, findings, summary)

    # --- 3. 重ねてみる(参照の解決と意味の検証) -----------------------------
    try:
        merged = apply_patch(kb, _add_ops(candidates, into))
        validate_refs(merged)
    except PynError as exc:
        findings.append(_finding("unresolved_reference", REJECT, detail=str(exc)))
        return _report(pid, proposal, kb, policy, findings, summary)
    try:
        validate_knowledge(merged)
        edges = typed_edges(merged)
    except SecurityError as exc:
        findings.append(_finding("unsafe_content", REJECT, detail=str(exc)))
        return _report(pid, proposal, kb, policy, findings, summary)
    except KnowledgeError as exc:
        findings.append(_finding("invalid_knowledge", REJECT, detail=str(exc)))
        return _report(pid, proposal, kb, policy, findings, summary)

    merged_index = index_by_id(merged)
    superseded = {e["target"] for e in edges
                  if e["kind"] == "supersedes" and e["source"] in kb_index}

    def inactive(node_id: str) -> bool:
        node = kb_index.get(node_id)
        return node is not None and (
            node.get("attrs", {}).get("status") in INACTIVE_STATUS or node_id in superseded)

    # --- 4. 出典と信頼性 -----------------------------------------------------
    top_ids = {n["id"] for n in candidates}
    for item in summary:
        item["reliability"] = None
    by_summary = {item["id"]: item for item in summary}
    # 入れ子のノードは、自分に cites が無ければ、提案の中のいちばん近い祖先の cites に従う。
    # 以前は最上位の候補しか見ていなかったので、出典を求めない型(source / relation)の
    # 下に置いた主張が、出典なしで通っていた。信頼性と確信度の検査も素通りしていた。
    inherited: dict[str, list] = {}

    def spread(nodes, cites_above):
        for n in nodes:
            own = _as_list(n.get("attrs", {}).get("cites"))
            inherited[n["id"]] = own or cites_above
            spread(n.get("children", []), own or cites_above)

    spread(candidates, [])

    for node in new_nodes:
        ident = node["id"]
        attrs = node.get("attrs", {})
        if node.get("type") == "source":
            # 提案者は自分の出典の信頼性を自己申告できない。台帳に載せるかは人が決める
            findings.append(_finding("new_source", REVIEW, node=ident))
            if "reliability" in attrs or attrs.get("status") not in (None, "unverified"):
                findings.append(_finding("self_assessed_reliability_ignored", INFO, node=ident))
        cites = inherited[ident]
        if not cites:
            if node.get("type") not in NO_CITES_TYPES:
                findings.append(_finding("missing_source", REJECT, node=ident))
            continue
        scores = []
        for ref in cites:
            src_attrs = merged_index[ref].get("attrs", {})
            if ref in kb_index:
                if src_attrs.get("status") == "blocked":
                    findings.append(_finding("blocked_source", REJECT, node=ident, about=ref))
                    continue
                score = src_attrs.get("reliability", policy["unverified_reliability"])
                if src_attrs.get("status") == "unverified":
                    score = min(score, policy["unverified_reliability"])
            else:
                score = policy["unverified_reliability"]
            if inactive(ref):
                findings.append(_finding("relies_on_inactive", REVIEW, node=ident, about=ref))
            scores.append(score)
        if not scores:
            continue
        reliability = max(scores)
        if ident in by_summary:
            by_summary[ident]["reliability"] = reliability
        if reliability < policy["min_reliability"]:
            findings.append(_finding("low_reliability", REVIEW, node=ident))
        confidence = attrs.get("confidence")
        if _number01(confidence) and confidence > reliability:
            findings.append(_finding("overconfident", REVIEW, node=ident))

    # --- 5. 既存知識との矛盾 -------------------------------------------------
    existing_claims: dict = {}
    for node in iter_nodes(kb):
        key = _claim_key(node)
        if key is not None and not inactive(node["id"]):
            existing_claims.setdefault(key, []).append(node)
    seen_new: dict = {}
    for node in new_nodes:
        key = _claim_key(node)
        if key is None:
            continue
        value = node["attrs"].get("value")
        for old in existing_claims.get(key, []):
            if not _overlaps(node, old):
                continue
            if old.get("attrs", {}).get("value") == value:
                findings.append(_finding("corroborates", INFO, node=node["id"], about=old["id"]))
            else:
                findings.append(_finding("conflicts_with", REVIEW, node=node["id"], about=old["id"]))
        for other in seen_new.get(key, []):
            if _overlaps(node, other) and other["attrs"].get("value") != value:
                findings.append(_finding("internal_conflict", REVIEW, node=node["id"],
                                         about=other["id"]))
        seen_new.setdefault(key, []).append(node)

    for edge in edges:
        if edge["id"] not in new_ids:
            continue
        old_ends = [end for end in (edge["source"], edge["target"]) if end in kb_index]
        if edge["kind"] == "contradicts" and old_ends:
            for end in old_ends:
                findings.append(_finding("contradicts_existing", REVIEW, node=edge["id"], about=end))
        elif edge["kind"] == "supersedes" and edge["target"] in kb_index:
            findings.append(_finding("supersedes_existing", REVIEW, node=edge["id"],
                                     about=edge["target"]))
        else:
            for end in old_ends:
                if inactive(end):
                    findings.append(_finding("relies_on_inactive", REVIEW, node=edge["id"],
                                             about=end))
    for node in new_nodes:
        evidence = node.get("attrs", {}).get("evidence_id")
        if isinstance(evidence, str) and inactive(evidence):
            findings.append(_finding("relies_on_inactive", REVIEW, node=node["id"], about=evidence))

    return _report(pid, proposal, kb, policy, findings, summary)


# --------------------------------------------------------------------------
# 承認と反映
# --------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _check_schema(value: object, schema: str, keys: tuple[str, ...]) -> dict:
    if not isinstance(value, dict) or value.get("schema") != schema:
        raise IntakeError(f"{schema} の形式ではありません")
    missing = [k for k in keys if k not in value]
    if missing:
        raise IntakeError(f"{schema} に必要なキーがありません: {', '.join(missing)}")
    return value


def approve(report: dict, by: str, *, accept_review: bool = False,
            now: str | None = None) -> dict:
    """評価の結果を承認し、承認記録(JSON にできる dict)を返す。

    却下(reject)は承認できない。要確認(review)は、確認したことを
    ``accept_review=True`` で明示したときだけ承認できる。
    """
    report = _check_schema(report, REPORT_SCHEMA,
                           ("proposal", "decision", "proposal_digest", "kb_digest", "findings"))
    if not isinstance(by, str) or not by.strip():
        raise IntakeError("承認者(--by)を書いてください")
    if report["decision"] == REJECT:
        raise IntakeError("却下(reject)された提案は承認できません")
    reviewed = sorted({f["code"] for f in report["findings"] if f.get("severity") == REVIEW})
    if report["decision"] == REVIEW and not accept_review:
        raise IntakeError(
            f"要確認の所見が {len(reviewed)} 種類あります({', '.join(reviewed)})。"
            "確認したうえで承認するなら --accept-review を付けてください")
    return {
        "schema": APPROVAL_SCHEMA,
        "proposal": report["proposal"],
        "decision": report["decision"],
        "proposal_digest": report["proposal_digest"],
        "kb_digest": report["kb_digest"],
        "approved_by": by,
        "approved_at": now or _now(),
        "accepted_findings": reviewed,
    }


def admit(kb: dict, proposal: dict, approval: dict) -> dict:
    """承認された提案を知識体系へ反映した、新しい文書を返す(引数は変更しない)。

    反映の直前にもう一度評価し、承認記録のダイジェストが今の提案・知識体系と
    一致しなければ断る。承認したあとに提案が書き換えられた場合も、知識体系が
    先に変わって評価の前提が崩れた場合も、同じく断る(評価からやり直す)。
    """
    from .knowledge import validate_knowledge

    approval = _check_schema(approval, APPROVAL_SCHEMA,
                             ("proposal", "decision", "proposal_digest", "kb_digest",
                              "approved_by", "approved_at"))
    report = evaluate(kb, proposal)
    if (report["proposal_digest"] != approval["proposal_digest"]
            or report["kb_digest"] != approval["kb_digest"]):
        raise IntakeError("承認記録が今の提案・知識体系と一致しません。評価からやり直してください")
    if report["decision"] == REJECT:
        raise IntakeError("提案は却下(reject)と評価されました")
    if report["decision"] == REVIEW and approval["decision"] != REVIEW:
        raise IntakeError("要確認の所見を確認した承認記録ではありません")

    kb = canonicalize(kb)
    prop = canonicalize(proposal)["root"][0]
    pattrs = prop.get("attrs", {})
    candidates = prop.get("children", [])
    policy = report["policy"]

    record_attrs = {
        "proposed_by": pattrs["proposed_by"],
        "proposed_at": pattrs["recorded_at"],
        "approved_by": approval["approved_by"],
        "approved_at": approval["approved_at"],
        "decision": report["decision"],
        "admitted": [n["id"] for n in candidates],
        "proposal_digest": report["proposal_digest"],
    }
    if approval.get("accepted_findings"):
        record_attrs["accepted_findings"] = list(approval["accepted_findings"])
    record = {"id": prop["id"], "type": "intake", "attrs": record_attrs}
    if prop.get("title"):
        record["title"] = prop["title"]
    if prop.get("prose"):
        record["prose"] = list(prop["prose"])  # 提案者が書いた「なぜ」をそのまま残す

    ops = _add_ops(candidates, pattrs.get("into"), mark=prop["id"])
    record_op = {"op": "add", "node": record}
    if "ledger" in policy:
        record_op["parent"] = policy["ledger"]
    ops.append(record_op)

    result = apply_patch(kb, ops)
    validate_refs(result)
    validate_knowledge(result)
    return result


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _read_pyn(path: Path) -> dict:
    from .guardrails import safe_read
    from .parser import parse

    return parse(safe_read(path, root=path.parent))


def _write_new(path: Path, data: dict) -> None:
    # 承認記録は上書きしない(取り違えや差し替えに気づけるように)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def main(argv: list[str] | None = None) -> int:
    """``python -m pyn.intake {evaluate|approve|admit} ...``"""
    from .cli import use_utf8_stdio, warn_dropped_comments, write_text

    use_utf8_stdio()  # pyn の CLI と同じく UTF-8・LF で書く(t-19 / t-21)
    ap = argparse.ArgumentParser(prog="pyn.intake", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="action", required=True)

    ev = sub.add_parser("evaluate", help="提案を評価する(何も書き換えない)")
    ev.add_argument("kb", type=Path, help="知識体系の .pyn")
    ev.add_argument("proposal", type=Path, help="提案の .pyn")
    ev.add_argument("-o", "--out", type=Path, help="評価結果(JSON)の出力先。既定は標準出力")

    apv = sub.add_parser("approve", help="評価結果を承認し、承認記録を作る")
    apv.add_argument("report", type=Path, help="evaluate の出力")
    apv.add_argument("--by", required=True, help="承認者(例: human:owner)")
    apv.add_argument("--accept-review", action="store_true",
                     help="要確認(review)の所見を確認したうえで承認する")
    apv.add_argument("-o", "--out", type=Path, required=True,
                     help="承認記録(JSON)の出力先。既にあるファイルには書かない")

    ad = sub.add_parser("admit", help="承認された提案を知識体系へ反映する")
    ad.add_argument("kb", type=Path)
    ad.add_argument("proposal", type=Path)
    ad.add_argument("--approval", type=Path, required=True, help="approve の出力")
    ad.add_argument("-o", "--out", type=Path, help="出力先(既定は知識体系を上書き)")
    ad.add_argument("--dry-run", action="store_true", help="反映できるかを確かめるだけ")

    args = ap.parse_args(argv)
    from .guardrails import SecurityError
    from .formatter import to_pyn_a

    try:
        if args.action == "evaluate":
            try:
                proposal = _read_pyn(args.proposal)
            except SecurityError as exc:
                print(f"reject: 提案が安全検査に当たりました({exc})", file=sys.stderr)
                return EXIT["reject"]
            report = evaluate(_read_pyn(args.kb), proposal)
            text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
            if args.out is None:
                sys.stdout.write(text)
            else:
                write_text(args.out, text)
            counts = {s: sum(1 for f in report["findings"] if f["severity"] == s)
                      for s in (REJECT, REVIEW, INFO)}
            print(f"{report['decision']}  ({REJECT} {counts[REJECT]} / {REVIEW} {counts[REVIEW]}"
                  f" / {INFO} {counts[INFO]})", file=sys.stderr)
            return EXIT[report["decision"]]

        if args.action == "approve":
            report = json.loads(args.report.read_text(encoding="utf-8"))
            approval = approve(report, args.by, accept_review=args.accept_review)
            _write_new(args.out, approval)
            print(f"approved {approval['proposal']}  → {args.out}", file=sys.stderr)
            return 0

        approval = json.loads(args.approval.read_text(encoding="utf-8"))
        result = admit(_read_pyn(args.kb), _read_pyn(args.proposal), approval)
        out = args.out or args.kb
        if args.dry_run:
            print(f"反映できます(--dry-run のため {out} は書き換えていません)", file=sys.stderr)
            return 0
        warn_dropped_comments(args.kb, out)
        write_text(out, to_pyn_a(result))
        print(f"admitted {approval['proposal']}  → {out}", file=sys.stderr)
        return 0
    except FileExistsError as exc:
        print(f"pyn.intake: 既にあるファイルには書きません: {exc.filename}", file=sys.stderr)
        return 1
    except (IntakeError, SecurityError, PynError, ValueError, OSError) as exc:
        print(f"pyn.intake: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
