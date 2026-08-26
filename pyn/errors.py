"""PYN の例外型。

エラーは必ず「行番号 + 何が起きたか + どう直すか」を持つ。
s-01 の設計上、インデント破損は *検出できる* ことが前提なので、
検出時のメッセージ品質がそのまま形式の実用性になる。
"""

from __future__ import annotations


class PynError(Exception):
    """PYN 関連の基底例外。"""


class PynSyntaxError(PynError):
    """.pyn の構文エラー。行番号と該当行を保持する。"""

    def __init__(self, message: str, line_no: int | None = None,
                 line: str | None = None, hint: str | None = None):
        self.message = message
        self.line_no = line_no
        self.line = line
        self.hint = hint
        parts = []
        if line_no is not None:
            parts.append(f"line {line_no}")
        parts.append(message)
        text = ": ".join(parts)
        if line is not None:
            text += f"\n  | {line}"
        if hint:
            text += f"\n  hint: {hint}"
        super().__init__(text)


class PynIndentError(PynSyntaxError):
    """インデントの不整合(s-01 が検出対象とする破損)。"""


class PynValidationError(PynError):
    """正準形として不正(重複ID、未定義参照、型違反など)。"""


class PynRefError(PynValidationError):
    """未定義の @id 参照(s-02: リンク切れは CI で落とす)。"""
