"""プロジェクトルートを sys.path に載せるためのマーカー。

pytest はルートの conftest.py がある位置を sys.path へ挿入するため、
`pyn` / `tools` パッケージをそのまま import できる。

あわせて子プロセスの stdio を UTF-8 に固定する。CLI を subprocess で起動する
テストは出力を UTF-8 として復号するが、Windows では子プロセスのパイプ出力が
ロケール既定(cp932)になるため、日本語のメッセージを読んだ時点で
UnicodeDecodeError となり `proc.stdout` が None で返ってくる。
利用者向けの回避策(AGENTS.md の `PYTHONIOENCODING=utf-8`)を、
テスト実行時にも同じ形で効かせる。
"""

import os

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
