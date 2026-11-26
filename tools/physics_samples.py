"""物理の層(pyn.physics)のサンプルを、状態と射影の行列から作る(t-32)。

    python -m tools.physics_samples          # samples/physics/*.pyn を書き出す
    python -m tools.physics_samples --check  # サンプルが、ここでの計算に追従しているかを見る

サンプルに入るのは射影どうしの関係(値)だけで、状態も行列も入らない。値は 12 桁に丸めた小数で書く。

浮動小数点の計算は、環境によって末尾の桁が変わりうる。丸めの境目に当たると書き出す文字列も
変わるので、--check はバイト単位では比べない。構造が同じで、数が 1e-9 以内で一致することを見る。
"""

from __future__ import annotations

import argparse
import cmath
import math
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pyn.cli import use_utf8_stdio  # noqa: E402
from pyn import parse, physics, to_pyn_a  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "samples" / "physics"
DEG = math.pi / 180
HEAD = "  ppyn: 0.2\n"
TOLERANCE = 1e-9


# ---------------------------------------------------------------- 小さな線形代数
def kron(a, b):
    return [[x * y for x in ra for y in rb] for ra in a for rb in b]


def matvec(m, v):
    return [sum(x * y for x, y in zip(row, v)) for row in m]


def matmul(a, b):
    cols = list(zip(*b))
    return [[sum(x * y for x, y in zip(row, col)) for col in cols] for row in a]


def trace(m):
    return sum(m[i][i] for i in range(len(m)))


def diag(values):
    return [[values[i] if i == j else 0 for j in range(len(values))] for i in range(len(values))]


def projector_on(v):
    norm = math.sqrt(sum(abs(x) ** 2 for x in v))
    v = [x / norm for x in v]
    return [[a * b.conjugate() for b in v] for a in v]


I2 = [[1, 0], [0, 1]]


def embed(op, position, count):
    """count 個の量子ビットのうち position 番目に op を作用させる行列。"""
    out = [[1]]
    for k in range(count):
        out = kron(out, op if k == position else I2)
    return out


def spin(theta_deg):
    """XZ 平面の中で、角度 theta の方向に上向きである射影。"""
    t = theta_deg * DEG
    return projector_on([math.cos(t / 2), math.sin(t / 2)])


def expectation(state, ops, word):
    """語の値 ω(語)。state は状態ベクトルか、密度行列。"""
    if isinstance(state[0], list):
        m = state
        for e in word:
            m = matmul(m, ops[e])
        return trace(m)
    vec = state[:]
    for e in reversed(word):
        vec = matvec(ops[e], vec)
    return sum(a.conjugate() * b for a, b in zip(state, vec))


def history_prob(state, ops, steps, results):
    """状態ベクトルに射影を順に当てたときの確率。"""
    vec = state[:]
    for e, r in zip(steps, results):
        applied = matvec(ops[e], vec)
        vec = applied if r == "yes" else [a - b for a, b in zip(vec, applied)]
    return sum(abs(x) ** 2 for x in vec)


def flowed(probs, op, t):
    """対角な密度行列 diag(probs) の流れ ρ^{it} a ρ^{-it}。"""
    n = len(probs)
    return [[cmath.exp(1j * t * (math.log(probs[j]) - math.log(probs[k]))) * op[j][k] for k in range(n)]
            for j in range(n)]


# ---------------------------------------------------------------- 書き出し
def num(x: float) -> str:
    """PYN の裸の数(指数なしの小数)として書く。12 桁に丸める。"""
    if abs(x) < 5e-13:
        return "0"
    x = float(f"{x:.12g}")
    if x == int(x) and abs(x) < 1e15:
        return str(int(x))
    return format(Decimal(repr(x)), "f")


def value_text(z) -> str:
    z = complex(z)
    if abs(z.imag) < 5e-13:
        return num(z.real)
    return f"[{num(z.real)}, {num(z.imag)}]"


def prose_block(lines):
    return '  """\n' + "\n".join("  " + line if line else "" for line in lines) + '\n  """\n\n'


def term(count, word, value):
    return (f'  term t-{count:03d} "ω({"·".join(word)})":\n'
            f'    factors: [{", ".join(word)}]\n'
            f'    value: {value_text(value)}\n')


