"""Opt-in AI memory semantics; no canonical PYN format changes."""

import copy

import pytest

from pyn.knowledge import KnowledgeError, context, typed_edges, validate_knowledge


def _doc(*nodes):
    return {"pyn": "1.0", "root": list(nodes)}


def _node(ident, node_type="observation", **attrs):
    return {"id": ident, "type": node_type, "attrs": attrs}


def _edge(ident, source, target, kind="supports", **attrs):
    return _node(ident, "relation", source=source, target=target, kind=kind, **attrs)


def test_extract_typed_edges_preserves_direction_and_metadata():
    doc = _doc(_node("a"), _node("b"), _edge("r", "a", "b", confidence=0.7))
    assert typed_edges(doc) == [{
        "id": "r", "source": "a", "kind": "supports", "target": "b", "confidence": 0.7
    }]


def test_mixed_normal_refs_and_relation_graph_do_not_conflict():
    doc = _doc({"id": "a", "type": "observation", "prose": ["@b"]},
               _node("b"), _edge("r", "a", "b"))
    assert len(typed_edges(doc)) == 1
    assert doc["root"][0]["prose"] == ["@b"]


@pytest.mark.parametrize("target", ["missing", None, 5])
def test_missing_or_invalid_relation_target_fails(target):
    with pytest.raises(KnowledgeError, match="target"):
        validate_knowledge(_doc(_node("a"), _edge("r", "a", target)))


def test_invalid_relation_source_fails():
    with pytest.raises(KnowledgeError, match="source"):
        validate_knowledge(_doc(_node("a"), _edge("r", "unknown", "a")))


@pytest.mark.parametrize("confidence", [-0.01, 1.1, True, "certain", float("nan"), float("inf")])
def test_confidence_must_be_finite_numeric_probability(confidence):
    with pytest.raises(KnowledgeError, match="confidence"):
        validate_knowledge(_doc(_node("a", confidence=confidence)))


def test_valid_confidence_boundaries():
    validate_knowledge(_doc(_node("a", confidence=0), _node("b", confidence=1.0)))


def test_evidence_id_must_resolve():
    with pytest.raises(KnowledgeError, match="evidence_id"):
        validate_knowledge(_doc(_node("a", evidence_id="missing")))
    validate_knowledge(_doc(_node("a", evidence_id="b"), _node("b")))


def test_dates_require_timezone_and_valid_order():
    with pytest.raises(KnowledgeError, match="timezone"):
        validate_knowledge(_doc(_node("a", occurred_at="2026-11-14T12:00:00")))
    with pytest.raises(KnowledgeError, match="precedes"):
        validate_knowledge(_doc(_node("a", valid_from="2026-11-15T00:00:00Z",
                                     valid_until="2026-11-14T00:00:00+00:00")))
    validate_knowledge(_doc(_node("a", valid_from="2026-11-14T10:00:00+09:00",
                                  valid_until="2026-11-14T02:00:00Z")))


def test_contradiction_does_not_assert_truth_or_require_resolution():
    doc = _doc(_node("a"), _node("b"),
               _edge("r1", "a", "b", "contradicts"),
               _edge("r2", "b", "a", "contradicts"))
    assert len(typed_edges(doc)) == 2


def test_supersedes_acyclic_chain_and_cycle_rejected():
    doc = _doc(_node("a"), _node("b"), _node("c"),
               _edge("r1", "c", "b", "supersedes"),
               _edge("r2", "b", "a", "supersedes"))
    validate_knowledge(doc)
    doc["root"].append(_edge("r3", "a", "c", "supersedes"))
    with pytest.raises(KnowledgeError, match="cycle"):
        validate_knowledge(doc)


def test_supersedes_self_rejected():
    with pytest.raises(KnowledgeError, match="same node"):
        validate_knowledge(_doc(_node("a"), _edge("r", "a", "a", "supersedes")))


def test_context_bounded_traversal_and_nonmutation():
    doc = _doc(_node("a", confidence=0.2), _node("b"), _node("c"),
               _node("unrelated"), _edge("r1", "a", "b"), _edge("r2", "b", "c"))
    before = copy.deepcopy(doc)
    one_hop = context(doc, "a", 1)
    assert {n["id"] for n in one_hop["nodes"]} == {"a", "b"}
    assert [r["id"] for r in one_hop["relations"]] == ["r1"]
    two_hops = context(doc, "a", 2)
    assert {n["id"] for n in two_hops["nodes"]} == {"a", "b", "c"}
    assert [r["id"] for r in two_hops["relations"]] == ["r1", "r2"]
    assert doc == before


def test_context_zero_hops_only_center():
    doc = _doc(_node("a"), _node("b"), _edge("r", "a", "b"))
    assert [n["id"] for n in context(doc, "a", 0)["nodes"]] == ["a"]
    assert context(doc, "a", 0)["relations"] == []


def test_nested_nodes_and_duplicate_ids():
    doc = _doc({"id": "root", "type": "spec", "children": [
        _node("a"), _node("b"), _edge("r", "a", "b")
    ]})
    assert len(typed_edges(doc)) == 1
    doc["root"].append(_node("a"))
    with pytest.raises(KnowledgeError, match="duplicate"):
        validate_knowledge(doc)


def test_relation_predicate_vocabulary_is_extensible_but_well_formed():
    doc = _doc(_node("a"), _node("b"), _edge("r", "a", "b", "observed_with"))
    validate_knowledge(doc)
    doc["root"][-1]["attrs"]["kind"] = "BAD KIND"
    with pytest.raises(KnowledgeError, match="kind"):
        validate_knowledge(doc)


def test_invalid_context_arguments():
    doc = _doc(_node("a"))
    with pytest.raises(KnowledgeError, match="unknown center"):
        context(doc, "missing")
    for hops in (-1, True, 11, 0.5):
        with pytest.raises(KnowledgeError, match="hops"):
            context(doc, "a", hops)


def test_real_pyn_sample_roundtrips_and_validates_when_parser_available():
    """Integration is exercised in the actual PYN repository."""
    import importlib.util
    from pathlib import Path

    if importlib.util.find_spec("pyn.parser") is None:
        pytest.skip("PYN core modules not present in isolated patch workspace")
    from pyn import parse, to_pyn_a, to_pyn_b, validate_refs
    sample = Path(__file__).resolve().parents[1] / "samples/agent-memory.pyn"
    doc = parse(sample.read_text(encoding="utf-8"))
    validate_refs(doc)
    validate_knowledge(doc)
    assert len(typed_edges(doc)) == 3
    assert parse(to_pyn_a(doc)) == doc
    assert parse(to_pyn_b(doc)) == doc
    assert {n["id"] for n in context(doc, "hyp-02", 1)["nodes"]} == {
        "hyp-02", "hyp-01", "obs-02"
    }


def test_real_pyn_docs_parse_when_parser_available():
    import importlib.util
    from pathlib import Path

    if importlib.util.find_spec("pyn.parser") is None:
        pytest.skip("PYN core modules not present in isolated patch workspace")
    from pyn import parse, validate_refs
    docs = Path(__file__).resolve().parents[1] / "docs/knowledge-memory.pyn"
    doc = parse(docs.read_text(encoding="utf-8"))
    validate_refs(doc)
    validate_knowledge(doc)
