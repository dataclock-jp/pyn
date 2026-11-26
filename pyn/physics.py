"""物理の層 PPYN(physics PYN)。実験的な、任意で使う意味の層(t-32)。

PYN の構文も正準形も変えない。根のノードに `ppyn` 属性(版)がある文書を PPYN の文書として読み、
いくつかの型と属性に物理の意味を与える。`pyn.knowledge` と同じ位置づけで、`pyn check` は
従来どおり同一文書の検証だけを行う。仕様は `docs/physics.md`(正本は `docs/physics.pyn`)。

    projector    要素射影(はい/いいえの最小の相互作用)。ee = e、e† = e
    measurement  3つ以上でもよい射影の組。互いに直交し、足すと 1 になる
    term         関係表の1項。射影の順序つきの積(語)に、複素数の値 ω を一つ与える
    define       式に名前を付ける
    quantity     式の値 ω(式) を計算する
    history      射影を順に行ったときの確率を計算する(実現規則)
    description  記述。要素射影の一部分だけを見る。記述ごとに時間の流れが決まる

式の中の演算:  +  -  *(順序つきの合成)  /(数で割る)  dag(x)  comm(x, y)  sqrt(数)
               flow(記述, x, t)  記述の時間の流れに沿って x を t だけ進める
虚数は 0.5i のように数の直後に i を付ける。式は自前の構文解析で読み、実行はしない。

    python -m pyn.physics check  doc.pyn                    構造・関係表・整合・式・履歴・流れの検証
    python -m pyn.physics eval   doc.pyn "式"               ω(式) を計算する
    python -m pyn.physics words  doc.pyn -L 2               長さ L までの語と、表に値があるかどうか
    python -m pyn.physics gns    doc.pyn [-d 記述]          関係表からヒルベルト空間での表現を復元する
    python -m pyn.physics flow   doc.pyn [-d 記述] [-t T]   関係表から時間の流れを作る

文書に入るのは射影どうしの関係(値)だけで、状態ベクトルも行列も時間も入らない。
必要なものは関係表から作る。ここで行うのは数値の検証と計算であって、その関係表が
現実の系を正しく表しているかどうかは判定しない。

標準ライブラリだけで動く(s-03)。固有値分解も自前(複素ヤコビ法)なので、
語の数が多い表(数十を超える行列)には向かない。
"""

from __future__ import annotations

import argparse
import cmath
import itertools
import json
import math
import re
import sys
from pathlib import Path

from .errors import PynError
from .refs import iter_nodes, validate_refs

__all__ = [
    "PhysicsError", "NotPhysicsDocument", "MissingTerms", "FlowUndefined",
    "System", "Representation", "Flow", "load", "ppyn_version", "eigh",
]

VERSION = "0.2"
TOL = 1e-9
MAX_MATRIX = 90
_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_TOKEN_RE = re.compile(
    r"\s*(?:(?P<num>(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?)(?P<imag>i\b)?"
    r"|(?P<name>[A-Za-z_][A-Za-z0-9_]*)|(?P<op>[-+*/(),]))")
DEFAULT_PARTY = "_"
WHOLE = "(全体)"


class PhysicsError(ValueError):
    """PPYN の文書の内容の誤り。"""


class NotPhysicsDocument(PhysicsError):
    """PYN の文書だが、PPYN の文書ではない。"""


class MissingTerms(PhysicsError):
    """計算に必要な項が関係表に無い。"""

    def __init__(self, words):
        self.words = sorted(set(words), key=lambda w: (len(w), w))
        super().__init__("関係表に値が無い語: " + ", ".join(show_word(w) for w in self.words[:8])
                         + (f" ほか {len(self.words) - 8} 個" if len(self.words) > 8 else ""))


class FlowUndefined(PhysicsError):
    """この記述では時間の流れを定義できない。"""


def show_word(w) -> str:
    return "·".join(w) if w else "1"


def fmt(z, digits=9) -> str:
    z = complex(z)
    re_part = round(z.real, digits) + 0.0
    im_part = round(z.imag, digits) + 0.0
    if im_part == 0:
        return f"{re_part:.{digits}g}"
    return f"{re_part:.{digits}g} {'+' if im_part > 0 else '-'} {abs(im_part):.{digits}g}i"


def show_poly(poly, digits=6) -> str:
    """多項式(語 → 係数)を読める形にする。"""
    small = 10 ** -digits
    parts = []
    for w, c in sorted(poly.items(), key=lambda item: (len(item[0]), item[0])):
        c = complex(c)
        if abs(c) < small:
            continue
        if abs(c.imag) < small:
            coef = f"{c.real:.{digits}g}"
        elif abs(c.real) < small:
            coef = f"{c.imag:.{digits}g}i"
        else:
            coef = f"({fmt(c, digits)})"
        if not w:
            parts.append(coef)
        elif coef in ("1", "-1"):
            parts.append(coef[:-1] + show_word(w))
        else:
            parts.append(f"{coef}·{show_word(w)}")
    return " + ".join(parts).replace("+ -", "- ") if parts else "0"


def to_number(value, where: str) -> float:
    """実数。PYN の裸の数は小数だけなので、指数表記は文字列("1e-6")として書いてよい。"""
    if isinstance(value, bool) or value is None:
        raise PhysicsError(f"{where}: 数ではありません")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            pass
    raise PhysicsError(f"{where}: 数ではありません: {value!r}")


def to_complex(value, where: str) -> complex:
    """実数、または [実部, 虚部]。"""
    if isinstance(value, list):
        if len(value) != 2:
            raise PhysicsError(f"{where}: 複素数は [実部, 虚部] の2つの数で書きます")
        return complex(to_number(value[0], where), to_number(value[1], where))
    return complex(to_number(value, where))


