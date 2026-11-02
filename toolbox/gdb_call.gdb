# gdb_call.gdb —— 直接调用程序内部函数
#
# 用法：
#   gdb -q -batch -x gdb_call.gdb ./目标程序
#
# 场景：程序里有个生成函数（比如把索引变成一串字节），逻辑很绕，
#       与其自己还原，不如让程序自己算给你看。
#
# 关键：
#   - 先 run 到一个"库/表已经初始化完"的断点，再 call
#   - 函数签名要猜对，返回类型不对就换个强转
#   - 参数是整数就用 (int)，是指针就用 (char*)
#   - 调用可能改变程序状态，所以只在你不需要后续流程时用

set pagination off
set confirm off

handle SIGTRAP  nostop noprint pass
handle SIGSEGV nostop noprint pass
handle SIGILL  nostop noprint pass
handle SIGBUS  nostop noprint pass
handle SIGFPE  nostop noprint pass

# 停在一个"表已就绪"的位置（按需改）
b *0x402bf5
commands
silent

# ---- 示例 1：连续调用生成函数，收集 42 个字节 ----
printf "=== 调用 0x402747(i) for i=0..41 ===\n"
set $i = 0
set $buf = 0x5AA320
while $i < 42
  set $b = ((unsigned char (*)(int))0x402747)($i)
  set {unsigned char}($buf + $i) = $b
  set $i = $i + 1
end
x/42xb 0x5AA320
printf "TEXT: %.42s\n", (char*)0x5AA320

# ---- 示例 2：单次调用看返回值 ----
# printf "f(0)=%d f(1)=%d\n", ((int (*)(int))0x402747)(0), ((int (*)(int))0x402747)(1)

c
end

run < /tmp/in.txt
quit