def build(name, title, prose, header_nodes, state, ops, max_len, extra_nodes, also=()):
    """header_nodes(射影・測定・define・description の .pyn テキスト)から代数を作り、
    長さ max_len までの語の値を計算して term として足す。逆順の語は複素共役なので、片方だけ書く。
    also に式を渡すと、その式に現れる語も足す(長い語を、使うものだけ入れるとき)。"""
    text = f'system {name} "{title}":\n' + HEAD + prose_block(prose) + header_nodes
    system = physics.System(parse(text))
    words = system.words_upto(max_len)
    for expr in also:
        words += sorted(system.parse(expr), key=lambda w: (len(w), w))
    lines, seen, count = [], set(), 0
    for word in words:
        if not word or word in seen:
            continue
        seen.add(word)
        seen.add(system.word_dagger(word))
        count += 1
        lines.append(term(count, word, expectation(state, ops, word)))
    text = text + "\n" + "\n".join(lines) + ("\n" + extra_nodes if extra_nodes else "")
    return to_pyn_a(parse(text))


def quantity(ident, title, expr, expect=None, tol="0.000001"):
    out = f'  quantity {ident} "{title}":\n'
    if expect is not None:
        out += f"    expect: {value_text(expect)}\n"
    out += f'    expr: "{expr}"\n'
    if expect is not None:
        out += f"    tol: {tol}\n"
    return out + "\n"


def history(ident, title, steps, results, expect):
    return (f'  history {ident} "{title}":\n    expect: {num(expect)}\n'
            f'    results: [{", ".join(results)}]\n    steps: [{", ".join(steps)}]\n    tol: 0.000001\n\n')


def description(ident, title, frequency=None, parties=None):
    out = f'  description {ident} "{title}":\n'
    if frequency is not None:
        out += f"    frequency: {frequency if frequency == 'none' else num(frequency)}\n"
    if parties:
        out += f"    parties: [{', '.join(parties)}]\n"
    return out + "\n"


# ---------------------------------------------------------------- 状態と射影(テストからも使う)
def model_qubit():
    psi = [math.cos(math.pi / 8), cmath.exp(1j * math.pi / 3) * math.sin(math.pi / 8)]
    return psi, {"e": projector_on([1, 0]), "f": projector_on([1, 1]), "g": projector_on([1, 1j])}


def model_qutrit():
    psi = [3 + 0j, 2 + 1j, 1 - 2j]
    norm = math.sqrt(sum(abs(x) ** 2 for x in psi))
    w = cmath.exp(2j * math.pi / 3)
    return [x / norm for x in psi], {
        "p1": projector_on([1, 0, 0]), "p2": projector_on([0, 1, 0]), "p3": projector_on([0, 0, 1]),
        "q1": projector_on([1, 1, 1]), "q2": projector_on([1, w, w * w]), "q3": projector_on([1, w * w, w]),
    }


def model_bell():
    s = 1 / math.sqrt(2)
    return [0j, s + 0j, -s + 0j, 0j], {
        "eA0": embed(spin(0), 0, 2), "eA1": embed(spin(90), 0, 2),
        "eB0": embed(spin(225), 1, 2), "eB1": embed(spin(315), 1, 2)}


PAIR_WEIGHTS = (0.75, 0.25)


def model_pair():
    p1, p2 = PAIR_WEIGHTS                       # √0.75 |01> - √0.25 |10>
    return [0j, math.sqrt(p1) + 0j, -math.sqrt(p2) + 0j, 0j], {
        "eA0": embed(spin(0), 0, 2), "eA1": embed(spin(90), 0, 2),
        "eB0": embed(spin(0), 1, 2), "eB1": embed(spin(90), 1, 2)}


def thermal_weights():
    p = 1 / (1 + math.exp(-2.0))                # e^{-K}/Z で K = 2·(1 - e)
    return [p, 1 - p]


def model_thermal():
    return diag(thermal_weights()), {"e": projector_on([1, 0]), "f": projector_on([1, 1])}


