# 同样调用 FFmpeg，为什么 pipeline 与 LosslessCut 的结果不同

2026-10-29。比较的是当前仓库中的 H.264 pipeline，以及官方 LosslessCut 3.70.2 的 Smart Cut。pipeline 尚未实现 HEVC，这里只比较相同的 130 个 H.264 素材/剪切组合。

FFmpeg 提供解复用、复制、解码、编码和封装；调用程序仍负责决定哪些帧必须编码、哪些压缩包可以保留，以及如何定义每段的起止。两套程序在这些决策上采用了不同的方法。

## 用同一个验收器复核

本轮直接引用上轮构建的、未经修改的 SmartCut.Core.dll，调用 `Verifier.Verify`，并执行原 PipelineIntegrationTests 对复制检查覆盖数的附加断言。没有重新导出或调整 LosslessCut 文件，也没有重新实现验收器。所引用 DLL 与上轮使用版本的 SHA-256 相同：`3b9f1fbadb8e5329e490efc6d8ec54edc7eda2bb347cd17f1c1eb739f57ed7ca`。

| 生成方案 | 使用 pipeline 自身的验收合同 | 结果来源 |
|---|---:|---|
| pipeline | 125/130 | 上轮同 SDK 的完整集成测试 TRX |
| LosslessCut Smart Cut | 19/130 | 本轮对已有输出逐项重新检查 |

两者验收使用同一个 `n8.1.3-24-g92611900bb-20261025` FFmpeg 构建。生成文件时，LosslessCut 保留其官方捆绑的 `n8.1.3-18-g46d899d7a0-20261020`，因此这不是把两个导出器替换成同一 FFmpeg 构建后的单变量实验。源码与实际命令已足以直接解释下述边界错误；不把全部差异归因于某一个参数。

pipeline 原验收没有 VCL 字节哈希、独立画面条码或完整输出 DTS/时长断言。它的 125/130 不能当作已通过 C++ h264 的全部检查。本轮 LosslessCut 的 19/130 与此前较严格检查下“正确性+复制量”结果数量相同，但验收定义不同。

“整体正确性通过率低”也不等于“多数文件无法解码”。LosslessCut 主矩阵有 123 个含视频轨的 H.264 输出，其中 111 个通过使用独立像素提取时钟的严格解码；很多文件能正常解码，却选错了画面或多留了尾帧。原 pipeline verifier 的 null 输出检查还会把下游 mux 时钟告警统一记为 strict decode reported，不能直接将其字段统计解释成纯视频解码器成功率。

## 执行设计上的差别

| 环节 | pipeline | LosslessCut 3.70.2 Smart Cut |
|---|---|---|
| 索引与恢复点 | 建立包/显示帧映射，读取 NAL 判断真实 IDR | 查找 ffprobe 包的 K 标记，以附近关键帧为复制入口 |
| 首尾边界 | 两端分别与 IDR 区间求交，不完整区间编码 | 主要编码开头到下一关键帧，余段按时间复制至结束 |
| 编码帧选择 | `select=between(n,begin,end-1)`，显式按显示帧选取 | `-ss/-t`，接缝前额外减去一个 frameDuration |
| 复制范围 | 已知安全入口及精确 packet count，使用 `-frames:v` | 复制段主要使用 `-ss/-t` |
| 新编码段 | closed GOP、B=0，减少新的重排序延迟 | 按输入 has_b_frames 设置 `-bf` |
| 封装 | concat 后明确使用 avc3 | 本轮 H.264 输出为 avc1；不能单靠标签判断剪切正确性 |

pipeline 保守地只复制完整 IDR 区间；长 Open GOP 可能被大范围重编码。它通过多编码一些帧来换取受约束输入下的可靠性。B=0 只作用于新编码段，复制段仍保留源视频原来的 B 帧。

## 同一案例的实际区别

源 closed_b3_id0，保留 `[13,227)`：

```text
pipeline：编码 [13,24) + 复制 [24,216) + 编码 [216,227) → 214 帧
LosslessCut：编码 [13,23) + 复制 [24,229)                 → 215 帧
```

LosslessCut 的实际条码漏掉第 23 帧，多出 227、228 帧。其编码命令使用 `-ss 0.520000 ... -t 0.400000`，而正确覆盖 13～23 共 11 帧需要 0.44 秒。源码中“下一关键帧减一帧”的处理与这个缺口直接对应。尾部复制又包含选区外的 B 帧。

pipeline 用帧号选择两端，避免了该例的时间减帧估计；复制只覆盖安全的完整区间。这些差别发生在调用 FFmpeg 前的计划和调用参数里。

## 参数集和时间轴不能一概而论

pipeline 使用 avc3 表达带内参数集更新，这是它的封装选择。LosslessCut 这个 H.264 文件的软件解码和十个同步点 seek 都能通过，但选区仍有上述错误；因此这例的主要问题可以由剪切范围解释，不能简单归因于没有重编号 SPS-ID。

pipeline 也仍有未解决问题。上轮五个 VFR 视频用例失败，包含实际应为 80 ms 的相邻帧间隔被拼成 40 ms 的例子；它预先计算的期望 PTS 用于验收，未逐包强制写入输出。其执行层仍依赖 FFmpeg 分段和 concat 的时间处理，尚未达到 C++ 单 muxer 方案的控制程度。

验收器负责发现错误，不会自动补回丢帧或修复 PTS。这里的差距主要来自已经采用的边界规划与命令策略；同样调用 FFmpeg 并不意味着执行了同一种剪切算法。

## 复现证据

- [同验收器逐项结果](pipeline-crosscheck.json)
- [复核程序](pipeline-crosscheck/Program.cs)：直接引用原 SmartCut.Core.dll。
- [原 pipeline 测试汇总](../h264-pipeline-comparison/pipeline-test-summary.json)
- [LosslessCut 完整测试报告](REPORT.md)
- [pipeline 分段命令](../pipeline/src/SmartCut.Core/PipelineRunner.cs)
- [pipeline 剪切计划](../pipeline/src/SmartCut.Core/Planner.cs)
- [LosslessCut 3.70.2 首端处理](https://github.com/mifi/lossless-cut/blob/2610284b6c9a68db124a7a2ef436464ce41ea1d2/src/renderer/src/hooks/useFfmpegOperations.ts)
