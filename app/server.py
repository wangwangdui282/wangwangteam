# -*- coding: utf-8 -*-
"""
server.py - 汪汪队立大功：本地图形界面服务

只监听 127.0.0.1。双击「启动.bat」= 跑这个 + 打开浏览器。
"""
import base64
import json
import re
import sys
import threading
import time
import urllib.parse
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import ai
import core
import reflux

APP = Path(__file__).resolve().parent
PROJ = APP.parent
UI = APP / "ui.html"
LOGS = PROJ / "logs"
WRITEUPS = PROJ / "writeups"
PORT_START = 8791

JOBS = {}
LOCK = threading.Lock()
GAVE_UP = set()          # 已经交过"放弃用"错误 flag 的 attempt id，防止重复提交
FAIL_STREAK = {}         # attempt id -> 连续答错次数（用于强制暂停换思路）


# ---------------------------------------------------------------- 日志文件
def _log_add(path, text):
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text.rstrip() + "\n")
    except Exception:
        pass


def format_event(ev):
    t = ev.get("t", "")
    kind = ev.get("type")
    if kind == "info":
        return "\n### [%s] %s\n" % (t, ev.get("text", ""))
    if kind == "model":
        body = (ev.get("text") or "").replace("\n", "\n> ")
        return "\n**[%s] 模型**：\n\n> %s\n" % (t, body)
    if kind == "tool":
        return ("\n**[%s] 工具** `%s` %s\n\n```\n%s\n```\n"
                % (t, ev.get("name", ""), ev.get("arg", ""), ev.get("out", "")))
    if kind == "human":
        return ("\n**需要用户操作 [%s]**\n\n- 做什么：%s\n- 为什么：%s\n- 贴什么：%s\n"
                % (t, ev.get("instruction", ""), ev.get("why", ""), ev.get("expect", "")))
    if kind == "answer":
        tag = "✅ 已验证" if ev.get("verified") else "⚠️ 未验证"
        ev_txt = (ev.get("evidence") or "（模型没有给出证据）").strip()
        return ("\n## 答案 %s\n\n`%s`\n\n理由：%s\n\n**证据**\n\n```\n%s\n```\n"
                % (tag, ev.get("answer", ""), ev.get("reason", "") or "-", ev_txt))
    if kind == "error":
        return "\n**错误 [%s]**：%s\n" % (t, ev.get("text", ""))
    return ""


def new_log(mode, label):
    LOGS.mkdir(parents=True, exist_ok=True)
    name = time.strftime("%Y%m%d-%H%M%S") + "-%s-%s.md" % (mode, label)
    p = LOGS / name
    p.write_text("# 解题日志\n\n- 时间：%s\n- 模式：%s\n- 题目：%s\n\n---\n"
                 % (time.strftime("%Y-%m-%d %H:%M:%S"), mode, label or "-"),
                 encoding="utf-8")
    return p


# ---------------------------------------------------------------- 任务
def new_job(meta):
    jid = uuid.uuid4().hex[:12]
    with LOCK:
        JOBS[jid] = {"id": jid, "running": True, "trace": [], "answer": None,
                     "error": None, "started": time.time(), **meta}
    return jid


def emit_to(jid):
    def emit(ev):
        path = None
        with LOCK:
            j = JOBS.get(jid)
            if not j:
                return
            ev["t"] = time.strftime("%H:%M:%S")
            j["trace"].append(ev)
            if len(j["trace"]) > 600:
                del j["trace"][:200]
            path = j.get("logpath")
        line = format_event(ev)
        if line:
            _log_add(path, line)
    return emit


def finish_job(jid, answer=None, error=None, verify=None):
    path = None
    with LOCK:
        j = JOBS.get(jid)
        if j:
            j["running"] = False
            j["answer"] = answer
            j["error"] = error
            j["verify"] = verify or {}
            j["ended"] = time.time()
            path = j.get("logpath")
    if path:
        v = verify or {}
        tag = "已验证" if v.get("verified") else "**未验证**"
        if answer:
            _log_add(path, "\n---\n\n**结束**：解出来了 -> `%s`（%s）\n\n证据：%s\n"
                     % (answer, tag, v.get("evidence") or "（无）"))
        elif error:
            _log_add(path, "\n---\n\n**结束**：失败 -> %s\n" % error)
        else:
            _log_add(path, "\n---\n\n**结束**：没有结果\n")

        # 自动生成复盘文档到 writeups/（失败不影响主流程）
        try:
            j = JOBS.get(jid) or {}
            title = (j.get("label") or j.get("title")
                     or (j.get("prompt") or "")[:40] or Path(path).stem)
            wp = reflux.save_writeup(path, {"title": title})
            if wp:
                with LOCK:
                    if jid in JOBS:
                        JOBS[jid]["writeup"] = str(wp)
                _log_add(path, "\n复盘文档已生成：`%s`\n" % wp.name)
        except Exception:
            pass