# ====================================================================== 行列(標準ライブラリだけ)
def eigh(matrix, eps=1e-14, max_sweeps=80):
    """エルミート行列の固有値分解(複素ヤコビ法)。(固有値の昇順リスト, V) を返す。
    V[i][k] は k 番目の固有ベクトルの第 i 成分で、A = V diag(w) V† が成り立つ。"""
    n = len(matrix)
    a = [[complex(x) for x in row] for row in matrix]
    v = [[1 + 0j if i == j else 0j for j in range(n)] for i in range(n)]
    for _ in range(max_sweeps):
        off = sum(abs(a[p][q]) ** 2 for p in range(n) for q in range(p + 1, n))
        total = sum(abs(a[i][i]) ** 2 for i in range(n)) + 2 * off
        if off <= (eps ** 2) * max(total, 1e-300):
            break
        for p in range(n - 1):
            for q in range(p + 1, n):
                b = a[p][q]
                mag = abs(b)
                if mag < 1e-300:
                    continue
                phase = b / mag
                tau = (a[q][q].real - a[p][p].real) / (2 * mag)
                t = (1.0 if tau >= 0 else -1.0) / (abs(tau) + math.sqrt(1 + tau * tau))
                c = 1 / math.sqrt(1 + t * t)
                s = t * c
                jpp, jpq = c, s
                jqp, jqq = -s * phase.conjugate(), c * phase.conjugate()
                for k in range(n):
                    akp, akq = a[k][p], a[k][q]
                    a[k][p] = akp * jpp + akq * jqp
                    a[k][q] = akp * jpq + akq * jqq
                for k in range(n):
                    apk, aqk = a[p][k], a[q][k]
                    a[p][k] = jpp * apk + jqp.conjugate() * aqk
                    a[q][k] = jpq * apk + jqq.conjugate() * aqk
                for k in range(n):
                    vkp, vkq = v[k][p], v[k][q]
                    v[k][p] = vkp * jpp + vkq * jqp
                    v[k][q] = vkp * jpq + vkq * jqq
    order = sorted(range(n), key=lambda i: a[i][i].real)
    return [a[i][i].real for i in order], [[v[r][i] for i in order] for r in range(n)]


def matmul(x, y):
    cols = list(zip(*y))
    return [[sum(a * b for a, b in zip(row, col)) for col in cols] for row in x]


def matvec(m, v):
    return [sum(a * b for a, b in zip(row, v)) for row in m]


def dagger(x):
    return [[x[j][i].conjugate() for j in range(len(x))] for i in range(len(x[0]))]


def madd(x, y, k=1):
    return [[a + k * b for a, b in zip(rx, ry)] for rx, ry in zip(x, y)]


def identity(n):
    return [[1 + 0j if i == j else 0j for j in range(n)] for i in range(n)]


def max_abs(x):
    return max((abs(a) for row in x for a in row), default=0.0)


def max_abs_diff(x, y):
    return max((abs(a - b) for rx, ry in zip(x, y) for a, b in zip(rx, ry)), default=0.0)


def inner(x, y):
    return sum(a.conjugate() * b for a, b in zip(x, y))


def function_of(evals, vecs, f):
    """V f(Λ) V†。"""
    n = len(evals)
    fv = [f(lam) for lam in evals]
    return [[sum(vecs[i][k] * fv[k] * vecs[j][k].conjugate() for k in range(n)) for j in range(n)]
            for i in range(n)]


def solve_hermitian(matrix, rhs, tol=1e-10, floor=1e-14):
    """エルミート行列の最小ノルム解(小さい固有値の方向は捨てる)。
    floor は、行列全体が丸め誤差だけのとき(すべて可換な場合など)に雑音を拾わないための下限。"""
    evals, vecs = eigh(matrix)
    top = max((abs(lam) for lam in evals), default=0.0)
    out = [0j] * len(rhs)
    for k, lam in enumerate(evals):
        if abs(lam) <= max(tol * top, floor):
            continue
        coef = sum(vecs[i][k].conjugate() * rhs[i] for i in range(len(rhs))) / lam
        for i in range(len(rhs)):
            out[i] += vecs[i][k] * coef
    return out


# ====================================================================== 文書の読み込み
def load(path) -> dict:
    """文書を読む。`pyn.knowledge` と同じく、安全検査(`pyn.guardrails`)を通してから解析する。

    CLI は文書の題名や式を標準出力に出すので、読む前に検査しておく。`.json` は正準形として読む。
    """
    from .guardrails import MAX_BYTES, inspect_bytes, safe_read
    path = Path(path)
    if path.suffix == ".json":
        from .model import canonicalize
        if path.stat().st_size > MAX_BYTES:
            raise ValueError("oversized_json_file")
        doc = canonicalize(json.loads(inspect_bytes(path.read_bytes())))
    else:
        from .parser import parse
        doc = parse(safe_read(path, root=path.parent))
    # 本体の参照(@id)は別の意味を持つので、そちらも切れていないことを確かめる
    validate_refs(doc)
    return doc


def ppyn_version(doc: dict):
    """根のノードの ppyn 属性。無ければ None(PPYN の文書ではない)。"""
    for node in doc.get("root", []):
        attrs = node.get("attrs") or {}
        if "ppyn" in attrs:
            return str(attrs["ppyn"])
    return None


