# -*- coding: utf-8 -*-
"""
core.py - 汪汪队立大功：核心逻辑

两块：
  1. NSSCTF 竞技场（领题 / 提交 / 记录）
  2. 本地题库（自己丢题目进来，解完给答案）
"""
import json
import os
import re
import tarfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

APP = Path(__file__).resolve().parent      # app/
PROJ = APP.parent                          # 项目根
DATA = PROJ / "data"                       # 配置与状态
DATA.mkdir(parents=True, exist_ok=True)

BASE = "https://www.nssctf.cn/api"

CHAL_DIR = PROJ / "challenges"
LOCAL_DIR = PROJ / "local"
STATE_FILE = DATA / "state.json"
HIST_FILE = DATA / "history.jsonl"
TOKEN_FILE = DATA / "token.txt"

FLAG_RE = re.compile(r"^NSSCTF\{.+\}$")
_cache = {"token": ""}


class ApiError(Exception):
    pass


# ---------------------------------------------------------------- token
def set_token(tok, remember=False):
    tok = (tok or "").strip()
    _cache["token"] = tok
    if remember and tok:
        TOKEN_FILE.write_text(tok, encoding="utf-8")
    return bool(tok)


def get_token():
    if _cache["token"]:
        return _cache["token"]
    tok = os.environ.get("NSS_TOKEN", "").strip()
    if not tok and TOKEN_FILE.is_file():
        tok = TOKEN_FILE.read_text(encoding="utf-8-sig").strip()
    _cache["token"] = tok
    return tok


def token_status():
    tok = get_token()
    if not tok:
        return {"set": False, "tail": "", "remembered": False}
    remembered = False
    try:
        remembered = (TOKEN_FILE.is_file()
                      and TOKEN_FILE.read_text(encoding="utf-8-sig").strip() == tok)
    except Exception:
        pass
    return {"set": True, "tail": "..." + tok[-6:], "remembered": remembered}


# ---------------------------------------------------------------- 平台接口
def api(method, path, body=None, timeout=40):
    tok = get_token()
    if not tok:
        raise ApiError("还没有设置 NSSCTF token")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Authorization": "Bearer " + tok,
                 "Content-Type": "application/json",
                 "User-Agent": "wangwang/2.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
    except urllib.error.URLError as e:
        raise ApiError("连不上平台：%s\n先确认域名是 nssctf.cn（两个 s）" % e)
    except Exception as e:
        raise ApiError("请求失败：%s" % e)

    try:
        obj = json.loads(raw)
    except Exception:
        raise ApiError("平台返回的不是 JSON，可能连错站了：\n" + raw[:200])

    if obj.get("code") != 200:
        msg = ""
        d = obj.get("data")
        if isinstance(d, dict):
            msg = d.get("message") or ""
        if obj.get("code") in (401, 403):
            msg = (msg + "  -> token 不对或已失效，去平台重置").strip()
        raise ApiError("平台返回 code=%s %s" % (obj.get("code"), msg))
    return obj.get("data") or {}


def api_raw(method, path, body=None, timeout=15):
    """不校验 code，返回 (状态码, 解析结果或 None, 原文)。用于试探未文档化接口。"""
    tok = get_token()
    if not tok:
        raise ApiError("还没有设置 NSSCTF token")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Authorization": "Bearer " + tok,
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            status, raw = r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return None, None, str(e)
    try:
        return status, json.loads(raw), raw
    except Exception:
        return status, None, raw


def current():
    return api("GET", "/skill/agent/arena/current/")


def fetch_next():
    return api("POST", "/skill/agent/arena/next/")


def submit_flag(attempt_id, flag):
    return api("POST", "/skill/agent/arena/attempt/%s/submit/" % attempt_id, {"flag": flag})


# ---------------------------------------------------------------- 本地状态
def load_state():
    if STATE_FILE.is_file():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8-sig"))
        except Exception:
            return {}
    return {}


def save_state(d):
    STATE_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def challenge_dir():
    st = load_state()
    if st.get("dir"):
        p = PROJ / st["dir"]
        if p.is_dir():
            return p
    return None


