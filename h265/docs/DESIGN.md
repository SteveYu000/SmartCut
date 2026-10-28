# MP4 / HEVC Smart Rendering 设计

编写日期：2026-10-28。实测运行记录：2026-10-27。实现入口：[engine.cpp](../src/engine.cpp)。

## 1. 目标与边界

精确保留按**显示顺序**指定的帧区间，压缩数据能安全保留时直接复制，仅重编码必要边界。输出为单视频轨 MP4；音频暂不实现，不涉及杜比技术。输入范围和未覆盖能力见 [README](../README.md)。这是通过限定输入测试的可行性实现，不是通用 HEVC 合规性检查器。

选择方案前已实际运行 skeskinen/smartcut 和 DeepRegular 的 Python 参考实现。前者在本轮样本上正确处理 CRA 前导帧；后者展示了裸流中转丢失原始时间关系的风险。最终保留前者的连续区间复制思路，复用 H.264 实验的容器时间轴，并加入经消融实验验证的 CRA→BLA 接入处理。数据见 [测试报告](IMPLEMENTATION_REPORT.md)。

## 2. 分工

| 工作 | 实现者 |
|---|---|
| MP4 解封装、sample 位置、PTS/DTS、时间基、codecpar | libavformat |
| HEVC 解码、参考图像管理、像素格式 | libavcodec HEVC decoder |
| 局部编码 | libavcodec 的 libx265 encoder |
| 长度前缀 NAL 与 Annex-B 转换、恢复带内参数集 | `hevc_mp4toannexb` BSF |
| `hvcC`、`stsd/stsc`、`stts/ctts`、edit list、faststart | FFmpeg MOV/MP4 muxer |
| 本项目核心 | 索引与剪切决策、保留原始显示时间关系、边界解码范围、CRA NAL 头改为 BLA |

不自己实现 HEVC 编码器、完整 SPS/PPS/RPS 解析器、CABAC、MP4 写入器。只检查 hvcC/NAL 长度、两字节 NAL 头及 `first_slice_segment_in_pic_flag`。VPS/SPS/PPS 作为不透明字节串保存和比较。

## 3. 输入索引：解码顺序与显示顺序同时保存

扫描选中的视频轨，记录每个 sample 的文件位置、长度、PTS、DTS、时长、NAL 类型和显示帧号；压缩负载不全量常驻内存，输出时按位置用 AVIO 读取。

按 PTS 排序得到显示帧序列 `t[0..N-1]`；末帧的结束边界 `t[N]` 来自末显示帧的 duration。拒绝缺失/重复 PTS、非递增 DTS、不可寻址 sample、动态 extradata、未知带内参数集等不符合当前输入合同的情况。

以真实 IRAP NAL 分组，不仅依赖 demuxer 的 key 标志。每组的解码包从 IRAP 开始，直到下一个 IRAP；显示区间取该组 sample 的最小、最大显示帧号。要求组内显示序列连续，且各组在显示时间线上首尾相接，不支持交错覆盖的特殊结构。

典型 x265 open GOP 的一个组可能是：

```text
解码顺序：CRA(24)，RASL(22)，RASL(21)，RASL(23)，后续图像……
显示顺序：21，22，23，24，25，……，44
组的显示区间：[21,45)，CRA 自身的显示位置：24
```

具体组内 B 帧解码次序由源文件决定；示意中的次序不作为硬编码规则。

## 4. 剪切决策

区间为 `[A,B)`，相邻用户区间先合并。按源文件的组遍历，每组与保留区间求交。

| 情形 | 决策 |
|---|---|
| 完整 IDR 组 | 复制，包括与其配套且可解码的 RADL |
| 完整 CRA 组，前一组也连续复制 | 原样复制；原参考关系仍在，保留 RASL |
| 在 CRA 处重新接入，只有 RASL 前导帧，尾部完整 | 用户需要的前导帧先重编码；复制 CRA 和 trailing pictures；CRA 改为 BLA_N_LP |
| 从 CRA 自身开始、尾部完整 | 直接进入上述复制分支，无需重编码已被用户排除的 RASL |
| 部分组、切点位于 trailing pictures、尾部不完整 | 重编码相交的显示帧 |
| 非连续 CRA 入口含 RADL | 保守重编码该组，避免错误假设 RADL 可以删除 |

