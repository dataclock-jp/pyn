"""プロジェクトルートを sys.path に載せるためのマーカー。

pytest はルートの conftest.py がある位置を sys.path へ挿入するため、
`pyn` / `tools` パッケージをそのまま import できる。

子プロセスの stdio をここで UTF-8 に固定することはしない。CLI 自身が
UTF-8 で書くことを保証しており(pyn/cli.py の use_utf8_stdio、t-19)、
ここで環境変数を足すとその保証が壊れてもテストが気づけなくなる。
"""
