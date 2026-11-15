"""Experimental typed knowledge relationships for PYN documents.

This is an *opt-in semantic layer*, not a new PYN syntax or canonical format.
A ``relation`` node uses ordinary PYN scalar attributes::

    relation e1 "Evidence for a hypothesis":
      source: observation-1
      kind: supports
      target: hypothesis-1

Normal ``@id`` / ``§id`` references still work exactly as before. Relations
are additionally validated, extracted and projected as small JSON contexts for
AI agents. Nothing here decides whether a hypothesis is true.

The standard library is the only dependency. This module is intentionally
self-contained so it does not alter existing parsers, renderers or tests.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import deque
from datetime import datetime
from pathlib import Path

__all__ = ["KnowledgeError", "validate_knowledge", "typed_edges", "context"]

_KIND_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
_TIME_KEYS = ("occurred_at", "recorded_at", "valid_from", "valid_until")
# Open vocabulary: applications can add predicates without changing the format.
COMMON_KINDS = (
    "supports", "contradicts", "supersedes", "derived_from", "causes",
    "tests", "depends_on",
)


class KnowledgeError(ValueError):
    """Invalid typed-knowledge semantics, without changing core PYN errors."""


def _nodes(doc: dict) -> list[dict]:
    if not isinstance(doc, dict) or not isinstance(doc.get("root"), list):
        raise KnowledgeError("document must be a PYN-style object with a root list")
    result: list[dict] = []
    seen: set[str] = set()
    stack = list(reversed(doc["root"]))
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            raise KnowledgeError("each node must be an object")
        ident = node.get("id")
        if not isinstance(ident, str) or not ident:
            raise KnowledgeError("every node must have a nonempty string ID")
        if ident in seen:
            raise KnowledgeError(f"duplicate node ID: {ident}")
        seen.add(ident)
        children = node.get("children", [])
        if not isinstance(children, list):
            raise KnowledgeError(f"{ident}: children must be a list")
        result.append(node)
        stack.extend(reversed(children))
    return result


def _time(value: object, node_id: str, field: str) -> datetime:
    if not isinstance(value, str):
        raise KnowledgeError(f"{node_id}: {field} must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise KnowledgeError(f"{node_id}: invalid {field}: {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise KnowledgeError(f"{node_id}: {field} requires an explicit timezone")
    return parsed


def _validated(doc: dict) -> tuple[list[dict], list[dict]]:
    # Fail closed on recognizable hostile text before building agent contexts.
    # This screening cannot replace a trusted tool authorization boundary.
    from .guardrails import scan_text, SecurityError
    nodes = _nodes(doc)
    for node in nodes:
        readable = [str(node.get("title") or "")]
        readable.extend(node.get("prose", []))
        attrs = node.get("attrs") or {}
        for value in attrs.values():
            if isinstance(value, list):
                readable.extend(str(item) for item in value)
            else:
                readable.append(str(value))
        for text_part in readable:
            findings = scan_text(text_part)
            if findings:
                raise SecurityError(f"unsafe_knowledge_node:{node['id']}:{findings[0].code}")
    by_id = {node["id"]: node for node in nodes}
    edges: list[dict] = []

    for node in nodes:
        ident = node["id"]
        attrs = node.get("attrs", {})
        if not isinstance(attrs, dict):
            raise KnowledgeError(f"{ident}: attrs must be an object")

        if "confidence" in attrs:
            value = attrs["confidence"]
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or not 0 <= value <= 1):
                raise KnowledgeError(f"{ident}: confidence must be a finite number in [0, 1]")

        times = {key: _time(attrs[key], ident, key)
                 for key in _TIME_KEYS if key in attrs}
        if ("valid_from" in times and "valid_until" in times
                and times["valid_until"] < times["valid_from"]):
            raise KnowledgeError(f"{ident}: valid_until precedes valid_from")

        if "evidence_id" in attrs:
            evidence = attrs["evidence_id"]
            if not isinstance(evidence, str) or evidence not in by_id:
                raise KnowledgeError(f"{ident}: unknown evidence_id {evidence!r}")

        if node.get("type") != "relation":
            continue
        source, target, kind = (attrs.get(key) for key in ("source", "target", "kind"))
        if not isinstance(source, str) or source not in by_id:
            raise KnowledgeError(f"{ident}: unknown relation source {source!r}")
        if not isinstance(target, str) or target not in by_id:
            raise KnowledgeError(f"{ident}: unknown relation target {target!r}")
        if not isinstance(kind, str) or not _KIND_RE.fullmatch(kind):
            raise KnowledgeError(f"{ident}: kind must be a lowercase predicate name")
        if source == target and kind in ("contradicts", "supersedes"):
            raise KnowledgeError(f"{ident}: {kind} cannot point to the same node")
        edge = {"id": ident, "source": source, "kind": kind, "target": target}
        for key in ("confidence", "evidence_id", "recorded_at"):
            if key in attrs:
                edge[key] = attrs[key]
        edges.append(edge)

    # A newer belief can supersede an older belief. A cycle cannot be a valid
    # replacement chain; contradictions, on the other hand, can be mutual.
    successor: dict[str, list[str]] = {}
    for edge in edges:
        if edge["kind"] == "supersedes":
            successor.setdefault(edge["source"], []).append(edge["target"])
    marks: dict[str, int] = {}

    def visit(node_id: str) -> None:
        state = marks.get(node_id, 0)
        if state == 1:
            raise KnowledgeError(f"supersedes relation has a cycle at {node_id}")
        if state == 2:
            return
        marks[node_id] = 1
        for old in successor.get(node_id, ()):
            visit(old)
        marks[node_id] = 2

    for start in successor:
        visit(start)
    return nodes, edges


def validate_knowledge(doc: dict) -> None:
    """Validate opt-in relation nodes and optional evidence/temporal metadata.

    Checks referential integrity, provenance references, confidence range,
    timezone-aware timestamps, valid-time ordering and supersession cycles.
    Does not mutate ``doc`` or infer truth from contradictory claims.
    """
    _validated(doc)


def typed_edges(doc: dict) -> list[dict]:
    """Return validated typed relationships in document order (JSON-ready)."""
    return _validated(doc)[1]


def context(doc: dict, center: str, hops: int = 1) -> dict:
    """Create a bounded, explicit, graph-neighborhood JSON projection.

    Both incoming and outgoing relations count as a hop, but their original
    directions are preserved. The result copies only selected nodes and
    relations; it does not pretend to be a valid standalone PYN document.
    """
    if isinstance(hops, bool) or not isinstance(hops, int) or hops < 0 or hops > 10:
        raise KnowledgeError("hops must be an integer between 0 and 10")
    nodes, edges = _validated(doc)
    by_id = {n["id"]: n for n in nodes}
    if center not in by_id:
        raise KnowledgeError(f"unknown center node: {center!r}")
    adjacent: dict[str, set[str]] = {}
    for edge in edges:
        s, t = edge["source"], edge["target"]
        adjacent.setdefault(s, set()).add(t)
        adjacent.setdefault(t, set()).add(s)
    selected = {center}
    queue = deque([(center, 0)])
    while queue:
        current, distance = queue.popleft()
        if distance >= hops:
            continue
        for neighbor in sorted(adjacent.get(current, ())):
            if neighbor not in selected:
                selected.add(neighbor)
                queue.append((neighbor, distance + 1))

    def snapshot(node: dict) -> dict:
        # Preserve readable content and provenance, but avoid bringing in
        # unrelated descendants and potentially large subtrees.
        return {key: node[key] for key in ("id", "type", "title", "attrs", "prose")
                if key in node}

    return {
        "center": center,
        "hops": hops,
        "nodes": [snapshot(n) for n in nodes if n["id"] in selected],
        "relations": [e for e in edges
                      if e["source"] in selected and e["target"] in selected],
    }


def main(argv: list[str] | None = None) -> int:
    """``python -m pyn.knowledge {check|edges|context} document.pyn``."""
    from .cli import use_utf8_stdio
    use_utf8_stdio()  # pyn の CLI と同じく UTF-8・LF で書く(t-19 / t-21)
    parser = argparse.ArgumentParser(description="Optional typed knowledge graph for PYN")
    parser.add_argument("action", choices=("check", "edges", "context"))
    parser.add_argument("path", type=Path)
    parser.add_argument("--id", help="center node ID for the context action")
    parser.add_argument("--hops", type=int, default=1, help="0..10 relation hops")
    args = parser.parse_args(argv)
    if args.action != "context" and args.id is not None:
        parser.error("--id is only valid with context")
    if args.action != "context" and args.hops != 1:
        parser.error("--hops is only valid with context")
    if args.action == "context" and not args.id:
        parser.error("context requires --id")
    from .errors import PynError

    try:
        from .guardrails import safe_read
        if args.path.suffix == ".json":
            from .guardrails import inspect_bytes, MAX_BYTES
            if args.path.stat().st_size > MAX_BYTES:
                raise ValueError("oversized_json_file")
            raw = inspect_bytes(args.path.read_bytes())
        else:
            raw = safe_read(args.path, root=args.path.parent)
        if args.path.suffix == ".json":
            doc = json.loads(raw)
        else:
            from .parser import parse
            doc = parse(raw)
        # Core PYN references have a separate meaning and must also be valid.
        from .refs import validate_refs
        validate_refs(doc)
        if args.action == "check":
            validate_knowledge(doc)
            print("OK: typed relations, provenance links, times and confidence")
        else:
            output = typed_edges(doc) if args.action == "edges" else context(
                doc, args.id, args.hops)
            print(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except (OSError, ValueError, PynError) as exc:
        print(f"knowledge: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