RASL 与 RADL 不能统一按“B 帧”处理。RASL 在从关联 CRA 随机接入时允许跳过；RADL 可以从关联 IRAP 解码，但不等于可以随意删掉。设计只利用 HEVC 随机接入的保证，不尝试自行猜测每帧的参考图。[RFC 7798 §1.1.2](https://www.rfc-editor.org/rfc/rfc7798.html#section-1.1.2)

实际例子：240 帧 open-GOP 输入，保留 `[13,227)`：

```text
[13,21)  编码切入组的尾部
[21,24)  在原上下文中解码并编码 RASL
[24,45)  复制，CRA→BLA_N_LP
[45,213) 连续复制，后续 CRA/RASL 均保持原样
[213,227) 编码切出组
```

共 214 帧：25 帧编码、189 帧保留压缩图像数据。189 帧中 188 帧 VCL 完全一致，1 帧仅改变 NAL 头的类型位。报告中的 `copied_frames` 包含这种接入帧，不能将其误读为所有 NAL 字节都相同。

## 5. 边界解码与编码

边界需要的帧必须先在**源码流的正确上下文**中解码。若目标起点在 CRA 的 RASL 区域，向前取前一个 IRAP 作为 decoder priming 起点；新 decoder 从 CRA 启动时跳过该启动组不可用、且不属于目标区间的 RASL。保留 RADL。随后按源解码顺序送包，根据原始 PTS 提取所需帧，并核对其显示次序、解码错误标记、尺寸、格式和逐行状态。

每个编码单元使用独立 libx265 encoder：闭合 GOP、无 B 帧、无场景切换，最多 4 个 pool worker、2 个 frame thread。使用源像素格式、SAR、色彩标记；默认 CRF 18。边界旧帧的 SEI side data 不直接套到新压缩数据上，当前实现会移除它们，因此不承诺完整 HDR/SEI 保留。

`--lossless` 设置 x265 `lossless=1`，而不是把 CRF 0 当成无损。实测支持在源压缩区间之间混入无损编码边界，无需照搬 H.264 实验的整段无损回退。这里的无损是相对于**输入解码后的像素**，不会恢复源文件之前已经损失的信息。[x265 lossless 选项](https://x265.readthedocs.io/en/master/cli.html#cmdoption-lossless)

## 6. 为什么需要 CRA→BLA

只修改 MP4 PTS/DTS，不会重置解码器内部的 POC 和 DPB 状态。把不同位置的源 CRA 接到旧序列后，POC LSB 可能被解释到错误的连续序列中。

本实现对每个非连续复制入口，将该 AU 中**所有 CRA slice NAL** 的类型从 21 改为 18（BLA_N_LP）；前导 RASL 已从复制单元移除，需要显示的部分另行编码。仅改变 NAL 头中的类型位，slice RBSP、PPS 引用、POC LSB 和编码图像负载不改。BLA 是 HEVC 提供的拼接接入类型，不是项目私有约定。[RFC 7798](https://www.rfc-editor.org/rfc/rfc7798.html#section-1.1.2)

必要性已有反例：800 帧输入取 `[24,93)` 和 `[312,429)`，不重编码任何一帧。不改 BLA 时，源帧 `91、92、312` 的输出次序变成 `312、91、92`；MP4 排序后的 PTS 和压缩帧数量仍然可以看起来正常。开启 BLA 后消失。连续复制的中间 CRA 不修改。

## 7. 参数集和 MP4

新编码器必须输出描述其实际码流的参数集，不能强行沿用旧 VPS/SPS/PPS。本实验中源与新 encoder 都会使用编号 0，编号相同并不代表内容相同。

提供两种传递方式，均不重写参数集编号或 slice 中的 PPS ID：

1. **`hev1`**：每个复制单元建立正确初始化的 `hevc_mp4toannexb` BSF，在复制 IRAP 处恢复源 VPS/SPS/PPS；编码区间使用 x265 重复头。MOV muxer 接收 Annex-B，写回 MP4 长度前缀 sample。
2. **`hvc1` / `--ps-mode stsd`**：在单元首包使用 `AV_PKT_DATA_NEW_EXTRADATA` 提供本单元的配置。由本次固定版本 FFmpeg 创建或复用 sample description，并在 sample-to-chunk 表中选择配置。输入复制包保留原始长度前缀，编码器采用 global header。

实测混合片段通常产生 2 个 hvc1 sample description，纯复制为 1 个。`hvcC` 并非只能装一套参数集；这里选择配置切换机制，避免自行管理编号命名空间和改写 slice bitstream。不能据此声称所有播放器都支持多 description 切换。

删除混合输出接入处的源 VPS/SPS/PPS 后，即使保持 MP4 sample 长度和位置不变，也观察到了丢帧及大量像素不一致。因此，**恢复正确配置是必要的；自己改编号在本范围内不是必要条件**。

## 8. 时间轴

对单元 `[a,b)`，输出起点为 `O`，每个原始显示时刻 `t` 映射为：

```text
PTS_out = O + t - t[a]
单元时长 = t[b] - t[a]
```

对该单元解码顺序的第 `j` 个输出包，定义单调 slot：

```text
slot[j] = O + t[a+j] - t[a]
D = max(0, 所有复制包的 slot[j] - PTS_out[j])
DTS_out[j] = slot[j] - D
```

全文件使用同一个 `D`。源复制包的解码顺序不变；编码器无 B 帧，编码单元按显示顺序输出，也适用这个时钟。这样保证 DTS 严格递增且不大于 PTS，而不是事后逐包钳制错误时间戳。

PTS 从 0 开始，允许初始 DTS 为负；交给 MOV muxer 生成 composition offsets 和 edit list。原始 VFR 的显示间隔以整数 tick 保留，不先转浮点秒或平均帧率。最后一个解码包的 duration 使用该单元最后显示帧的时长，避免 VFR 尾部总时长偏差。

这是 MP4 播放时间轴方案，未建立原始 HRD/CPB 的传输时序合规性证明，不应直接推广到广播 TS、DASH 切片或保留任意 HRD SEI 的场景。

## 9. 仍需完善

- 目前要求固定的一组 VPS/SPS/PPS，尚不能再次剪切本工具生成的混合参数集输出。
- 有 RADL 的非连续 CRA 入口采取整组编码，未做更细的依赖优化。
- 邻接编码单元仍分别创建 decoder/encoder，可合并上下文减少重复解码和初始化成本。
- 索引为 O(N) 元数据；长文件需要完整扫描，未实现持久索引和任务取消。
- 未普测长期参考、稀有编码器结构、多层/隔行、动态分辨率、所有 SEI、专业硬件播放器。
- 检查 sample/NAL 结构不能代替对所有复制负载完整解码；生产实现还需可选的全量输入校验及输出验收模式。

以上缺口不会由“更多 SPS ID”自动解决，应分别增加输入合同、依赖策略和容器/播放器验证。
