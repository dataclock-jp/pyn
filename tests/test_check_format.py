"""t-29: check は、次の patch で整形が入ることを先に知らせる。

手で書かれてきた `.pyn` に初めて patch を当てると、属性がキー順に並び、子ノードの前に
空行が入る。仕様どおりだが、中身の変更に整形の差分が混ざる。`pyn check` が先に知らせれば、
利用者は整形だけのコミットを先に切れる。検証の結果(終了コードと標準出力)は変えない。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from pyn import dumps, parse, to_pyn_a, to_pyn_b
from pyn.cli import format_drift

ROOT = Path(__file__).resolve().parents[1]

#: 属性がキー順でなく、子ノードの前に空行も無い。手で書くとこうなる
HAND_WRITTEN = '''\
spec s "仕様":
  task t-2 "二つめ":
    status: todo
    est: 3d
  task t-1 "一つめ":
    est: 1d
'''


def _run(*args, cwd=ROOT):
    return subprocess.run([sys.executable, "-m", "pyn", *args],
                          cwd=cwd, capture_output=True, text=True, encoding="utf-8")


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


# --------------------------------------------------------------------------
# 判定
# --------------------------------------------------------------------------

def test_canonical_text_has_no_drift(tmp_path):
    doc = parse(HAND_WRITTEN)
    assert format_drift(_write(tmp_path / "a.pyn", to_pyn_a(doc)), doc) is None
    assert format_drift(_write(tmp_path / "b.pyn", to_pyn_b(doc)), doc) is None
    assert format_drift(_write(tmp_path / "c.json", dumps(doc)), doc) is None


def test_hand_written_text_drifts(tmp_path):
    src = _write(tmp_path / "doc.pyn", HAND_WRITTEN)
    projection, changed = format_drift(src, parse(HAND_WRITTEN))
    assert projection == "a"
    assert changed >= 2  # 属性の並べ替えと、子ノードの前の空行


def test_projection_follows_the_source(tmp_path):
    """patch は入力と同じ射影で書き戻すので、比べる相手もその射影。"""
    doc = parse(HAND_WRITTEN)
    flat = to_pyn_b(doc).replace("est: 1d", "est:   1d")
    assert flat != to_pyn_b(doc)
    assert format_drift(_write(tmp_path / "b.pyn", flat), parse(flat))[0] == "b"


def test_trailing_newline_only_counts_as_one_line(tmp_path):
    doc = parse(HAND_WRITTEN)
    src = _write(tmp_path / "doc.pyn", to_pyn_a(doc) + "\n")
    assert format_drift(src, doc) == ("a", 1)


def test_json_input_is_compared_with_canonical_json(tmp_path):
    doc = parse(HAND_WRITTEN)
    src = _write(tmp_path / "doc.json", json.dumps(doc, ensure_ascii=False))
    assert format_drift(src, doc)[0] == "json"


# --------------------------------------------------------------------------
# CLI — 知らせるだけで、検証の結果は変えない
# --------------------------------------------------------------------------

def test_check_notes_drift_but_still_passes(tmp_path):
    src = _write(tmp_path / "doc.pyn", HAND_WRITTEN)
    proc = _run("check", str(src))
    assert proc.returncode == 0
    assert proc.stdout.startswith("OK")
    assert "注:" in proc.stderr
    assert "次の patch" in proc.stderr
    assert "コメント" not in proc.stderr  # コメントの無い文書では言わない


def test_check_is_quiet_for_canonical_text(tmp_path):
    src = _write(tmp_path / "doc.pyn", to_pyn_a(parse(HAND_WRITTEN)))
    proc = _run("check", str(src))
    assert proc.returncode == 0
    assert proc.stderr == ""


def test_check_mentions_comments_when_present(tmp_path):
    src = _write(tmp_path / "doc.pyn", "# 作業メモ\n" + to_pyn_a(parse(HAND_WRITTEN)))
    proc = _run("check", str(src))
    assert proc.returncode == 0
    assert "コメント" in proc.stderr


def test_suggested_command_makes_the_note_go_away(tmp_path):
    """知らせた手順をそのまま実行すれば、整形だけが済む(中身は変わらない)。"""
    src = _write(tmp_path / "doc.pyn", HAND_WRITTEN)
    assert f"pyn a {src} -o {src}" in _run("check", str(src)).stderr
    assert _run("a", str(src), "-o", str(src)).returncode == 0
    assert parse(src.read_text(encoding="utf-8")) == parse(HAND_WRITTEN)
    assert _run("check", str(src)).stderr == ""


def test_path_with_spaces_is_quoted(tmp_path):
    folder = tmp_path / "my docs"
    folder.mkdir()
    src = _write(folder / "doc.pyn", HAND_WRITTEN)
    assert f'-o "{src}"' in _run("check", str(src)).stderr


def test_first_patch_then_writes_nothing_but_the_change(tmp_path):
    """整形を先に済ませれば、patch の差分は変えた行だけになる。"""
    src = _write(tmp_path / "doc.pyn", HAND_WRITTEN)
    _run("a", str(src), "-o", str(src))
    before = src.read_text(encoding="utf-8").splitlines()
    ops = _write(tmp_path / "ops.json", json.dumps(
        [{"op": "set_attr", "id": "t-1", "key": "est", "value": "2d"}]))
    assert _run("patch", str(src), "--ops", str(ops)).returncode == 0
    after = src.read_text(encoding="utf-8").splitlines()
    assert len(before) == len(after)
    assert sum(a != b for a, b in zip(before, after)) == 1


def test_repository_documents_are_canonical():
    """このリポジトリの正本は patch で運用しているので、整形の差分は残っていない。"""
    for name in ("Handover.pyn", "docs/agents.pyn", "docs/readme.pyn"):
        path = ROOT / name
        assert format_drift(path, parse(path.read_text(encoding="utf-8"))) is None, name
