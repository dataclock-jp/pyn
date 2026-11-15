"""Cross-document change-impact analysis: deterministic review *proposals*.

Never executes an agent, edits PYN sources, runs tools or decides that a claim
is true. Documents are screened before ingestion. A baseline is an unauthenticated
snapshot: place it in a trusted directory/version control and review the plan.

Commands:
  python -m pyn.impact snapshot --root <docs> --output before.json
  python -m pyn.impact plan --root <docs> --baseline before.json --output review.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import deque
from pathlib import Path

from .guardrails import MAX_FILES, SecurityError, safe_read, require_read_only

SCHEMA = "pyn-impact-snapshot-v1"
MAX_NODES = 100_000
MAX_EDGES = 500_000
KEY_RE = re.compile(r"^[^#\n\r\\]+\.pyn#[A-Za-z0-9_][A-Za-z0-9_.-]*$")


class ImpactError(ValueError):
    """Invalid workspace, graph or baseline."""


def _key(rel: str, ident: str) -> str:
    key = f"{rel}#{ident}"
    if not KEY_RE.fullmatch(key):
        raise ImpactError("unsafe file path or node identifier")
    return key


def _load(path: Path, root: Path) -> dict:
    # The guard runs before parsing: untrusted instructions must not reach the
    # PYN parser or any AI context in this workflow.
    raw = safe_read(path, root=root)
    from .parser import parse
    from .refs import validate_refs
    doc = parse(raw)
    validate_refs(doc)
    return doc


def _flatten(doc: dict):
    stack = [(node, None, i) for i, node in reversed(list(enumerate(doc.get("root", []))))]
    while stack:
        node, parent, pos = stack.pop()
        yield node, parent, pos
        stack.extend((child, node["id"], j)
                     for j, child in reversed(list(enumerate(node.get("children", [])))))


def snapshot_workspace(root: str | Path, *, max_files: int = MAX_FILES) -> dict:
    """Screen, parse and index *all* .pyn files, including incoming referrers.

    Cross-links are not used to select files: a document which links *to* a
    changed document still needs review even if the changed one has no links.
    """
    require_read_only("snapshot")
    boundary = Path(root).resolve(strict=True)
    if not boundary.is_dir():
        raise ImpactError("root is not a directory")
    if isinstance(max_files, bool) or not isinstance(max_files, int) or not 1 <= max_files <= 4096:
        raise ImpactError("max_files must be between 1 and 4096")
    files = sorted(boundary.rglob("*.pyn"))
    if len(files) > max_files:
        raise ImpactError("too many PYN files")
    from .knowledge import typed_edges
    from .xrefs import scan_links
    data: dict[str, str] = {}
    dependencies: set[tuple[str, str, str]] = set()
    for file in files:
        rel = file.relative_to(boundary).as_posix()
        doc = _load(file, boundary)
        index = {node["id"]: _key(rel, node["id"]) for node, _, _ in _flatten(doc)}
        local: list[tuple[str, dict]] = []
        for node, parent, pos in _flatten(doc):
            ident = node["id"]
            here = index[ident]
            # Parent/sibling ordering is observable structure; descendants'
            # contents are not copied into an ancestor's digest.
            flat = {k: v for k, v in node.items() if k != "children"}
            flat["_parent"] = parent
            flat["_position"] = pos
            flat["_children"] = [n["id"] for n in node.get("children", [])]
            encoded = json.dumps(flat, ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":"), allow_nan=False).encode("utf-8")
            data[here] = hashlib.sha256(encoded).hexdigest()
            local.append((here, node))
            for target in node.get("refs", []):
                dependencies.add((here, _key(rel, target), "local_ref"))
            attrs = node.get("attrs") or {}
            for field in ("evidence_id", "depends", "depends_on"):
                value = attrs.get(field, [])
                for target in (value if isinstance(value, list) else [value]):
                    if target and isinstance(target, str):
                        dependencies.add((here, _key(rel, target), "attr:" + field))
            for prose in node.get("prose", []):
                for link in scan_links(prose):
                    # Resolve all paths inside the authorized root. Do not read
                    # arbitrary linked targets or follow symbolic links.
                    dest = (file.parent / link.path).resolve()
                    if not dest.is_relative_to(boundary):
                        raise ImpactError("cross-document link escapes root")
                    if dest.suffix != ".pyn":
                        raise ImpactError("cross-document link has wrong suffix")
                    dependencies.add((here, _key(dest.relative_to(boundary).as_posix(),
                                                  link.node_id), "cross_ref"))
        for rel_edge in typed_edges(doc):
            relation = index[rel_edge["id"]]
            source = index[rel_edge["source"]]
            target = index[rel_edge["target"]]
            kind = rel_edge["kind"]
            # Relational assertions themselves require review when either
            # operand changes, and either operand may need reassessment when
            # the relational assertion changes.
            dependencies.add((relation, source, "relation_operand:" + kind))
            dependencies.add((relation, target, "relation_operand:" + kind))
            dependencies.add((source, relation, "relation_revision:" + kind))
            dependencies.add((target, relation, "relation_revision:" + kind))
            if kind in ("supports", "causes", "tests"):
                dependencies.add((target, source, "typed:" + kind))
            elif kind in ("derived_from", "depends_on", "supersedes"):
                dependencies.add((source, target, "typed:" + kind))
            elif kind == "contradicts":
                dependencies.add((source, target, "typed:" + kind))
                dependencies.add((target, source, "typed:" + kind))
    if len(data) > MAX_NODES or len(dependencies) > MAX_EDGES:
        raise ImpactError("knowledge graph exceeds configured bounds")
    return {
        "schema": SCHEMA,
        "nodes": dict(sorted(data.items())),
        "dependencies": [
            {"dependent": dependent, "prerequisite": prerequisite, "kind": kind}
            for dependent, prerequisite, kind in sorted(dependencies)
        ],
    }


def _validate_snapshot(value: object) -> dict:
    if not isinstance(value, dict) or value.get("schema") != SCHEMA:
        raise ImpactError("unexpected snapshot schema")
    nodes = value.get("nodes")
    deps = value.get("dependencies")
    if not isinstance(nodes, dict) or not isinstance(deps, list):
        raise ImpactError("invalid snapshot payload")
    if len(nodes) > MAX_NODES or len(deps) > MAX_EDGES:
        raise ImpactError("snapshot too large")
    for key, value_hash in nodes.items():
        if not isinstance(key, str) or not KEY_RE.fullmatch(key):
            raise ImpactError("invalid snapshot node key")
        path_part = key.rsplit("#", 1)[0]
        if (any(part in ("", ".", "..") for part in path_part.split("/"))
                or any(not (part.isalnum() or part in "_- .") for part in path_part.replace("/", ""))):
            raise ImpactError("unsafe snapshot file path")
        if not isinstance(value_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", value_hash):
            raise ImpactError("invalid snapshot digest")
    for dep in deps:
        if (not isinstance(dep, dict) or
            any(not isinstance(dep.get(k), str) for k in ("dependent", "prerequisite", "kind")) or
            not KEY_RE.fullmatch(dep["dependent"]) or
            not KEY_RE.fullmatch(dep["prerequisite"]) or
            len(dep["kind"]) > 100 or not re.fullmatch(r"[a-z_:0-9-]+", dep["kind"])):
            raise ImpactError("invalid snapshot dependency")
    return value


def review_plan(before: dict, after: dict, *, max_hops: int = 2) -> dict:
    """Compute changed/affected IDs using old+new dependency edges.

    Review entries are suggestions only. No tool execution, truth adjudication,
    file rewriting, notification, or autonomous agent scheduling occurs.
    """
    require_read_only("review_plan")
    before = _validate_snapshot(before)
    after = _validate_snapshot(after)
    if isinstance(max_hops, bool) or not isinstance(max_hops, int) or not 1 <= max_hops <= 10:
        raise ImpactError("max_hops must be an integer between 1 and 10")
    old, new = before["nodes"], after["nodes"]
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    modified = sorted(k for k in old.keys() & new.keys() if old[k] != new[k])
    changed = set(added + removed + modified)
    reverse: dict[str, list[tuple[str, str]]] = {}
    for graph in (before, after):
        for dep in graph["dependencies"]:
            reverse.setdefault(dep["prerequisite"], []).append((dep["dependent"], dep["kind"]))
    queue = deque((key, 0) for key in sorted(changed))
    visited = set(changed)
    affected = []
    while queue:
        current, distance = queue.popleft()
        if distance >= max_hops:
            continue
        for dependent, reason in sorted(set(reverse.get(current, []))):
            if dependent in visited:
                continue
            visited.add(dependent)
            # Removed nodes cannot be reviewed, but their former dependents
            # can and must be surfaced even if now dangling.
            if dependent in new:
                affected.append({
                    "id": dependent, "via": current, "reason": reason,
                    "hops": distance + 1, "status": "needs_human_or_agent_review",
                })
            queue.append((dependent, distance + 1))
    missing = sorted({d["prerequisite"] for d in after["dependencies"]
                      if d["prerequisite"] not in new})
    return {
        "proposal_only": True,
        "actions_authorized": False,
        "added": added,
        "removed": removed,
        "modified": modified,
        "affected": affected,
        "missing_references": missing,
        "summary": {"changed": len(changed), "affected": len(affected),
                    "missing_references": len(missing)},
    }


def _write_json(path: Path, data: dict) -> None:
    # Exclusive creation avoids accidental overwrite of an existing baseline.
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def main(argv: list[str] | None = None) -> int:
    from .cli import use_utf8_stdio
    use_utf8_stdio()  # pyn の CLI と同じく UTF-8・LF で書く(t-19 / t-21)
    ap = argparse.ArgumentParser(description="Create a read-only PYN impact review plan")
    ap.add_argument("action", choices=("snapshot", "plan"))
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--baseline", type=Path)
    ap.add_argument("--output", type=Path)
    ap.add_argument("--hops", type=int, default=2)
    args = ap.parse_args(argv)
    if args.action == "plan" and args.baseline is None:
        ap.error("plan requires --baseline")
    if args.action == "snapshot" and args.baseline is not None:
        ap.error("snapshot does not accept --baseline")
    try:
        current = snapshot_workspace(args.root)
        if args.action == "snapshot":
            result = current
        else:
            if args.baseline.stat().st_size > 20_000_000:
                raise ImpactError("baseline file too large")
            before = json.loads(args.baseline.read_text(encoding="utf-8"))
            result = review_plan(before, current, max_hops=args.hops)
        if args.output is None:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            _write_json(args.output, result)
            print(f"Wrote {args.output} (review proposal only)", file=sys.stderr)
        return 0
    except (ValueError, OSError, UnicodeError, TypeError) as exc:
        print(f"PYN impact: refused: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
