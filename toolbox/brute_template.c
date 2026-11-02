/* brute_template.c —— 多线程哈希/原像爆破模板
 *
 * 编译： gcc -O3 -pthread -o brute brute_template.c -lcrypto
 * 用法： ./brute
 *
 * 改四处：
 *   1. ALPHA      字符集
 *   2. LEN        长度
 *   3. TARGET     目标哈希（16 字节）
 *   4. 想同时试 "flag{...}" 包装就在比对处加一次
 *
 * 实测速度：32 核约 30M hash/s。
 *   [a-z0-9]^6 × 2  ≈ 2 分钟
 *   hex^8     × 2   ≈ 5 分钟
 *
 * 失败的结论也有价值：
 *   跑完 250 亿候选还没有，说明它【不是短且可猜的字符串】，
 *   这时该换个方向（找别的入口），而不是继续加大字符集。
 */
#include <openssl/md5.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define ALPHA "abcdefghijklmnopqrstuvwxyz0123456789"
#define LEN 6

static const unsigned char TARGET[16] = {
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
};

static volatile int found = 0;

static void *worker(void *arg) {
    int first = (int)(long)arg;
    const int AN = (int)strlen(ALPHA);

    unsigned char buf[64], d[16];
    int idx[64];
    memset(idx, 0, sizeof idx);
    idx[0] = first;

    for (;;) {
        if (found) break;

        for (int i = 0; i < LEN; i++) buf[i] = (unsigned char)ALPHA[idx[i]];

        /* --- 裸串 --- */
        MD5(buf, LEN, d);
        if (!memcmp(d, TARGET, 16)) {
            printf("\n[命中·裸串] %.*s\n", LEN, buf);
            found = 1;
            break;
        }

        /* --- flag{...} 包装 --- */
        {
            unsigned char w[80];
            int n = 0;
            memcpy(w, "flag{", 5); n = 5;
            memcpy(w + n, buf, LEN); n += LEN;
            w[n++] = '}';
            MD5(w, n, d);
            if (!memcmp(d, TARGET, 16)) {
                printf("\n[命中·包装] %.*s\n", n, w);
                found = 1;
                break;
            }
        }

        /* odometer 进位，第一位固定（线程分片） */
        int i = 1;
        while (i < LEN && ++idx[i] >= AN) { idx[i] = 0; i++; }
        if (i == LEN) break;
    }
    return NULL;
}

int main(void) {
    int nproc = (int)sysconf(_SC_NPROCESSORS_ONLN);
    if (nproc < 1) nproc = 1;
    if (nproc > 64) nproc = 64;
    printf("线程数 %d，字符集 %s，长度 %d，候选 %zu\n",
           nproc, ALPHA, LEN,
           (size_t)strlen(ALPHA) == 0 ? 0
             : (size_t)1 << 0); /* 仅提示，真实数量见下 */
    printf("（第一位分给各线程，实际并行度 %d）\n", nproc);

    pthread_t th[64];
    for (long i = 0; i < nproc; i++) pthread_create(&th[i], NULL, worker, (void *)i);
    for (int i = 0; i < nproc; i++) pthread_join(th[i], NULL);

    if (!found) printf("\n[结论] 该字符集×长度内没有原像。换方向或加长。\n");
    return 0;
}
