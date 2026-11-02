#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
vmops.py - Kali 虚拟机操作小工具（零依赖，只用标准库）

给 agent 用：自动找虚拟机 IP / 远程跑命令 / 传文件 / 跑 ELF 并抓输出。
WSL 用不了的时候，这条链路就是 Windows 主机上唯一的 Linux 执行环境。

前置（一次性）：
    # 虚拟机里
    sudo systemctl start ssh && sudo systemctl enable ssh
    mkdir -p ~/.ssh && echo "<公钥>" >> ~/.ssh/authorized_keys
    chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys

    # Windows 侧需要有私钥：%USERPROFILE%\\.ssh\\id_kali_ww

命令行自测：
    python vmops.py find                 # 自动扫描并记住虚拟机 IP
    python vmops.py ip                   # 显示当前记住的 IP
    python vmops.py run "uname -a"       # 远程跑一条命令
    python vmops.py put 本地文件 /root/  # 上传
    python vmops.py x 本地ELF 参数...    # 上传 + chmod +x + 运行
"""
import os
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

KEY = Path(os.path.expanduser("~")) / ".ssh" / "id_kali_ww"
CACHE = Path(__file__).resolve().parent.parent / "data" / "vm_ip.txt"
SSH_OPTS = [
    "-o", "BatchMode=yes",
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=NUL",
    "-o", "ConnectTimeout=6",
]


def _key_args():
    return ["-i", str(KEY)] + SSH_OPTS if KEY.exists() else SSH_OPTS


def local_ips():
    """本机所有 IPv4 地址"""
    out = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            out.add(info[4][0])
    except Exception:
        pass
    # 兜底：连一个外部地址看出口网卡
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        out.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    return sorted(out)


def _port_open(ip, port=22, timeout=0.4):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((ip, port))
        return True
    except Exception:
        return False
    finally:
        s.close()


def find_vm(timeout=0.3, workers=64):
    """扫本机所在的所有 /24 网段，找开了 22 端口的主机"""
    cands = []
    for ip in local_ips():
        if not ip.startswith(("192.168.", "10.", "172.")):
            continue
        prefix = ".".join(ip.split(".")[:3])
        cands += ["%s.%d" % (prefix, i) for i in range(1, 255)]

    if not cands:
        return None

    hits = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_port_open, ip, 22, timeout): ip for ip in cands}
        for f in futs:
            if f.result():
                hits.append(futs[f])

    if not hits:
        return None

    # 优先已经有 known_hosts 的（这次连过的），否则取第一个
    for ip in sorted(hits):
        if ip == get_ip():
            return ip
    return sorted(hits)[0]


def get_ip():
    try:
        return CACHE.read_text(encoding="utf-8").strip() or None
    except Exception:
        return None


def set_ip(ip):
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(ip, encoding="utf-8")


def ensure_ip():
    ip = get_ip()
    if ip and _port_open(ip, 22, 0.6):
        return ip
    ip = find_vm()
    if ip:
        set_ip(ip)
    return ip


def run(cmd, ip=None, timeout=120, cwd=None):
    """远程执行命令，返回 (returncode, stdout, stderr)"""
    ip = ip or ensure_ip()
    if not ip:
        return 255, "", "找不到虚拟机（22 端口扫不到）。检查虚拟机是否开机、网络是否为 NAT、sshd 是否启动。"
    full = ["ssh"] + _key_args() + ["root@%s" % ip, cmd]
    try:
        p = subprocess.run(full, capture_output=True, timeout=timeout)
        return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        return 124, "", "命令超时（%ds）" % timeout
    except Exception as e:
        return 255, "", str(e)


def put(local, remote="~/", ip=None, timeout=180):
    ip = ip or ensure_ip()
    if not ip:
        return 255, "找不到虚拟机"
    full = ["scp"] + _key_args() + [str(local), "root@%s:%s" % (ip, remote)]
    try:
        p = subprocess.run(full, capture_output=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr).decode("utf-8", "replace")
    except Exception as e:
        return 255, str(e)


def get(remote, local=".", ip=None, timeout=180):
    ip = ip or ensure_ip()
    if not ip:
        return 255, "找不到虚拟机"
    full = ["scp"] + _key_args() + ["root@%s:%s" % (ip, remote), str(local)]
    try:
        p = subprocess.run(full, capture_output=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr).decode("utf-8", "replace")
    except Exception as e:
        return 255, str(e)


def run_elf(local_path, args="", stdin_data=None, ip=None, timeout=120):
    """上传一个 ELF、chmod +x、运行，返回输出文本

    这是 reverse/pwn 最常用的一步：本地写脚本编译，丢到虚拟机里跑。
    """
    local_path = Path(local_path).resolve()
    if not local_path.exists():
        return "文件不存在: %s" % local_path
    rc, msg = put(local_path, "/root/", ip)
    if rc != 0:
        return "上传失败: %s" % msg

    remote = "/root/%s" % local_path.name
    cmd = "chmod +x %s && %s %s" % (remote, remote, args)
    if stdin_data is not None:
        cmd = "printf %s | %s" % (_shell_quote(stdin_data), cmd)

    rc, out, err = run(cmd, ip=ip, timeout=timeout)
    return out + (("\n[stderr] " + err) if err.strip() else "")


def _shell_quote(s):
    return "'" + str(s).replace("'", "'\\''") + "'"


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "ip"

    if cmd == "find":
        print("本机 IP:", local_ips())
        print("扫描中…")
        ip = find_vm()
        if ip:
            set_ip(ip)
            print("找到虚拟机:", ip)
            rc, out, err = run("uname -a; id", ip=ip)
            print("自检:", out.strip() or err.strip())
        else:
            print("没找到。检查：虚拟机开机？网络 NAT？sshd 启动？公钥装好？")

    elif cmd == "ip":
        ip = ensure_ip()
        print(ip or "（没有）")

    elif cmd == "run":
        rc, out, err = run(" ".join(sys.argv[2:]))
        print(out, end="")
        if err.strip():
            print("[stderr]", err, file=sys.stderr)
        sys.exit(rc)

    elif cmd == "put":
        print(put(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "/root/"))

    elif cmd == "get":
        print(get(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "."))

    elif cmd == "x":
        print(run_elf(sys.argv[2], " ".join(sys.argv[3:])))

    else:
        print(__doc__)
