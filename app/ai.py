# -*- coding: utf-8 -*-
"""
ai.py - 汪汪队立大功：模型层

DeepSeek（OpenAI 兼容）+ 一个最小 agent 循环。只用标准库。

工具：list_dir / read_file / write_file / run_cmd / need_human / finish
"""
import json
import re
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

APP = Path(__file__).resolve().parent
PROJ = APP.parent
DATA = PROJ / "data"
DATA.mkdir(parents=True, exist_ok=True)
SETTINGS = DATA / "settings.json"
SKILLS_DIR = PROJ / "skills"

DEFAULT_BASE = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"

# 每题型最多跑多少步（会再和用户设置取较小值）
# 2026-11 调整：Web 从 12 提到 20 —— 有过一次模型已经把 flag 打到屏幕上，
# 但 12 步用尽没能 finish 的事故。宁可多跑几步也别丢到手的答案。
TYPE_STEPS = {"Pwn": 35, "Reverse": 35, "Crypto": 30, "Misc": 25, "Web": 30, "Blockchain": 30}

# 任何工具输出里出现这种形状，就当"疑似 flag"处理
FLAG_PAT = re.compile(r"[A-Za-z0-9_]{2,24}\{[^{}\s]{4,120}\}")


# ---------------------------------------------------------------- 配置
def load_settings():
    if SETTINGS.is_file():
        try:
            return json.loads(SETTINGS.read_text(encoding="utf-8-sig"))
        except Exception:
            return {}
    return {}


def save_settings(d):
    cur = load_settings()
    cur.update(d or {})
    SETTINGS.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
    return cur


def cfg():
    s = load_settings()
    return {
        "key": (s.get("ai_key") or "").strip(),
        "base": (s.get("ai_base") or DEFAULT_BASE).rstrip("/"),
        "model": s.get("ai_model") or DEFAULT_MODEL,
        "max_steps": int(s.get("max_steps") or 50),
    }


def key_status():
    k = cfg()["key"]
    return {"set": bool(k), "tail": ("..." + k[-4:]) if k else ""}


def custom_tools():
    """settings.json 的 tools_text，每行一个「名字=调用命令」。"""
    raw = (load_settings().get("tools_text") or "").strip()
    out = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, cmd = line.split("=", 1)
        name, cmd = name.strip(), cmd.strip()
        if name and cmd:
            out.append({"name": name, "cmd": cmd})
    return out


# ---------------------------------------------------------------- 模型调用
def chat(messages, tools=None, timeout=120):
    c = cfg()
    if not c["key"]:
        raise RuntimeError("还没有填 DeepSeek API Key，右上角设置里填一下")
    body = {"model": c["model"], "messages": messages, "temperature": 0.3}
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    req = urllib.request.Request(
        c["base"] + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + c["key"]})
    last = ""
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:400]
            last = "HTTP %s %s" % (e.code, detail)
            if e.code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(2 ** attempt)
                continue
            if e.code in (401, 403):
                raise RuntimeError("API Key 无效或没有权限（%s）" % detail[:200])
            raise RuntimeError("模型接口报错：%s" % last)
        except Exception as e:
            last = repr(e)
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
    if "timed out" in last.lower() or "timeout" in last.lower():
        raise RuntimeError("模型 %d 秒没有响应（网络或 API 端卡住了）。可以再点一次开始解题重试" % timeout)
    raise RuntimeError("调用模型失败：%s" % last)


# ---------------------------------------------------------------- 工具
def _guard(workdir, rel):
    root = Path(workdir).resolve()
    p = (root / rel).resolve() if not Path(rel).is_absolute() else Path(rel).resolve()
    if p != root and root not in p.parents:
        raise ValueError("只能访问工作目录内的路径：%s" % rel)
    return p


def _decode(b):
    for enc in ("utf-8", "gbk"):
        try:
            return b.decode(enc)
        except Exception:
            continue
    return b.decode("utf-8", "replace")


def _tool_list(workdir, path="."):
    p = _guard(workdir, path)
    if not p.exists():
        return "路径不存在：%s" % path
    if p.is_file():
        return "%s（文件，%d 字节）" % (path, p.stat().st_size)
    lines = []
    for x in sorted(p.iterdir()):
        tag = "<DIR>" if x.is_dir() else "%d B" % x.stat().st_size
        lines.append("%-8s %s" % (tag, x.name))
    return "\n".join(lines) or "（空目录）"


