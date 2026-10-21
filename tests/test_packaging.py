"""t-20: pip で入れられること(pyproject.toml)の検証。

実際にインストールして `pyn` コマンドを動かす確認は CI の install ジョブが行う。
ここでは、そのための前提が崩れていないことを見る。
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pyn
from pyn import cli, schema

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_console_script_points_at_cli_main():
    assert PYPROJECT["project"]["scripts"] == {"pyn": "pyn.cli:main"}
    assert callable(cli.main)


def test_only_the_package_is_distributed():
    """tools/ と tests/ はリポジトリの保守用。配布物に混ぜない。"""
    assert PYPROJECT["tool"]["setuptools"]["packages"] == ["pyn"]


def test_no_runtime_dependencies():
    """本体は標準ライブラリのみ(s-03)。"""
    assert PYPROJECT["project"]["dependencies"] == []


def test_version_has_a_single_source():
    assert PYPROJECT["project"]["dynamic"] == ["version"]
    assert PYPROJECT["tool"]["setuptools"]["dynamic"]["version"] == {"attr": "pyn.__version__"}
    assert pyn.__version__.count(".") == 2


def test_license_file_is_shipped():
    assert PYPROJECT["project"]["license"] == "MIT"
    assert all((ROOT / f).exists() for f in PYPROJECT["project"]["license-files"])


def test_load_schema_does_not_need_the_repository_file(monkeypatch, tmp_path):
    """pip で入れた環境には schema/ が無い。それでも `pyn check` が同じ schema で検証できる。"""
    in_repo = json.loads(schema.SCHEMA_PATH.read_text(encoding="utf-8"))
    monkeypatch.setattr(schema, "SCHEMA_PATH", tmp_path / "missing.json")
    assert schema.load_schema() == in_repo
