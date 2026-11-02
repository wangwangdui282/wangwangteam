# Skill · Misc 打法

## 1. 先判断"它真实是什么"

这台机器**没有 `file` 命令**，用 Python 自己读魔数：

```python
from pathlib import Path
head = Path("文件名").read_bytes()[:16]
print(head.hex(" "))

# 常见魔数
# 7f 45 4c 46   ELF
# 50 4b 03 04   ZIP
# 89 50 4e 47   PNG
# ff d8 ff      JPEG
# 1f 8b         gzip
# 4d 5a         PE(Windows)
# 52 61 72 21   RAR
```

**别信扩展名**——`.txt` 可能是图片，`.jpg` 可能是压缩包。

## 2. 编码套娃（最常见的一类）

一层层试，**别只试一个就放弃**：

| 顺序 | 方法 |
|---|---|
| 1 | base64 解码 |
| 2 | hex（`bytes.fromhex`） |
| 3 | ROT13 / 凯撒 |
| 4 | URL 编码 |
| 5 | 摩斯 / 培根 / 栅栏 |
| 6 | 再来一轮 base64（套娃很常见） |

```python
import base64, codecs
s = "xxxx"
print(base64.b64decode(s + "=" * (-len(s) % 4)))
print(bytes.fromhex(s))
print(codecs.decode(s, "rot13"))
```

**判断依据**：解出来如果能读（可见 ASCII / 中文字），就继续下一层；否则换一个。

## 3. 图片题

按这个顺序：

1. **看尾部**：很多题把数据直接追加在文件末尾
   ```python
   data = open("a.png","rb").read()
   print(data[-200:])            # 看有没有明文
   print(data.rfind(b"IEND"))    # PNG 的 IEND 之后就是追加内容
   ```
2. **看元数据**：`exiftool` 装了就用，没装就自己找 `tEXt` / `comment` 块
3. **看 LSB**：像素最低位藏数据，用 Pillow 读像素
4. **高度被改**：PNG 的 IHDR 里宽度/高度被改小，CRC 会对不上，改回去

## 4. 压缩包题

- **伪加密**：ZIP 头里有个 flag 位，改掉就不用密码（可以在 Python 里改字节）
- **文件名的提示**：`zip.namelist()` 看看有没有奇怪的名字
- **明文攻击**：知道某个文件内容时，可以反推密钥
- **弱口令**：`python -c "import zipfile; zipfile.ZipFile('a.zip').extractall(pwd=b'123456')"`

先 `zipfile.ZipFile(...).namelist()` 把内容列出来，再决定怎么做。

## 5. 流量包（pcap）

- 有 `tshark` 就直接用：`tshark -r x.pcap -Y "http" -T fields -e http.request.uri`
- 没有 tshark：用 Python 读，先看长度分布，找异常大的包
- 常见套路：导出 HTTP 对象、找 base64、找被切的文件

## 6. 取证 / 磁盘镜像

1. 先看有没有分区表 / 文件系统类型
2. 找被删除的文件（未分配空间里搜魔数）
3. 关键字搜索：`flag`、`NSSCTF`、`password`

## 7. 什么时候停

Misc 题线索通常很明确。**如果试完上面 6 类都不对**，
说明题面里还有没注意到的东西——回去重读 `content`，或者用 `need_human` 让用户看看平台有没有额外描述。

---

## 8. 思考方式：Misc 是"分类题"

Misc 没有统一的数学结构，但有**统一的分类流程**。先归类，再动手：

```
拿到附件
  ↓
读魔数 / 看大小 / 算熵
  ↓
它的主类别是什么？
  ├─ 纯文本      → 编码套娃（§2）
  ├─ 图片        → 隐写（§3）
  ├─ 压缩包      → 包结构/伪加密/明文攻击（§4）
  ├─ 流量        → 协议分析（§5）
  ├─ 磁盘/内存镜像 → 取证（§6）
  └─ 可执行/脚本  → 转 reverse
```

**分类错了，后面全白做。** 所以第一步永远是"它真实是什么"。

## 9. 一次性探针，不要一层层试

**最浪费时间的做法**：base64 解一次，看看不像，再试 hex，再看看不像……

**正确做法**：写一个脚本，一次把所有常见解码都跑一遍，把"长得像明文"的结果挑出来。

```python
# -*- coding: utf-8 -*-
import base64, binascii, codecs, urllib.parse, gzip, zlib

def looks_text(b):
    if isinstance(b, str): b = b.encode('latin1','ignore')
    if not b: return False
    ok = sum(1 for c in b if 32 <= c < 127 or c in (9,10,13))
    return ok / len(b) > 0.85

def try_all(s):
    out = []
    s = s.strip()
    cands = []
    try: cands.append(("b64", base64.b64decode(s + "="*(-len(s)%4), validate=False)))
    except Exception: pass
    try: cands.append(("b32", base64.b32decode(s + "="*(-len(s)%8))))
    except Exception: pass
    try: cands.append(("b85", base64.b85decode(s)))
    except Exception: pass
    try: cands.append(("hex", binascii.unhexlify(s)))
    except Exception: pass
    try: cands.append(("url", urllib.parse.unquote(s)))
    except Exception: pass
    try: cands.append(("rot13", codecs.decode(s, "rot13")))
    except Exception: pass
    for name, v in cands:
        if isinstance(v, bytes) and looks_text(v):
            out.append((name, v[:200]))
        elif isinstance(v, str) and v != s:
            out.append((name, v[:200]))
    return out

s = open("附件").read()
for i in range(6):                      # 最多剥 6 层
    hits = try_all(s)
    if not hits: break
    print("第%d层 -> %s" % (i+1, [h[0] for h in hits]))
    name, s = hits[0]
    print("   ", s)
```