class System:
    """PPYN の物理の層。要素射影の代数と、関係表 ω を持つ。"""

    def __init__(self, doc: dict):
        self.version = ppyn_version(doc)
        if self.version is None:
            raise NotPhysicsDocument("PPYN の文書ではありません(根のノードに ppyn 属性がありません)")
        self.party: dict[str, str] = {}
        self.titles: dict[str, str] = {}
        self.meas_of: dict[str, str] = {}
        self.eliminated: dict[str, list[str]] = {}
        self.defines: dict[str, str] = {}
        self.quantities: list[dict] = []
        self.histories: list[dict] = []
        self.descriptions: dict[str, dict] = {}
        self.table: dict[tuple, complex] = {(): 1 + 0j}
        self.source: dict[tuple, str] = {}
        self.term_count = 0
        self._expr_cache: dict[str, dict] = {}
        self._rep_cache: dict[tuple, "Representation"] = {}
        self._flow_cache: dict[tuple, "Flow"] = {}
        nodes = list(iter_nodes(doc))
        by_type: dict[str, list[dict]] = {}
        for node in nodes:
            by_type.setdefault(node["type"], []).append(node)

        for node in by_type.get("projector", []):
            ident = node["id"]
            if not _NAME_RE.fullmatch(ident):
                raise PhysicsError(f"{ident}: 要素射影の ID は英数字とアンダースコアだけにします(式の中で使うため)")
            self.party[ident] = str((node.get("attrs") or {}).get("party", DEFAULT_PARTY))
            self.titles[ident] = node.get("title") or ident
        if not self.party:
            raise PhysicsError("projector ノードが一つもありません")

        for node in by_type.get("measurement", []):
            outcomes = (node.get("attrs") or {}).get("outcomes")
            if not isinstance(outcomes, list) or len(outcomes) < 2:
                raise PhysicsError(f"{node['id']}: outcomes に2つ以上の要素射影を並べます")
            for e in outcomes:
                if e not in self.party:
                    raise PhysicsError(f"{node['id']}: outcomes の {e!r} は要素射影ではありません")
                if e in self.meas_of:
                    raise PhysicsError(f"{e}: 一つの要素射影は一つの measurement にしか入れられません")
                self.meas_of[e] = node["id"]
            if len({self.party[e] for e in outcomes}) != 1:
                raise PhysicsError(f"{node['id']}: 一つの測定の結果は、同じ party に属する必要があります")
            # 最後の結果は「1 - ほかの結果の和」として消去する。関係表には書かない。
            self.eliminated[outcomes[-1]] = list(outcomes[:-1])

        self.generators = [e for e in self.party if e not in self.eliminated]
        self.parties = sorted({self.party[e] for e in self.party})

        for node in by_type.get("define", []):
            ident = node["id"]
            if not _NAME_RE.fullmatch(ident):
                raise PhysicsError(f"{ident}: define の ID は英数字とアンダースコアだけにします")
            expr = (node.get("attrs") or {}).get("expr")
            if not isinstance(expr, str):
                raise PhysicsError(f"{ident}: expr に式を文字列で書きます")
            self.defines[ident] = expr

        for node in by_type.get("description", []):
            self._add_description(node)

        for node in by_type.get("term", []):
            self._add_term(node)

        for node in by_type.get("quantity", []):
            attrs = node.get("attrs") or {}
            if not isinstance(attrs.get("expr"), str):
                raise PhysicsError(f"{node['id']}: expr に式を文字列で書きます")
            self.quantities.append({
                "id": node["id"], "title": node.get("title") or "", "expr": attrs["expr"],
                "expect": to_complex(attrs["expect"], node["id"] + ".expect") if "expect" in attrs else None,
                "tol": to_number(attrs["tol"], node["id"] + ".tol") if "tol" in attrs else TOL,
            })

        for node in by_type.get("history", []):
            attrs = node.get("attrs") or {}
            steps, results = attrs.get("steps"), attrs.get("results")
            if not isinstance(steps, list) or not steps:
                raise PhysicsError(f"{node['id']}: steps に要素射影を順に並べます")
            if results is None:
                results = ["yes"] * len(steps)
            if not isinstance(results, list) or len(results) != len(steps) \
                    or any(r not in ("yes", "no") for r in results):
                raise PhysicsError(f"{node['id']}: results は steps と同じ長さで、yes か no を並べます")
            for e in steps:
                if e not in self.party:
                    raise PhysicsError(f"{node['id']}: steps の {e!r} は要素射影ではありません")
            self.histories.append({
                "id": node["id"], "title": node.get("title") or "", "steps": steps, "results": results,
                "expect": to_number(attrs["expect"], node["id"] + ".expect") if "expect" in attrs else None,
                "tol": to_number(attrs["tol"], node["id"] + ".tol") if "tol" in attrs else TOL,
            })

    def _add_description(self, node):
        ident = node["id"]
        if not _NAME_RE.fullmatch(ident):
            raise PhysicsError(f"{ident}: description の ID は英数字とアンダースコアだけにします(式の中で使うため)")
        attrs = node.get("attrs") or {}
        gens = None
        if "parties" in attrs:
            wanted = attrs["parties"] if isinstance(attrs["parties"], list) else [attrs["parties"]]
            wanted = {str(p) for p in wanted}
            unknown = wanted - set(self.parties)
            if unknown:
                raise PhysicsError(f"{ident}: parties の {sorted(unknown)} という party はありません")
            gens = [e for e in self.generators if self.party[e] in wanted]
        if "projectors" in attrs:
            listed = attrs["projectors"] if isinstance(attrs["projectors"], list) else [attrs["projectors"]]
            for e in listed:
                if e not in self.party:
                    raise PhysicsError(f"{ident}: projectors の {e!r} は要素射影ではありません")
                if e in self.eliminated:
                    raise PhysicsError(f"{ident}: {e} は測定の最後の結果です。ほかの結果を並べてください")
            chosen = [e for e in self.generators if e in set(listed)]
            gens = chosen if gens is None else [e for e in self.generators if e in set(gens) | set(chosen)]
        if gens is None:
            gens = list(self.generators)
        if not gens:
            raise PhysicsError(f"{ident}: この記述に入る要素射影がありません")
        expect = None
        if "frequency" in attrs:
            raw = attrs["frequency"]
            expect = "none" if raw == "none" else to_number(raw, ident + ".frequency")
        self.descriptions[ident] = {
            "id": ident, "title": node.get("title") or "", "generators": gens, "frequency": expect,
            "tol": to_number(attrs["tol"], ident + ".tol") if "tol" in attrs else 1e-6,
        }

    # ------------------------------------------------------------------ 語の演算
    def reduce(self, seq):
        """順序つきの積を標準形にする。0 になるときは None。
        規則: 別の party は可換 / 同じ射影が続けば1つにまとめる / 同じ測定の別の結果が続けば 0。"""
        groups: dict[str, list[str]] = {p: [] for p in self.parties}
        for g in seq:
            sub = groups[self.party[g]]
            if sub:
                if sub[-1] == g:
                    continue
                m = self.meas_of.get(g)
                if m is not None and self.meas_of.get(sub[-1]) == m:
                    return None
            sub.append(g)
        return tuple(itertools.chain.from_iterable(groups[p] for p in self.parties))

    def word_dagger(self, w):
        return self.reduce(tuple(reversed(w)))

    def words_upto(self, level: int, gens=None):
        """長さ level までの、0 でない標準形の語(長さ順)。gens で要素射影を限れる。"""
        gens = self.generators if gens is None else gens
        found = {()}
        frontier = [()]
        for _ in range(level):
            nxt = []
            for w in frontier:
                for g in gens:
                    r = self.reduce(w + (g,))
                    if r is not None and len(r) == len(w) + 1 and r not in found:
                        found.add(r)
                        nxt.append(r)
            frontier = nxt
        return sorted(found, key=lambda w: (len(w), w))

    # ------------------------------------------------------------------ 多項式(語 → 係数)
    @staticmethod
    def p_add(x, y, sign=1):
        out = dict(x)
        for w, c in y.items():
            out[w] = out.get(w, 0) + sign * c
        return {w: c for w, c in out.items() if abs(c) > 1e-15}

    @staticmethod
    def p_scale(x, k):
        return {w: c * k for w, c in x.items() if abs(c * k) > 1e-15}

    def p_mul(self, x, y):
        out: dict[tuple, complex] = {}
        for u, cu in x.items():
            for v, cv in y.items():
                w = self.reduce(u + v)
                if w is not None:
                    out[w] = out.get(w, 0) + cu * cv
        return {w: c for w, c in out.items() if abs(c) > 1e-15}

    def p_dagger(self, x):
        out: dict[tuple, complex] = {}
        for w, c in x.items():
            r = self.word_dagger(w)
            if r is not None:
                out[r] = out.get(r, 0) + c.conjugate()
        return out

    def atom(self, ident):
        """要素射影1つを多項式にする。測定の最後の結果は「1 - ほかの和」に展開する。"""
        if ident in self.eliminated:
            poly = {(): 1 + 0j}
            for other in self.eliminated[ident]:
                poly[(other,)] = -1 + 0j
            return poly
        return {(ident,): 1 + 0j}

    # ------------------------------------------------------------------ 関係表
    def _add_term(self, node):
        ident = node["id"]
        attrs = node.get("attrs") or {}
        factors = attrs.get("factors")
        if not isinstance(factors, list) or not factors:
            raise PhysicsError(f"{ident}: factors に要素射影を順に並べます")
        for e in factors:
            if e not in self.party:
                raise PhysicsError(f"{ident}: factors の {e!r} は要素射影ではありません")
            if e in self.eliminated:
                raise PhysicsError(f"{ident}: {e} は測定の最後の結果で、ほかの結果から決まります。関係表には書きません")
        if "value" not in attrs:
            raise PhysicsError(f"{ident}: value がありません")
        value = to_complex(attrs["value"], ident + ".value")
        self.term_count += 1
        word = self.reduce(tuple(factors))
        if word is None:
            if abs(value) > TOL:
                raise PhysicsError(f"{ident}: この積は 0 になる(同じ測定の別の結果が続く)のに、値が 0 ではありません")
            return
        reverse = self.word_dagger(word)
        if reverse == word and abs(value.imag) > TOL:
            raise PhysicsError(f"{ident}: {show_word(word)} は逆順にしても同じ語なので、値は実数でなければなりません"
                            f"({fmt(value)})")
        for w, val in ((word, value), (reverse, value.conjugate())):
            if w in self.table and abs(self.table[w] - val) > TOL:
                other = self.source.get(w, "ω(1) = 1")
                raise PhysicsError(f"{ident}: {show_word(w)} の値が {other} と食い違います"
                                f"({fmt(val)} と {fmt(self.table[w])})。逆順の語の値は複素共役になります")
            self.table[w] = val
            self.source.setdefault(w, ident)

    def omega(self, poly) -> complex:
        missing = [w for w in poly if w not in self.table]
        if missing:
            raise MissingTerms(missing)
        return sum(c * self.table[w] for w, c in poly.items())

    # ------------------------------------------------------------------ 式
    def parse(self, text: str):
        """式を多項式(語 → 係数)にする。"""
        if text not in self._expr_cache:
            self._expr_cache[text] = _Parser(self, text, ()).run()
        return self._expr_cache[text]

    def evaluate(self, text: str) -> complex:
        return self.omega(self.parse(text))

    def history_probability(self, steps, results) -> float:
        """実現規則。C = E_k ⋯ E_1 として、確率は ω(C† C)。"""
        c = {(): 1 + 0j}
        for e, r in zip(steps, results):
            factor = self.atom(e) if r == "yes" else self.p_add({(): 1 + 0j}, self.atom(e), -1)
            c = self.p_mul(factor, c)
        return self.omega(self.p_mul(self.p_dagger(c), c)).real

    # ------------------------------------------------------------------ 行列
    def moment_level(self):
        """Γ[u,v] = ω(u†v) の全成分がそろう最大の水準と、その語の一覧。"""
        best_level, best_words = 0, [()]
        level = 1
        while True:
            words = self.words_upto(level)
            if len(words) == len(best_words) or len(words) > MAX_MATRIX:
                break
            complete = True
            for u in words:
                ud = self.word_dagger(u)
                for v in words:
                    w = self.reduce(ud + v)
                    if w is not None and w not in self.table:
                        complete = False
                        break
                if not complete:
                    break
            if not complete:
                break
            best_level, best_words = level, words
            level += 1
        return best_level, best_words

    def _lookup(self, w):
        if w is None:
            return 0j
        if w not in self.table:
            raise MissingTerms([w])
        return self.table[w]

    def moment_matrix(self, words, insert=None):
        """Γ[u][v] = ω(u† (insert) v)。"""
        mid = (insert,) if insert else ()
        return [[self._lookup(self.reduce(tuple(reversed(u)) + mid + v)) for v in words] for u in words]

    def reversed_matrix(self, words):
        """合成の順序を逆にした表 G'[u][v] = ω(v u†)。"""
        return [[self._lookup(self.reduce(v + tuple(reversed(u)))) for v in words] for u in words]

    def joint_probabilities(self):
        """別々の party から射影を1つずつ選んだ組について、はい/いいえの同時確率を出す。
        (組, 最小の確率, 和) のリスト。必要な項が無い組は飛ばす。"""
        by_party = {p: [e for e in self.party if self.party[e] == p] for p in self.parties}
        results, skipped = [], 0
        for r in range(1, len(self.parties) + 1):
            for combo in itertools.combinations(self.parties, r):
                for choice in itertools.product(*[by_party[p] for p in combo]):
                    try:
                        probs = [self.history_probability(choice, pattern)
                                 for pattern in itertools.product(("yes", "no"), repeat=r)]
                    except MissingTerms:
                        skipped += 1
                        continue
                    results.append((choice, min(probs), sum(probs)))
        return results, skipped

    # ------------------------------------------------------------------ 記述
    def description_generators(self, ident=None):
        if ident is None:
            return list(self.generators)
        if ident not in self.descriptions:
            raise PhysicsError(f"description {ident!r} がありません")
        return self.descriptions[ident]["generators"]

    def representation(self, ident=None) -> "Representation":
        key = tuple(self.description_generators(ident))
        if key not in self._rep_cache:
            self._rep_cache[key] = Representation(self, key)
        return self._rep_cache[key]

    def flow(self, ident=None) -> "Flow":
        key = tuple(self.description_generators(ident))
        if key not in self._flow_cache:
            self._flow_cache[key] = Flow(self.representation(ident))
        return self._flow_cache[key]