def model_ghz():
    s = 1 / math.sqrt(2)
    psi = [0j] * 8
    psi[0] = psi[7] = s + 0j
    ops = {}
    for k, who in enumerate("ABC"):
        ops[f"x{who}"] = embed(projector_on([1, 1]), k, 3)
        ops[f"y{who}"] = embed(projector_on([1, 1j]), k, 3)
    return psi, ops


LINK = projector_on([1, 0, 0, 1])               # 隣り合う2個が (|00> + |11>)/√2 にあること


def model_mediator():
    """2個の量子ビット |00>。a は A だけ、b は B だけ、m は両方にまたがる射影。"""
    return [1 + 0j, 0j, 0j, 0j], {
        "a": embed(projector_on([1, 1]), 0, 2), "b": embed(projector_on([1, 0]), 1, 2), "m": LINK}


def model_chain():
    """3個の量子ビット |000>。s1 と s3 は両端、b12 と b23 は隣どうしをつなぐ射影。"""
    psi = [0j] * 8
    psi[0] = 1 + 0j
    return psi, {"s1": embed(projector_on([1, 1]), 0, 3), "s3": embed(projector_on([1, 0]), 2, 3),
                 "b12": kron(LINK, I2), "b23": kron(I2, LINK)}


def chance(state, ops, steps, last):
    """steps を順に当て(結果は問わない)、最後に last を当てて「はい」になる確率。"""
    total = 0.0
    for k in range(2 ** len(steps)):
        results = ["yes" if k >> i & 1 else "no" for i in range(len(steps))]
        total += history_prob(state, ops, [*steps, last], [*results, "yes"])
    return total


#: 関係表を状態から計算できるサンプル(名前 → 状態と射影)。テストが、表の値と突き合わせる
MODELS = {"qubit": model_qubit, "qutrit": model_qutrit, "bell": model_bell,
          "pair": model_pair, "thermal": model_thermal, "ghz": model_ghz,
          "mediator": model_mediator, "chain": model_chain}


# ---------------------------------------------------------------- サンプル
def sample_qubit():
    psi, ops = model_qubit()
    header = (
        '  projector e "Z 方向で上向き":\n\n'
        '  projector f "X 方向で上向き":\n\n'
        '  projector g "Y 方向で上向き":\n\n'
        + description("whole", "全体の記述(純粋なので、流れを定義できない)", "none").rstrip("\n") + "\n"
    )
    extra = (
        quantity("q-ef", "e のあと f", "e*f", expectation(psi, ops, ("e", "f")))
        + quantity("q-fe", "f のあと e(順序を逆にすると複素共役になる)", "f*e", expectation(psi, ops, ("f", "e")))
        + quantity("q-comm", "交換子(順序を入れ替えたときの差。純虚数になる)", "comm(e, f)",
                   expectation(psi, ops, ("e", "f")) - expectation(psi, ops, ("f", "e")))
        + quantity("q-efg", "3つの射影の積", "e*f*g", expectation(psi, ops, ("e", "f", "g")))
        + history("h-ef", "e を先に、f を後に", ["e", "f"], ["yes", "yes"],
                  history_prob(psi, ops, ["e", "f"], ["yes", "yes"]))
        + history("h-fe", "f を先に、e を後に(確率が変わる)", ["f", "e"], ["yes", "yes"],
                  history_prob(psi, ops, ["f", "e"], ["yes", "yes"]))
    )
    prose = [
        "1個の量子ビット。3つの要素射影は互いに可換でないので、積の順序で値が変わる。",
        "",
        "値は、ある純粋状態と3つの射影から計算した(`tools/physics_samples.py`)。この文書に入っているのは射影どうしの関係だけで、状態ベクトルは入っていない。",
    ]
    return build("qubit", "量子ビット:合成の順序と複素数の値", prose, header, psi, ops, 4, extra)


