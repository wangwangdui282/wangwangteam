# Skill · Reverse 打法

## 1. 这台机器能干什么

- **有**：`objdump`（GNU binutils，能反汇编 Linux ELF）、`strings`、`python`、`node`
- **没有**：Linux 运行环境、gdb、IDA、Ghidra
- 结论：**静态分析完全够用**，不能运行不影响读逻辑。

## 2. 标准流程

```bash
# 1. 确认类型
objdump -h 文件          # 第一行 file format

# 2. 抓字符串（最快建立直觉）
strings -n 4 文件        # 重点看：错误提示、菜单、printf 格式串、可疑路径

# 3. 列函数
objdump -d -M intel --no-show-raw-insn 文件 > dis.txt
# 然后找函数名：Select-String ">:" dis.txt

# 4. 看数据段初值
objdump -s -j .data 文件

# 5. 看符号表（如果没 strip）
objdump -t 文件 | findstr "F .text"
```

## 3. 从哪开始读

**不要**从 `_start` 一行行读。按这个顺序：

1. 先看 `strings` 的**输出顺序**——编译器通常把字符串按代码出现顺序排，
   从字符串反推代码在哪一段是常规手法
2. 找 `main`：没 strip 直接有；strip 过就从 `__libc_start_main` 的第一个参数反查
3. **找比较点**：`cmp` / `strcmp` / `memcmp` 附近的代码，通常就是校验逻辑
4. 从**输出**倒推，比从输入正推快

## 4. Python 打包的程序

看到 `pyinstaller` / `_MEIPASS` / `PYZ-00` 字样：

1. 先 `strings` 确认是 pyinstaller
2. 用 `pyinstxtractor` 解包（可以 `pip install`，也可以手写脚本读 PKG 结构）
3. 找到 `main` 的 `.pyc`（可能缺 magic number，要补 `\x55\x0d\x0d\x0a` 这类头部）
4. 反编译：`uncompyle6` / `decompyle3` / 在线工具

**这条路比硬啃二进制快十倍。**

## 5. 虚拟机壳 / 字节码题

代码里有一大坨看不懂的字节数组 + 一个分发循环：

1. **找分发器**：一个大 `while`，里面 `switch` 或函数指针表
2. **抠 handler**：每个 opcode 对应哪个分支、它对栈做了什么
3. **写 Python 模拟器**：把 handler 语义翻译成 Python
4. **差分验证**：拿原程序（能跑的话）和自己的模拟器喂同样输入，比对输出
5. **反解**：知道变换链之后倒推输入

## 6. 花指令 / 反调试

- `jmp` 到一条指令的**中间**（花指令）：objdump 反汇编错位时，从这个地址重新反汇编
- 反调试：找 `ptrace` / `getppid` / `rdtsc`，patch 掉

## 7. 什么时候停

- 4 步内还没定位到"校验点" → 回去重新看 strings，可能方向选错了
- 大二进制（>5MB）不要全量反汇编，先 grep 关键字定位
- 真跑不起来的题，**明确在 finish 里说"只能静态分析，逻辑我已还原但无法验证"**

---

## 8. 远程 Linux 执行环境（Windows 主机的最佳解法）

Windows 上没有 Linux 运行时不是死路：**开一台 Kali 虚拟机 + 打通 SSH**，就能远程跑 ELF、
gdb 调试、dump 内存。这一步是 reverse/pwn 的质变，优先级高于任何静态技巧。

一次性配置：

```powershell
# 1) 虚拟机里启动 sshd 并装公钥
sudo systemctl start ssh && sudo systemctl enable ssh
mkdir -p ~/.ssh
echo "<你的公钥>" >> ~/.ssh/authorized_keys
chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys

# 2) Windows 侧免密连接（注意 UserKnownHostsFile=NUL，否则沙箱会拦 known_hosts 写入）
ssh -i $env:USERPROFILE\.ssh\id_kali_ww -o BatchMode=yes `
    -o StrictHostKeyChecking=no -o UserKnownHostsFile=NUL root@<VM_IP> "uname -a"

