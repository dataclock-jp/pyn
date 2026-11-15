"""Opt-in, cross-document PYN links (experimental).

Syntax in prose: ``@{relative/path.pyn#node-id}``.

This is an additive layer over the v1.1 canonical model.  The original ``@id``
links, parser, formatters and patch API are unchanged.  Cross-document links
are checked against *actual local files* and converted to ordinary links to
rendered HTML/Markdown pages when a workspace is built.

Usage::

    python -m pyn.xrefs check samples/xrefs/index.pyn --root samples/xrefs
    python -m pyn.xrefs edges samples/xrefs/index.pyn --root samples/xrefs
    python -m pyn.xrefs build samples/xrefs/index.pyn --root samples/xrefs \
        --out-dir /tmp/pyn-site --format both

The root bounds file reads even if a link uses ../ or a symlink.  Nothing is
fetched from the network or executed from linked documents.
"""
from __future__ import annotations

import argparse
import copy
import json
import ntpath
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import quote

__all__ = ["CrossReferenceError", "CrossEdge", "Workspace", "scan_links",
           "validate_workspace", "build_site"]

# @id continues to mean an intra-document link.  Braces avoid ambiguity with
# dots already allowed in v1 node IDs (e.g. @auth.v2).
_OPEN = re.compile(r"(?<![A-Za-z0-9_\\])@\{")
_TOKEN = re.compile(r"(?<![A-Za-z0-9_\\])@\{([^}\n]*)\}")
_ID = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")
_CODE = re.compile(r"`[^`\n]*`")
_FENCE = re.compile(r"^\s*(```|~~~)")
_MAX_FILE_BYTES = 2_000_000
_MAX_FILES = 256


class CrossReferenceError(ValueError):
    """A cross-document reference is invalid or cannot be resolved."""


@dataclass(frozen=True)
class CrossLink:
    start: int
    end: int
    expression: str
    path: str
    node_id: str


@dataclass(frozen=True)
class CrossEdge:
    source_file: Path
    source_id: str
    target_file: Path
    target_id: str


@dataclass
class Workspace:
    root: Path
    entry: Path
    docs: dict[Path, dict]
    edges: list[CrossEdge]


def _masked_line(line: str) -> str:
    return _CODE.sub(lambda m: " " * len(m.group()), line)


def _split_expr(expression: str) -> tuple[str, str]:
    if expression.count("#") != 1:
        raise CrossReferenceError(
            f"@{{{expression}}}: expected 'relative/file.pyn#node-id'")
    path, node_id = expression.split("#")
    if (not path or "\\" in path or "\x00" in path or
            ntpath.isabs(path) or PurePosixPath(path).is_absolute() or
            ":" in path or "?" in path or "#" in path or
            PurePosixPath(path).suffix != ".pyn"):
        raise CrossReferenceError(f"@{{{expression}}}: invalid relative .pyn path")
    if not _ID.fullmatch(node_id):
        raise CrossReferenceError(f"@{{{expression}}}: invalid PYN node ID")
    return path, node_id


def scan_links(text: str) -> list[CrossLink]:
    """Read external links from one prose block; ignore code spans/fences.

    Positions refer to original text, so rewriting cannot change adjacent prose.
    Malformed @{} references fail explicitly, not as missing links later.
    """
    found: list[CrossLink] = []
    fence = False
    pos = 0
    for line in text.splitlines(keepends=True):
        if _FENCE.match(line):
            fence = not fence
        elif not fence:
            masked = _masked_line(line)
            tokens = list(_TOKEN.finditer(masked))
            for opener in _OPEN.finditer(masked):
                if not any(t.start() == opener.start() for t in tokens):
                    raise CrossReferenceError("Unclosed @{...} link in prose")
            for m in tokens:
                path, node_id = _split_expr(m.group(1))
                found.append(CrossLink(pos + m.start(), pos + m.end(),
                                       m.group(1), path, node_id))
        pos += len(line)
    return found


