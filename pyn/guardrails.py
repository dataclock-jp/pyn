"""Fail-closed *screening* for untrusted PYN text (experimental).

This is not an LLM prompt-injection proof. The caller MUST keep document
contents in an untrusted data channel and enforce tool permissions outside
of the model. No bytes from a PYN document are executed or imported here.

Security invariants:
* Local, regular ``.pyn`` UTF-8 files only; no symlinks or root escapes.
* Bounded files, rejection of binary signatures, controls and hidden text.
* High-signal prompt-injection signatures trigger quarantine (no rewriting).
* No implicit privilege escalation from *any* node or document attribute.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import os
import re
import stat
import sys
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path

MAX_BYTES = 2_000_000
MAX_FILES = 256
# Do not include snippets: errors/logs can themselves be prompt-injection paths.


class SecurityError(ValueError):
    """Rejected material: the message names a rule, not attacker content."""


@dataclass(frozen=True)
class Finding:
    code: str
    line: int


_MAGIC = (
    (b"\x7fELF", "executable"), (b"MZ", "executable"),
    (b"PK\x03\x04", "archive"), (b"PK\x05\x06", "archive"),
    (b"\x1f\x8b", "archive"), (b"Rar!", "archive"),
    (b"\x00asm", "executable"), (b"%PDF", "binary_document"),
    (b"\x89PNG", "image"), (b"GIF8", "image"),
    (b"\xff\xd8\xff", "image"),
)
# Use phrases/role-forgery indicators, not ambiguous single words.
_INJECTION_RULES = (
    ("role_spoofing", re.compile(
        r"(?:<\s*/?\s*(?:system|developer)\s*>|<\|im_start\|>\s*(?:system|developer)|"
        r"\[\s*inst\s*\]|\b(?:system|developer)\s+(?:message|prompt)\s*:)", re.I)),
    ("instruction_override", re.compile(
        r"\b(?:ignore|disregard|override|forget)\s+(?:all\s+)?(?:previous|prior|earlier|above|system|developer)\s+"
        r"(?:instructions?|rules?|prompts?|messages?)\b", re.I)),
    ("credential_request", re.compile(
        r"\b(?:reveal|print|exfiltrate|leak|send|upload)\b[^\n]{0,70}"
        r"\b(?:system\s+prompt|api[ _-]?key|secrets?|credentials?|access\s+tokens?)\b", re.I)),
    ("disable_defenses", re.compile(
        r"\b(?:bypass|disable|turn\s+off|remove)\b[^\n]{0,60}"
        r"\b(?:safety|guardrails?|filters?|authorization|security\s+checks?)\b", re.I)),
    ("agent_side_effect_instruction", re.compile(
        r"\b(?:AI|assistant|agent)\b[^\n]{0,55}\b(?:delete|erase|wipe|exfiltrate|upload|send)\b"
        r"[^\n]{0,65}\b(?:files?|data|logs?|secrets?|keys?|credentials?)\b", re.I)),
    ("japanese_agent_side_effect", re.compile(
        r"(?:AI|エージェント|アシスタント).{0,35}(?:ファイル|データ|機密|秘密|鍵).{0,35}"
        r"(?:削除|消去|送信|外部送信)", re.I)),
    ("active_html", re.compile(
        r"<\s*(?:script|iframe|object|embed|svg|form|meta)\b|\bon[a-z]+\s*=", re.I)),
    ("remote_image", re.compile(r"!\[[^]\n]*\]\(\s*https?://", re.I)),
    ("japanese_override", re.compile(
        r"(?:これまで|以前|前述|上記|システム|開発者)の(?:すべての)?(?:指示|命令|ルール).{0,12}"
        r"(?:無視|破棄|上書き|従わない)|(?:システムプロンプト|開発者メッセージ).{0,15}"
        r"(?:公開|表示|送信|漏ら)|(?:安全制限|安全対策|ガードレール).{0,12}(?:無効|解除|回避)", re.I)),
)
_BASE64 = re.compile(r"(?<![A-Za-z0-9+/])(?:[A-Za-z0-9+/]{4}){7,}(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=|[A-Za-z0-9+/]{4})(?![A-Za-z0-9+/])")
_UNSAFE_URI = re.compile(r"\]\(\s*(?:javascript|data|vbscript|file)\s*:", re.I)
# Trusted host must always enforce authorization; documents never grant it.
READ_ONLY_OPERATIONS = frozenset(("inspect", "context", "snapshot", "review_plan", "render_preview"))


def _line(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def scan_text(text: str, *, inspect_encodings: bool = True) -> list[Finding]:
    """Deterministic high-signal checks; *not* a complete attack classifier."""
    if not isinstance(text, str):
        raise TypeError("text must be str")
    findings: list[Finding] = []
    found: set[tuple[str, int]] = set()

    def add(code: str, lineno: int):
        key = (code, lineno)
        if key not in found:
            found.add(key)
            findings.append(Finding(code, lineno))

    for i, ch in enumerate(text):
        if ch == "\ufeff" and i == 0:
            continue
        cat = unicodedata.category(ch)
        if ch == "\x00" or (cat in ("Cc", "Cf", "Cs") and ch not in "\n\r\t"):
            add("hidden_or_control_character", _line(text, i))
    normalized = unicodedata.normalize("NFKC", text).casefold()
    # Treat normalized positions as approximate when compatibility normalization
    # expands a character. Line numbers remain correct unless newlines expand.
    for name, rule in _INJECTION_RULES:
        for match in rule.finditer(normalized):
            add(name, _line(normalized, match.start()))
    for match in _UNSAFE_URI.finditer(normalized):
        add("dangerous_markdown_uri", _line(normalized, match.start()))

    if inspect_encodings:
        # Inspect bounded base64 content; never execute, deserialize or load it.
        for match in _BASE64.finditer(unicodedata.normalize("NFKC", text)):
            token = match.group()
            if len(token) > 65536:
                add("oversized_encoded_payload", _line(normalized, match.start()))
                continue
            try:
                decoded = base64.b64decode(token, validate=True)
            except (ValueError, binascii.Error):
                continue
            if any(decoded.startswith(m) for m, _ in _MAGIC):
                add("embedded_binary_payload", _line(normalized, match.start()))
            try:
                inner = decoded.decode("utf-8", errors="strict")
            except UnicodeError:
                continue
            for issue in scan_text(inner, inspect_encodings=False):
                add("encoded_" + issue.code, _line(normalized, match.start()))
    return sorted(findings, key=lambda f: (f.line, f.code))


def inspect_bytes(data: bytes, *, screen: bool = True) -> str:
    """Return validated text, or refuse before giving bytes to a parser/AI.

    ``screen=False`` keeps the size, binary-signature and UTF-8 checks but skips
    the whole-file content screening. Only for callers that screen every piece
    they pass on themselves (``pyn.query`` screens each hit node, t-27).
    """
    if len(data) > MAX_BYTES:
        raise SecurityError("oversized_file")
    for magic, reason in _MAGIC:
        if data.startswith(magic):
            raise SecurityError("binary_signature:" + reason)
    try:
        text = data.decode("utf-8-sig", errors="strict")
    except UnicodeError as exc:
        raise SecurityError("invalid_utf8") from exc
    if not screen:
        return text
    findings = scan_text(text)
    if findings:
        first = findings[0]
        raise SecurityError(f"{first.code}:line:{first.line} ({len(findings)} findings)")
    return text


def safe_read(path: str | Path, *, root: str | Path | None = None,
              screen: bool = True) -> str:
    """Bounded, fail-closed PYN ingestion. No network, shell or decoders."""
    file = Path(path).absolute()
    boundary = Path(root).resolve(strict=True) if root is not None else file.parent.resolve(strict=True)
    if file.suffix != ".pyn":
        raise SecurityError("only_pyn_extension_allowed")
    try:
        rel = file.relative_to(boundary)
    except ValueError as exc:
        raise SecurityError("path_outside_workspace") from exc
    for component in rel.parts:
        if scan_text(component, inspect_encodings=False):
            raise SecurityError("unsafe_path_component")
    try:
        if file.resolve(strict=True) != boundary.joinpath(*rel.parts).resolve(strict=True):
            raise SecurityError("path_resolution_error")
        if not file.resolve(strict=True).is_relative_to(boundary):
            raise SecurityError("path_outside_workspace")
        # In-root symlinks are forbidden too: reduce indirect file surprises.
        current = boundary
        for part in rel.parts:
            current = current / part
            if current.is_symlink():
                raise SecurityError("symlink_denied")
    except OSError as exc:
        raise SecurityError("file_access_denied") from exc
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(file, flags)
        try:
            meta = os.fstat(fd)
            if not stat.S_ISREG(meta.st_mode):
                raise SecurityError("not_regular_file")
            if meta.st_size > MAX_BYTES:
                raise SecurityError("oversized_file")
            with os.fdopen(fd, "rb") as handle:
                fd = -1
                data = handle.read(MAX_BYTES + 1)
        finally:
            if fd != -1:
                os.close(fd)
    except OSError as exc:
        raise SecurityError("file_access_denied") from exc
    return inspect_bytes(data, screen=screen)


def require_read_only(action: str) -> None:
    """Deny side effects in the PYN data-processing layer, even if requested
    by a PYN document. A trusted, separate host must handle approvals/actions.
    """
    if action not in READ_ONLY_OPERATIONS:
        raise SecurityError("action_denied_by_data_layer")


def main(argv: list[str] | None = None) -> int:
    from .cli import use_utf8_stdio
    use_utf8_stdio()  # pyn の CLI と同じく UTF-8・LF で書く(t-19 / t-21)
    ap = argparse.ArgumentParser(description="Screen untrusted PYN files (not a security proof)")
    ap.add_argument("action", choices=("scan", "scan-tree"))
    ap.add_argument("path", type=Path)
    ap.add_argument("--root", type=Path)
    args = ap.parse_args(argv)
    try:
        if args.action == "scan":
            safe_read(args.path, root=args.root)
            print("OK: allowed by deterministic screening (not a proof of safety)")
        else:
            root = (args.root or args.path).resolve(strict=True)
            if not root.is_dir():
                raise SecurityError("not_a_directory")
            paths = sorted(root.rglob("*.pyn"))
            if len(paths) > MAX_FILES:
                raise SecurityError("too_many_files")
            for path in paths:
                safe_read(path, root=root)
            print(f"OK: {len(paths)} screened PYN files (not a proof of safety)")
        return 0
    except (OSError, SecurityError) as exc:
        print(f"QUARANTINE: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
