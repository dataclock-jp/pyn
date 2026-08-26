"""PYN — JSON と HTML の良いとこ取りをする Python 風の文書形式。

中心的な主張(s-meta):
    真実は「型とIDを持つノードの集合と、その間の辺」である。
    インデント形もフラット形も JSON も HTML も、すべて再生成可能な射影にすぎない。

したがって本パッケージは「新しいファイル形式」ではなく、
正準形 JSON に対する複数の可逆な *射影* を提供するライブラリである(s-03)。

    canonical JSON  ←→  .pyn 投影A(インデント形 / 人間用)
                    ←→  .pyn 投影B(フラット形 / AIパッチ用)
                     →  HTML(人間が読む生成物)
                     →  Markdown(同上。GitHub 等で読む用)
"""

from __future__ import annotations

from .errors import (
    PynError,
    PynIndentError,
    PynRefError,
    PynSyntaxError,
    PynValidationError,
)
from .formatter import to_pyn, to_pyn_a, to_pyn_b
from .model import CANONICAL_VERSION, canonicalize, dumps, loads, new_doc, new_node
from .parser import comment_lines, detect_projection, parse
from .patch import apply as apply_patch
from .refs import edges, extract_refs, index_by_id, iter_nodes, validate_refs
from .render_html import render_html
from .render_md import render_md
from .schema import PynSchemaError, build_schema, load_schema, validate_schema
from .repair import Candidate, Finding, diagnose, report

__version__ = "0.1.0"

__all__ = [
    "CANONICAL_VERSION",
    "Candidate",
    "Finding",
    "diagnose",
    "report",
    "PynError",
    "PynIndentError",
    "PynRefError",
    "PynSyntaxError",
    "PynValidationError",
    "apply_patch",
    "PynSchemaError",
    "build_schema",
    "canonicalize",
    "comment_lines",
    "detect_projection",
    "dumps",
    "edges",
    "extract_refs",
    "index_by_id",
    "iter_nodes",
    "loads",
    "new_doc",
    "new_node",
    "parse",
    "render_html",
    "render_md",
    "to_pyn",
    "to_pyn_a",
    "to_pyn_b",
    "load_schema",
    "validate_refs",
    "validate_schema",
]
