# pipeline 架构修复 VFR 的验证报告

日期：2026-10-29。研究基线：SmartCut `c1a5d14667b3d82e47a970c93c5efff1e25f9ae1`。

**结论：可以。** C# 调度官方 FFmpeg 子进程的架构能够修复当前 VFR 用例，不需要改 FFmpeg 源码，也不需要新增 C++ 封装器。本次在独立副本中完成修复和测试：原有视频测试 **130/130** 通过，另外 **20/20** 个 VFR/时间精度用例通过更强的独立检查。正式仓库未修改、未提交或推送；可审阅改动见 [vfr-repair.patch](vfr-repair.patch)，原型见 [PipelineRunner.cs](prototype/src/SmartCut.Core/PipelineRunner.cs)。新增代码沿用项目 GPL-3.0 许可要求。

## 1. 架构有能力，原实现缺少时间轴控制

原实现的分工是：C# 建立显示帧索引，按真实 IDR 划分复制或重编码单元；FFmpeg 生成单元文件，最后通过 concat demuxer 直接复制拼接。这个分工可以保留。

问题出在执行层没有完整落实计划中的时间边界：

- VFR 帧的持续时间不能由平均帧率推算。一个单元导出为独立 MP4 后，其末帧可能被赋予错误的默认时长。
- 原 ffconcat 清单只有 `file`，下一单元从哪里开始依赖分段文件报告的时长；错误会移动后续 PTS。
- `-fps_mode passthrough` 保留帧数，但还需要明确编码器时基和输出轨道时基。
- B 帧使显示顺序与解码顺序不同。只修 PTS 或只延长最后一个包，不能同时保证显示终点、解码跨度和 MP4 时长表一致。

最小例子是保留源显示帧 `[23,25)`：第一帧应持续 80 ms，第二帧持续 40 ms。原执行路径把前一单元当作 40 ms，得到输出 PTS `0, 40 ms`；正确值为 `0, 80 ms`，总时长 120 ms。显式指定单元时长即可修复此处；单独保留 `[23,24)` 则还必须正确保存 80 ms 的末帧持续时间。可复核 [initial-experiment.json](initial-experiment.json)。

## 2. 修复方式

### 2.1 按计划指定 concat 的持续时间

对每个单元写入 `duration`，数值来自源视频真实显示时间边界。先把累计输出边界取整到微秒，再相减得到单元时长，避免逐段取整误差累积。

编码单元继续使用 `-fps_mode passthrough`，增加源时基对应的 `-enc_time_base`；各输出显式设置 `-video_track_timescale`。没有按平均帧率重建 PTS，也没有转成 CFR。

### 2.2 用现有 setts 重建一致的解码时间轴

单纯补上 concat duration 后，PTS 可以正确，带 B 帧的结尾仍可能存在时长问题。进一步试验发现，修改终止包时长可能只让 ffprobe 的派生时长通过，而 `mdhd`、`stts` 等记录仍不一致。因此最终原型采用与 `h264/src/engine.cpp` 相同的解码调度原则。

设一个单元保留源显示帧 `[a,b)`，输出起点为 `O`，源显示时间边界为 `t`：

```text
某包的显示帧序号为 r：PTS = O + t[r] - t[a]
单元内第 n 个解码包：slot = O + t[a+n] - t[a]
全局重排序延迟：delay = max(0, 所有包的 slot - PTS)
DTS = slot - delay
```

包的 VCL 内容和解码顺序保持不变。编码单元使用无 B 帧输出，因此其解码顺序就是显示顺序；复制单元使用索引中已有的解码顺序映射。每包时长按相应显示帧的真实间隔生成；最终解码包另用最后显示帧的持续时间，使解码跨度准确结束。

这些值由 C# 的现有索引和计划算出，交给官方 FFmpeg 的 `setts` bitstream filter 设置 DTS、duration；显式指定 `pts=PTS` 保留拼接后的 PTS。没有改 SPS/PPS、slice 或编码器参数集。

实现把数值压缩为等差区间，再生成平衡的查找表达式，通过 `-/bsf:v timeline.bsf` 加载，避免 Windows 命令行长度限制。最终视频拼接和后续带音频封装均应用同一时间计划，防止重复封装再次丢失时长信息。

这一做法证明进程管线能够完成所需控制；查找表达式是可验证原型的实现选择，并不代表已经完成长视频规模的性能优化。

## 3. 实测结果

环境：Windows、.NET SDK 10.0.403、FFmpeg `n8.1.3-24-g92611900bb-20261025`。未修改 FFmpeg。完整解决方案 Release 构建为 0 警告、0 错误；回归测试源文件未改动。

| 原有测试分类 | 原版复测 | 修复原型 |
| --- | ---: | ---: |
| 视频，13 种素材 × 10 种剪切 | 125/130 | **130/130** |
| 音频集成 | 12/24 | 16/24 |
| 逻辑 | 8/8 | 8/8 |
| 负向 | 3/3 | 3/3 |
| 合计 | 148/165 | **157/165** |