def _tool_read(workdir, path):
    p = _guard(workdir, path)
    if not p.is_file():
        return "文件不存在：%s" % path
    data = p.read_bytes()
    if len(data) > 300_000:
        return "文件太大（%d 字节），先用 run_cmd 取片段" % len(data)
    return _decode(data)


def _tool_write(workdir, path, content):
    p = _guard(workdir, path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content or "", encoding="utf-8")
    return "已写入 %s（%d 字符）" % (path, len(content or ""))


def source_hint(workdir, out):
    """把"短输出"回溯到题目源码。

    为什么需要它：
        很多失败信息其实是源码里某个分支打印的（`die("O.o")`、`echo "wrong"`）。
        模型常把它当成"这个 payload 不行，换一个"，而不知道
        **这个字符串指向源码里的某一处，能反推触发条件**。
        真实事故：一个 `O.o` 耗掉了 15 步，其实正则就在它刚下载的源码第一屏。

    触发条件：输出很短（像一条提示），且能在题目原始文件里找到同样的字符串。
    """
    t = (out or "").strip()
    if not t or len(t) > 120 or t.count("\n") > 3:
        return ""

    root = Path(workdir)
    dirs = []
    if (root / "raw").is_dir():
        dirs = [root / "raw"]
    else:
        dirs = [root]

    key = max(t.splitlines(), key=len).strip()
    if len(key) < 2:
        return ""

    import html as _html
    for d in dirs:
        for f in d.rglob("*"):
            if not f.is_file() or "solve" in f.parts:
                continue
            try:
                if f.stat().st_size > 4_000_000:
                    continue
            except Exception:
                continue
            try:
                data = f.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            idx = data.find(key)
            if idx < 0:
                data = _html.unescape(data)
                idx = data.find(key)
            if idx < 0:
                continue
            ctx = data[max(0, idx - 320):idx + 320].replace("\r", "")
            # 源码可能被 highlight_file 渲染成 HTML，去掉标签才看得懂
            if "<" in ctx and ">" in ctx:
                ctx = re.sub(r"<[^>]{0,80}>", " ", ctx)
                ctx = (ctx.replace("&nbsp;", " ").replace("&lt;", "<")
                          .replace("&gt;", ">").replace("&amp;", "&")
                          .replace("&quot;", '"'))
                ctx = re.sub(r"[ \t]{2,}", " ", ctx)
            return ("\n\n---\n💡 **这个输出不是「服务器出错」，它是题目源码里某个分支打印的**"
                    "（来自 `%s`）：\n```\n%s\n```\n"
                    "👉 **去看触发这条输出的条件是什么**，别急着换输入。\n"
                    "如果是校验失败，把校验代码翻译成一句人话，再用它检查你的输入。"
                    % (f.name, ctx.strip()))
    return ""


def _tool_run(workdir, cmd, timeout=120):
    """执行命令，带真正的超时。

    注意：不能直接用 subprocess.run(timeout=...)。
    shell=True 时 Python 只杀外壳，pip 这类会派生子进程，
    孙进程继续握着 stdout 管道，communicate() 就会永远等下去
    ——表面上"超时了"，实际整个任务永久挂死（踩过一次）。
    所以超时后必须用 taskkill /T 把整棵进程树一起杀掉。

    timeout 由模型自己给：它被迫在跑之前想一下"这一步要多久"。
    """
    try:
        tmo = max(5, min(int(timeout or 120), 600))
    except Exception:
        tmo = 120

    try:
        proc = subprocess.Popen(cmd, shell=True, cwd=str(workdir),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except Exception as e:
        return "命令启动失败：%s" % e

    try:
        out, err = proc.communicate(timeout=tmo)
        timed_out = False
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            # /T 连子进程一起杀，/F 强制
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, timeout=15)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        try:
            out, err = proc.communicate(timeout=15)
        except Exception:
            out, err = b"", b""

    text = (_decode(out) + _decode(err)).strip()
    if len(text) > 3000:
        text = text[:1500] + "\n...[截断]...\n" + text[-1500:]

    if timed_out:
        return ("命令超时（%d 秒），已强制结束整棵进程树。\n"
                "**这不正常**：正常脚本不该跑这么久。先想清楚是算法错还是数据太大，"
                "再决定要不要重跑，别原样再交一次。\n" % tmo
                + (text or "（无输出）"))
    return "exit=%d\n%s" % (proc.returncode, text or "（无输出）")


