#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
conn.py —— CTF 远程连接小工具（零依赖，Windows 也能用）

放进工作目录，AI 和人都能直接 import，不用每次手拼 socket。

用法：
    import conn

    c = conn.Conn("node5.anna.nssnode.cn", 21330)
    print(c.recv(1.0))            # 先看 banner（读 1 秒内的输出）
    c.sendline(b"3", b"0")        # 发两行：3 和 0
    print(c.recv(0.5))

    c.sendline(b"1")
    print(c.recv_until(b"> "))    # 读到菜单提示符

    c.close()

常用的打包函数：
    conn.p32(x) / conn.p64(x) / conn.u32(b) / conn.u64(b)
"""
import socket
import struct
import time

p32 = lambda x: struct.pack("<I", x & 0xFFFFFFFF)
p64 = lambda x: struct.pack("<Q", x & 0xFFFFFFFFFFFFFFFF)
u32 = lambda b: struct.unpack("<I", (bytes(b) + b"\x00" * 4)[:4])[0]
u64 = lambda b: struct.unpack("<Q", (bytes(b) + b"\x00" * 8)[:8])[0]


class Conn:
    def __init__(self, host, port, timeout=10.0):
        self.host = host
        self.port = int(port)
        self.timeout = timeout
        self.buf = b""
        self.s = None
        self.connect()

    # ---------------- 连接 ----------------
    def connect(self):
        self.s = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self.s.settimeout(0.3)
        return self

    def reconnect(self):
        try:
            if self.s:
                self.s.close()
        except Exception:
            pass
        self.buf = b""
        return self.connect()

    def close(self):
        try:
            if self.s:
                self.s.close()
        except Exception:
            pass
        self.s = None

    # ---------------- 发送 ----------------
    def send(self, data):
        if isinstance(data, str):
            data = data.encode()
        self.s.sendall(data)
        return self

    def sendline(self, *parts):
        """sendline(b'3', b'0')  =>  发送 '3\\n0\\n'"""
        if not parts:
            self.s.sendall(b"\n")
            return self
        data = b""
        for p in parts:
            data += (p.encode() if isinstance(p, str) else p) + b"\n"
        self.s.sendall(data)
        return self

    # ---------------- 接收 ----------------
    def _try_recv(self):
        try:
            d = self.s.recv(65536)
            if not d:
                return None          # 对端关闭
            self.buf += d
            return d
        except socket.timeout:
            return b""
        except Exception:
            return None

    def recv(self, timeout=0.6):
        """读到「安静 timeout 秒」为止，返回这段时间收到的全部数据"""
        end = time.time() + timeout
        while time.time() < end:
            d = self._try_recv()
            if d is None:
                break
            if d:                        # 只有真的收到数据才续期，否则一直循环
                end = time.time() + 0.15
        out, self.buf = self.buf, b""
        return out

    def recv_until(self, token, timeout=5.0):
        """读到「出现」token 为止（包含在返回里）。

        ⚠️ token 会匹配到任意位置：等菜单提示符 `> ` 时，
        输出里如果出现 `flag -> xxx`，`-> ` 里的 `> ` 会提前截断。
        等提示符请改用 recv_prompt()。
        """
        if isinstance(token, str):
            token = token.encode()
        end = time.time() + timeout
        while token not in self.buf and time.time() < end:
            if self._try_recv() is None:
                break
        if token in self.buf:
            i = self.buf.index(token) + len(token)
            out, self.buf = self.buf[:i], self.buf[i:]
            return out
        return self.recv(0.2)

    def recv_prompt(self, token=b"> ", timeout=5.0):
        """等到「缓冲区以 token 结尾」为止 —— 等交互提示符用这个。

        比 recv_until 稳：不会因为正文里出现同样的字符而提前返回。
        """
        if isinstance(token, str):
            token = token.encode()
        end = time.time() + timeout
        while time.time() < end:
            if self._try_recv() is None:
                break
            if self.buf.endswith(token):
                break
        out, self.buf = self.buf, b""
        return out

    def recv_all(self, timeout=1.0):
        return self.recv(timeout)


if __name__ == "__main__":
    # 命令行快速自测： python conn.py HOST PORT
    import sys
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(0)
    c = Conn(sys.argv[1], sys.argv[2])
    print(c.recv(1.5).decode("utf-8", "replace"))
    c.close()