def sample_qutrit():
    psi, ops = model_qutrit()
    header = (
        '  projector p1 "測定 P の結果1":\n\n  projector p2 "測定 P の結果2":\n\n  projector p3 "測定 P の結果3":\n\n'
        '  projector q1 "測定 Q の結果1":\n\n  projector q2 "測定 Q の結果2":\n\n  projector q3 "測定 Q の結果3":\n\n'
        '  measurement mP "測定 P(3つの結果)":\n    outcomes: [p1, p2, p3]\n\n'
        '  measurement mQ "測定 Q(3つの結果)":\n    outcomes: [q1, q2, q3]\n'
    )
    extra = (
        quantity("q-sum", "測定 P の結果の確率の和", "p1 + p2 + p3", 1)
        + quantity("q-p3", "最後の結果 p3 の確率(1 からほかを引いて決まる)", "p3", expectation(psi, ops, ("p3",)))
        + quantity("q-orth", "同じ測定の別の結果の積は 0", "p1*p2", 0)
        + quantity("q-p3q3", "消去された射影どうしの積", "p3*q3", expectation(psi, ops, ("p3", "q3")))
        + history("h-pq", "P で結果3、続けて Q で結果3", ["p3", "q3"], ["yes", "yes"],
                  history_prob(psi, ops, ["p3", "q3"], ["yes", "yes"]))
    )
    prose = [
        "3つの状態を持つ系。`measurement` は3つの要素射影を結ぶ関係で、互いに直交し、足すと 1 になることを表す。",
        "",
        "各測定の最後の結果(p3 と q3)は、ほかの結果から決まるので、関係表には現れない。式の中では使える。",
    ]
    return build("qutrit", "3準位系:3つの結果を持つ測定", prose, header, psi, ops, 3, extra)


BELL_HEADER = (
    '  projector eA0 "アリスの設定0で上向き":\n    party: A\n\n'
    '  projector eA1 "アリスの設定1で上向き":\n    party: A\n\n'
    '  projector eB0 "ボブの設定0で上向き":\n    party: B\n\n'
    '  projector eB1 "ボブの設定1で上向き":\n    party: B\n\n'
    '  define A0 "アリスの設定0の結果(±1)":\n    expr: "2*eA0 - 1"\n\n'
    '  define A1 "アリスの設定1の結果(±1)":\n    expr: "2*eA1 - 1"\n\n'
    '  define B0 "ボブの設定0の結果(±1)":\n    expr: "2*eB0 - 1"\n\n'
    '  define B1 "ボブの設定1の結果(±1)":\n    expr: "2*eB1 - 1"\n\n'
    '  define S "CHSH の式":\n    expr: "A0*B0 - A0*B1 + A1*B0 + A1*B1"\n\n'
)


def sample_bell():
    psi, ops = model_bell()
    header = (BELL_HEADER
              + description("whole", "全体の記述(純粋なので、流れを定義できない)", "none")
              + description("dA", "アリスの側だけの記述(最大にもつれているので、流れは止まる)", 0, ["A"]).rstrip("\n") + "\n")
    extra = (
        quantity("q-S", "CHSH 値", "S", 2 * math.sqrt(2))
        + quantity("q-gap", "上限 2√2 との差", "S - 2*sqrt(2)", 0)
        + quantity("q-A0", "アリスの設定0の平均", "A0", 0)
        + quantity("q-commAB", "アリスとボブの射影は可換", "comm(eA0, eB1)", 0)
        + history("h-ab", "アリスが設定0で上、ボブが設定0で上", ["eA0", "eB0"], ["yes", "yes"],
                  history_prob(psi, ops, ["eA0", "eB0"], ["yes", "yes"]))
        + history("h-aa", "アリスが設定0で上、続けて設定1で上", ["eA0", "eA1"], ["yes", "yes"],
                  history_prob(psi, ops, ["eA0", "eA1"], ["yes", "yes"]))
    )
    prose = [
        "最大にもつれた2個の量子ビット。アリスとボブの要素射影は `party` が違うので可換になる。",
        "",
        "CHSH 値は 2√2 で、量子論の上限に一致する。関係表は長さ5までの語を含むので、合成を1段入れた整合条件を確かめられ、4次元のヒルベルト空間を復元できる。",
    ]
    return build("bell", "ベル状態:CHSH 値 2√2", prose, header, psi, ops, 5, extra)