class _Parser:
    """式の再帰下降パーサ。eval は使わない。"""

    def __init__(self, system: System, text: str, stack: tuple):
        self.sys = system
        self.text = text
        self.stack = stack
        self.tokens = []
        pos = 0
        while pos < len(text):
            if text[pos:].strip() == "":
                break
            m = _TOKEN_RE.match(text, pos)
            if not m:
                raise PhysicsError(f"式を読めません: {text[pos:pos + 12]!r}(式: {text})")
            if m.group("num") is not None:
                value = complex(float(m.group("num")))
                self.tokens.append(("num", value * 1j if m.group("imag") else value))
            elif m.group("name") is not None:
                self.tokens.append(("name", m.group("name")))
            else:
                self.tokens.append(("op", m.group("op")))
            pos = m.end()
        self.i = 0

    def peek(self):
        return self.tokens[self.i] if self.i < len(self.tokens) else (None, None)

    def take(self, kind=None, value=None):
        tok = self.peek()
        if (kind is not None and tok[0] != kind) or (value is not None and tok[1] != value):
            raise PhysicsError(f"式の書き方が正しくありません(式: {self.text})")
        self.i += 1
        return tok

    def run(self):
        poly = self.expr()
        if self.i != len(self.tokens):
            raise PhysicsError(f"式の途中に演算子がありません(式: {self.text})")
        return poly

    def expr(self):
        poly = self.term()
        while self.peek() in (("op", "+"), ("op", "-")):
            sign = 1 if self.take()[1] == "+" else -1
            poly = System.p_add(poly, self.term(), sign)
        return poly

    def term(self):
        poly = self.factor()
        while self.peek() in (("op", "*"), ("op", "/")):
            op = self.take()[1]
            right = self.factor()
            if op == "*":
                poly = self.sys.p_mul(poly, right)
            else:
                poly = System.p_scale(poly, 1 / self.scalar(right))
        return poly

    def factor(self):
        if self.peek() in (("op", "-"), ("op", "+")):
            sign = -1 if self.take()[1] == "-" else 1
            return System.p_scale(self.factor(), sign)
        kind, value = self.peek()
        if kind == "num":
            self.take()
            return {(): value} if value != 0 else {}
        if kind == "op" and value == "(":
            self.take()
            poly = self.expr()
            self.take("op", ")")
            return poly
        if kind == "name":
            self.take()
            if self.peek() == ("op", "("):
                return self.call(value)
            return self.name(value)
        raise PhysicsError(f"式の書き方が正しくありません(式: {self.text})")

    def scalar(self, poly, allow_zero=False) -> complex:
        if any(w != () for w in poly):
            raise PhysicsError(f"ここには数が必要です(式: {self.text})")
        value = poly.get((), 0j)
        if value == 0 and not allow_zero:
            raise PhysicsError(f"0 では割れません(式: {self.text})")
        return value

    def call(self, name):
        self.take("op", "(")
        if name == "flow":
            kind, ident = self.take("name")
            if ident not in self.sys.descriptions:
                raise PhysicsError(f"flow の1つ目は description の ID にします: {ident!r}(式: {self.text})")
            self.take("op", ",")
            target = self.expr()
            self.take("op", ",")
            time = self.scalar(self.expr(), allow_zero=True)
            self.take("op", ")")
            if abs(time.imag) > 0:
                raise PhysicsError(f"flow の時間は実数にします(式: {self.text})")
            return self.sys.flow(ident).advance(target, time.real)
        args = [self.expr()]
        while self.peek() == ("op", ","):
            self.take()
            args.append(self.expr())
        self.take("op", ")")
        if name == "dag" and len(args) == 1:
            return self.sys.p_dagger(args[0])
        if name == "comm" and len(args) == 2:
            return System.p_add(self.sys.p_mul(args[0], args[1]), self.sys.p_mul(args[1], args[0]), -1)
        if name == "sqrt" and len(args) == 1:
            value = self.scalar(args[0], allow_zero=True)
            if abs(value.imag) > 0 or value.real < 0:
                raise PhysicsError(f"sqrt の中は 0 以上の実数にします(式: {self.text})")
            return {(): complex(math.sqrt(value.real))}
        raise PhysicsError(f"知らない関数です: {name}(式: {self.text})")

    def name(self, ident):
        if ident in self.sys.party:
            return self.sys.atom(ident)
        if ident in self.sys.defines:
            if ident in self.stack:
                raise PhysicsError(f"define が循環しています: {' → '.join(self.stack + (ident,))}")
            return _Parser(self.sys, self.sys.defines[ident], self.stack + (ident,)).run()
        raise PhysicsError(f"式の中の {ident!r} は、要素射影でも define でもありません(式: {self.text})")