def job_status(jid, since=0):
    with LOCK:
        j = JOBS.get(jid)
        if not j:
            return {"error": "任务不存在"}
        return {"running": j["running"], "total": len(j["trace"]),
                "events": j["trace"][since:], "answer": j.get("answer"),
                "error": j.get("error"), "waiting": j.get("waiting"),
                "verify": j.get("verify") or {},
                "mode": j.get("mode"), "cid": j.get("cid"),
                "elapsed": int(time.time() - j.get("started", time.time()))}


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "Wangwang/2.0"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    def _q(self):
        q = urllib.parse.parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
        return {k: v[0] for k, v in q.items()}

    # ------------------------------------------------------------ GET
    def do_GET(self):
        path = self.path.split("?")[0]
        q = self._q()
        if path in ("/", "/index.html"):
            if not UI.is_file():
                return self._send(500, "ui.html 不见了", "text/plain; charset=utf-8")
            return self._send(200, UI.read_bytes(), "text/html; charset=utf-8")
        if path == "/favicon.ico":
            return self._send(204, b"")
        if path == "/api/ping":
            return self._send(200, "wangwang", "text/plain; charset=utf-8")
        if path == "/api/state":
            return self._json(self.collect())
        if path == "/api/settings":
            return self._json(self.settings_payload())
        if path == "/api/env":
            return self._json(ai.probe_detail(force=(q.get("refresh") == "1")))
        if path == "/api/ai/status":
            return self._json(job_status(q.get("job", ""), int(q.get("since") or 0)))
        if path == "/api/activity":
            days = {}
            if core.HIST_FILE.is_file():
                for line in core.HIST_FILE.read_text(encoding="utf-8-sig").splitlines():
                    try:
                        r = json.loads(line)
                    except Exception:
                        continue
                    ts = r.get("ts")
                    if ts:
                        day = time.strftime("%Y-%m-%d", time.localtime(float(ts) / 1000))
                        days[day] = days.get(day, 0) + 1
            return self._json({"days": days})
        if path == "/api/history":
            rows = []
            if core.HIST_FILE.is_file():
                for line in core.HIST_FILE.read_text(encoding="utf-8-sig").splitlines():
                    try:
                        rows.append(json.loads(line))
                    except Exception:
                        continue
            # 同一道题可能被交过多次（又交了一次错的 / 重领同一题 / 自动第二轮），
            # 列表里只保留最近一次，并用 times 标出交了几次。
            seen, uniq = {}, []
            for r in reversed(rows):
                k = r.get("attempt_id") or r.get("problem") or "_"
                if k in seen:
                    seen[k]["times"] = seen[k].get("times", 1) + 1
                    continue
                r = dict(r)
                r["times"] = 1
                seen[k] = r
                uniq.append(r)
            return self._json({"rows": uniq[:60]})
        if path == "/api/logs":
            items = []
            if LOGS.is_dir():
                for f in sorted(LOGS.glob("*.md"), reverse=True)[:60]:
                    items.append({"name": f.name, "size": f.stat().st_size,
                                  "mtime": int(f.stat().st_mtime * 1000)})
            return self._json({"logs": items})
        if path == "/api/logfile":
            f = (LOGS / q.get("name", "")).resolve()
            if LOGS.resolve() not in f.parents or not f.is_file():
                return self._json({"error": "找不到这个日志"})
            return self._json({"text": f.read_text(encoding="utf-8", errors="replace").lstrip("\ufeff")})
        if path == "/api/reflux/list":
            return self._json({"writeups": reflux.list_writeups(),
                               "skills": sorted(p.name for p in reflux.SKILLS.glob("*.md"))
                               if reflux.SKILLS.is_dir() else []})
        if path == "/api/reflux/file":
            f = (WRITEUPS / q.get("name", "")).resolve()
            if WRITEUPS.resolve() not in f.parents or not f.is_file():
                return self._json({"error": "找不到这个复盘文档"})
            return self._json({"text": f.read_text(encoding="utf-8", errors="replace")})
        if path == "/api/local/list":
            return self._json({"cases": core.list_local_cases()})
        if path == "/api/local/get":
            try:
                cid = q.get("cid", "")
                return self._json({"case": core.get_local_case(cid),
                                   "files": core.list_local_files(cid)})
            except core.ApiError as e:
                return self._json({"error": str(e)})
        if path == "/api/file":
            try:
                return self._json({"text": core.read_challenge_file(q.get("p", ""))})
            except Exception as e:
                return self._json({"error": str(e)})
        if path == "/api/localfile":
            try:
                return self._json({"text": core.read_local_file(q.get("cid", ""), q.get("p", ""))})
            except Exception as e:
                return self._json({"error": str(e)})
        return self._send(404, "not found", "text/plain; charset=utf-8")

    # ------------------------------------------------------------ POST
    def do_POST(self):
        path = self.path.split("?")[0]
        body = self._body()
        routes = {
            "/api/token": self.r_token,
            "/api/settings": self.r_settings,
            "/api/next": self.r_next,
            "/api/submit": self.r_submit,
            "/api/giveup": self.r_giveup,
            "/api/giveup_all": self.r_giveup_all,
            "/api/wp": self.r_wp,
            "/api/detail": self.r_detail,
            "/api/open": self.r_open_challenge,
            "/api/ai/solve": self.r_solve,
            "/api/ai/resume": self.r_resume,
            "/api/local/new": self.r_local_new,
            "/api/local/upload": self.r_local_upload,
            "/api/local/open": self.r_local_open,
            "/api/local/delete": self.r_local_delete,
            "/api/reflux/draft": self.r_reflux_draft,
            "/api/reflux/apply": self.r_reflux_apply,
        }
        fn = routes.get(path)
        if not fn:
            return self._send(404, "not found", "text/plain; charset=utf-8")
        try:
            self._json(fn(body))
        except core.ApiError as e:
            self._json({"error": str(e)})
        except Exception as e:
            self._json({"error": "意外错误：%r" % (e,)})

    # ------------------------------------------------------------ 状态
    def settings_payload(self):
        c = ai.cfg()
        return {"ai": {"set": bool(c["key"]),
                       "key_tail": ("..." + c["key"][-4:]) if c["key"] else "",
                       "base": c["base"], "model": c["model"],
                       "max_steps": c["max_steps"],
                       "tools_text": ai.load_settings().get("tools_text") or ""},
                "token": core.token_status()}

    def collect(self):
        out = {"settings": self.settings_payload(), "stats": core.stats()}
        if not out["settings"]["token"]["set"]:
            out["need_token"] = True
            return out
        try:
            data = core.current()
            out["agent"] = data.get("agent") or {}
            out["attempt"] = data.get("attempt")
            if out["attempt"]:
                at = out["attempt"]
                mx = at.get("max_wrong_count") or 0
                wr = at.get("wrong_count") or 0
                # 提交预算：让前端常驻显示，别拿机会去试格式
                out["budget"] = {"max": mx, "used": wr, "left": max(0, mx - wr),
                                 "expire_at": at.get("expire_at")}
                out["tips"] = core.tips(at.get("problem"))
        except core.ApiError as e:
            out["error"] = str(e)
            return out
        d = core.challenge_dir()
        out["local"] = {"dir": str(d) if d else "", "files": core.list_files()}
        return out

    # ------------------------------------------------------------ 设置
    def r_token(self, b):
        core.set_token(b.get("token"), bool(b.get("remember")))
        return self.collect()

    def r_settings(self, b):
        upd = {}
        if b.get("ai_key") is not None:
            k = (b.get("ai_key") or "").strip()
            if k.startswith("nss_agent_"):
                raise core.ApiError(
                    "这串是 NSSCTF token，不是 DeepSeek Key。\n"
                    "请贴到下面的「NSSCTF Token」那一格；DeepSeek Key 以 sk- 开头。")
            upd["ai_key"] = k
        if b.get("ai_base") is not None:
            upd["ai_base"] = (b.get("ai_base") or "").strip()
        if b.get("ai_model") is not None:
            upd["ai_model"] = (b.get("ai_model") or "").strip()
        if b.get("max_steps") is not None:
            upd["max_steps"] = int(b.get("max_steps") or 16)
        if b.get("tools_text") is not None:
            upd["tools_text"] = b.get("tools_text") or ""
        ai.save_settings(upd)
        ai._ENV_CACHE.clear()
        return self.settings_payload()

    # ------------------------------------------------------------ 竞技场
    def r_next(self, b):
        data = core.fetch_next()
        at = data.get("attempt")
        if not at:
            raise core.ApiError("没拿到题，稍后再试")
        d, got, errs = core.prepare_challenge(at)
        res = self.collect()
        res["reused"] = bool(data.get("reused"))
        res["downloaded"] = got
        if errs:
            res["download_errors"] = errs
        return res

    def r_submit(self, b):
        flag = (b.get("flag") or "").strip()
        if not flag:
            raise core.ApiError("flag 是空的")

        # 依据是「建议填写」不是「必须」：拍板的人永远是你，工具只提醒。
        basis = (b.get("basis") or "").strip()

        data = core.current()
        at = data.get("attempt")
        if not at:
            raise core.ApiError("现在没有进行中的题（可能已超时或已提交）")
        tid = at["id"]

        streak = FAIL_STREAK.get(tid, 0)
        warnings = []
        if not basis:
            warnings.append("这次没写依据。下次顺手写一句，能少浪费机会。")
        if streak >= 2:
            warnings.append("这道题已经连错 %d 次，建议先换个思路再交。" % streak)

        before = (data.get("agent") or {}).get("rating")
        r = core.submit_flag(tid, flag)
        att = r.get("attempt") or {}
        aft = r.get("agent") or {}
        correct = bool(r.get("correct"))
        FAIL_STREAK[tid] = 0 if correct else streak + 1
        core.log(attempt_id=tid, flag=flag, correct=correct, basis=basis,
                 delta=att.get("rating_delta"), rating_after=aft.get("rating"),
                 problem=(at.get("problem") or {}).get("name"),
                 ptype=(at.get("problem") or {}).get("type_label"))
        out = self.collect()
        out["result"] = {"correct": correct, "flag": flag, "rating_before": before,
                         "rating_after": aft.get("rating"), "delta": att.get("rating_delta"),
                         "basis": basis, "streak": FAIL_STREAK.get(tid, 0),
                         "warnings": warnings,
                         "wrong_left": (att.get("max_wrong_count") or 0) - (att.get("wrong_count") or 0)}
        return out

    def r_giveup(self, b):
        data = core.current()
        at = data.get("attempt")
        if not at:
            raise core.ApiError("现在没有进行中的题")
        tid = at["id"]
        # 幂等：同一道题只允许交一次"放弃用"的错误 flag
        with LOCK:
            if tid in GAVE_UP:
                raise core.ApiError("这道题已经交过错误 flag 了。平台没有放弃接口，只能等它超时")
            GAVE_UP.add(tid)
        flag = (b.get("flag") or "").strip() or "NSSCTF{giveup_wangwang}"
        core.submit_flag(tid, flag)
        core.log(attempt_id=tid, flag=flag, correct=False, giveup=True,
                 problem=(at.get("problem") or {}).get("name"),
                 ptype=(at.get("problem") or {}).get("type_label"))
        d = core.challenge_dir()
        if d:
            (d / "GAVE_UP.md").write_text("交错误 flag 于 " + time.strftime("%Y-%m-%d %H:%M:%S") + "\n",
                                          encoding="utf-8")
        out = self.collect()
        out["message"] = ("已交了一次错误 flag（消耗 1 次提交机会）。"
                          "平台没有放弃接口，题目仍在进行中，只能等它超时。")
        return out

    def r_giveup_all(self, b):
        """把本题的提交机会**全部用光**，让题目结束，然后自动领下一题。

        为什么要这么做：
            平台上没有"放弃"接口，唯一能让一道题结束的办法就是交满错误次数。
            交满后题目自动结束，才能领下一题。

        代价：会扣 rating（Elo 会掉），所以前端必须二次确认。
        """
        data = core.current()
        at = data.get("attempt")
        if not at:
            raise core.ApiError("现在没有进行中的题")
        # 安全闸：必须显式确认，防止误调用（curl、脚本、前端 bug）把机会烧光
        if not b.get("confirm"):
            raise core.ApiError(
                "这个操作会把剩余提交机会全部交成错误 flag（会扣 rating）。\n"
                "确认要执行请传 confirm=true。")
        tid = at["id"]
        max_wrong = at.get("max_wrong_count") or 20
        used = at.get("wrong_count") or 0
        left = max(0, max_wrong - used)
        if left <= 0:
            left = 1                    # 已经交满，再补一次确认结束

        burned, last_err = 0, ""
        for i in range(min(left + 3, 26)):
            try:
                core.submit_flag(tid, "NSSCTF{giveup_wangwang_%d}" % i)
                burned += 1
            except core.ApiError as e:
                last_err = str(e)
                break
            # 每交一次检查题目是否已经结束
            try:
                if not (core.current() or {}).get("attempt"):
                    break
            except core.ApiError:
                break

        with LOCK:
            GAVE_UP.add(tid)

        d = core.challenge_dir()
        if d:
            try:
                (d / "GAVE_UP.md").write_text(
                    "用光提交机会放弃于 %s（共交 %d 次）\n"
                    % (time.strftime("%Y-%m-%d %H:%M:%S"), burned),
                    encoding="utf-8")
            except Exception:
                pass

        msg = "已交 %d 次错误 flag，这道题应该结束了。" % burned
        if last_err:
            msg += "（%s）" % last_err

        # 自动领下一题
        try:
            nxt = core.fetch_next()
            nat = nxt.get("attempt") or {}
            if nat.get("id"):
                prepared, _got, _errs = core.prepare_challenge(nat)
                res = self.collect()
                res["burned"] = burned
                res["auto_next"] = True
                res["message"] = msg + " 已自动领到下一题（id%s）。" % nat.get("id")
                return res
            out = self.collect()
            out["burned"] = burned
            out["message"] = msg + " 但没有领到新题（可能暂时没题可领）。"
            return out
        except Exception as e:
            out = self.collect()
            out["burned"] = burned
            out["message"] = msg
            out["next_error"] = str(e)
            return out

    def r_open_challenge(self, b):
        import os
        d = core.challenge_dir()
        if not d:
            raise core.ApiError("还没有题目目录，先领一道题")
        if sys.platform == "win32":
            os.startfile(str(d))
        return {"ok": True, "path": str(d)}

    def r_detail(self, b):
        tid = b.get("id")
        if not tid:
            raise core.ApiError("没有题目 id")
        cands = ["/skill/agent/arena/attempt/%s/" % tid,
                 "/skill/agent/arena/attempt/%s" % tid,
                 "/skill/agent/arena/attempt/%s/detail/" % tid,
                 "/skill/agent/arena/problem/%s/" % tid]
        tries = []
        for p in cands:
            st, obj, raw = core.api_raw("GET", p)
            tries.append({"path": p, "status": st,
                          "body": obj if obj is not None else (raw or "")[:300]})
            if st == 200 and isinstance(obj, dict) and obj.get("code") == 200:
                break
        return {"tries": tries}

    # ------------------------------------------------------------ writeup
    def r_wp(self, b):
        flag = (b.get("flag") or "").strip()
        name = (b.get("log") or "").strip()
        src = None
        if name:
            p = (LOGS / name).resolve()
            if LOGS.resolve() in p.parents and p.is_file():
                src = p
        if src is None and LOGS.is_dir():
            cands = sorted(LOGS.glob("*.md"), reverse=True)
            src = cands[0] if cands else None
        if src is None:
            raise core.ApiError("还没有任何解题日志，先解一道题吧")

        text = src.read_text(encoding="utf-8", errors="replace").lstrip("\ufeff")
        if not flag:
            m = re.search(r"`([A-Za-z0-9_]{2,20}\{[^`\n]{1,120}\})`", text)
            if m:
                flag = m.group(1)

        acts, seen = [], set()
        for fn, arg in re.findall(r"工具\*\* `(\w+)`\s*(.*)", text):
            key = (fn, arg.strip()[:60])
            if key in seen:
                continue
            seen.add(key)
            acts.append("- `%s` %s" % (fn, arg.strip()[:80]))
        if len(acts) > 20:
            acts = acts[:20] + ["- ...（还有 %d 条，见完整日志）" % (len(acts) - 20)]

        body = "\n".join([
            "# Writeup", "",
            "- 来源日志：`%s`" % src.name,
            "- 生成时间：%s" % time.strftime("%Y-%m-%d %H:%M:%S"),
            "- flag：%s" % ("`%s`" % flag if flag else "（没抓到，自己填）"), "",
            "## 一、一句话结论", "", "（自己补一句：这题的关键是什么）", "",
            "## 二、关键动作", "", "\n".join(acts) if acts else "（没有工具调用记录）", "",
            "## 三、完整过程", "", "<details>", "<summary>展开自动记录的解题过程</summary>", "",
            text.replace("```", "'''"), "", "</details>", "",
            "## 四、我卡在哪 / 走了什么弯路", "",
            "## 五、下次遇到同类题先做什么", ""])

        WRITEUPS.mkdir(parents=True, exist_ok=True)
        out = WRITEUPS / (src.stem + ".wp.md")
        out.write_text(body, encoding="utf-8")
        return {"text": body, "path": str(out), "flag": flag}

    # ------------------------------------------------------------ AI 解题
    def r_solve(self, b):
        mode = b.get("mode") or "arena"
        ptype, label = None, ""
        if mode == "arena":
            data = core.current()
            at = data.get("attempt")
            if not at:
                raise core.ApiError("现在没有进行中的题")
            pb = at.get("problem") or {}
            d = core.challenge_dir()
            if not d:
                d, _, _ = core.prepare_challenge(at)
            prompt = core.problem_prompt(at)
            env_addr = (b.get("env_addr") or "").strip()
            if env_addr:
                prompt += ("\n【用户提供的环境地址】%s\n-> 先 curl -i / nc 确认可达。\n" % env_addr)
            else:
                # 容器题没有地址 = 几乎做不出来（尤其 Pwn/Web）。
                # 必须在开头就挡回去要地址，否则模型会把整轮步数花在静态分析上。
                cont = (pb.get("container") or {})
                t = (pb.get("type_label") or "").lower()
                if pb.get("container_enabled") and not cont.get("url"):
                    prompt = ("⚠️ **这道题的容器地址是空的**（container.url = []，state = %s）。\n"
                              "题型是 %s，没有远程地址几乎不可能拿到 flag：\n"
                              "  - Pwn：flag 在远端进程里，本地算偏移没意义\n"
                              "  - Web：没有目标就无从下手\n"
                              "**第一步就用 need_human**，让用户去平台点「启动容器」"
                              "并把地址贴回来。\n"
                              "在拿到地址之前：不要 objdump、不要读 libc、不要写 exploit，"
                              "那些都不会产生 flag，只会白烧步数。\n\n"
                              % (cont.get("state"), pb.get("type_label") or "未知")) + prompt
            workdir, cid = d, None
            ptype = pb.get("type_label")
            label = "id%s-%s" % (at.get("id"), ptype or "unknown")
        else:
            cid = b.get("cid") or ""
            meta = core.get_local_case(cid)
            workdir = core.local_root(cid) / "work"
            workdir.mkdir(parents=True, exist_ok=True)
            prompt = core.local_prompt(meta)
            label = (meta.get("name") or cid)[:30]

        if not ai.key_status()["set"]:
            raise core.ApiError("还没有填 DeepSeek API Key（右上角设置）")

        # conn.py 放进工作目录
        try:
            import shutil
            src = PROJ / "tools" / "conn.py"
            if src.is_file():
                shutil.copy2(str(src), str(Path(workdir) / "conn.py"))
        except Exception:
            pass

        # 预侦察
        try:
            rdir = (Path(workdir) / "raw") if mode == "arena" else Path(workdir)
            rc = core.recon(rdir)
            if rc:
                prompt += "\n\n" + rc
        except Exception:
            pass

        try:
            jid = new_job({"mode": mode, "cid": cid, "prompt": prompt,
                           "workdir": str(workdir), "ptype": ptype, "rounds": 1})
            emit = emit_to(jid)
            sess = ai.Session(workdir, prompt, mode, emit, ptype=ptype)
        except RuntimeError as e:
            raise core.ApiError(str(e))

        with LOCK:
            JOBS[jid]["session"] = sess
            JOBS[jid]["waiting"] = None
            JOBS[jid]["logpath"] = str(new_log(mode, label))

        threading.Thread(target=self._pump, args=(jid, sess, mode, cid, emit, None),
                         daemon=True).start()
        return {"job": jid}

    def _pump(self, jid, sess, mode, cid, emit, resume_text=None):
        try:
            out = sess.resume(resume_text) if resume_text is not None else sess.run()
        except Exception as e:
            emit({"type": "error", "text": str(e)})
            finish_job(jid, None, str(e))
            return

        if out.get("waiting"):
            with LOCK:
                j = JOBS.get(jid)
                if j:
                    j["waiting"] = out["waiting"]
                    j["running"] = False
            return

        ans = (out.get("answer") or "").strip()
        err = out.get("error")
        vmeta = out.get("verify") or {}

        # 兜底：模型没调 finish，但工具输出里已经出现过 flag 形状的串。
        # 这种情况照样把它捞出来交给人判断 —— 宁可给个"未验证的候选"，
        # 也别让已经到手的答案随步数一起蒸发。
        cand = (out.get("candidate") or "").strip()
        if not ans and cand:
            ans = cand
            vmeta = {"verified": False,
                     "evidence": "模型在工具输出里看到过这个串，但没来得及调用 finish 就结束了。"
                                 "请自己核对一遍再决定要不要提交。",
                     "reason": "自动兜底：从工具输出里捞回的候选"}
            err = (err + "（已从输出里兜底捞回候选，未验证）") if err else "已兜底捞回候选"

        # 第一轮失败 -> 自动换思路再来一轮
        if not ans:
            with LOCK:
                j = JOBS.get(jid) or {}
                rounds = j.get("rounds", 1)
                prompt0 = j.get("prompt") or ""
                wd = j.get("workdir")
                pt = j.get("ptype")
            # 时间闸门：第一轮已经烧掉大半预算时，不再自动开第二轮。
            # 之前 50 步 × 2 轮 = 100 次调用，经常把一小时预算吃光。
            with LOCK:
                _j2 = JOBS.get(jid) or {}
                _elapsed = time.time() - (_j2.get("started") or time.time())
            _too_slow = _elapsed > 18 * 60
            if _too_slow:
                emit({"type": "info",
                      "text": "⏱ 第一轮已用 %d 分钟，放弃自动重试（避免超时）" % int(_elapsed // 60)})
            if rounds < 2 and prompt0 and wd and not _too_slow:
                emit({"type": "info", "text": "第一轮没解出来（%s）。自动换思路开第二轮…"
                                              % (err or "步数用尽")})
                hint = (prompt0 + "\n\n【第二轮·换思路】\n"
                        "上一轮没解出来，原因：%s\n"
                        "这一次不要重复上一轮做过的事：换个方向、换个工具、换个切入点。\n"
                        "先花 1 步重新看一眼预侦察信息，再决定从哪下手。" % (err or "步数用尽"))
                try:
                    sess2 = ai.Session(wd, hint, mode, emit, ptype=pt)
                except Exception as e:
                    finish_job(jid, None, str(e))
                    return
                with LOCK:
                    JOBS[jid]["rounds"] = rounds + 1
                    JOBS[jid]["session"] = sess2
                    JOBS[jid]["running"] = True
                    JOBS[jid]["waiting"] = None
                threading.Thread(target=self._pump, args=(jid, sess2, mode, cid, emit, None),
                                 daemon=True).start()
                return

        if ans and mode == "arena" and not core.FLAG_RE.match(ans):
            m = re.search(r"\{[^}]*\}", ans)
            if m:
                ans = "NSSCTF" + m.group(0)
        if ans and mode == "local":
            try:
                core.update_local_answer(cid, ans)
            except Exception:
                pass
        # 结束状态必须写进日志区：用户常常只盯着日志面板看，
        # 只弹 toast 会让人误以为"还卡在那儿"。
        if ans:
            emit({"type": "info", "text": "✅ 结束：拿到答案 %s%s"
                                          % (ans, "（已验证）" if vmeta.get("verified") else "（未验证，请自己核）")})
        elif err:
            emit({"type": "info", "text": "❌ 结束：失败 —— %s" % err})
        else:
            emit({"type": "info", "text": "⚠️ 结束：没有结果"})
        emit({"type": "done", "answer": ans, "error": err, "verify": vmeta})
        finish_job(jid, ans or None, err, verify=vmeta)

    # ------------------------------------------------------------ 日志回流
    def r_reflux_draft(self, b):
        """从一份日志抽出可直接追加进 skills/<题型>.md 的经验条目。"""
        name = (b.get("log") or "").strip()
        ptype = (b.get("ptype") or "").strip().lower()
        if not name:
            raise core.ApiError("要指定一个日志文件")
        f = (LOGS / name).resolve()
        if LOGS.resolve() not in f.parents or not f.is_file():
            raise core.ApiError("找不到这个日志：%s" % name)
        draft = reflux.skill_draft(str(f), ptype=ptype)
        if not draft:
            raise core.ApiError("这个日志里没有可抽取的信息")
        return {"draft": draft, "ptype": ptype, "log": name}

    def r_reflux_apply(self, b):
        """用户确认后，把条目追加进 skills/<题型>.md（只追加，不改原文）。"""
        ptype = (b.get("ptype") or "").strip().lower()
        draft = (b.get("draft") or "").strip()
        if not ptype:
            raise core.ApiError("要指定题型（reverse / pwn / crypto / misc / web）")
        if not draft:
            raise core.ApiError("草稿是空的")
        out = reflux.apply_skill(ptype, draft)
        if not out:
            return {"skipped": True, "message": "这个条目已经在技能文件里了，没有重复追加"}
        return {"ok": True, "path": str(out), "message": "已追加到 %s" % out.name}

    def r_resume(self, b):
        jid = (b.get("job") or "").strip()
        text = b.get("text") or ""
        with LOCK:
            j = JOBS.get(jid)
            if not j:
                raise core.ApiError("任务不存在（页面刷新过？重新点开始解题吧）")
            sess = j.get("session")
            if not sess:
                raise core.ApiError("这个任务没有可恢复的会话")
            if j.get("running"):
                raise core.ApiError("它还在跑，等一下")
            if j.get("answer") or j.get("error"):
                raise core.ApiError("这个任务已经结束了")
            j["waiting"] = None
            j["running"] = True
            mode, cid = j.get("mode"), j.get("cid")
        emit = emit_to(jid)
        threading.Thread(target=self._pump, args=(jid, sess, mode, cid, emit, text),
                         daemon=True).start()
        return {"job": jid}

    # ------------------------------------------------------------ 本地题库
    def r_local_new(self, b):
        return {"case": core.new_local_case(b.get("name") or "未命名题目", b.get("desc") or "")}

    def r_local_upload(self, b):
        cid = b.get("cid") or ""
        name = b.get("name") or "file.bin"
        try:
            data = base64.b64decode((b.get("b64") or "").split(",", 1)[-1])
        except Exception:
            raise core.ApiError("文件内容解析失败")
        if len(data) > 40 * 1024 * 1024:
            raise core.ApiError("文件太大了（超过 40MB）")
        meta = core.upload_local_file(cid, name, data)
        return {"case": meta, "files": core.list_local_files(cid)}

    def r_local_open(self, b):
        import os
        cid = b.get("cid") or ""
        which = b.get("which") or "files"
        d = core.local_root(cid) / which
        d.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(str(d))
        return {"ok": True, "path": str(d)}

    def r_local_delete(self, b):
        import shutil
        d = core.local_root(b.get("cid") or "")
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)
        return {"ok": True}


# ---------------------------------------------------------------- 启动
class Server(ThreadingHTTPServer):
    # Windows 上 SO_REUSEADDR 会让两个进程抢同一端口却不报错，宁可让它失败
    allow_reuse_address = False
    daemon_threads = True


def already_running():
    """看看是不是已经有实例在跑（socket 短超时，别用 urllib）。"""
    import socket
    for p in range(PORT_START, PORT_START + 30):
        s = None
        try:
            s = socket.create_connection(("127.0.0.1", p), timeout=0.15)
            s.settimeout(0.6)
            s.sendall(b"GET /api/ping HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n")
            if b"wangwang" in s.recv(300):
                return p
        except Exception:
            continue
        finally:
            try:
                if s:
                    s.close()
            except Exception:
                pass
    return None


def main():
    print("正在检查是否已有实例在运行…", flush=True)
    old = already_running()
    if old:
        url = "http://127.0.0.1:%d/" % old
        print("=" * 62, flush=True)
        print("  已经在运行了：%s" % url, flush=True)
        print("  直接用它就行，不用再开一个。", flush=True)
        print("  想彻底重启：先把所有黑窗口关掉，再双击 启动.bat", flush=True)
        print("=" * 62, flush=True)
        if "--no-browser" not in sys.argv:
            webbrowser.open(url)
        time.sleep(2)
        return 0

    port = PORT_START
    httpd = None
    for p in range(PORT_START, PORT_START + 30):
        try:
            httpd = Server(("127.0.0.1", p), Handler)
            port = p
            break
        except OSError:
            continue
    if httpd is None:
        print("找不到可用端口，关掉一些程序再试")
        input("按回车退出")
        return 1

    url = "http://127.0.0.1:%d/" % port
    print("=" * 62, flush=True)
    print("  汪汪队立大功   Wangwang Team Saves the Day", flush=True)
    print("=" * 62, flush=True)
    print("  地址: %s" % url, flush=True)
    print("  关掉这个窗口 = 关掉程序", flush=True)
    print("=" * 62, flush=True)
    if "--no-browser" not in sys.argv:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    return 0


if __name__ == "__main__":
    sys.exit(main())