def sample_pair():
    psi, ops = model_pair()
    p1, p2 = PAIR_WEIGHTS
    freq = math.log(p1 / p2)
    a1 = spin(90)
    two_time = trace(matmul(matmul(diag([p1, p2]), a1), flowed([p1, p2], a1, 0.5)))
    header = (
        '  projector eA0 "アリスの Z 方向で上向き":\n    party: A\n\n'
        '  projector eA1 "アリスの X 方向で上向き":\n    party: A\n\n'
        '  projector eB0 "ボブの Z 方向で上向き":\n    party: B\n\n'
        '  projector eB1 "ボブの X 方向で上向き":\n    party: B\n\n'
        + description("whole", "全体の記述(純粋なので、流れを定義できない)", "none")
        + description("dA", "アリスの側だけの記述", freq, ["A"])
        + description("dB", "ボブの側だけの記述", freq, ["B"]).rstrip("\n") + "\n"
    )
    extra = (
        quantity("q-two-time", "アリスの時間で 0.5 だけ離れた、同じ射影どうしの相関",
                 "eA1 * flow(dA, eA1, 0.5)", two_time)
        + quantity("q-still", "流れのもとで ω は変わらない", "flow(dA, eA1, 0.7) - eA1", 0)
        + quantity("q-fixed", "生成子と可換な射影は、流れで動かない", "eA1 * (flow(dA, eA0, 0.7) - eA0)", 0)
    )
    prose = [
        "部分的にもつれた2個の量子ビット(重みは 0.75 と 0.25)。全体は純粋な状態なので、全体の記述には時間の流れが無い。",
        "",
        "アリスの側だけ、ボブの側だけの記述には流れがあり、振動数は log(0.75 / 0.25) = 1.0986 になる。流れの速さは、相手とのもつれ方で決まる。",
    ]
    return build("pair", "部分的にもつれた対:部分の記述にだけ時間が流れる", prose, header, psi, ops, 5, extra)


def sample_thermal():
    rho, ops = model_thermal()
    probs = thermal_weights()
    two_time = trace(matmul(matmul(rho, ops["f"]), flowed(probs, ops["f"], 0.5)))
    header = (
        '  projector e "エネルギーの低い側":\n\n'
        '  projector f "X 方向で上向き":\n\n'
        + description("d", "全体の記述(混ざった状態なので、流れがある)", 2).rstrip("\n") + "\n"
    )
    extra = (
        quantity("q-two-time", "時間 0.5 だけ離れた、同じ射影どうしの相関", "f * flow(d, f, 0.5)", two_time)
        + quantity("q-still", "流れのもとで ω は変わらない", "flow(d, f, 0.9) - f", 0)
        + quantity("q-fixed", "生成子と可換な射影は、流れで動かない", "f * (flow(d, e, 0.9) - e)", 0)
    )
    prose = [
        "熱平衡にある1個の量子ビット。状態は e^{-K} に比例し、K = 2·(1 − e) である。",
        "",
        "`python -m pyn.physics flow` は、関係表だけから振動数 2 と、生成子 K = 2 − 2·e を読み出す。分かるのは K(温度の逆数とエネルギーの積)までで、時間の絶対的な単位は決まらない。",
    ]
    return build("thermal", "熱平衡:関係表から流れの生成子を読む", prose, header, rho, ops, 5, extra)


def sample_ghz():
    psi, ops = model_ghz()
    header = ""
    for who in "ABC":
        header += (f'  projector x{who} "{who} の X 方向で上向き":\n    party: {who}\n\n'
                   f'  projector y{who} "{who} の Y 方向で上向き":\n    party: {who}\n\n'
                   f'  define X{who} "{who} の X の結果(±1)":\n    expr: "2*x{who} - 1"\n\n'
                   f'  define Y{who} "{who} の Y の結果(±1)":\n    expr: "2*y{who} - 1"\n\n')
    header = header.rstrip("\n") + "\n"
    extra = (
        quantity("q-mermin", "Mermin の式(古典の上限は 2)", "XA*XB*XC - XA*YB*YC - YA*XB*YC - YA*YB*XC", 4)
        + quantity("q-single", "単独の平均は 0", "XA", 0)
        + quantity("q-pair", "2体の相関は 0", "XA*XB", 0)
        + quantity("q-pair2", "別の2体の相関も 0", "YB*XC", 0)
        + quantity("q-triple", "3体の相関は 1", "XA*XB*XC", 1)
    )
    prose = [
        "もつれた3個の量子ビット。単独の平均も2体の相関もすべて 0 で、3者を結ぶ項にだけ情報がある。",
        "",
        "2項の関係だけでは、この状態と完全なノイズを区別できない。",
    ]
    return build("ghz", "GHZ 状態:3者を結ぶ項が必要な例", prose, header, psi, ops, 3, extra)