# 3) 传文件
scp -i $env:USERPROFILE\.ssh\id_kali_ww <本地文件> root@<VM_IP>:/root/
```

**踩过的坑**

- 本地 `Test-Connection` / ICMP 可能被策略拦，**直接试 TCP 端口**判断通不通
- SSH 报 `Permission denied (publickey,password)` = 公钥没装进去
- 端口 `Connection refused` = sshd 没起；`timed out` = 网络/防火墙问题
- PowerShell 的 here-string 是 **CRLF**，直接 pipe 给 `bash -s` 会带 `\r` 报错
  → 先 `-replace "\`r", ""`，或者本地写成 .sh 再 `scp` 过去执行

---

## 9. gdb 批量脚本（无交互、可复现）

```bash
cat > /tmp/t.gdb <<'GDBEOF'
set pagination off
set confirm off
b *0x402d22
commands
silent
printf "STATE gacc=%016lx\n", $r8
x/16xb $rsp+0x10
c
end
run < /tmp/in
quit
GDBEOF
gdb -q -batch -x /tmp/t.gdb ./binary
```

**关键技巧**

- 在**每条关键指令后**打断点 dump 中间状态，是"验证自己实现"的唯一可靠办法
- `call ((int(*)(int))0x402747)(0)` 可以在 gdb 里**直接调用程序内部函数**，
  省掉还原复杂生成的功夫
- 遇到程序起不来，先看它 detect 了什么（`strings` 里的报错文案）

---

## 10. 反调试：伪造 /proc（不改二进制）

改二进制会改变校验和/自校验逻辑，所以优先用**命名空间伪造**：

```c
#define _GNU_SOURCE
#include <sched.h>
#include <sys/mount.h>
int main(int argc, char **argv) {
    unshare(CLONE_NEWNS);
    mount(NULL, "/", NULL, MS_REC | MS_PRIVATE, NULL);
    mkdir("/tmp/fakeproc", 0755);
    mount("tmpfs", "/tmp/fakeproc", "tmpfs", 0, "");
    mkdir("/tmp/fakeproc/self", 0755);
    FILE *f = fopen("/tmp/fakeproc/self/status", "w");
    fprintf(f, "Name:\tprog\nTracerPid:\t0\nThreads:\t1\n");
    fclose(f);
    mount("/tmp/fakeproc", "/proc", NULL, MS_BIND, NULL);
    execv(argv[1], argv + 1);
}
```

- 这样 `/proc/self/status` 里的 `TracerPid` 恒为 0，骗过 ptrace 检测
- **注意**：`ptrace(PTRACE_TRACEME)` 不要在 launcher 里调 —— 没有 tracer 时进程会在
  exec 后停在 SIGTRAP 里挂死（这个坑踩过）
- 信号类自检（`sigaction` 装 SIGSEGV/SIGTRAP handler）不能用 `ret` 硬顶，
  会破坏信号帧；要么用真实 handler，要么在 gdb 里 `handle SIGxxx nostop noprint pass`

---

## 11. 改二进制的正确姿势

```python
data = bytearray(open("bin","rb").read())
off = 0x402f68 - 0x400000        # VMA → 文件偏移（ELF64 常见映射，先用 objdump -h 确认）
assert data[off:off+4] == bytes.fromhex("85c07524")   # 先断言原字节
data[off:off+4] = bytes.fromhex("90909090")           # 再改
open("bin_patched","wb").write(data)
```

- **永远先 assert 原字节**，否则地址算错会改坏别处
- `movaps` / `movdqa` 要求 16 字节对齐，改地址或 dump 时注意段错误
- 改完之后**自己算一遍新校验和**（如果程序有自校验）

---

## 12. 自写实现 → 逐字节对齐（最重要的一条）

自己写模拟器/解密器之后，**必须**和原程序逐阶段对比，不然后面全是错的：

1. 在二进制里每个阶段后面打断点，dump 内存
2. 用同样的输入跑自己的实现，打印同样的中间量
3. **逐字节比对**，不一致就停下来定位
4. 全对之后再做后续（爆破、求逆、构造输入）

**演练记录**：靠这个办法把一个白盒 AES 的
`FNV → G 函数 → 4 轮 → 16×T-box 塔 → 3000 万次累加器 → 输出混合`
全部对齐了。花的每一个小时都值。

---

## 13. 什么时候必须止损

- 需要**完整求逆**一个 128 位以上状态、且没有逐字节剪枝空间的构造
- 反调试 + 自校验 + 信号自检叠了三层以上
- 投入时间已经远超题目分值

止损时给出**「卡在哪一步、为什么」**的明确结论，比含糊的"没做出来"有价值得多。

---

## 14. Android APK / ARM64 逆向 ★

### 第一步永远是看文件类型

```python
import zipfile
z = zipfile.ZipFile('x.apk')
for n in z.namelist():
    if n.endswith(('.dex', '.so')):
        print(n, z.getinfo(n).file_size)
```

APK 是 zip。重点看两样：

- **`classes*.dex`** → Java 层（校验逻辑常常就在这，**优先看**）
- **`lib/<abi>/lib*.so`** → native 层（只有 Java 层是 `native` 声明时才需要）

### 第二步：先在 so 的字符串里找答案（30 秒见效）

**别一上来就反汇编。** flag 常常直接躺在 `.rodata` / `.data` 里：

```python
d = open('libnative-lib.so','rb').read()
import re
for m in re.finditer(rb'[\x20-\x7e]{5,}', d):
    print(hex(m.start()), m.group().decode('latin1'))
```

真实案例（强网杯 12209）：直接 dump `.data` 前 64 字节就看到了

```
00 00 00 00 00 00 00 00 74 68 31 73 31 73 74 68 33 6b 32 37 ...
                       t  h  1  s  1  s  t  h  3  k  2  7
