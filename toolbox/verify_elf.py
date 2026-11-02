#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_elf.py - 一次看清一个 ELF 的关键信息（零依赖）

用法：
    python toolbox/verify_elf.py 文件

输出：类型 / 架构 / 是否 PIE / 段表 / 符号表 / 高信号字符串 / 可疑大块数据

为什么要先跑这个：
    1. 判断是 ELF 还是别的（别被扩展名骗）
    2. 有没有 PIE 决定你能不能写死地址
    3. 有符号表就先看符号，别一上来全量反汇编
    4. 高信号字符串能直接指出题眼（/flag、system、secret...）
"""
import re
import struct
import subprocess
import sys
from pathlib import Path

HIGH_SIGNAL = ("/flag", "flag{", "system", "execve", "secret", "password", "passwd",
               "key", "license", "serial", "debug", "ptrace", "TracerPid",
               "correct", "wrong", "invalid", "bravo", "congrat", "sorry")


def read_elf_header(data):
    if data[:4] != b"\x7fELF":
        return None
    cls = data[4]          # 1=32bit 2=64bit
    endian = data[5]       # 1=LE 2=BE
    etype = struct.unpack_from("<H" if endian == 1 else ">H", data, 16)[0]
    machine = struct.unpack_from("<H" if endian == 1 else ">H", data, 18)[0]
    return {"cls": cls, "endian": endian, "etype": etype, "machine": machine}


def kind_of(data):
    if data[:4] == b"\x7fELF":
        return "ELF"
    if data[:2] == b"MZ":
        return "PE"
    if data[:4] == b"PK\x03\x04":
        return "ZIP"
    if data[:4] == b"\x89PNG":
        return "PNG"
    if data[:3] == b"\xff\xd8\xff":
        return "JPEG"
    if data[:2] == b"\x1f\x8b":
        return "gzip"
    if data[:4] == b"Rar!":
        return "RAR"
    try:
        data[:512].decode("utf-8")
        return "text"
    except Exception:
        return "binary"


def run(cmd, timeout=30):
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout)
        out = p.stdout + p.stderr
        for enc in ("utf-8", "gbk"):
            try:
                return out.decode(enc)
            except Exception:
                continue
        return out.decode("utf-8", "replace")
    except Exception as e:
        return "(命令失败: %s)" % e


def main(path):
    p = Path(path)
    if not p.is_file():
        print("文件不存在:", p)
        return 1
    data = p.read_bytes()
    print("=" * 62)
    print("文件: %s" % p)
    print("大小: %d 字节" % len(data))
    print("磁类型: %s   首字节: %s" % (kind_of(data), data[:8].hex(" ")))
    print("=" * 62)

    hdr = read_elf_header(data)
    if not hdr:
        print("(不是 ELF，下面是前 200 字节的可打印内容)")
        txt = "".join(chr(c) if 32 <= c < 127 else "." for c in data[:200])
        print(txt)
        return 0

    bits = {1: "32-bit", 2: "64-bit"}.get(hdr["cls"], "?")
    endian = {1: "little-endian", 2: "big-endian"}.get(hdr["endian"], "?")
    etype = {1: "REL(可重定位)", 2: "EXEC(可执行, 一般无 PIE)",
             3: "DYN(共享库 / PIE)", 4: "CORE"}.get(hdr["etype"], str(hdr["etype"]))
    mach = {3: "x86", 62: "x86-64", 40: "ARM", 183: "AArch64",
            8: "MIPS", 243: "RISC-V"}.get(hdr["machine"], str(hdr["machine"]))
    print("架构: %s %s" % (bits, endian))
    print("类型: %s" % etype)
    print("机器: %s" % mach)
    print("PIE : %s" % ("是（地址随机，不能写死绝对地址）"
                        if hdr["etype"] == 3 else "否（地址固定，可以直接写死）"))

    print("\n--- 段表（objdump -h）---")
    print(run(["objdump", "-h", str(p)])[:2600])

    print("\n--- 符号表里带 F 的（函数）---")
    sym = run(["objdump", "-t", str(p)])
    fns = []
    for line in sym.splitlines():
        if re.search(r"\bF\b", line) and ".text" in line:
            fns.append(line.strip())
    if fns:
        for s in fns[:40]:
            print("  " + s)
    else:
        print("  (没有符号表，被 strip 了)")

    print("\n--- 高信号字符串 ---")
    st = run(["strings", "-n", "4", str(p)])
    hits, seen = [], set()
    for line in st.splitlines():
        low = line.lower().strip()
        if not low or len(line) > 80:
            continue
        if "glibc" in low or low.startswith(".") or "gcc:" in low:
            continue
        if any(k in low for k in HIGH_SIGNAL) and line not in seen:
            seen.add(line)
            hits.append(line.strip())
        if len(hits) >= 30:
            break
    print("\n".join("  " + h for h in hits) if hits else "  (没有明显的高信号字符串)")

    print("\n--- 数据段（只看前 400 字节）---")
    print(run(["objdump", "-s", "-j", ".data", str(p)])[:1200])

    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    sys.exit(main(sys.argv[1]))