def unasked(target, first):
    """first を当ててから(結果は問わない)target を見る、という式。"""
    return f"{first}*{target}*{first} + (1 - {first})*{target}*(1 - {first})"


def sample_mediator():
    psi, ops = model_mediator()
    header = (
        '  projector a "場所 A の X 方向で上向き":\n    party: A\n\n'
        '  projector b "場所 B の Z 方向で上向き":\n    party: B\n\n'
        '  projector m "A と B の2個がそろっている(両方にまたがる射影)":\n    party: [A, B]\n\n'
        f'  define viaM "m を当ててから b を見る(m の結果は問わない)":\n    expr: "{unasked("b", "m")}"\n'
    )
    direct = f"({unasked('b', 'a')})"
    extra = (
        quantity("q-comm", "a と b は可換(party が重ならない)", "comm(a, b)", 0)
        + quantity("q-direct", "a を当てても、b の確率は変わらない(関係表の値によらず 0)",
                   f"{unasked('b', 'a')} - b", 0)
        + quantity("q-before", "m のあとの b の確率", "viaM", chance(psi, ops, ["m"], "b"))
        + quantity("q-mediated", "a、m、b の順に当てると、a を当てたことで b の確率が変わる",
                   f"{unasked('viaM', 'a')} - viaM",
                   chance(psi, ops, ["a", "m"], "b") - chance(psi, ops, ["m"], "b"))
        + quantity("q-wrong-order", "m、a、b の順では変わらない(関係表の値によらず 0)",
                   f"{unasked(direct, 'm')} - viaM", 0)
        + history("h-am", "a を先に、m を後に", ["a", "m"], ["yes", "yes"],
                  history_prob(psi, ops, ["a", "m"], ["yes", "yes"]))
        + history("h-ma", "m を先に、a を後に(確率が変わるので、a と m は可換でない)", ["m", "a"], ["yes", "yes"],
                  history_prob(psi, ops, ["m", "a"], ["yes", "yes"]))
    )
    prose = [
        "2個の量子ビット。`a` は場所 A だけ、`b` は場所 B だけに関わる。`m` は `party: [A, B]` と書いた、両方にまたがる射影である。`a` と `b` は party が重ならないので可換になり、`m` はどちらとも可換でない。",
        "",
        "`a` を当てても `b` の確率は変わらない。`a`、`m`、`b` の順に当てたときだけ、`a` を当てたことで `b` の確率が変わる(0.5 が 0.75 になる)。離れた2つは、間をつなぐ射影を通ってだけ作用し合う。",
    ]
    return build("mediator", "仲立ち:間をつなぐ射影を通ってだけ作用が届く", prose, header, psi, ops, 5, extra)