```

→ `th1s1sth3k27`

**这一个动作比反汇编 12KB 的 OLLVM 函数快一百倍。**

### 第三步：找 native 方法名，定位入口

```bash
objdump -T libxxx.so | grep Java_
```

输出形如 `Java_com_example_rectf_MainActivity_checkflag`，
从名字就能推出 Java 里的包名、类名、方法名。

### ⚠️ 绝对不要自己写反汇编器

**这台机器上已经装好了：**

| 工具 | 位置 / 用法 |
|---|---|
| **capstone** | `import capstone` ← Python 库，已装 5.0.7 |
| **objdump** | `D:\w64devkit\bin\objdump.exe -d 文件` |
| Python 解析 ELF | 自己读 `.text` 段就行 |

capstone 用法（ARM64）：

```python
import capstone
d = open('lib.so','rb').read()
TEXT_VA, TEXT_OFF, TEXT_SZ = 0x7160, 0x7160, 0x1dfd0   # 从 objdump -h 拿
md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
md.detail = True                     # ← 不设这个 operands 是空的！
for ins in md.disasm(d[TEXT_OFF:TEXT_OFF+TEXT_SZ], TEXT_VA):
    print(hex(ins.address), ins.mnemonic, ins.op_str)
```

**实测：30708 条指令全部正确解码。**
如果你的反汇编器输出 `???`，九成是你自己写的那个不完整，**换 capstone，别去补它**。

### 第四步：OLLVM 平坦化的正确打法

看到这种模式就是 OLLVM 控制流平坦化：

- 一个 `while` 循环 + `switch(state)`
- 每个 case 是一堆 `movz`/`movk` 拼出的 32 位魔数比较
- 真实的运算被拆散在各个「状态块」里

**不要手写反汇编器去还原它。** 按性价比排序：

| 方法 | 说明 |
|---|---|
| **先搜字符串/常量** | 很多题的答案根本不在这段代码里 |
| Ghidra + D810 插件 | 自动去平坦化，最好的效果 |
| angr / 符号执行 | 有明确输入输出时可行 |
| **动态跑**（qemu-aarch64） | 能跑就 trace，别硬看 |
| 手动还原 | 最后手段，非常耗时 |

### 第五步：什么情况才真需要啃 native

只有同时满足这三条：

1. Java 层是 `native` 声明，逻辑全在 so
2. so 里**没有**可读的字符串/常量线索
3. 无法动态执行（没有 ARM 环境 / 没有 qemu）

**不满足就先去干别的。**

### 环境清单（Windows 主机）

| 需要 | 有什么 |
|---|---|
| ARM64 反汇编 | ✅ capstone（Python）、✅ objdump（w64devkit） |
| APK 解包 | ✅ Python zipfile |
| dex 反编译 | ⚠️ 没有 jadx，但可以用 `strings` 先扫 dex |
| 跑 ARM64 | ❌ 需要 Kali 虚拟机 + `qemu-user-aarch64` |

### 真实事故（强网杯 12209）

模型花了 50 步：

- ❌ 直接跳到 .so 反汇编
- ❌ 发现 objdump 输出 `???`，**决定自己写一个完整 AArch64 反汇编器**
- ❌ 写了 20+ 步还没写完

**正确答案在 `.data` 段偏移 0x8，dump 64 字节就看到了。**

> **教训：先花 30 秒 dump 字符串，再决定要不要反汇编。
> 反汇编器不是你要写的东西，是你要用的东西。**


### ⚠️ 搜到字符串不等于找到答案（血泪）

**dump 出字符串之后，必须确认它被谁引用。**

真实事故（强网杯 12209）：`.data` 里有一段 `th1s1sth3k27`，看起来非常像 flag，
直接拿去交——**错了**。因为 `checkflag` 从头到尾**没有引用过它**，它是死数据。

正确的做法：

```python
# 用 capstone 找 adrp + add 组合，算出所有被引用的地址
for i, ins in enumerate(il):
    if ins.mnemonic != 'adrp': continue
    page = ins.operands[1].imm
    for j in range(i+1, min(i+5, len(il))):
        x = il[j]
        if x.mnemonic == 'add' and x.op_str.startswith(reg + ','):
            target = page + int(x.op_str.split(',')[2].strip()[1:], 16)
            print('被引用:', hex(target))
```

然后拿这些地址和你的字符串地址比对：

- **被引用** → 大概率是目标
- **没被引用** → 死数据，误导你的

那次真正被引用的是 `.rodata` 的 `qihainanami`（`0x9788` 处的 `adrp+add`）
以及紧随其后的填充数据，`.data` 那段完全没人用。

> **规则：字符串搜索是"找线索"，不是"得答案"。
> 线索必须通过"被谁引用"这一步才能变成结论。**
