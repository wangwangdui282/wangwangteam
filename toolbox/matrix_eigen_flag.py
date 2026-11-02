#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
matrix_eigen_flag.py - 矩阵型 crypto 题求解器（纯标准库，不需要 Sage）

适用题型：
    L = [msg, temp1, ..., temp_{n-1}]        （列向量）
    R = [d0*msg, d1*temp1, ...]              （每列随机缩放）
    C = (R * L^(-1)) ** e

    => R·L⁻¹ = L·D·L⁻¹  （相似变换）
    => C = L·D^e·L⁻¹
    => **msg 是 C 的特征向量**

用法：
    python toolbox/matrix_eigen_flag.py output.txt
    python toolbox/matrix_eigen_flag.py output.txt --n 42 --show-eigs

性能说明（重要）：
    求 42 个特征值的朴素做法要跑十分钟以上。
    这里的关键优化是：x^((p-1)/2) mod f **只算一次**，
    之后每个分裂节点用 h mod g 复用，不再重复 255 次多项式平方。
"""
import argparse
import ast
import random
import re
import sys
import time


# ---------------------------------------------------------------- 多项式
def ptrim(a, p):
    a = [x % p for x in a]
    while len(a) > 1 and a[-1] == 0:
        a.pop()
    return a


def pmul(a, b, p):
    if len(a) == 1 and a[0] == 0:
        return [0]
    if len(b) == 1 and b[0] == 0:
        return [0]
    out = [0] * (len(a) + len(b) - 1)
    for i, x in enumerate(a):
        if x:
            for j, y in enumerate(b):
                if y:
                    out[i + j] = (out[i + j] + x * y) % p
    return out


def pmod(a, f, p):
    a = a[:]
    df = len(f) - 1
    if df <= 0:
        return [0]
    inv = pow(f[-1], -1, p)
    while len(a) >= len(f):
        coef = a[-1] * inv % p
        if coef:
            shift = len(a) - len(f)
            for i in range(len(f)):
                a[i + shift] = (a[i + shift] - coef * f[i]) % p
        while len(a) > 1 and a[-1] == 0:
            a.pop()
        if not a:
            return [0]
    return a


def pdivmod(a, b, p):
    a = ptrim(a[:], p)
    b = ptrim(b[:], p)
    inv = pow(b[-1], -1, p)
    q = [0] * max(1, len(a) - len(b) + 1)
    while len(a) >= len(b) and not (len(a) == 1 and a[0] == 0):
        shift = len(a) - len(b)
        coef = a[-1] * inv % p
        q[shift] = coef
        if coef:
            for i in range(len(b)):
                a[i + shift] = (a[i + shift] - coef * b[i]) % p
        while len(a) > 1 and a[-1] == 0:
            a.pop()
    return ptrim(q, p), a


def pgcd(a, b, p):
    a = ptrim(a[:], p)
    b = ptrim(b[:], p)
    while not (len(b) == 1 and b[0] == 0):
        _, r = pdivmod(a, b, p)
        a, b = b, r
    if len(a) > 1:
        inv = pow(a[-1], -1, p)
        a = [x * inv % p for x in a]
    return a


def ppowmod(base, exp, f, p):
    r = [1]
    base = pmod(trim(base, p), f, p)
    while exp > 0:
        if exp & 1:
            r = pmod(pmul(r, base, p), f, p)
        base = pmod(pmul(base, base, p), f, p)
        exp >>= 1
    return r


def trim(a, p):
    return ptrim(a[:], p)


# ---------------------------------------------------------------- 线性代数
def matvec(M, v, p):
    return [sum(M[i][j] * v[j] for j in range(len(v))) % p for i in range(len(M))]


def nullspace(M, p):
    n, m = len(M), len(M[0])
    A = [[M[i][j] % p for j in range(m)] for i in range(n)]
    piv, r = [], 0
    for c in range(m):
        sel = None
        for i in range(r, n):
            if A[i][c]:
                sel = i
                break
        if sel is None:
            continue
        A[r], A[sel] = A[sel], A[r]
        inv = pow(A[r][c], -1, p)
        A[r] = [(x * inv) % p for x in A[r]]
        for i in range(n):
            if i != r and A[i][c]:
                f = A[i][c]
                A[i] = [(A[i][j] - f * A[r][j]) % p for j in range(m)]
        piv.append(c)
        r += 1
        if r == n:
            break
    free = [j for j in range(m) if j not in piv]
    basis = []
    for fj in free:
        v = [0] * m
        v[fj] = 1
        for idx, pc in enumerate(piv):
            v[pc] = (-A[idx][fj]) % p
        basis.append(v)
    return basis


def berlekamp_massey(seq, p):
    C, B = [1], [1]
    L, m, b = 0, 1, 1
    for n in range(len(seq)):
        d = seq[n] % p
        for i in range(1, L + 1):
            d = (d + C[i] * seq[n - i]) % p
        if d == 0:
            m += 1
            continue
        coef = d * pow(b, -1, p) % p
        T = C[:]
        if len(C) < len(B) + m:
            C += [0] * (len(B) + m - len(C))
        for j in range(len(B)):
            C[j + m] = (C[j + m] - coef * B[j]) % p
        if 2 * L <= n:
            L = n + 1 - L
            B, b, m = T, d, 1
        else:
            m += 1
    # 注意：BM 给的是「联络多项式」C(x)（满足 sum C_i a_{n-i} = 0），
    # 真正的特征多项式是它的互反：x^L · C(1/x)。
    # 忘了这一步，解出来的"根"根本不是特征值（零空间维数会是 0）。
    coeffs = [x % p for x in C[:L + 1]]
    return ptrim(list(reversed(coeffs)), p)


# ---------------------------------------------------------------- 求根（惰性，带复用）
def eigen_roots_lazy(f, p, rng, deadline=None):
    """逐个 yield f 的根。

    核心优化：x^((p-1)/2) mod f 这一类"重"运算只预计算有限几次，
    之后每个分裂节点用 base mod g 复用（便宜），不再重复 255 次多项式平方。
    """
    f = ptrim(f[:], p)
    if len(f) <= 1:
        return
    if len(f) == 2:
        yield (-f[0] * pow(f[1], -1, p)) % p
        return

    # 1) 先掐出线性因子部分：gcd(x^p - x, f)
    xp = ppowmod([0, 1], p, f, p)
    lin = ptrim([(xp[i] - (1 if i == 1 else 0)) % p for i in range(len(xp))], p)
    g = pgcd(lin, f, p)
    if len(g) <= 1:
        return
    if len(g) == 2:
        yield (-g[0] * pow(g[1], -1, p)) % p
        return

    # 2) 预计算几个二次特征基（每个约 0.7s，总共几秒）
    bases = []
    for a in (0, 1, 2):
        bases.append(ppowmod([a % p, 1], (p - 1) // 2, f, p))

    # 3) 分裂成线性因子
    stack = [g]
    while stack:
        if deadline and time.time() > deadline:
            return
        h = stack.pop()
        if len(h) == 2:
            yield (-h[0] * pow(h[1], -1, p)) % p
            continue
        if len(h) <= 1:
            continue

        split = None
        for base in bases:
            hh = pmod(base[:], h, p)
            hh = ptrim([(hh[i] - (1 if i == 0 else 0)) % p for i in range(len(hh))], p)
            d = pgcd(hh, h, p)
            if 1 < len(d) < len(h):
                split = d
                break

        if split is None:
            # 预计算的基都没劈开 -> 现算一个新 shift（慢，但很少发生）
            for _ in range(4):
                a = rng.randrange(1, p)
                base = ppowmod([a % p, 1], (p - 1) // 2, f, p)
                hh = pmod(base[:], h, p)
                hh = ptrim([(hh[i] - (1 if i == 0 else 0)) % p for i in range(len(hh))], p)
                d = pgcd(hh, h, p)
                if 1 < len(d) < len(h):
                    split = d
                    bases.append(base)
                    break

        if split is None:
            # 实在劈不开：次数小就暴力试，大就放弃这部分
            if len(h) <= 8:
                rest = ptrim(h[:], p)
                for v in range(0, 100000):
                    if deadline and time.time() > deadline:
                        return
                    val = 0
                    for c in reversed(rest):
                        val = (val * v + c) % p
                    if val == 0:
                        yield v
                        rest, _ = pdivmod(rest, [(-v) % p, 1], p)
                        if len(rest) <= 1:
                            break
            continue

        stack.append(split)
        stack.append(pdivmod(h, split, p)[0])


# ---------------------------------------------------------------- 归一化
def printable_from_vec(v, p):
    n = len(v)
    for i in range(n):
        vi = v[i]
        if vi == 0:
            continue
        inv = pow(vi, -1, p)
        for target in range(32, 127):
            s = target * inv % p
            out = [(x * s) % p for x in v]
            if all(32 <= x <= 126 for x in out):
                return bytes(out)
    return None


def solve(p, C_flat, n=None, deadline_s=240, verbose=True):
    if n is None:
        n = int(round(len(C_flat) ** 0.5))
    if n * n != len(C_flat):
        raise SystemExit("C 长度 %d 不是完全平方，用 --n 指定阶数" % len(C_flat))
    C = [C_flat[i * n:(i + 1) * n] for i in range(n)]
    rng = random.Random(0xC0FFEE)
    t0 = time.time()
    deadline = t0 + deadline_s

    if verbose:
        print("[*] n = %d, p 有 %d 位十进制" % (n, len(str(p))), flush=True)

    v = [rng.randrange(1, p) for _ in range(n)]
    seq, cur = [], v
    for _ in range(2 * n + 6):
        seq.append(cur[0])
        cur = matvec(C, cur, p)
    mu = berlekamp_massey(seq, p)
    if verbose:
        print("[*] 最小多项式次数 %d（%.1fs）" % (len(mu) - 1, time.time() - t0), flush=True)

    cnt = 0
    for lam in eigen_roots_lazy(mu, p, rng, deadline):
        cnt += 1
        M = [[(C[i][j] - (lam if i == j else 0)) % p for j in range(n)] for i in range(n)]
        for vec in nullspace(M, p):
            got = printable_from_vec(vec, p)
            if got:
                if verbose:
                    print("[*] 第 %d 个特征值命中（%.1fs）" % (cnt, time.time() - t0), flush=True)
                return got
        if time.time() > deadline:
            if verbose:
                print("[!] 超时，试了 %d 个特征值" % cnt, flush=True)
            break
    if verbose and cnt:
        print("[*] 试完 %d 个特征值都没命中（%.1fs）" % (cnt, time.time() - t0), flush=True)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--n", type=int, default=None)
    ap.add_argument("--deadline", type=int, default=240)
    args = ap.parse_args()

    text = open(args.file, encoding="utf-8", errors="replace").read()
    mp = re.search(r"\bp\s*=\s*(\d+)", text)
    if not mp:
        sys.exit("没找到 p = ...")
    p = int(mp.group(1))
    mc = re.search(r"\bC\s*=\s*\[[^\]]*\]", text, re.S)
    if not mc:
        sys.exit("没找到 C = [...]")
    C = ast.literal_eval(mc.group(0).split("=", 1)[1].strip())

    ans = solve(p, C, args.n, args.deadline)
    if ans:
        print("\n[+] 特征向量（当 bytes 看）：")
        print(repr(ans))
        print("\n[+] 直接当 flag：")
        try:
            print(ans.decode())
            print("\n[+] 加外壳：flag{%s}" % ans.decode())
        except Exception:
            print("（含非 UTF-8 字节，见上面的 repr）")
    else:
        print("\n[-] 没找到。可调方向：")
        print("    1) --n 指定正确阶数")
        print("    2) 把 C 转置（可能是左特征向量）")
        print("    3) 放宽 ASCII 区间（有的题产物含控制字符）")
        print("    4) --deadline 加大")


if __name__ == "__main__":
    main()