def sample_chain():
    psi, ops = model_chain()
    wrong = unasked(f"({unasked('s3', 'b12')})", "b23")
    header = (
        '  projector s1 "場所1の X 方向で上向き":\n    party: P1\n\n'
        '  projector b12 "場所1と2をつなぐ射影":\n    party: [P1, P2]\n\n'
        '  projector b23 "場所2と3をつなぐ射影":\n    party: [P2, P3]\n\n'
        '  projector s3 "場所3の Z 方向で上向き":\n    party: P3\n\n'
        f'  define via23 "b23 を当ててから s3 を見る(b23 の結果は問わない)":\n    expr: "{unasked("s3", "b23")}"\n\n'
        f'  define viaBoth "b12、b23 の順に当ててから s3 を見る":\n    expr: "{unasked("via23", "b12")}"\n\n'
        f'  define viaWrong "b23、b12 の順に当ててから s3 を見る":\n    expr: "{wrong}"\n'
    )
    quantities = (
        ("q-comm", "両端の s1 と s3 は可換", "comm(s1, s3)", 0),
        ("q-comm-far", "s1 は、離れた b23 とも可換", "comm(s1, b23)", 0),
        ("q-direct", "s1 を当てても、s3 の確率は変わらない(関係表の値によらず 0)", f"{unasked('s3', 's1')} - s3", 0),
        ("q-one-link", "b23 だけを通しても変わらない(関係表の値によらず 0)",
         f"{unasked('via23', 's1')} - via23", 0),
        ("q-before", "b12、b23 のあとの s3 の確率", "viaBoth", chance(psi, ops, ["b12", "b23"], "s3")),
        ("q-chain", "s1、b12、b23、s3 の順に当てると、s1 を当てたことで s3 の確率が変わる",
         f"{unasked('viaBoth', 's1')} - viaBoth",
         chance(psi, ops, ["s1", "b12", "b23"], "s3") - chance(psi, ops, ["b12", "b23"], "s3")),
        ("q-wrong-order", "b23、b12 の順では変わらない(関係表の値によらず 0)",
         f"{unasked('viaWrong', 's1')} - viaWrong", 0),
    )
    extra = "".join(quantity(*q) for q in quantities)
    prose = [
        "3個の量子ビットの鎖。`s1` と `s3` は両端の射影で、`b12` と `b23` は隣どうしをつなぐ射影である。`b12` と `b23` は場所2を共有するので可換でなく、`s1` と `b23`、`b12` と `s3` は party が重ならないので可換になる。",
        "",
        "`s1` を当てたことが `s3` の確率に現れるのは、`s1`、`b12`、`b23`、`s3` の順に当てたときだけである(0.75 が 0.625 になる)。つなぐ射影を1つ飛ばしても、順序を逆にしても、変化は 0 になる。作用は、つなぐ射影を順にたどってだけ伝わる。",
        "",
        "関係表は、長さ2までの語と、上の式に現れる語だけを含む。長さ7までの語は 571 個あるので、すべては入れていない。",
    ]
    return build("chain", "鎖:作用は、つなぐ射影を順にたどって伝わる", prose, header, psi, ops, 2, extra,
                 also=[q[2] for q in quantities])


def sample_classical():
    """共通の実体 λ(円周上の一様な角度)。A_i は cos(λ - a_i) > 0 で上、B_j は cos(λ - b_j) < 0 で上。
    すべての射影が同時に値を持つので、全部が可換になる。"""
    angles = {"a0": 0, "a1": 90, "b0": 45, "b1": 135}
    names = list(angles)

    def up(name, lam):
        c = math.cos((lam - angles[name]) * DEG)
        return c > 0 if name.startswith("a") else c < 0

    arcs = [22.5 + 45 * k for k in range(8)]        # 符号が変わる境界は 45 度刻み
    header = "".join(f'  projector {n} "{("アリス" if n[0] == "a" else "ボブ")}の設定{n[1]}で上向き":\n'
                     f'    party: {n.upper()}\n\n' for n in names)
    header += (
        '  define A0:\n    expr: "2*a0 - 1"\n\n  define A1:\n    expr: "2*a1 - 1"\n\n'
        '  define B0:\n    expr: "2*b0 - 1"\n\n  define B1:\n    expr: "2*b1 - 1"\n\n'
        + description("whole", "全体の記述(すべて可換なので、流れは止まる)", 0).rstrip("\n") + "\n"
    )
    prose = [
        "共通の実体(円周上の角度)があり、4つの射影すべてが同時に値を持つモデル。同時に値を持つので、4つの射影はすべて可換である。それを `party` を1つずつ分けて表している。",
        "",
        "関係表は、4つの射影の同時確率そのものになる。4つを結ぶ項まで含めて 15 項。CHSH の式の絶対値は 2 で、ベルの不等式の上限に一致する。すべてが可換な表は対称なので、時間は流れない。",
    ]
    text = 'system classical "共通の値を持つモデル:CHSH 値は 2 で止まる":\n' + HEAD + prose_block(prose) + header
    system = physics.System(parse(text))
    lines, count = [], 0
    for word in system.words_upto(4):
        if not word:
            continue
        count += 1
        lines.append(term(count, word, sum(all(up(n, lam) for n in word) for lam in arcs) / len(arcs)))
    extra = (quantity("q-S", "CHSH の式(絶対値が 2)", "A0*B0 - A0*B1 + A1*B0 + A1*B1", -2)
             + quantity("q-four", "a0・a1・b1 が上向きで b0 が下向きになる確率(4つを結ぶ項を使う)",
                        "a0*a1*(1 - b0)*b1", 0.125))
    return to_pyn_a(parse(text + "\n" + "\n".join(lines) + "\n" + extra))