# ====================================================================== 関係表からの復元(GNS 構成)
class Representation:
    """記述(要素射影の一部分)について、関係表からヒルベルト空間での表現を復元する。
    語 u のベクトル x_u を ⟨x_u, x_v⟩ = ω(u†v) となるように作り、
    各要素射影 e の行列を ⟨x_u, E x_v⟩ = ω(u† e v) から決める。"""

    def __init__(self, system: System, gens, rank_tol=1e-8):
        self.sys = system
        self.gens = list(gens)
        best, level = None, 0
        while True:
            words = system.words_upto(level, self.gens)
            if best is not None and len(words) == len(best[1]):
                break
            if len(words) > MAX_MATRIX:
                break
            try:
                gamma = system.moment_matrix(words)
                inserts = {e: system.moment_matrix(words, insert=e) for e in self.gens}
            except MissingTerms as exc:
                self.missing = exc
                break
            best = (level, words, gamma, inserts)
            level += 1
        if best is None:
            raise PhysicsError("復元に必要な項が足りません。" + str(getattr(self, "missing", "")))
        self.level, self.words, self.gamma, inserts = best
        self.index = {w: i for i, w in enumerate(self.words)}
        self.evals, self.vecs = eigh(self.gamma)
        top = max(self.evals)
        n = len(self.words)
        self.keep = [k for k, lam in enumerate(self.evals) if lam > rank_tol * max(top, 1e-300)]
        self.kernel = [[self.vecs[i][k] for i in range(n)] for k in range(n) if k not in set(self.keep)]
        self.dim = len(self.keep)
        # (X†)^+ = Λ^{-1/2} V_r†  と  X^+ = V_r Λ^{-1/2}
        self.left = [[self.vecs[i][k].conjugate() / math.sqrt(self.evals[k]) for i in range(n)] for k in self.keep]
        self.right = dagger(self.left)
        self.ops = {e: self.compress(m) for e, m in inserts.items()}
        self.vectors = {w: [math.sqrt(self.evals[k]) * self.vecs[i][k].conjugate() for k in self.keep]
                        for w, i in self.index.items()}
        self.omega = self.vectors[()]
        self._basis = None

    def compress(self, matrix):
        """語の添字の行列を、復元した空間の行列にする。"""
        return matmul(matmul(self.left, matrix), self.right)

    def operator(self, e):
        if e in self.sys.eliminated:
            m = identity(self.dim)
            for other in self.sys.eliminated[e]:
                m = madd(m, self.ops[other], -1)
            return m
        return self.ops[e]

    def matrix_of(self, poly):
        """多項式(語 → 係数)を、復元した空間の行列にする。"""
        out = [[0j] * self.dim for _ in range(self.dim)]
        for w, c in poly.items():
            m = identity(self.dim)
            for e in w:
                if e not in self.ops:
                    raise PhysicsError(f"{show_word(w)} は、この記述の外の要素射影 {e} を含みます")
                m = matmul(m, self.ops[e])
            out = madd(out, m, c)
        return out

    def basis(self):
        """復元した空間を張る語を、短いものから dim 個選ぶ。"""
        if self._basis is None:
            chosen, ortho = [], []
            for w in self.words:
                x = self.vectors[w]
                size = math.sqrt(inner(x, x).real)
                if size < 1e-9:
                    continue
                rest = x[:]
                for q in ortho:
                    k = inner(q, rest)
                    rest = [a - k * b for a, b in zip(rest, q)]
                norm = math.sqrt(inner(rest, rest).real)
                if norm > 1e-6 * size:
                    ortho.append([a / norm for a in rest])
                    chosen.append(w)
                if len(chosen) == self.dim:
                    break
            gram = [[inner(self.vectors[u], self.vectors[v]) for v in chosen] for u in chosen]
            self._basis = (chosen, gram)
        return self._basis

    def poly_of(self, matrix):
        """復元した空間の行列を、語の重ね合わせに戻す(行列が Ω に作用したベクトルから決める)。"""
        chosen, gram = self.basis()
        target = matvec(matrix, self.omega)
        coefs = solve_hermitian(gram, [inner(self.vectors[w], target) for w in chosen])
        return {w: c for w, c in zip(chosen, coefs) if abs(c) > 1e-12}

    def report(self):
        system = self.sys
        out = {"level": self.level, "words": len(self.words), "dimension": self.dim,
               "min_eigenvalue": self.evals[0], "hermitian": 0.0, "idempotent": 0.0,
               "commute": 0.0, "orthogonal": 0.0, "reproduce": 0.0, "checked_words": 0}
        for e in self.gens:
            out["hermitian"] = max(out["hermitian"], max_abs_diff(self.ops[e], dagger(self.ops[e])))
            out["idempotent"] = max(out["idempotent"], max_abs_diff(matmul(self.ops[e], self.ops[e]), self.ops[e]))
        for a, b in itertools.combinations(self.gens, 2):
            if system.party[a] != system.party[b]:
                out["commute"] = max(out["commute"], max_abs_diff(matmul(self.ops[a], self.ops[b]),
                                                                  matmul(self.ops[b], self.ops[a])))
            elif system.meas_of.get(a) is not None and system.meas_of.get(a) == system.meas_of.get(b):
                out["orthogonal"] = max(out["orthogonal"], max_abs(matmul(self.ops[a], self.ops[b])))
        inside = set(self.gens)
        for w, val in system.table.items():
            if not set(w) <= inside:
                continue
            vec = self.omega[:]
            for e in reversed(w):
                vec = matvec(self.ops[e], vec)
            out["reproduce"] = max(out["reproduce"], abs(inner(self.omega, vec) - val))
            out["checked_words"] += 1
        out["closed"] = out["idempotent"] <= 1e-6 and out["reproduce"] <= 1e-6
        return out


