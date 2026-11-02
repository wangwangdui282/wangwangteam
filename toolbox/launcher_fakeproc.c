/* launcher_fakeproc.c —— 伪造 /proc 骗过反调试（不改目标二进制）
 *
 * 编译： gcc -O2 -o launcher launcher_fakeproc.c
 * 用法： ./launcher ./目标程序 [参数...]
 *
 * 原理：unshare 一个新的 mount namespace，用 tmpfs 覆盖 /proc，
 *       在里面写一份 TracerPid=0 的 status。这样目标程序读到的是我们伪造的，
 *       而二进制本身一个字节都没动（不会触发自校验/校验和变化）。
 *
 * 常见配套检查：
 *   - /proc/self/status 的 TracerPid   -> 本文件解决
 *   - ptrace(PTRACE_TRACEME) 返回值    -> 需要 gdb 里 catch syscall 或单独 patch
 *   - timing / rdtsc 检测              -> 本文件解决不了，要 patch 或伪造时钟
 *
 * 重要：本文件【故意不调用】 ptrace(PTRACE_TRACEME)。
 *       没有 tracer 时调它，进程 exec 后会停在 SIGTRAP 里挂死。
 */
#define _GNU_SOURCE
#include <errno.h>
#include <sched.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mount.h>
#include <sys/stat.h>
#include <unistd.h>

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "用法: %s <程序> [参数...]\n", argv[0]);
        return 2;
    }

    if (unshare(CLONE_NEWNS) != 0) {
        fprintf(stderr, "unshare 失败: %s\n", strerror(errno));
        return 2;
    }
    /* 让挂载不影响宿主的 mount namespace */
    mount(NULL, "/", NULL, MS_REC | MS_PRIVATE, NULL);

    mkdir("/tmp/fakeproc", 0755);
    if (mount("tmpfs", "/tmp/fakeproc", "tmpfs", 0, "") != 0) {
        fprintf(stderr, "tmpfs 挂载失败: %s\n", strerror(errno));
        return 2;
    }

    mkdir("/tmp/fakeproc/self", 0755);
    mkdir("/tmp/fakeproc/self/fd", 0755);

    FILE *f = fopen("/tmp/fakeproc/self/status", "w");
    if (!f) {
        fprintf(stderr, "写 status 失败: %s\n", strerror(errno));
        return 2;
    }
    fprintf(f,
            "Name:\tprog\n"
            "Umask:\t0022\n"
            "State:\tR (running)\n"
            "Tgid:\t%d\n"
            "Pid:\t%d\n"
            "PPid:\t1\n"
            "TracerPid:\t0\n"
            "Uid:\t0\t0\t0\t0\n"
            "Gid:\t0\t0\t0\t0\n"
            "Threads:\t1\n"
            "VmSize:\t  131072 kB\n"
            "VmRSS:\t    4096 kB\n",
            getpid(), getpid());
    fclose(f);

    /* 有些程序还会读 maps / cmdline / exe，给个最小版本 */
    f = fopen("/tmp/fakeproc/self/maps", "w");
    if (f) {
        fprintf(f, "00400000-005bb000 r-xp 00000000 00:00 0\n"
                   "005bb000-005bd000 rw-p 00000000 00:00 0\n");
        fclose(f);
    }
    f = fopen("/tmp/fakeproc/self/cmdline", "w");
    if (f) {
        fwrite(argv[1], 1, strlen(argv[1]), f);
        fclose(f);
    }

    if (mount("/tmp/fakeproc", "/proc", NULL, MS_BIND, NULL) != 0) {
        fprintf(stderr, "bind /proc 失败: %s\n", strerror(errno));
        return 2;
    }

    execv(argv[1], argv + 1);
    fprintf(stderr, "execv 失败: %s\n", strerror(errno));
    return 2;
}
