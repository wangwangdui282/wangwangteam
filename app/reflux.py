# -*- coding: utf-8 -*-
"""
reflux.py - 日志回流：把一次解题过程沉淀成 writeup 和技能条目

为什么要它：
    每场比赛最值钱的不是某道题的答案，而是「哪条路通了、哪条是死路」。
    这些信息现在散在 logs/ 里没人看。这个模块做三件事：

      1. writeup_from_log()   把日志整理成可读的复盘文档 -> writeups/
      2. skill_draft()        抽出一小段可直接追加进 skills/<题型>.md 的经验
      3. apply_skill()        用户确认后，追加到技能文件（只在末尾加，不改原文）

设计原则：
    - 只用标准库
    - 解析不出来就给空，绝不乱猜
    - 追加技能时永远在文件末尾，原内容一个字不动
"""
import re
import time
from pathlib import Path

APP = Path(__file__).resolve().parent
PROJ = APP.parent
SKILLS = PROJ / "skills"
WRITEUPS = PROJ / "writeups"

# 从命令里能看出"用了什么技术"的关键字
TECH_HINTS = [
    (r"\bobjdump\b", "objdump 反汇编"),
    (r"\bstrings\b", "strings 抓字符串"),
    (r"\bgdb\b", "gdb 动态调试"),
    (r"\bssh\b|\bscp\b", "远程 Linux 执行"),
    (r"\bunshare\b|fakeproc", "伪造 /proc 绕反调试"),
    (r"\bpython\b.*\.py", "写 Python 脚本"),
    (r"\bgcc\b|\bclang\b", "编译 C"),
    (r"\bz3\b", "z3 约束求解"),
    (r"\bhashcat\b|\bbrute\b", "爆破"),
    (r"\bcurl\b|\brequests\b", "HTTP 请求"),
    (r"\bpwntools\b|\bpwn\b", "pwntools"),
    (r"\bbinwalk\b|\bexiftool\b|\bzsteg\b", "隐写/文件分析"),
    (r"\bopenssl\b", "openssl"),
    (r"\bradare2\b|\br2\b|\brizin\b", "radare2"),
]

DEAD_END_HINTS = [
    (r"失败|错误|不可用|找不到|没有这个|不存在", "命令失败/路径不存在"),
    (r"Rejected|Wrong|Invalid|不对|错误答案", "程序拒绝/校验失败"),
    (r"超时|timeout", "超时"),
    (r"Traceback|Exception|Error:", "脚本报错"),
]


def _read(path):
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _strip_ansi(s):
    return re.sub(r"\x1b\[[0-9;]*m", "", s)


def parse_log(text):
    """从日志里抽结构化信息。返回 dict。"""
    text = _strip_ansi(text or "")
    lines = text.splitlines()

    info = {"title": "", "started": "", "mode": "", "commands": [],
            "answers": [], "errors": [], "verdict": ""}

    for ln in lines:
        s = ln.strip()
        low = s.lower()
        if s.startswith("- 时间："):
            info["started"] = s.split("：", 1)[1].strip()
        elif s.startswith("- 模式："):
            info["mode"] = s.split("：", 1)[1].strip()
        elif s.startswith("- 题目："):
            info["title"] = s.split("：", 1)[1].strip()
        elif "**结束**" in s:
            v = s.replace("**", "").strip()
            # 结论只留一行摘要：长错误信息塞进来会让整段没法读
            info["verdict"] = v[:160] + ("…" if len(v) > 160 else "")
        elif "答案" in s and "★" in s:
            m = re.search(r"★\s*答案[^：]*：(.+)", s)
            if m:
                info["answers"].append(m.group(1).strip()[:200])
        elif s.startswith("$ ") or re.match(r"^\s*run_cmd", s):
            info["commands"].append(s[:300])

    # 抓所有看起来像命令的行（日志里通常是缩进或带 cmd=）
    for ln in lines:
        if ("cmd" in ln.lower() and "=" in ln) or ln.strip().startswith("$ "):
            c = re.sub(r"^.*?cmd\s*=\s*", "", ln.strip())
            if c and c not in info["commands"]:
                info["commands"].append(c[:300])

    for pat, tag in DEAD_END_HINTS:
        if re.search(pat, text, re.IGNORECASE):
            info["errors"].append(tag)

    return info


def techs_used(text):
    """从整份日志里判断用了哪些技术。"""
    out = []
    for pat, name in TECH_HINTS:
        if re.search(pat, text or "", re.IGNORECASE):
            if name not in out:
                out.append(name)
    return out


