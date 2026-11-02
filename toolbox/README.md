# toolbox · 随手能用的真文件

这里的文件是**可以直接 `cp` 走用的**，不是文档里的代码块。
技能文档（`skills/*.md`）里提到需要什么，就从这里复制。

| 文件 | 用途 | 怎么用 |
|---|---|---|
| `launcher_fakeproc.c` | 伪造 `/proc/self/status` 骗过反调试 | 见下方 |
| `gdb_dump.gdb` | gdb 批量断点 dump 中间状态的模板 | 改地址直接用 |
| `gdb_call.gdb` | 在 gdb 里直接调用程序内部函数 | 省掉还原复杂生成 |
| `brute_template.c` | 多线程哈希/原像爆破模板 | 改 ALPHA/TARGET |
| `verify_elf.py` | 一次看清 ELF 的类型/保护/符号/可疑字符串 | `python verify_elf.py 文件` |
| `patch_bytes.py` | 安全地改二进制（先断言原字节） | `python patch_bytes.py` |

---

## 1. launcher_fakeproc.c —— 绕反调试

很多 crackme 会读 `/proc/self/status` 看 `TracerPid`，或者调 `ptrace(PTRACE_TRACEME)`。
直接改二进制会破坏自校验，所以用**命名空间伪造**。

```bash
# 在 Linux（虚拟机）里编译
gcc -O2 -o launcher launcher_fakeproc.c
./launcher ./目标程序
```

**注意**：launcher 里**故意不调** `ptrace(PTRACE_TRACEME)`——没有 tracer 时进程会在
exec 后停在 SIGTRAP 里挂死。

## 2. gdb 两个模板

```bash
# dump 中间状态
gdb -q -batch -x gdb_dump.gdb ./目标

# 直接调用内部函数（比如生成函数）
gdb -q -batch -x gdb_call.gdb ./目标
```

## 3. brute_template.c

```bash
gcc -O3 -pthread -o brute brute_template.c -lcrypto
./brute
```

32 核实测约 3000 万 hash/s。

## 4. verify_elf.py

```bash
python toolbox/verify_elf.py challenges/xxx/raw/附件
```

一次输出：ELF 类型、架构、是否 PIE、符号表、高信号字符串、可疑的大块数据段。

## 5. patch_bytes.py

改二进制**必须先断言原字节**，否则地址算错会静默改坏别处。

```bash
python toolbox/patch_bytes.py 原文件 输出文件 0x402f68 90909090
```