# ====================================================================== 時間の流れ(モジュラー流)
class Flow:
    """記述に限った関係表から、時間の流れを作る。

    G[u][v] = ω(u†v)(関係表)と G'[u][v] = ω(v u†)(合成の順序を逆にした表)のずれが流れを決める。
    復元した空間の上で ⟨x_u, Δ x_v⟩ = G'[u][v] となる Δ を作ると、流れは σ_t(a) = Δ^{it} a Δ^{-it}。
    表が対称(G = G')なら Δ = 1 で、流れは止まる。"""

    def __init__(self, rep: Representation):
        self.rep = rep
        report = rep.report()
        if not report["closed"]:
            raise PhysicsError("使える語が張る空間が閉じていないので、流れを作れません。より長い語の項が必要です")
        reverse = rep.sys.reversed_matrix(rep.words)
        self.asymmetry = max_abs_diff(rep.gamma, reverse)
        # a·Ω = 0 となる重ね合わせ a が a†·Ω = 0 も満たすこと(状態が忠実であること)が要る
        leak = max((max(abs(x) for x in matvec(reverse, k)) for k in rep.kernel), default=0.0)
        if leak > 1e-6:
            raise FlowUndefined("この記述では流れを定義できません。純粋な成分があります"
                                "(a·Ω = 0 なのに a†·Ω ≠ 0 となる重ね合わせ a がある)")
        self.delta = rep.compress(reverse)
        self.d_evals, self.d_vecs = eigh(self.delta)
        if self.d_evals[0] <= 1e-9:
            raise FlowUndefined("この記述では流れを定義できません。純粋な成分があります(Δ が正でない)")
        self.frequencies = [math.log(lam) for lam in self.d_evals]
        self.max_frequency = max(abs(f) for f in self.frequencies)
        self.fixed_omega = max(abs(a - b) for a, b in zip(matvec(self.delta, rep.omega), rep.omega))
        self._generator = None

    def power(self, z):
        """Δ^z。"""
        return function_of(self.d_evals, self.d_vecs, lambda lam: cmath.exp(z * math.log(lam)))

    def advance_matrix(self, matrix, t):
        return matmul(matmul(self.power(1j * t), matrix), self.power(-1j * t))

    def advance(self, poly, t):
        """σ_t(式) を、語の重ね合わせとして返す。"""
        return self.rep.poly_of(self.advance_matrix(self.rep.matrix_of(poly), t))

    def levels(self):
        """振動数(log Δ の固有値)を、近いものをまとめて (値, 重複度) で返す。"""
        out = []
        for f in sorted(self.frequencies):
            if out and abs(out[-1][0] - f) < 1e-6:
                out[-1][1] += 1
            else:
                out.append([f, 1])
        return [(0.0 if abs(f) < 5e-10 else f, m) for f, m in out]

    def generator(self):
        """流れの生成子 K を、語の重ね合わせで表す。σ_t(a) = e^{-itK} a e^{itK}。
        定数は決まらないので、K の最小の固有値を 0 にそろえる。
        戻り値 (多項式, K の準位のリスト, 交換子のずれ)。"""
        if self._generator is None:
            rep = self.rep
            chosen, _ = rep.basis()
            log_delta = function_of(self.d_evals, self.d_vecs, math.log)
            mats = [rep.matrix_of({w: 1 + 0j}) for w in chosen]

            def comm(x, y):
                return madd(matmul(x, y), matmul(y, x), -1)

            columns = [[a for e in rep.gens for row in comm(m, rep.ops[e]) for a in row] for m in mats]
            target = [a for e in rep.gens for row in comm(log_delta, rep.ops[e]) for a in row]
            normal = [[inner(ci, cj) for cj in columns] for ci in columns]
            coefs = solve_hermitian(normal, [inner(ci, target) for ci in columns])
            h = [[0j] * rep.dim for _ in range(rep.dim)]
            for c, m in zip(coefs, mats):
                h = madd(h, m, c)
            residual = max((max_abs_diff(comm(h, rep.ops[e]), comm(log_delta, rep.ops[e])) for e in rep.gens),
                           default=0.0)
            k = [[-(a + b.conjugate()) / 2 for a, b in zip(row, col)] for row, col in zip(h, zip(*h))]
            k_evals, _ = eigh(k)
            k = madd(k, identity(rep.dim), -k_evals[0])
            levels = []
            for lam in k_evals:
                lam -= k_evals[0]
                if levels and abs(levels[-1][0] - lam) < 1e-6:
                    levels[-1][1] += 1
                else:
                    levels.append([lam, 1])
            self._generator = (rep.poly_of(k), [(0.0 if abs(v) < 5e-10 else v, m) for v, m in levels], residual)
        return self._generator