def _contained(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _target(source: Path, rel: str, root: Path) -> Path:
    # Path.resolve() resolves symlinks too: no read outside the selected root.
    target = (source.parent / Path(*PurePosixPath(rel).parts)).resolve()
    if not _contained(target, root):
        raise CrossReferenceError(
            f"link in {source}: {rel!r} points outside --root ({root})")
    # Reject even in-root symlink aliases: all document-to-document paths should
    # resolve predictably, rather than silently changing the trust boundary.
    lexical = Path(os.path.normpath(str(source.parent / Path(*PurePosixPath(rel).parts))))
    current = lexical
    while _contained(current, root):
        if current.is_symlink():
            raise CrossReferenceError("cross-document symlink denied")
        if current == root:
            break
        current = current.parent
    return target


def _nodes(doc: dict):
    todo = list(reversed(doc.get("root", [])))
    while todo:
        n = todo.pop()
        yield n
        todo.extend(reversed(n.get("children", [])))


def _load_doc(path: Path) -> dict:
    if not path.is_file():
        raise CrossReferenceError(f"Referenced PYN file not found: {path}")
    if path.stat().st_size > _MAX_FILE_BYTES:
        raise CrossReferenceError(f"PYN file too large: {path}")
    from .parser import parse
    from .refs import validate_refs
    from .errors import PynError
    from .guardrails import safe_read, SecurityError
    try:
        doc = parse(safe_read(path, root=path.parent))
        validate_refs(doc)  # Existing local @id validation remains mandatory.
        return doc
    except (UnicodeError, ValueError, PynError, SecurityError) as exc:
        raise CrossReferenceError(f"Failed to parse/check {path}: {exc}") from exc


def validate_workspace(entry: str | Path, *, root: str | Path | None = None,
                       max_files: int = _MAX_FILES) -> Workspace:
    """Validate reachable links, recursively, and return a bounded graph.

    Cycles across files are allowed. Each file is parsed once. The default
    security boundary is the entry file's directory. Pass --root to authorize
    links across sibling subdirectories inside a chosen workspace.
    """
    entry = Path(entry).resolve()
    root = Path(root).resolve() if root is not None else entry.parent
    if not root.is_dir() or not _contained(entry, root):
        raise CrossReferenceError("entry must be inside the existing --root directory")
    if entry.suffix != ".pyn":
        raise CrossReferenceError("entry must be a .pyn file")
    if isinstance(max_files, bool) or not 1 <= max_files <= 4096:
        raise CrossReferenceError("max_files must be between 1 and 4096")
    docs: dict[Path, dict] = {}
    edges: list[CrossEdge] = []
    pending = [entry]
    while pending:
        file = pending.pop(0)
        if file in docs:
            continue
        if len(docs) >= max_files:
            raise CrossReferenceError(f"cross-document graph exceeds {max_files} files")
        doc = _load_doc(file)
        docs[file] = doc
        for node in _nodes(doc):
            for prose in node.get("prose", []):
                try:
                    links = scan_links(prose)
                except CrossReferenceError as exc:
                    raise CrossReferenceError(f"{file}#{node['id']}: {exc}") from exc
                for link in links:
                    dst = _target(file, link.path, root)
                    edges.append(CrossEdge(file, node["id"], dst, link.node_id))
                    if dst not in docs and dst not in pending:
                        pending.append(dst)
    indexes = {path: {n["id"]: n for n in _nodes(doc)}
               for path, doc in docs.items()}
    for edge in edges:
        if edge.target_id not in indexes[edge.target_file]:
            rel = edge.target_file.relative_to(root).as_posix()
            src = edge.source_file.relative_to(root).as_posix()
            raise CrossReferenceError(
                f"{src}#{edge.source_id}: missing target {rel}#{edge.target_id}")
    return Workspace(root, entry, docs, edges)


def _link_label(title: str) -> str:
    # Basic Markdown renderer accepts [label](url) but does not implement the
    # full CommonMark escaping grammar. Keep generated text well-formed.
    return title.replace("[", "［").replace("]", "］").replace("\n", " ")


def _rewrite(text: str, source: Path, workspace: Workspace,
             destinations: dict[Path, Path]) -> str:
    links = scan_links(text)
    if not links:
        return text
    parts: list[str] = []
    start = 0
    index = {file: {n["id"]: n for n in _nodes(doc)}
             for file, doc in workspace.docs.items()}
    for link in links:
        target = _target(source, link.path, workspace.root)
        node = index[target][link.node_id]
        label = _link_label(node.get("title") or link.node_id)
        rel = os.path.relpath(destinations[target], destinations[source].parent)
        url = quote(Path(rel).as_posix(), safe="/.-_~") + "#" + quote(link.node_id, safe=".-_~")
        parts.extend((text[start:link.start], f"[{label}]({url})"))
        start = link.end
    parts.append(text[start:])
    return "".join(parts)


def _render_doc(file: Path, workspace: Workspace, destinations: dict[Path, Path],
                fmt: str) -> str:
    # Do NOT mutate canonical source documents: external links are a *display*
    # projection and the on-disk .pyn file remains the sole editable source.
    doc = copy.deepcopy(workspace.docs[file])
    for node in _nodes(doc):
        if "prose" in node:
            node["prose"] = [_rewrite(s, file, workspace, destinations)
                             for s in node["prose"]]
    if fmt == "html":
        from .render_html import render_html
        return render_html(doc, title=file.stem)
    from .render_md import render_md
    return render_md(doc, title=file.stem)


def build_site(workspace: Workspace, output: str | Path,
               *, fmt: str = "both") -> list[Path]:
    """Render all reachable .pyn files with working relative output links.

    The input tree's directory layout is preserved under output. No target
    files are silently created outside the selected output directory.
    """
    if fmt not in ("html", "md", "both"):
        raise CrossReferenceError("format must be html, md or both")
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for kind in (("html", "md") if fmt == "both" else (fmt,)):
        destinations = {file: output / file.relative_to(workspace.root).with_suffix("." + kind)
                        for file in workspace.docs}
        for file in workspace.docs:
            dest = destinations[file]
            if not _contained(dest.resolve(), output):
                raise CrossReferenceError(f"output destination escapes --out-dir: {dest}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            # Parent may contain a pre-existing symlink; check once again.
            if not _contained(dest.resolve(), output):
                raise CrossReferenceError(f"output destination escapes --out-dir: {dest}")
            dest.write_text(_render_doc(file, workspace, destinations, kind),
                            encoding="utf-8", newline="\n")
            written.append(dest)
    return written


def main(argv: list[str] | None = None) -> int:
    from .cli import use_utf8_stdio
    use_utf8_stdio()  # pyn の CLI と同じく UTF-8・LF で書く(t-19 / t-21)
    ap = argparse.ArgumentParser(description="Cross-file PYN reference experiment")
    ap.add_argument("action", choices=("check", "edges", "build"))
    ap.add_argument("path", type=Path, help="entry .pyn file")
    ap.add_argument("--root", type=Path, help="workspace boundary (default: entry parent)")
    ap.add_argument("--out-dir", type=Path, help="site output directory for build")
    ap.add_argument("--format", choices=("html", "md", "both"), default="both")
    ap.add_argument("--max-files", type=int, default=_MAX_FILES)
    args = ap.parse_args(argv)
    if args.action == "build" and args.out_dir is None:
        ap.error("build requires --out-dir")
    if args.action != "build" and args.out_dir is not None:
        ap.error("--out-dir requires build")
    try:
        workspace = validate_workspace(args.path, root=args.root,
                                       max_files=args.max_files)
        if args.action == "check":
            print(f"OK  files={len(workspace.docs)}  cross_links={len(workspace.edges)}")
        elif args.action == "edges":
            print(json.dumps([
                {"source": e.source_file.relative_to(workspace.root).as_posix() + "#" + e.source_id,
                 "target": e.target_file.relative_to(workspace.root).as_posix() + "#" + e.target_id}
                for e in workspace.edges], ensure_ascii=False, indent=2))
        else:
            written = build_site(workspace, args.out_dir, fmt=args.format)
            print(f"Rendered {len(written)} files into {Path(args.out_dir).resolve()}")
    except (CrossReferenceError, OSError, UnicodeError) as exc:
        print(f"PYN cross-reference error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
