"""プロジェクトルートを sys.path に載せるためのマーカー。

pytest はルートの conftest.py がある位置を sys.path へ挿入するため、
`pyn` / `tools` パッケージをそのまま import できる。
"""