# ====================================================================== コマンド
def describe(system: System, ident):
    if ident is None:
        return WHOLE
    d = system.descriptions[ident]
    return f"{ident} {d['title']}".strip() + f"(要素射影: {', '.join(d['generators'])})"


def cmd_check(system: System) -> int:
    failures = 0
    print(f"PPYN {system.version}  要素射影 {len(system.party)}"
          f"(独立な生成元 {len(system.generators)})  party {len(system.parties)}"
          f"  項 {system.term_count}  語 {len(system.table) - 1}")
    print("OK  関係表: 食い違いなし(逆順の語は複素共役、という規則も満たす)")

    level, words = system.moment_level()
    unchecked = level == 0
    if level == 0:
        missing = []
        for u in system.words_upto(1):
            for v in system.words_upto(1):
                w = system.reduce(system.word_dagger(u) + v)
                if w is not None and w not in system.table:
                    missing.append(w)
        print("--  整合: 確かめるための項が足りません。" + str(MissingTerms(missing)))
    else:
        evals, _ = eigh(system.moment_matrix(words))
        rank = sum(1 for lam in evals if lam > 1e-8 * max(evals[-1], 1e-300))
        ok = evals[0] >= -1e-8
        failures += 0 if ok else 1
        print(f"{'OK' if ok else 'NG'}  整合(公理4): 長さ {level} までの語 {len(words)} 個の行列"
              f"  最小固有値 {evals[0]:.3e}  階数 {rank}")

    joint, skipped = system.joint_probabilities()
    if joint:
        worst = min(joint, key=lambda item: item[1])
        norm = max(abs(item[2] - 1) for item in joint)
        ok = worst[1] >= -1e-8 and norm <= 1e-8
        failures += 0 if ok else 1
        print(f"{'OK' if ok else 'NG'}  同時確率: 独立な射影の組 {len(joint)} 通り"
              f"  最小の確率 {worst[1]:.3e}({' と '.join(worst[0])})  和のずれ {norm:.1e}"
              + (f"  項が無くて飛ばした組 {skipped}" if skipped else ""))

    for q in system.quantities:
        try:
            value = system.evaluate(q["expr"])
        except PhysicsError as exc:
            print(f"NG  quantity {q['id']}: {exc}")
            failures += 1
            continue
        line = f"quantity {q['id']} {q['title']}: {fmt(value)}"
        if q["expect"] is None:
            print("    " + line)
        else:
            ok = abs(value - q["expect"]) <= q["tol"]
            failures += 0 if ok else 1
            print(f"{'OK' if ok else 'NG'}  {line}  (期待値 {fmt(q['expect'])})")

    for h in system.histories:
        try:
            p = system.history_probability(h["steps"], h["results"])
        except PhysicsError as exc:
            print(f"NG  history {h['id']}: {exc}")
            failures += 1
            continue
        steps = " → ".join(f"{e}={r}" for e, r in zip(h["steps"], h["results"]))
        line = f"history {h['id']} {h['title']}: {steps}  確率 {p:.9g}"
        if h["expect"] is None:
            print("    " + line)
        else:
            ok = abs(p - h["expect"]) <= h["tol"]
            failures += 0 if ok else 1
            print(f"{'OK' if ok else 'NG'}  {line}  (期待値 {h['expect']:.9g})")

    for ident, d in system.descriptions.items():
        label = f"description {ident} {d['title']}".rstrip()
        try:
            flow = system.flow(ident)
        except FlowUndefined:
            ok = d["frequency"] in (None, "none")
            failures += 0 if ok else 1
            mark = "  " if d["frequency"] is None else ("OK" if ok else "NG")
            print(f"{mark}  {label}: 流れを定義できない(純粋な成分がある)"
                  + ("" if ok else f"  (期待値は振動数 {d['frequency']:.9g})"))
            continue
        except PhysicsError as exc:
            print(f"NG  {label}: {exc}")
            failures += 1
            continue
        line = f"{label}: 次元 {flow.rep.dim}  最大の振動数 {flow.max_frequency:.9g}"
        if d["frequency"] is None:
            print("    " + line)
        elif d["frequency"] == "none":
            failures += 1
            print(f"NG  {line}  (流れを定義できないはず、という指定)")
        else:
            ok = abs(flow.max_frequency - d["frequency"]) <= d["tol"]
            failures += 0 if ok else 1
            print(f"{'OK' if ok else 'NG'}  {line}  (期待値 {d['frequency']:.9g})")

    print("結果: " + ("問題なし" if failures == 0 else f"{failures} 件の問題")
          + ("(整合は項が足りず、確かめていません)" if unchecked else ""))
    return 0 if failures == 0 else 1


