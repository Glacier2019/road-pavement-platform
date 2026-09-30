/* 用真正的 OpenCRG 1.1.2 库读我们生成的 .crg —— 端到端格式验证
   函数签名取自 inc/crgBaseLib.h（非猜测） */
#include <stdio.h>
#include "crgBaseLib.h"

int main(int argc, char** argv) {
    const char* fn = (argc > 1) ? argv[1] : "route_0p1m.crg";
    int dataSet, cp;
    double uMin, uMax, vMin, vMax, uInc, vInc, x, y, z, u, v;

    crgMsgSetLevel(3);

    dataSet = crgLoaderReadFile(fn);
    if (dataSet <= 0) { printf("X crgLoaderReadFile 失败 (返回 %d)\n", dataSet); return 1; }
    printf("OK crgLoaderReadFile 成功, dataSet=%d\n", dataSet);

    if (crgDataSetGetURange(dataSet, &uMin, &uMax))
        printf("  u 范围 = %.3f .. %.3f m\n", uMin, uMax);
    if (crgDataSetGetVRange(dataSet, &vMin, &vMax))
        printf("  v 范围 = %.3f .. %.3f m\n", vMin, vMax);
    if (crgDataSetGetIncrements(dataSet, &uInc, &vInc))
        printf("  增量   = u %.6f, v %.6f m\n", uInc, vInc);

    crgDataPrintRoadInfo(dataSet);   /* 官方自带的道路信息打印 */

    cp = crgContactPointCreate(dataSet);
    if (cp < 0) { printf("X 创建接触点失败\n"); return 1; }

    printf("\n--- 逐点求值 ---\n");
    double probe[5][2] = {{0,0},{1000,0},{2900,0},{5000,0},{5805.0,0}};
    for (int i = 0; i < 5; i++) {
        u = probe[i][0]; v = probe[i][1];
        if (crgEvaluv2xy(cp, u, v, &x, &y) && crgEvaluv2z(cp, u, v, &z))
            printf("  u=%8.3f v=%.1f -> x=%10.3f y=%10.3f z=%8.3f\n", u, v, x, y, z);
        else
            printf("  u=%8.3f v=%.1f -> 求值失败\n", u, v);
    }

    /* 往返一致性：uv -> xy -> uv */
    if (crgEvaluv2xy(cp, 1000.0, 0.0, &x, &y) && crgEvalxy2uv(cp, x, y, &u, &v))
        printf("\n  往返 u=1000 -> xy(%.3f, %.3f) -> uv(%.3f, %.3f)\n", x, y, u, v);

    crgContactPointDelete(cp);
    crgDataSetRelease(dataSet);
    printf("\nOK 全部求值成功 —— 我们生成的 .crg 能被官方库正常读取\n");
    return 0;
}
