#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
winops.py - Windows 桌面自动化小工具（零依赖，只用 ctypes）

给 agent 用：启动程序 / 找窗口 / 发按键 / 看窗口标题 / 看新文件
没法"看画面"，但很多题的答案会落在文件、窗口标题或剪贴板里。

命令行自测：
    python winops.py titles
    python winops.py run  程序路径
    python winops.py keys ggggbbbb
"""
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time
from pathlib import Path

user32 = ctypes.windll.user32
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]


def windows(only_visible=True):
    """返回 [(hwnd, title), ...]"""
    out = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def _cb(hwnd, _):
        try:
            if not only_visible or user32.IsWindowVisible(hwnd):
                n = user32.GetWindowTextLengthW(hwnd)
                if n > 0:
                    buf = ctypes.create_unicode_buffer(n + 1)
                    user32.GetWindowTextW(hwnd, buf, n + 1)
                    out.append((hwnd, buf.value))
        except Exception:
            pass
        return True

    user32.EnumWindows(_cb, 0)
    return out


def find_window(keyword):
    kw = keyword.lower()
    for hwnd, title in windows():
        if kw in title.lower():
            return hwnd, title
    return None, None


def focus(keyword):
    hwnd, title = find_window(keyword)
    if not hwnd:
        return None, None
    try:
        user32.ShowWindow(hwnd, 9)      # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.3)
    except Exception:
        pass
    return hwnd, title


def send_text(text, delay=0.06):
    """按字符发按键（只支持可见 ASCII）"""
    for ch in text:
        vk = user32.VkKeyScanW(ord(ch))
        if vk == -1:
            continue
        code = vk & 0xFF
        shift = (vk >> 8) & 0xFF
        if shift & 1:
            user32.keybd_event(0x10, 0, 0, 0)     # Shift down
        user32.keybd_event(code, 0, 0, 0)
        time.sleep(0.01)
        user32.keybd_event(code, 0, 2, 0)
        if shift & 1:
            user32.keybd_event(0x10, 0, 2, 0)     # Shift up
        time.sleep(delay)


def launch(path, wait=6.0):
    path = Path(path).resolve()
    p = subprocess.Popen([str(path)], cwd=str(path.parent))
    time.sleep(wait)
    return p


def snapshot(dirpath, limit=12):
    """列出目录里最近改动的文件"""
    try:
        files = [f for f in Path(dirpath).rglob("*") if f.is_file()]
        files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
        return ["%s  %dB  %s" % (f.name, f.stat().st_size,
                                 time.strftime("%H:%M:%S", time.localtime(f.stat().st_mtime)))
                for f in files[:limit]]
    except Exception as e:
        return ["(读取失败 %s)" % e]


def get_clipboard():
    try:
        user32.OpenClipboard(None)
        h = user32.GetClipboardData(13)      # CF_UNICODETEXT
        if not h:
            user32.CloseClipboard()
            return ""
        p = ctypes.windll.kernel32.GlobalLock(h)
        text = ctypes.wstring_at(p)
        ctypes.windll.kernel32.GlobalUnlock(h)
        user32.CloseClipboard()
        return text
    except Exception:
        return ""


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "titles"
    if cmd == "titles":
        for hwnd, title in windows():
            print("%s  %s" % (hwnd, title))
    elif cmd == "run":
        p = launch(sys.argv[2])
        print("pid", p.pid)
        for hwnd, title in windows():
            print("  窗口:", title)
    elif cmd == "keys":
        focus(sys.argv[2]) if len(sys.argv) > 2 else None
        send_text(sys.argv[-1])
        print("已发送")
    else:
        print(__doc__)