def sample_pr_box():
    header = (
        '  projector eA0 "アリスの設定0で上向き":\n    party: A\n\n'
        '  projector eA1 "アリスの設定1で上向き":\n    party: A\n\n'
        '  projector eB0 "ボブの設定0で上向き":\n    party: B\n\n'
        '  projector eB1 "ボブの設定1で上向き":\n    party: B\n\n'
        '  define S "CHSH の式":\n    expr: "(2*eA0-1)*(2*eB0-1) - (2*eA0-1)*(2*eB1-1) + (2*eA1-1)*(2*eB0-1) + (2*eA1-1)*(2*eB1-1)"\n'
    )
    prose = [
        "超光速通信はできないが、量子論では実現しない相関(CHSH の式が 4)。`python -m pyn.physics check` は、整合の条件(行列の半正定値性)を満たさないとして NG を返す。",
        "",
        "同じ側の2つの射影の積(eA0·eA1 と eB0·eB1)は測定できないので、ここでは独立な場合の 0.25 を入れてある。この2つをどう選んでも整合しない。整合する表では、CHSH の式は 2√2 を超えないからである。",
    ]
    text = 'system pr_box "PR 箱:整合しない関係表":\n' + HEAD + prose_block(prose) + header + "\n"
    terms = [(("eA0",), 0.5), (("eA1",), 0.5), (("eB0",), 0.5), (("eB1",), 0.5),
             (("eA0", "eB0"), 0.5), (("eA0", "eB1"), 0.0), (("eA1", "eB0"), 0.5), (("eA1", "eB1"), 0.5),
             (("eA0", "eA1"), 0.25), (("eB0", "eB1"), 0.25)]
    for k, (word, val) in enumerate(terms, start=1):
        text += term(k, word, val) + "\n"
    return to_pyn_a(parse(text + quantity("q-S", "CHSH の式", "S", 4)))


#: (ファイル名, 作る関数)。pr_box だけは、検証で NG になることを見せるためのサンプル
SAMPLES = (
    ("qubit.pyn", sample_qubit), ("qutrit.pyn", sample_qutrit), ("bell.pyn", sample_bell),
    ("pair.pyn", sample_pair), ("thermal.pyn", sample_thermal), ("ghz.pyn", sample_ghz),
    ("classical.pyn", sample_classical), ("pr_box.pyn", sample_pr_box),
    ("mediator.pyn", sample_mediator), ("chain.pyn", sample_chain),
)


def same(a, b, tolerance=TOLERANCE) -> bool:
    """正準形の値を比べる。数だけは許容差つきで比べ、ほかは一致を求める。"""
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) <= tolerance
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(same(x, y, tolerance) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k], tolerance) for k in a)
    return a == b


def main(argv=None) -> int:
    use_utf8_stdio()  # CLI と同じく UTF-8・LF で書く(t-19 / t-21)
    ap = argparse.ArgumentParser(prog="physics_samples", description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="書き出さず、追従しているかだけを見る")
    args = ap.parse_args(argv)

    stale: list[str] = []
    for filename, make in SAMPLES:
        expected = make()
        path = OUT / filename
        target = path.relative_to(ROOT).as_posix()
        if args.check:
            if not path.exists() or not same(parse(path.read_text(encoding="utf-8")), parse(expected)):
                stale.append(target)
            continue
        OUT.mkdir(parents=True, exist_ok=True)
        path.write_text(expected, encoding="utf-8", newline="\n")
        print(f"wrote {target}")

    if args.check:
        if stale:
            print("NG  ここでの計算に追従していません: " + ", ".join(stale)
                  + " — python -m tools.physics_samples で再生成", file=sys.stderr)
            return 1
        print("OK  サンプルは追従しています")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