原先失败的 5 个 VFR 视频用例为 `both_edges`、`head_only`、`tail_only`、`cross_idr`、`multiple`，现均通过。另有 4 个音频集成用例随视频 PTS 修复而通过。没有新增失败；剩余 8 项仍是原有音频脉冲计数失败，不能将整套测试表述为全部通过。本次没有改动音频编码或脉冲检测策略，也不能由这些计数失败直接推断音频丢样。

对比明细见 [regression-summary.json](regression-summary.json)，完整新测试记录见 [pipeline-vfr-repaired.trx](test-results/pipeline-vfr-repaired.trx)。

另用独立 Python 检查器运行 20 个用例，覆盖：原有 VFR 10 种剪切、7 个额外边界场景、NTSC 的 80 个单帧区间拼接，以及新生成的 90 kHz VFR 素材的双边剪切和 80 个单帧区间拼接。额外边界场景包含单长帧、长帧结尾、完整复制 epoch、多段不连续长帧；部分与原始用例重叠，用于单独核查末帧时长，不能算作 20 种互不重叠的场景。

**20/20 通过，所有包与解码帧的 PTS 均与预期精确相同，最大误差为 0 个源时间刻度。** 独立检查还覆盖严格解码、帧数、画面条码顺序、复制 VCL 保留数量、复制画面一致性、逐帧质量和实际轨道时长；重编码画面逐帧 PSNR 下限为 30 dB。结果及媒体文件路径见 [validation.json](validation.json)，复测脚本见 [validate.py](validate.py)。

进一步直接读取 20 个输出的 MP4 box，检查 `mdhd` 与 `stts` 解码跨度精确等于计划；`mvhd`、`tkhd`、`elst` 按电影时基正确取整。**20/20 通过**，见 [box-audit.json](box-audit.json) 和 [audit_boxes.py](audit_boxes.py)。电影时基仍采用 FFmpeg 默认的 1000 Hz，所以这些电影级时长字段允许按 1 ms 向上取整；视频轨道的 PTS 和时长仍使用原轨道时基。

有用的消融结果：保留原 DTS、仅修显式 concat 边界和终止包时长时，独立检查通过 18/20，`head_only` 和 `multiple` 的轨道时长仍失败；采用统一解码时间计划后为 20/20。参见 [validation-attempt-4.json](validation-attempt-4.json)。因此 DTS 处理有实测必要性。

## 4. 适用范围与剩余工程工作

本次结论限定为当前 H.264/MP4 输入范围及测试样本。它证明现有 VFR 失败不是 C#、子进程调用或官方 FFmpeg 的架构性限制。

投入正式实现前仍需处理以下事项：

1. **时间精度上限。** concat 的 `duration` 经微秒表示。累计边界取整已通过 NTSC 和 90 kHz 测试，但不保证所有超高时基、亚微秒间隔或极端 VFR 都能逐 tick 精确。应检查拼接后的 PTS，无法准确表示时明确拒绝或换路径，不能静默接受误差。
2. **长素材规模。** 最坏情况下时间表达式和索引均随帧数增长；平衡树降低查找深度，但解析成本、内存及长视频吞吐未做基准测试。直接用 libavformat 写包更便于输送大型时间表，现有进程架构也可进一步设计批次或时间表接口。不能据本次短素材测试宣称两者性能相同。
3. **加强正式验证器。** 原 Verifier 没有完整检查实际终点和 MP4 时长表，本次用独立检查补齐。合入时应把关键断言纳入持续回归，并在发布输出前验证帧数、PTS 和终点。
4. **音频余项独立解决。** 仍有 8 项脉冲检测失败；本次原型不把它们当作 VFR 修复已解决的问题。

补丁只修改 `PipelineRunner.cs`，`git apply --check` 已通过；正式仓库仍保持干净。原型核心程序集 SHA-256：`cd90abac958e068469ebcffe703e27600ae6704fcf4ad8472c30ac51f7fe0407`。

## 5. 本地复测

在本报告目录使用当前环境可运行：

```powershell
# 先按本地工具路径设置 DOTNET_CLI_HOME、SMARTCUT_FFMPEG_DIR、SMARTCUT_FIXTURE_DIR。
dotnet build prototype/SmartCut.Pipeline.slnx -c Release --artifacts-path artifacts --no-restore
dotnet vstest artifacts/bin/SmartCut.Tests/release/SmartCut.Tests.dll /ResultsDirectory:test-results
python validate.py
python audit_boxes.py
python summarize_regression.py
```

`validate.py` 复用同工作区内已有的独立检查器 `../losslesscut-comparison/run_suite.py`，源素材使用正式仓库中的 h264 测试集；它是本地实验复现入口，不是可单独分发的完整测试包。输出媒体保留在 `validation.json` 所列目录。