def cmd_words(system: System, level: int, ident) -> int:
    for w in system.words_upto(level, system.description_generators(ident)):
        mark = fmt(system.table[w]) if w in system.table else "(値なし)"
        print(f"{show_word(w):40s} {mark}")
    return 0


def cmd_gns(system: System, ident, show_matrices: bool) -> int:
    rep = system.representation(ident)
    report = rep.report()
    print(f"記述: {describe(system, ident)}")
    print(f"使った語: 長さ {report['level']} までの {report['words']} 個   Γ の最小固有値 {report['min_eigenvalue']:.3e}")
    print(f"復元された空間の次元: {report['dimension']}")
    print(f"射影の行列の検査:  E† = E のずれ {report['hermitian']:.1e}   EE = E のずれ {report['idempotent']:.1e}"
          f"   別の party どうしの交換子 {report['commute']:.1e}   同じ測定の結果の積 {report['orthogonal']:.1e}")
    print(f"関係表の再現: {report['checked_words']} 個の語で最大のずれ {report['reproduce']:.1e}")
    if not report["closed"]:
        print("注: 使った語が張る空間が射影の作用で閉じていません。より長い語の項が必要です。")
    if show_matrices:
        print("状態ベクトル Ω:", "  ".join(fmt(z, 5) for z in rep.omega))
        for e in system.party:
            if e in rep.ops or (e in system.eliminated and set(system.eliminated[e]) <= set(rep.ops)):
                print(f"{e}:")
                for row in rep.operator(e):
                    print("   " + "  ".join(f"{fmt(z, 5):>22s}" for z in row))
    return 0 if report["closed"] else 1


def cmd_flow(system: System, ident, time) -> int:
    print(f"記述: {describe(system, ident)}")
    try:
        flow = system.flow(ident)
    except FlowUndefined as exc:
        rep = system.representation(ident)
        print(f"使った語: 長さ {rep.level} までの {len(rep.words)} 個   復元された空間の次元 {rep.dim}")
        print(f"流れ: {exc}")
        return 1
    rep = flow.rep
    print(f"使った語: 長さ {rep.level} までの {len(rep.words)} 個   復元された空間の次元 {rep.dim}")
    print(f"関係表と、順序を逆にした表の差(最大): {flow.asymmetry:.6g}")
    if flow.max_frequency < 1e-7:
        print("流れ: 止まっている(関係表が対称なので、Δ = 1)")
    else:
        print("流れ: 定義できる")
    print("振動数(log Δ の固有値): " + "  ".join(f"{f:.9g}" + (f" (×{m})" if m > 1 else "") for f, m in flow.levels()))
    k_poly, k_levels, residual = flow.generator()
    print("生成子 K(最小の固有値を 0 にそろえた): K = " + show_poly(k_poly))
    print("K の準位: " + "  ".join(f"{v:.9g}" + (f" (×{m})" if m > 1 else "") for v, m in k_levels))
    probe = 0.7
    drift = 0.0
    for e in rep.gens:
        moved = flow.advance({(e,): 1 + 0j}, probe)
        drift = max(drift, abs(system.omega(moved) - system.omega({(e,): 1 + 0j})))
    print(f"検査:  Δ·Ω = Ω のずれ {flow.fixed_omega:.1e}   生成子の交換子のずれ {residual:.1e}"
          f"   流れのもとで ω が変わらないこと(t = {probe})のずれ {drift:.1e}")
    if time is not None:
        print(f"t = {time:g} での要素射影:")
        for e in rep.gens:
            print(f"   σ_t({e}) = " + show_poly(flow.advance({(e,): 1 + 0j}, time)))
    return 0


def main(argv: list[str] | None = None) -> int:
    """``python -m pyn.physics {check|eval|words|gns|flow} document.pyn``.

    終了コード: 0 = 問題なし(gns は空間が閉じた、flow は流れを作れた)、1 = 問題あり・エラー、
    2 = 使い方の誤り。そのアクションが読まないフラグは、黙って無視せずに止める(t-18)。
    """
    from .cli import use_utf8_stdio
    use_utf8_stdio()  # pyn の CLI と同じく UTF-8・LF で書く(t-19 / t-21)
    ap = argparse.ArgumentParser(prog="python -m pyn.physics", description="物理の層 PPYN(実験的)")
    ap.add_argument("action", choices=("check", "eval", "words", "gns", "flow"))
    ap.add_argument("path", type=Path)
    ap.add_argument("expr", nargs="?", help="eval: 計算する式")
    ap.add_argument("-L", "--level", type=int, help="words: 語の長さの上限(既定は 2)")
    ap.add_argument("-d", "--description", help="words / gns / flow: 記述の ID(省くと全体)")
    ap.add_argument("-t", "--time", type=float, help="flow: この時間だけ進めた要素射影を表示する")
    ap.add_argument("--matrices", action="store_true", help="gns: 復元した行列を表示する")
    args = ap.parse_args(argv)
    if args.action != "eval" and args.expr is not None:
        ap.error("式を渡せるのは eval だけです")
    if args.action == "eval" and args.expr is None:
        ap.error("eval には式が要ります")
    if args.level is not None and args.action != "words":
        ap.error("--level は words で使います")
    if args.level is not None and not 0 <= args.level <= 12:
        ap.error("--level は 0 から 12 の整数にします")
    if args.description is not None and args.action not in ("words", "gns", "flow"):
        ap.error("--description は words / gns / flow で使います")
    if args.time is not None and args.action != "flow":
        ap.error("--time は flow で使います")
    if args.matrices and args.action != "gns":
        ap.error("--matrices は gns で使います")
    try:
        system = System(load(args.path))
        if args.action == "check":
            return cmd_check(system)
        if args.action == "eval":
            print(fmt(system.evaluate(args.expr), 12))
            return 0
        if args.action == "words":
            return cmd_words(system, 2 if args.level is None else args.level, args.description)
        if args.action == "gns":
            return cmd_gns(system, args.description, args.matrices)
        return cmd_flow(system, args.description, args.time)
    except (OSError, ValueError, PynError) as exc:
        print(f"physics: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