def writeup_from_log(logpath, meta=None):
    """把一份日志整理成复盘 Markdown。返回 (标题, 正文)。"""
    text = _read(logpath)
    if not text:
        return None, ""
    info = parse_log(text)
    techs = techs_used(text)

    title = (meta or {}).get("title") or info["title"] or Path(logpath).stem
    lines = []
    lines.append("# %s" % title)
    lines.append("")
    lines.append("> 自动生成于 %s ｜ 日志：`%s`" % (
        time.strftime("%Y-%m-%d %H:%M:%S"), Path(logpath).name))
    lines.append("")
    lines.append("## 基本信息")
    lines.append("")
    lines.append("- 题目：%s" % (info["title"] or "（未记录）"))
    lines.append("- 模式：%s" % (info["mode"] or "（未记录）"))
    lines.append("- 开始：%s" % (info["started"] or "（未记录）"))
    lines.append("- 结论：%s" % (info["verdict"] or "（未记录）"))
    lines.append("")

    if techs:
        lines.append("## 用到的手法")
        lines.append("")
        for t in techs:
            lines.append("- %s" % t)
        lines.append("")

    if info["commands"]:
        lines.append("## 关键命令（去重后前 20 条）")
        lines.append("")
        seen = set()
        n = 0
        for c in info["commands"]:
            key = c[:80]
            if key in seen:
                continue
            seen.add(key)
            lines.append("```")
            lines.append(c)
            lines.append("```")
            n += 1
            if n >= 20:
                break
        lines.append("")

    if info["answers"]:
        lines.append("## 过程中出现过的候选答案")
        lines.append("")
        for a in info["answers"][:10]:
            lines.append("- `%s`" % a)
        lines.append("")
        lines.append("> 提醒：候选 != 答案。只有本地验证过的才算。")
        lines.append("")

    if info["errors"]:
        lines.append("## 出现过的失败信号")
        lines.append("")
        for e in info["errors"]:
            lines.append("- %s" % e)
        lines.append("")

    lines.append("## 原始日志")
    lines.append("")
    lines.append("完整过程见 `%s`。" % logpath)
    lines.append("")

    return title, "\n".join(lines)


def save_writeup(logpath, meta=None, extra=""):
    """生成复盘文档并写入 writeups/，返回文件路径。"""
    title, body = writeup_from_log(logpath, meta)
    if not body:
        return None
    WRITEUPS.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w\u4e00-\u9fa5\-]+", "_", (title or "writeup"))[:60]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = WRITEUPS / ("%s-%s.md" % (stamp, safe))
    if extra:
        body += "\n## 人工补充\n\n" + extra.strip() + "\n"
    out.write_text(body, encoding="utf-8")
    return out


def skill_draft(logpath, ptype=None, meta=None):
    """抽一小段可直接追加到 skills/<题型>.md 的经验条目。"""
    text = _read(logpath)
    if not text:
        return ""
    info = parse_log(text)
    techs = techs_used(text)

    ptype = (ptype or "").strip().lower()
    title = (meta or {}).get("title") or info["title"] or "（未知题目）"
    date = time.strftime("%Y-%m-%d")

    lines = []
    lines.append("### %s ｜ %s" % (date, title))
    lines.append("")
    if info["verdict"]:
        lines.append("- **结论**：%s" % info["verdict"])
    else:
        lines.append("- **结论**：日志里没有记录结束状态")
    if techs:
        lines.append("- **用到的**：%s" % "、".join(techs))
    if info["errors"]:
        lines.append("- **踩到的坑**：%s" % "、".join(info["errors"]))
    lines.append("- **日志**：`%s`" % Path(logpath).name)
    lines.append("")
    return "\n".join(lines)


def apply_skill(ptype, draft, header="踩过的坑（自动收集）"):
    """把条目追加到 skills/<题型>.md。**只追加，不改原文。**"""
    ptype = (ptype or "").strip().lower()
    if not ptype or not draft.strip():
        return None
    p = SKILLS / ("%s.md" % ptype)
    SKILLS.mkdir(parents=True, exist_ok=True)
    old = _read(p) if p.is_file() else ""
    if not old:
        old = "# Skill · %s 打法\n" % ptype

    # 同一道题重复追加要能看出来：靠标题行去重
    first_line = draft.strip().splitlines()[0] if draft.strip() else ""
    if first_line and first_line in old:
        return None

    if header not in old:
        old = old.rstrip() + "\n\n---\n\n## %s\n\n" % header
    new = old.rstrip() + "\n\n" + draft.strip() + "\n"
    p.write_text(new, encoding="utf-8")
    return p


def list_writeups(limit=50):
    """列出已生成的复盘文档（新的在前）。"""
    if not WRITEUPS.is_dir():
        return []
    out = []
    for f in sorted(WRITEUPS.glob("*.md"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            out.append({"name": f.name, "size": f.stat().st_size,
                        "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(f.stat().st_mtime))})
        except Exception:
            pass
        if len(out) >= limit:
            break
    return out
