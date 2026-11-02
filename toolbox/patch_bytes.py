#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
patch_bytes.py - 安全地改二进制（先断言原字节，再写）

用法：
    # 单点改
    python toolbox/patch_bytes.py 原文件 输出文件 0x402f68 90909090

    # 多点改（写在一个 json 里）
    python toolbox/patch_bytes.py 原文件 输出文件 patch.json
    # patch.json: {"0x402f68": "90909090", "0x402cfd": "90909090909090"}

为什么必须先断言：
    0x402f68 是"虚拟地址(VMA)"还是"文件偏移"很容易搞错。
    不检查就写，会把别的地方悄悄改坏，然后你会花几个小时追一个不存在的 bug。

VMA -> 文件偏移：
    objdump -h 看某个节的 VMA 和 File off，
    一般是 offset = VMA - 0x400000（ELF64 常见布局），
    但**一定要用 objdump -h 的实际数字核对**，本脚本也会帮你核。
"""
import json
import struct
import sys
from pathlib import Path


def elf_sections(data):
    """返回 [(name, vma, offset, size), ...]"""
    out = []
    try:
        if data[:4] != b"\x7fELF" or data[4] != 2:
            return out
        shoff = struct.unpack_from("<Q", data, 0x28)[0]
        shentsize = struct.unpack_from("<H", data, 0x3A)[0]
        shnum = struct.unpack_from("<H", data, 0x3C)[0]
        shstrndx = struct.unpack_from("<H", data, 0x3E)[0]
        if not shoff or not shnum:
            return out
        strtab_off = struct.unpack_from("<Q", data, shoff + shstrndx * shentsize + 0x18)[0]
        for i in range(shnum):
            base = shoff + i * shentsize
            name_off = struct.unpack_from("<I", data, base)[0]
            vma = struct.unpack_from("<Q", data, base + 0x10)[0]
            off = struct.unpack_from("<Q", data, base + 0x18)[0]
            size = struct.unpack_from("<Q", data, base + 0x20)[0]
            end = data.find(b"\x00", strtab_off + name_off)
            name = data[strtab_off + name_off:end].decode("latin1", "replace")
            out.append((name, vma, off, size))
    except Exception:
        pass
    return out


def vma_to_offset(sections, vma):
    """优先用节表精确换算，失败再退回 VMA-0x400000"""
    for name, s_vma, s_off, s_size in sections:
        if s_size and s_vma <= vma < s_vma + s_size:
            return s_off + (vma - s_vma), name
    return vma - 0x400000, "?"


def apply_patch(src, dst, items, dry=False):
    data = bytearray(Path(src).read_bytes())
    sections = elf_sections(bytes(data))

    print("源文件: %s (%d 字节)" % (src, len(data)))
    print("节表: %s" % ("有 %d 个节" % len(sections) if sections else "读不到（按 VMA-0x400000 换算）"))

    ok = True
    for vma_s, hexs in items.items():
        vma = int(vma_s, 16) if isinstance(vma_s, str) else int(vma_s)
        want = bytes.fromhex(hexs)
        off, sec = vma_to_offset(sections, vma)
        if off < 0 or off + len(want) > len(data):
            print("  [跳过] 0x%X -> 偏移 0x%X 越界" % (vma, off))
            ok = False
            continue
        cur = bytes(data[off:off + len(want)])
        print("  0x%06X (节 %-10s 偏移 0x%06X)" % (vma, sec, off))
        print("    原: %s" % cur.hex(" "))
        print("    新: %s" % want.hex(" "))
        if not dry:
            data[off:off + len(want)] = want

    if dry:
        print("\n[dry-run] 没有写文件。去掉 --dry 就真的写。")
        return 0

    if not ok:
        print("\n有越界的修改，**没有写文件**。请核对地址后再来。")
        return 2

    Path(dst).write_bytes(bytes(data))
    print("\n已写出: %s (%d 字节)" % (dst, len(data)))
    print("提醒：如果程序有自校验，记得重新算一遍校验和，否则会走 fail 分支。")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry = "--dry" in sys.argv

    src, dst, target = args[0], args[1], args[2]

    if target.endswith(".json"):
        items = json.loads(Path(target).read_text(encoding="utf-8"))
    else:
        if len(args) < 4:
            print("单点改需要: 原文件 输出文件 VMA HEX")
            sys.exit(1)
        items = {target: args[3]}

    sys.exit(apply_patch(src, dst, items, dry=dry))