**一次运行 = 一次探针**，把"哪一层有效"直接看明白。

## 10. 判断"还有没有下一层"的三个指标

| 指标 | 含义 |
|---|---|
| **全部可打印 ASCII** | 可能是明文，也可能还能再解一层 |
| **出现 `{` `}`** | 极可能是 flag，先当答案 |
| **长度是 4 的倍数 / 全是 hex** | 大概率还有一层 |

反过来，出现大量不可打印字节（熵高）→ 说明这条路走错了，**不要再往上叠解码**。

## 11. 搜索的策略

文件里找东西，**先搜再读**，不要从头读一遍：

```python
data = open("x.bin","rb").read()
for kw in (b"flag", b"NSSCTF", b"GWHT", b"password", b"key", b"PK\x03\x04"):
    i = data.find(kw)
    if i >= 0:
        print(kw, "@", hex(i), data[max(0,i-40):i+120])
```

**`find` 比"从头遍历"快一百倍**，而且能直接定位上下文。

## 12. 什么时候止损

- 试完 6 类主类别都不对 → 回去重读题面（关键词、hint、附件名）
- 套娃剥了 6 层还没有明文 → 大概率一开始就解错了方向
- 花的时间超过题目其他部分的总和 → 换题

**Misc 的特点是：对了很快，不对就是完全不对。**
如果一条路走了 10 步还没看到"越来越像 flag"的迹象，那就是错的。


---

## 13. 流量分析（pcap）：从"看不懂"到"有答案"的固定流程 ★

### 第一步：别急着看包，先问"这题要什么"

| 问法 | 找法 |
|---|---|
| 要某个**文件名** | 搜 `filename=`、`Content-Disposition`、`> /路径`、`mv`、`cp` |
| 要某个**命令** | 搜 `command=`、`cmd=`、`exec`、`system(` |
| 要某个**密码/密钥** | 搜 `password`、`passwd`、`token`、`key`、`Authorization` |
| 要某个**文件内容** | 找上传/下载的流，按 Content-Length 重组 |
| 要某个**访问的 URL** | 提取所有 `GET /POST` 行，按时间排序 |

**先确定目标，再决定搜什么。** 没有目标地翻包是最慢的。

### 第二步：纯 Python 解析（不依赖 tshark）

```python
import struct

d = open('x.pcap','rb').read()
magic = d[:4]
fmt = '>' if magic == b'\xd4\xc3\xb2\xa1' else '<'   # 注意大小端！
off, pkts = 24, []
while off + 16 <= len(d):
    ts, tus, incl, orig = struct.unpack_from(fmt + 'IIII', d, off)
    off += 16
    pkts.append(d[off:off+incl])
    off += incl
print('包数', len(pkts))
```

**常见的坑**：pcap 的 magic 有四种，大小端和小数点位置都不同。
`d4c3b2a1` 是大端，`a1b2c3d4` 是小端，`4d3cb2a1` / `a1b23c4d` 是纳秒精度。

### 第三步：抓 HTTP 的关键就三行

```python
import re, urllib.parse

for i, p in enumerate(pkts):
    if b'POST ' in p or b'GET ' in p:
        head = re.match(rb'(GET|POST) (\S+) HTTP', p)
        if head:
            print(i, head.group(1), head.group(2))
```

**bodies 的提取**：从 `\r\n\r\n` 之后开始，长度看 `Content-Length`。
跨包的要把后续包按 TCP 序号接上——**但这题往往不需要**，
因为请求体一般在一个包里。

### 第四步：攻击链的标准识别

**看到 `POST /exec`、`/cmd`、`/shell` 这类接口，就是命令执行，直接 dump body。**

真实案例（强网杯 12208）：

```
[5]  echo <base64 C源码>|base64 -d >/tmp/1.c      ← 上传恶意文件
[8]  echo <base64 Makefile>|base64 -d >/tmp/Makefile
[10] cd /tmp;make
[11] mv /tmp/1.c /tmp/looter.c
[12] cd /tmp;make
[14] cp /tmp/looter.so /lib/x86_64-linux-gnu/security/
[18] echo "auth optional looter.so">>/etc/pam.d/common-auth
[20] service ssh restart
```

**从命令序列能直接还原完整攻击链**，比看包的时间线快十倍。

### 第五步：常见答题关键词对照

| 题目问 | 通常在哪 |
|---|---|
| 上传的**文件名** | `> /path`、`mv`、`cp`、`filename=`、`Content-Disposition` |
| 攻击者**用的命令** | `POST /exec` 的 body |
| 被**下载**的文件 | `GET /download`、响应体的前几个字节（看魔数） |
| 泄露的**账号密码** | `pam` / `/etc/passwd` / `Authorization` / `Cookie` |
| **C2 地址** | 大量重复的 `POST` 目标、DNS 查询 |
| **webshell 路径** | `GET /xxx.php?cmd=` 的 URL 路径 |

### 第六步：不要做的事

- ❌ **不要上来就逐个包看 hex**。140 个包能看瞎，先提取 HTTP 行
- ❌ **不要用 `strings` 当唯一手段**。base64 的内容 strings 出来是乱码
- ❌ **不要只搜 flag**。很多题的答案是一个文件名/命令，不是 flag 本身
- ❌ **不要忽略 `Referer` / `Cookie`**。JWT、session、token 常常藏在这里

### 元规则

**流量分析的效率 = 你提取 HTTP 层的速度。**
先把所有请求行和 body 倒出来，再看内容。
在原始字节里翻找，是最慢的路。
