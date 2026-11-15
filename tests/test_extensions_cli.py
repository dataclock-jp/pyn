"""拡張モジュール(pyn.knowledge / xrefs / impact / guardrails)の CLI も、
pyn 本体と同じく標準出力を UTF-8・LF で書くことの検証(t-19 / t-24)。

ChatGPT の実験版 v0.3 として入ってきたときは本体の CLI を通らず、`⇄` や `—` を
含む文書を cp932 のパイプへ出すと UnicodeEncodeError で落ちていた。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOC = 'spec s "往復 ⇄ 一致 — 確認":\n  k: v\n'


@pytest.fixture
def doc(tmp_path):
    path = tmp_path / "doc.pyn"
    path.write_text(DOC, encoding="utf-8")
    return path


def _run(*args):
    return subprocess.run(
        [sys.executable, "-m", *args], cwd=ROOT, capture_output=True,
        env={**os.environ, "PYTHONIOENCODING": "cp932"},
    )


@pytest.mark.parametrize("module,action", [
    ("pyn.knowledge", "context"),
    ("pyn.knowledge", "edges"),
    ("pyn.xrefs", "edges"),
])
def test_extension_stdout_is_utf8_lf_on_cp932_pipe(doc, module, action):
    args = [module, action, str(doc)] + (["--id", "s"] if action == "context" else [])
    proc = _run(*args)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    assert b"\r" not in proc.stdout
    if action == "context":
        assert "往復 ⇄ 一致 — 確認" in proc.stdout.decode("utf-8")


def test_impact_snapshot_stdout_is_utf8_on_cp932_pipe(doc):
    proc = _run("pyn.impact", "snapshot", "--root", str(doc.parent))
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    proc.stdout.decode("utf-8")
    assert b"\r" not in proc.stdout
