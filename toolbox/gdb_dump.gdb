# gdb_dump.gdb —— 批量断点 dump 中间状态
#
# 用法：
#   gdb -q -batch -x gdb_dump.gdb ./目标程序
#
# 改三处：
#   1. 输入文件路径（run < 那行）
#   2. 断点地址（0x402d22 换成你关心的指令地址）
#   3. 要 dump 的内存表达式（$rsp+0x10 之类）
#
# 为什么要在每条关键指令后打断点？
#   因为你是要"验证自己的实现"，不是"猜"。只有拿到程序每一步的真实值，
#   才能确定自己的模拟器/解密器哪一行写错了。

set pagination off
set confirm off

# 如果程序装了信号自检（sigaction + SIGTRAP/SIGSEGV），必须放行，
# 否则 gdb 会拦下信号导致程序走不到后面。
handle SIGTRAP  nostop noprint pass
handle SIGSEGV nostop noprint pass
handle SIGILL  nostop noprint pass
handle SIGBUS  nostop noprint pass
handle SIGFPE  nostop noprint pass

# ---------------- 断点 1：某个阶段之后 ----------------
b *0x402d22
commands
silent
printf "=== STAGE1 gacc=%016lx\n", $r8
x/16xb $rsp+0x10
c
end

# ---------------- 断点 2：输出缓冲区 ----------------
# b *0x4028e8
# commands
# silent
# printf "=== STAGE2 buf@0x5AA320\n"
# x/48xb 0x5AA320
# c
# end

# ---------------- 断点 3：调用后 ----------------
# b *0x402d47
# commands
# silent
# printf "=== STAGE3\n"
# x/42xb 0x5AA320
# c
# end

run < /tmp/in.txt
quit