DISPATCH = {
    "list_dir": lambda wd, a: _tool_list(wd, a.get("path", ".")),
    "read_file": lambda wd, a: _tool_read(wd, a.get("path", "")),
    "write_file": lambda wd, a: _tool_write(wd, a.get("path", ""), a.get("content", "")),
    "run_cmd": lambda wd, a: _tool_run(wd, a.get("cmd", ""), a.get("timeout", 120)),
}

TOOLS = [
    {"type": "function", "function": {
        "name": "list_dir",
        "description": "列出工作目录里的文件",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "相对路径，默认 ."}}}}},
    {"type": "function", "function": {
        "name": "read_file",
        "description": "读取文本文件（相对工作目录）",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "write_file",
        "description": "写文件（覆盖）。写解题脚本用这个",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"}}, "required": ["path", "content"]}}},
    {"type": "function", "function": {
        "name": "run_cmd",
        "description": ("在工作目录执行一条 shell 命令（Windows 下是 cmd），返回输出。"
                        "**跑之前先估一下要多久**，用 timeout 参数表达你的预估："
                        "正常命令 30-60 秒足够；装库/编译可以给 180；"
                        "超过 240 秒的操作基本等于写错了算法，回去改代码别硬跑。"),
        "parameters": {"type": "object", "properties": {
            "cmd": {"type": "string"},
            "timeout": {"type": "integer",
                        "description": "这次执行最多等多少秒（默认 120，上限 600）"}},
            "required": ["cmd"]}}},
    {"type": "function", "function": {
        "name": "need_human",
        "description": ("只有人才能做的事（用浏览器、看 Burp、用你调不动的工具、"
                        "判断截图）时调用。用户会照做并把结果贴回来，你再继续。"),
        "parameters": {"type": "object", "properties": {
            "instruction": {"type": "string", "description": "让用户做什么，一步一步写清楚"},
            "why": {"type": "string", "description": "为什么必须人来做"},
            "expect": {"type": "string", "description": "用户应该把什么结果贴回来"}},
            "required": ["instruction"]}}},
    {"type": "function", "function": {
        "name": "finish",
        "description": ("给出最终答案并结束任务。找到答案后必须调用。"
                        "**必须**如实填写 verified 和 evidence：没在本地验证过就填 false，"
                        "不许为了好看而谎称验证过。"),
        "parameters": {"type": "object", "properties": {
            "answer": {"type": "string", "description": "最终答案（完整 flag）"},
            "reason": {"type": "string", "description": "一句话说明怎么得到的"},
            "verified": {"type": "boolean",
                         "description": ("你**在本地**验证过这个答案吗？"
                                         "例如：跑通程序拿到它、哈希匹配上、正则命中校验点、"
                                         "用解出的密钥解密得到明文。"
                                         "只是「看起来像」或「猜的」一律填 false。")},
            "evidence": {"type": "string",
                         "description": ("验证证据：贴出关键命令 + 输出的那几行。"
                                         "没验证就写「未验证，仅推测」。"
                                         "这一栏会直接显示给用户看，用来决定要不要花提交机会。")}},
            "required": ["answer", "verified", "evidence"]}}},
]


# ---------------------------------------------------------------- 纪律
DISCIPLINE = r"""## 纪律（比聪明更重要）

1. **每一步都要带来新信息。** 同一条命令不许重复跑，除非参数真的变了。
2. **先侦察再动手。** 别一上来就猜。
3. **命令失败了先读输出。** 报错里通常写着原因。
4. **卡 3 步没进展就换角度。** 从输出倒推、缩小问题、换个工具。
5. **算出来才算。** 不许猜答案、不许编答案；没算出来就别调 finish。
6. **输出要节制。** 大文件只取需要的片段。
7. **只在工作目录里动手。**
8. **只有人能做的时候用 need_human。** 写清楚「做什么、为什么、贴什么」，然后停下来等。
9. **不许伪造靶场。** 你在本地写出来的文件不是靶场。
10. **不要用 `python -c` 塞长代码。** 引号会被 shell 改写。
    正确做法：`write_file` 写脚本 -> `run_cmd python 脚本名.py`。
11. **省着用上下文。** 已经给过你的内容别再读一遍（尤其是 `conn.py` 源码）；
    上下文被垃圾挤满，你就想不动事了。
12. **崩了先做对照实验，别连着猜。** 只改一个变量再跑一次，一次砍掉一半可能性。
13. **答案必须能自证。** 调 finish 前先问自己：有哪一行输出能证明这个答案是对的？
    能指出具体那一行 -> verified=true；说不出来 -> verified=false，别硬撑。
14. **警惕"看起来像"的东西。** 字符串里出现一段十六进制、一段 base64、一段像 flag 的文本，
    都不等于答案。要能说清它是怎么被校验/解密出来的。
15. **格式也要验证。** 平台要求的 flag 外壳（`NSSCTF{}` / `flag{}` / 裸串）先在本地对一遍格式，
    别拿提交机会去试格式。
16. **装库很慢，先看环境里有没有。** 上面的「环境」段已经列了哪些库可用、哪些不可用。
    要用的库不可用时，先想能不能用标准库顶替（比如 sympy 的多数功能，
    Python 自带的 `fractions` / `itertools` 就能做）。
    非装不可就一次装完，别在解题中途反复 pip——每次都要卡两分钟，还会算进步数。
17. **先估一下要跑多久，别写出跑不完的循环。** 开跑前问一句：这个循环大概多少次？
    - 试除法分解 100 位以上的数 = 10^50 次以上 = 永远跑不完
    - 2^40 次以上的迭代基本就不该在这跑
    真要重活就写 C 编译（快 100 倍），或者换算法（Pollard rho / 已知分解 / ECM）。
    **一个跑不完的脚本 = 白丢一步 + 白等一轮超时。**
18. **先读源码，写下结构摘要，再动手写代码。** 读完题目脚本后，先用三句话回答：
    - 它把明文变成了什么？（pow / 矩阵乘 / 置换 / 异或 / 编码套娃）
    - 我手上有什么？（p、C、密文、公钥……）
    - **这个变换在数学上属于哪一类？**（数论 / 线性代数 / 编码 / 哈希）
    **结构没看清就写脚本 = 大概率写错方向。** 真实事故：矩阵题跑去分解 p-1，
    白丢 20 步。
19. **先用小样例自测，再上真实数据。** 算法写好后，先造一个 n=3 的小例子
    （自己知道正确答案），跑通再上 n=42 的真数据。
    这一步通常只要 30 秒，能省掉好几轮"结果不对但不知道哪一步错"。
    **在错误的方向上优化性能是最浪费时间的。**
20. **结果不对时，加探针定位，不要盲改重写。** 在关键步骤后打印一个能证伪的数字：
    秩 / 零空间维数 / 长度 / 前几项 / 是否整除……
    **一个数字就能定位 bug**，比改十行瞎猜强十倍。
    真实案例：特征向量没命中 -> 打印零空间维数 -> 是 0 -> 立刻发现多项式系数翻转错了。
21. **失败要留下"为什么失败"，而不是只留"失败了"。**
    比如"试了 42 个特征值都没可打印向量"能推出"根算错了"；
    只写"没解出来"什么信息都没有，下一轮还得重来。
22. **看到校验代码（正则 / 黑名单 / 替换），先把它翻译成一句人话。**
    `preg_replace('/[^\W]+\((?R)?\)/', '', $code)` 翻译成：
    「只能有 `func()` 和 `func(func())`，括号里不许出现字面量参数」。
    翻译完再检查你的输入。
    **看不懂正则就逐段拆**：`[^\W]`=单词字符、`\(` `\)`=括号、`(?R)`=整体递归、`+` `?`=次数。
    真实事故：一个 `,7` 违反了这个规则，白花 15 步。
23. **同一个输出出现第二次，禁止换输入。**
    必须先回答两句：这条输出是代码哪一行产生的？触发它的条件是什么？
    答不出来就去搜源码（工具会自动帮你把短输出回溯到源码）。
    **盲目换输入，只会让你更快地犯同样的错。**
24. **容器题先确认地址，再谈别的。**
    `container_enabled` 但 `url` 是空的时候，**第一步就 need_human 要地址**。
    没有远程地址：
    - Pwn 题算偏移、找 main_arena、写 exploit 全是白干（flag 在远端进程里）
    - Web 题没有目标，连"探针"都发不出去
    真实事故：一道堆题把 26 步全花在静态分析 libc 上，一步都没连过远端。
"""


# ---------------------------------------------------------------- 环境探测
CMD_TOOLS = [
    ("python", "跑脚本", ""), ("pip", "装库", ""),
    ("curl", "发 HTTP 请求", ""), ("wget", "下载", ""),
    ("git", "拉源码", ""), ("ssh", "连远程", ""), ("nc", "连端口", ""),
    ("docker", "起容器", ""), ("node", "跑 js", ""),
    ("java", "跑 jar", "需要装 JDK"),
    ("file", "看文件真实类型", "Windows 上一般没有，用 Python 读魔数代替"),
    ("strings", "抓二进制字符串", ""),
    ("7z", "解压缩", "装 7-Zip"),
    ("tshark", "解析 pcap", "装 Wireshark"),
    ("sqlmap", "SQL 注入", "pip install sqlmap"),
    ("dirsearch", "目录扫描", "pip install dirsearch"),
    ("ffuf", "目录爆破", "需要 Go"),
    ("nuclei", "模板扫描", "需要 Go"),
    ("exiftool", "图片元数据", "需要下载 exe"),
    ("steghide", "图片隐写", "需要下载 exe"),
    ("binwalk", "文件夹带分析", "pip install binwalk"),
    ("upx", "脱壳", "需要下载 exe"),
    ("wsl", "Linux 子系统", "wsl --install"),
    ("gdb", "调试器", "需要 Linux/WSL"),
    ("readelf", "看 ELF 结构", "需要 Linux/WSL"),
    ("objdump", "反汇编（能读 ELF）", ""),
    ("checksec", "看二进制保护", "需要 Linux/WSL"),
]

PY_MODULES = [
    ("requests", "HTTP 客户端", "pip install requests"),
    ("z3", "约束求解", "pip install z3-solver"),
    ("pwn", "pwn 脚本库", "pip install pwntools"),
    ("Crypto", "密码学库", "pip install pycryptodome"),
    ("gmpy2", "大数加速", "pip install gmpy2"),
    ("PIL", "图片处理", "pip install pillow"),
    # 下面这些以前没探测到，导致模型盲试 pip install 白卡两分钟
    ("sympy", "符号数学/数论", "pip install sympy"),
    ("numpy", "数组/线性代数", "pip install numpy"),
    ("scipy", "科学计算", "pip install scipy"),
    ("itertools", "组合迭代（标准库，一定有）", ""),
    ("fractions", "有理数（标准库，一定有）", ""),
]

_ENV_CACHE = {}


def probe_detail(force=False):
    import importlib.util
    import shutil
    if _ENV_CACHE.get("detail") and not force:
        return _ENV_CACHE["detail"]
    have, missing = [], []
    for name, desc, how in CMD_TOOLS:
        item = {"name": name, "desc": desc, "how": how, "kind": "cmd"}
        (have if shutil.which(name) else missing).append(item)
    for name, desc, how in PY_MODULES:
        item = {"name": name, "desc": desc, "how": how, "kind": "py"}
        try:
            found = importlib.util.find_spec(name) is not None
        except Exception:
            found = False
        (have if found else missing).append(item)
    probed = {x["name"] for x in have}
    cnames = set()
    for t in custom_tools():
        cnames.add(t["name"])
        if t["name"] in probed:
            continue
        have.append({"name": t["name"], "desc": "自定义", "how": "", "kind": "custom"})
    missing = [x for x in missing if x["name"] not in cnames]
    detail = {"have": have, "missing": missing}
    _ENV_CACHE["detail"] = detail
    return detail


def probe_env(force=False):
    if _ENV_CACHE.get("text") and not force:
        return _ENV_CACHE["text"]
    d = probe_detail(force)
    lines = ["## 环境（已探测，别调用不存在的命令）", ""]
    names = [x["name"] for x in d["have"] if x.get("kind") != "custom"]
    lines.append("可用：" + ("、".join(names) if names else "（几乎没有）"))
    miss = [x["name"] for x in d["missing"]]
    if miss:
        lines.append("不可用：" + "、".join(miss))
        lines.append("-> 不可用的别硬调，用 Python 自己实现替代。")
    customs = custom_tools()
    if customs:
        lines.append("")
        lines.append("## 自己配的工具（用 run_cmd 按这个形式调）")
        for t in customs:
            lines.append("  %s ->  %s" % (t["name"], t["cmd"]))
    lines.append("")
    lines.append("-> 没有 Burp 也没关系：改包=改 header/body 重发，爆破=写循环。")
    lines.append("-> 探测不到不等于没有。觉得该有却没有，用 need_human 问用户怎么调。")
    _ENV_CACHE["text"] = "\n".join(lines)
    return _ENV_CACHE["text"]


# ---------------------------------------------------------------- 题型打法（兜底）
PLAYBOOKS = {
    "Crypto": """## Crypto 打法

1. 读脚本，找**参数**（n/e/c、p/q/g/y）和**最后 print 出来的东西**。
2. 问两个问题：已知什么？还缺什么？缺的能不能从已知推出来。
3. 先算 bit 长度，对比已知量，常常一眼看出结构。
4. 常见弱点自查：e 很小 / dp 泄露 / n 有特殊结构 / 共模 / 小因子 / 群阶光滑 / 低指数广播。
5. 别急着爆破，先想清楚数学结构。算完要回代验证。
""",
    "Reverse": """## Reverse 打法

1. 先看真实类型，再抓字符串线索。
2. 找**比较点**，从比对处倒推比从头顺推快。
3. 虚拟机壳：找分发器 -> 抠 handler 语义 -> 写模拟器 -> 差分验证 -> 反解。
4. 动态优先静态：能跑就跑，能打印就打印。
""",
    "Misc": """## Misc 打法

1. 先看真实文件类型，别信扩展名。
2. 编码套娃：base64 -> hex -> rot13 -> url，一层层试。
3. 图片：元数据、夹带、末尾追加数据单独截出来。
4. 压缩包：伪加密、文件名提示、CRC 碰撞、弱口令。
5. 流量包：先看协议分级统计，再跟流。
""",
    "Web": """## Web 打法

1. 先 `curl -i` 看响应头和状态码。
2. 找输入点：URL 参数、表单、Cookie、Header。
3. 源码泄露优先试：`.git/`、`.bak`、`www.zip`、`robots.txt`。
4. 常见方向：SQL 注入、命令执行、文件包含、反序列化、SSRF、模板注入。
5. **没有 Burp 照样能做**：改包=改 header/body 重发，爆破=写循环。
""",
    "Pwn": """## Pwn 打法

1. 这台机器有 `objdump`（能反汇编 Linux ELF）和 `strings`，但没有 Linux 运行环境。
2. 流程：`objdump -h` 确认 ELF -> `objdump -d` 抓函数名 -> `objdump -s -j .data` 看初始值
   -> `strings` 找 /flag、system、pthread_create、usleep。
3. 看到 `pthread_create` + `usleep` 就是竞态；看到 `printf(buf)` 就是格式化字符串。
4. 远程用工作目录里的 `conn.py` 连，等提示符用 `recv_prompt()`。
5. 崩了先做对照实验（把目标换成 puts 试试），别连着猜。
""",
}


def load_general():
    """skills/general.md —— 所有题型都要读的通用经验。"""
    p = SKILLS_DIR / "general.md"
    if p.is_file():
        try:
            return p.read_text(encoding="utf-8").strip()
        except Exception:
            pass
    return ""


def load_skill(ptype):
    """优先读 skills/<题型小写>.md，没有就用内置兜底。"""
    if ptype:
        p = SKILLS_DIR / ("%s.md" % str(ptype).strip().lower())
        if p.is_file():
            try:
                txt = p.read_text(encoding="utf-8").strip()
                if txt:
                    return txt
            except Exception:
                pass
    return PLAYBOOKS.get(ptype or "", "")


ARENA_EXTRA = """## 输出格式

平台要求答案形如 `NSSCTF{...}`。
如果附件里给的是 `GWHT{...}`、`flag{...}` 这类，把前缀换成 `NSSCTF` 再放进 answer。
"""

LOCAL_EXTRA = """## 输出格式

这是本地题目，用户会自己拿答案去提交。
把最终 flag **原样**放进 answer，不要改前缀。
"""


def compose_prompt(workdir, mode, ptype):
    parts = [
        "你是一个 CTF 解题助手，在本地沙箱里工作。",
        "",
        "工作目录：%s" % workdir,
        "（只能读写这个目录里的文件，命令也在这个目录下执行）",
        "",
        "可用工具：list_dir / read_file / write_file / run_cmd / need_human / finish",
        "",
        "工作目录里已经放好一个 `conn.py`（零依赖的远程连接小工具）。",
        "要连远程服务时：`import conn; c = conn.Conn(\"host\", port)`，",
        "然后用 c.sendline() / c.recv() / c.recv_until() / c.recv_prompt()。",
        "**不要 read_file conn.py** —— 用法已经写在这里了，读它只会白白占掉上下文。",
        "",
        DISCIPLINE,
        probe_env(),
        load_general(),
        load_skill(ptype),
        ARENA_EXTRA if mode == "arena" else LOCAL_EXTRA,
        "算出答案后，**必须调用 finish**，把完整答案放进 answer。",
    ]
    return "\n".join(p for p in parts if p)


# ---------------------------------------------------------------- 会话
class Session:
    """一次解题会话。可以中途暂停等人工反馈，再继续。"""

    def __init__(self, workdir, problem_text, mode, emit, ptype=None, max_steps=None):
        c = cfg()
        if not c["key"]:
            raise RuntimeError("还没有填 DeepSeek API Key")
        base = max_steps or c["max_steps"]
        cap = TYPE_STEPS.get(ptype or "", base)
        self.workdir = workdir
        self.mode = mode
        self.emit_cb = emit
        self.max_steps = min(base, cap)
        self.model = c["model"]
        self.ptype = ptype
        self.step = 0
        self.nudges = 0
        self.answer = None
        self.error = None
        self.answer_meta = {}
        self.flag_seen = None       # 输出里瞥见过的疑似 flag（兜底用）
        self.flag_nudged = set()    # 已经提醒过的串，避免反复唠叨
        self.out_hist = {}          # 输出指纹 -> 出现次数（打转检测）
        self.waiting = None
        self.pending_call = None
        self.messages = [
            {"role": "system", "content": compose_prompt(workdir, mode, ptype)},
            {"role": "user", "content": problem_text},
        ]

    def emit(self, ev):
        try:
            self.emit_cb(ev)
        except Exception:
            pass

    def run(self):
        if self.step == 0:
            self.emit({"type": "info", "text": "开始，模型 %s，题型 %s，最多 %d 步"
                                              % (self.model, self.ptype or "未知", self.max_steps)})
        while self.step < self.max_steps:
            self.step += 1
            self.emit({"type": "step", "n": self.step, "text": "第 %d 步：思考中…" % self.step})
            resp = chat(self.messages, TOOLS)
            msg = (resp.get("choices") or [{}])[0].get("message") or {}
            content = (msg.get("content") or "").strip()
            calls = msg.get("tool_calls") or []
            if content:
                self.emit({"type": "model", "text": content[:1600]})
            if not calls:
                self.nudges += 1
                if self.nudges > 3:
                    self.error = ("模型连续 %d 次只输出文字、不调工具，提前停止。"
                                  "（它在原地空想，没在验证任何东西）" % self.nudges)
                    return self.result()
                self.messages.append({"role": "assistant", "content": content or "(空)"})
                self.messages.append({"role": "user", "content":
                    "⚠️ 你刚才只是又输出了一段文字，没有调用任何工具。这是第 %d 次了。\n"
                    "**立刻去跑一个命令**——哪怕只是一条 `curl`、`ls`、或者写个 5 行的脚本。\n"
                    "如果你正在反复纠结同一个问题（比如「哪个函数能绕过滤」），"
                    "说明再想也想不出来：**写个小脚本把所有候选都试一遍**，让机器告诉你答案。\n"
                    "需要人来做的事用 need_human；有答案了就 finish。" % self.nudges})
                continue
            # 成功动手了 -> 重置计数（这个计数只该惩罚"连续"不动手，不该累计）
            self.nudges = 0

            # 方向检查点：步数越多越容易陷在错的方向里，
            # 每隔一段时间强制它抬头看一眼（这是 50 步也解不出来的主因）
            if self.step in (15, 28) and not self.answer:
                self.messages.append({"role": "user", "content":
                    "🛑 **方向检查（第 %d 步）**\n"
                    "先停一下，用三句话回答（不用调工具，回答完再继续）：\n"
                    "1. 我现在**确信**的结论是什么？（哪一步的输出能证明它）\n"
                    "2. 我**还没排除**的可能性有哪些？\n"
                    "3. 如果这条路是错的，**最先暴露的信号**会是什么？\n\n"
                    "如果答不上第 1 句，说明你在猜——**回去找证据，别继续加代码**。\n"
                    "如果这三句指向「该换方向了」，就换。步数还有，但方向错了再多步也没用。"
                    % self.step})
            self.messages.append({"role": "assistant", "content": content, "tool_calls": calls})
            for call in calls:
                name = (call.get("function") or {}).get("name")
                try:
                    args = json.loads((call.get("function") or {}).get("arguments") or "{}")
                except Exception:
                    args = {}

                if name == "finish":
                    self.answer = (args.get("answer") or "").strip()
                    verified = args.get("verified")
                    # 模型有时把布尔写成字符串，这里兼容一下
                    if isinstance(verified, str):
                        verified = verified.strip().lower() in ("true", "1", "yes", "y", "是")
                    self.answer_meta = {
                        "verified": bool(verified),
                        "evidence": (args.get("evidence") or "").strip()[:1200],
                        "reason": (args.get("reason") or "").strip()[:400],
                    }
                    self.emit({"type": "answer", "answer": self.answer,
                               "reason": self.answer_meta["reason"],
                               "verified": self.answer_meta["verified"],
                               "evidence": self.answer_meta["evidence"]})
                    return self.result()

                if name == "need_human":
                    self.waiting = {"instruction": (args.get("instruction") or "").strip(),
                                    "why": (args.get("why") or "").strip(),
                                    "expect": (args.get("expect") or "").strip()}
                    self.pending_call = call.get("id", "")
                    self.emit({"type": "human", **self.waiting})
                    return {"waiting": self.waiting}

                fn = DISPATCH.get(name)
                try:
                    out = fn(self.workdir, args) if fn else "未知工具：%s" % name
                except Exception as e:
                    out = "工具出错：%s" % e
                brief = (args.get("cmd") or args.get("path") or "")
                # 短输出回溯到源码：很多"错误提示"其实是源码里某个分支打印的，
                # 把它送回去，模型才知道该去看哪段逻辑（而不是换 payload）
                try:
                    hint = source_hint(self.workdir, out)
                    if hint:
                        out = out + hint
                        self.emit({"type": "info", "text": "🔎 这条输出在源码里找到了出处"})
                except Exception:
                    pass
                self.emit({"type": "tool", "name": name, "arg": brief[:160], "out": out[:1200]})
                self.messages.append({"role": "tool",
                                      "tool_call_id": call.get("id", ""), "content": out})

                # 打转检测：同一个短输出反复出现 = 在同一个坑里换输入
                sig = (out or "").strip()[:120]
                if sig and len(sig) <= 120:
                    self.out_hist[sig] = self.out_hist.get(sig, 0) + 1
                    n = self.out_hist[sig]
                    if n in (3, 6):
                        self.emit({"type": "info",
                                   "text": "🔁 同一个输出已经出现 %d 次了" % n})
                        self.messages.append({"role": "user", "content":
                            "🔁 **你已经第 %d 次拿到一模一样的输出了**：`%s`\n"
                            "再换一个 payload 只会得到同样的结果。**停下来做两件事**：\n"
                            "1. 这个字符串是源码里哪一行打印的？（上面已经给过出处）\n"
                            "2. 触发它的条件是什么？把你的输入逐字符对照那个条件。\n"
                            "如果这两个问题你答不上来，**这一轮不要写任何新 payload**，"
                            "先把源码读一遍。" % (n, sig)})

                # 兜底：输出里出现 flag 形状的串，立刻推它一把去 finish。
                # 背景：曾经有过模型把 flag 打到屏幕上，却继续去找别的路径，
                # 最后步数耗尽、答案丢了。
                for hit in FLAG_PAT.findall(out or "")[:3]:
                    if not self.flag_seen:
                        self.flag_seen = hit
                    if hit in self.flag_nudged or self.answer:
                        continue
                    self.flag_nudged.add(hit)
                    self.emit({"type": "info", "text": "👀 输出里出现疑似 flag：%s" % hit})
                    self.messages.append({"role": "user", "content":
                        "⚠️ 你刚才这次工具输出里出现了 `%s`，很可能就是最终答案。\n"
                        "如果它是从题目环境 / 附件 / 解密结果里拿到的，**立刻调用 finish 交它**，"
                        "不要再去找别的路径。\n"
                        "如果你确认它只是无关字符串（比如源码里的示例、文档里的占位符），"
                        "用一句话说明理由再继续。" % hit})
        self.error = "达到 %d 步上限还没解出来" % self.max_steps
        return self.result()

    def resume(self, human_text):
        self.messages.append({"role": "tool",
                              "tool_call_id": self.pending_call or "",
                              "content": "【用户执行结果】\n" + (human_text or "（用户没有提供内容）")})
        self.emit({"type": "info", "text": "收到你的反馈，继续解题"})
        self.waiting = None
        self.pending_call = None
        return self.run()

    def result(self):
        return {"answer": self.answer, "error": self.error,
                "verify": self.answer_meta or {},
                "candidate": self.flag_seen}


def solve(workdir, problem_text, mode, emit, ptype=None, max_steps=None):
    return Session(workdir, problem_text, mode, emit, ptype, max_steps).run()