def prepare_challenge(attempt):
    pb = attempt.get("problem") or {}
    tid = attempt.get("id")
    slug = str(pb.get("name") or "task").replace(" ", "_")[:40]
    d = CHAL_DIR / ("%s-%s" % (tid, slug))
    for sub in ("", "raw", "solve"):
        (d / sub).mkdir(parents=True, exist_ok=True)

    annex = pb.get("annex")
    items = [annex] if isinstance(annex, dict) else (annex or [])
    got, errs = [], []
    for a in items:
        name = a.get("name") or "annex.bin"
        url = a.get("url")
        target = d / "raw" / name
        try:
            urllib.request.urlretrieve(url, target)
            got.append(name)
            low = name.lower()
            if low.endswith(".zip"):
                with zipfile.ZipFile(target) as z:
                    z.extractall(d / "raw")
            elif low.endswith((".tar", ".tar.gz", ".tgz")):
                with tarfile.open(target) as t:
                    t.extractall(d / "raw")
        except Exception as e:
            errs.append("%s: %s" % (name, e))

    (d / "problem.json").write_text(json.dumps(attempt, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
    save_state({
        "attempt_id": tid,
        "dir": str(d.relative_to(PROJ)),
        "started_ms": int(time.time() * 1000),
        "expire_ms": attempt.get("expire_at"),
        "problem_name": pb.get("name"),
        "type_label": pb.get("type_label"),
        "problem_rating": pb.get("rating"),
        "attachments": got,
    })
    return d, got, errs


def problem_prompt(attempt):
    """把题目信息拼成给模型看的文字。"""
    pb = attempt.get("problem") or {}
    annex = pb.get("annex")
    items = [annex] if isinstance(annex, dict) else (annex or [])
    lines = ["【平台】NSSCTF Agent 竞技场"]
    lines.append("【题型】%s" % (pb.get("type_label") or "?"))
    lines.append("【题目】%s" % (pb.get("name") or "未命名"))
    lines.append("【题目说明】%s" % (pb.get("content") or "无"))
    if pb.get("hint"):
        lines.append("【提示】%s" % pb["hint"])

    has_env = False
    if pb.get("container_enabled"):
        cont = pb.get("container") or {}
        urls = cont.get("url") or []
        if isinstance(urls, str):
            urls = [urls]
        if urls:
            has_env = True
            addr = str(urls[0])
            host, _, port = addr.partition(":")
            lines.append("【远程地址】%s" % ", ".join(str(u) for u in urls))
            lines.append("-> 用工作目录里的 conn.py 连它：")
            lines.append('   import conn; c = conn.Conn("%s", %s); print(c.recv(1.0))'
                         % (host, port or "0"))
            lines.append("-> 先看清它给的菜单/提示，再决定发什么。")
        else:
            lines.append("【容器环境】平台标记需要容器，但这次没有返回地址。")
            lines.append("-> 先静态分析附件；确实必须有环境才能推进，就在 finish 里说明卡在哪。")

    if items:
        lines.append("【附件】已下载到 raw/ 目录：")
        for a in items:
            lines.append("  - %s（%s 字节）" % (a.get("name"), a.get("size")))
    else:
        lines.append("【附件】没有附件")

    if not items and not has_env:
        lines.append("")
        lines.append("注意：这题既没有附件、也没有环境地址。")
        lines.append("需要人帮忙取地址或看网页时，用 need_human 说清楚要做什么。")
        lines.append("不要在本地伪造一个网站自己打，那没有意义。")

    lines.append("")
    lines.append("请在当前工作目录里解题，找到 flag 后调用 finish。")
    return "\n".join(lines)


# ---------------------------------------------------------------- 本地题库
def _case_id():
    return time.strftime("%Y%m%d-%H%M%S")


def local_root(cid):
    return LOCAL_DIR / cid


def new_local_case(name, desc=""):
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    cid = _case_id()
    d = LOCAL_DIR / cid
    for sub in ("files", "work"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    meta = {"id": cid, "name": (name or "未命名题目").strip(), "desc": desc or "",
            "created": int(time.time() * 1000), "files": [], "answer": None, "solved": False}
    (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def list_local_cases():
    out = []
    if not LOCAL_DIR.is_dir():
        return out
    for d in sorted(LOCAL_DIR.iterdir(), reverse=True):
        f = d / "meta.json"
        if f.is_file():
            try:
                out.append(json.loads(f.read_text(encoding="utf-8-sig")))
            except Exception:
                pass
    return out


def get_local_case(cid):
    f = local_root(cid) / "meta.json"
    if not f.is_file():
        raise ApiError("找不到这个本地题目：%s" % cid)
    return json.loads(f.read_text(encoding="utf-8-sig"))


def save_local_case(meta):
    d = local_root(meta["id"])
    d.mkdir(parents=True, exist_ok=True)
    (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def upload_local_file(cid, name, data: bytes):
    import shutil
    meta = get_local_case(cid)
    safe = Path(name).name or "file.bin"
    target = local_root(cid) / "files" / safe
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    try:
        shutil.copy2(target, local_root(cid) / "work" / safe)
    except Exception:
        pass
    if safe not in meta.get("files", []):
        meta.setdefault("files", []).append(safe)
    save_local_case(meta)
    return meta


def local_prompt(meta):
    files = meta.get("files") or []
    lines = ["【来源】本地题目（不联网，解完把答案给用户，用户自己提交）",
             "【题目】%s" % meta.get("name"),
             "【题目说明】%s" % (meta.get("desc") or "（用户没写说明，自己看文件）")]
    if files:
        lines.append("【附件】已放在当前工作目录：")
        for f in files:
            lines.append("  - %s" % f)
    lines.append("")
    lines.append("请在当前工作目录里解题，找到答案后调用 finish。")
    return "\n".join(lines)


def update_local_answer(cid, answer, solved=True):
    meta = get_local_case(cid)
    meta["answer"] = answer
    meta["solved"] = solved
    meta["answered_at"] = int(time.time() * 1000)
    return save_local_case(meta)


# ---------------------------------------------------------------- 预侦察
def _kind(head: bytes) -> str:
    if head[:4] == b"\x7fELF":
        return "ELF"
    if head[:2] == b"MZ":
        return "PE(Windows)"
    if head[:4] == b"PK\x03\x04":
        return "ZIP"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "PNG"
    if head[:2] == b"\xff\xd8":
        return "JPEG"
    if head[:2] == b"\x1f\x8b":
        return "gzip"
    if head[:4] == b"Rar!":
        return "压缩包"
    try:
        head.decode("utf-8")
        return "文本"
    except Exception:
        return "二进制"


HIGH_SIGNAL = ("/flag", "flag", "system", "printf", "read", "gets", "strcmp",
               "/bin", "open", "welcome", "key", "pass", "secret", "error", "sh")


def _run(cmd, timeout=6):
    import subprocess
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout)
        out = p.stdout + p.stderr
        for enc in ("utf-8", "gbk"):
            try:
                return out.decode(enc)
            except Exception:
                continue
        return out.decode("utf-8", "replace")
    except Exception:
        return ""


def recon(dirpath, max_files=6, max_steps=12):
    """对附件做一次静态侦察，返回给模型看的摘要。"""
    import shutil
    d = Path(dirpath)
    if not d.is_dir():
        return ""
    files = [f for f in sorted(d.rglob("*")) if f.is_file()]
    if not files:
        return ""
    has_objdump = shutil.which("objdump") is not None
    has_strings = shutil.which("strings") is not None
    out = ["【预侦察】（下面这些已经跑过了，不要重复做）"]
    used = 0
    for f in files[:max_files]:
        if used >= max_steps:
            break
        try:
            size = f.stat().st_size
        except Exception:
            continue
        rel = str(f.relative_to(d)).replace("\\", "/")
        if size > 8 * 1024 * 1024:
            out.append("- %s：%d 字节（太大，跳过）" % (rel, size))
            continue
        head = f.read_bytes()[:16]
        kind = _kind(head)
        out.append("- %s：%d 字节，%s，首字节 %s" % (rel, size, kind, head[:8].hex(" ")))
        used += 1

        if kind == "ELF" and has_objdump:
            info = _run(["objdump", "-f", str(f)])
            if "elf" in info:
                pie = "无 PIE（地址固定，可直接写死）" if "EXEC_P" in info else "可能是 PIE"
                arch = "x86-64" if "x86-64" in info else "其他架构"
                out.append("  - %s，%s" % (arch, pie))
                used += 1
            dis = _run(["objdump", "-d", str(f)])
            fns = []
            for line in dis.splitlines():
                line = line.strip()
                if line.endswith(">:") and "<" in line:
                    name = line.split("<", 1)[1].rstrip(">:")
                    if (not name.startswith("_") and not name.startswith(".")
                            and not name.endswith("@plt")):
                        fns.append(name)
            if fns:
                out.append("  - 函数：" + "、".join(fns[:14]))
                used += 1

        if kind in ("ELF", "二进制", "PE(Windows)") and has_strings:
            st = _run(["strings", "-n", "4", str(f)])
            hits = []
            for line in st.splitlines():
                low = line.lower().strip()
                if not low or len(line) > 60:
                    continue
                if "glibc" in low or low.startswith("."):
                    continue
                if any(k in low for k in HIGH_SIGNAL) and line not in hits:
                    hits.append(line.strip())
                if len(hits) >= 10:
                    break
            if hits:
                out.append("  - 高信号字符串：" + " | ".join(hits[:10]))
                used += 2
        elif kind == "ZIP":
            try:
                with zipfile.ZipFile(f) as z:
                    out.append("  - 压缩包内容：" + "、".join(z.namelist()[:10]))
                used += 1
            except Exception:
                pass
        elif kind == "文本":
            try:
                txt = f.read_text(encoding="utf-8", errors="replace")
                out.append("  - 开头：" + txt[:160].replace("\n", " / "))
                used += 1
            except Exception:
                pass

    if len(out) <= 1:
        return ""
    return "\n".join(out)


# ---------------------------------------------------------------- 文件浏览
def _walk_files(base, prefix=""):
    out = []
    if not base.is_dir():
        return out
    for p in sorted(base.rglob("*")):
        if p.is_file() and p.stat().st_size < 5_000_000:
            out.append({"path": (prefix + str(p.relative_to(base))).replace("\\", "/"),
                        "size": p.stat().st_size})
    return out


def _read_any(p: Path):
    if p.stat().st_size > 2_000_000:
        return "（文件太大，%d 字节，用本地编辑器打开吧）" % p.stat().st_size
    data = p.read_bytes()
    for enc in ("utf-8", "gbk"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return "（二进制文件，前 32 字节 hex）\n" + data[:32].hex()


def list_files():
    d = challenge_dir()
    if not d:
        return []
    out = []
    for sub in ("raw", "solve"):
        out.extend(_walk_files(d / sub, sub + "/"))
    return out


def read_challenge_file(rel):
    d = challenge_dir()
    if not d:
        raise ApiError("没有当前题目录")
    target = (d / rel).resolve()
    if d.resolve() not in target.parents and target != d.resolve():
        raise ApiError("越界了")
    if not target.is_file():
        raise ApiError("文件不存在")
    return _read_any(target)


def list_local_files(cid):
    return _walk_files(local_root(cid) / "files")


def read_local_file(cid, rel):
    d = (local_root(cid) / "files").resolve()
    target = (d / rel).resolve()
    if d not in target.parents and target != d:
        raise ApiError("越界了")
    if not target.is_file():
        raise ApiError("文件不存在")
    return _read_any(target)


# ---------------------------------------------------------------- 统计
def stats():
    rows = []
    if HIST_FILE.is_file():
        for line in HIST_FILE.read_text(encoding="utf-8-sig").splitlines():
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    rows = [r for r in rows if not r.get("giveup")]
    ok = [r for r in rows if r.get("correct")]
    ds = [r.get("delta") or 0 for r in ok]
    # 最近记录按题去重：同一道题交多次只显示一次（统计数仍然按次数算）
    seen, recent = set(), []
    for r in reversed(rows):
        k = r.get("attempt_id") or r.get("problem") or "_"
        if k in seen:
            continue
        seen.add(k)
        recent.append(r)
        if len(recent) >= 8:
            break
    return {"total": len(rows), "ok": len(ok), "ng": len(rows) - len(ok),
            "rate": (100 * len(ok) // len(rows)) if rows else 0,
            "gain": sum(ds), "recent": recent}


def log(**kw):
    kw["ts"] = int(time.time() * 1000)
    HIST_FILE.parent.mkdir(parents=True, exist_ok=True)
    with HIST_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(kw, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- 提示
TYPE_TIP = {
    "Crypto": "读脚本找参数和校验逻辑，先问：我到底缺什么？",
    "Misc": "先弄清楚给的是什么文件；编码类先试 base64 / hex / rot13",
    "Reverse": "先 file / strings / 找入口，别一上来就全反编译",
    "Web": "看源码和接口；容器题要先有环境",
    "Pwn": "先静态逆，再看远程交互",
}

EXT_TIP = {
    ".py": "Python 脚本 -> 直接读，看 import 和 print",
    ".zip": "压缩包 -> 已自动解开",
    ".txt": "文本 -> 先读一遍",
    ".pcap": "流量包 -> Wireshark 打开，先看协议分级",
    ".png": "图片 -> 隐写：binwalk / exiftool",
    ".jpg": "图片 -> 隐写：binwalk / exiftool",
    ".exe": "Windows 程序，别直接双击",
    ".bin": "二进制 -> file 看类型，再 strings",
}


def tips(problem):
    out = []
    if (problem or {}).get("container_enabled"):
        out.append("[容器题] 先看 container 字段有没有地址；有就贴进环境地址框")
    t = (problem or {}).get("type_label")
    if t in TYPE_TIP:
        out.append("[%s] %s" % (t, TYPE_TIP[t]))
    annex = (problem or {}).get("annex")
    items = [annex] if isinstance(annex, dict) else (annex or [])
    for a in items:
        name = a.get("name") or ""
        ext = Path(name).suffix.lower()
        if not ext:
            out.append("%s：没有扩展名，先按二进制看（strings / 读文件头）" % name)
        elif ext in EXT_TIP:
            out.append("%s：%s" % (name, EXT_TIP[ext]))
    return out
