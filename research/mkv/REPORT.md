# MKV / Matroska Smart Rendering 方案研究

研究与测试：2026-10-30—2026-10-31。环境：Windows x64、MSVC 19.51、C++20、FFmpeg 9.0.2。本文关注 AVC、HEVC、AV1，以及沿用 FFmpeg 的音频、字幕封装流程。

## 1. 结论

**AVC 和 HEVC 可以沿用现有视频规划算法，增加 MKV 输入适配及独立的 Matroska 输出策略。AV1 只能有条件地沿用 MP4 方案。**

本轮 C++ 原型直接读取 MKV、直接写 MKV，不经 MP4 中转；使用 libavformat 处理 EBML、Block、交织和 Cues，使用现有视频核心决定哪些帧复制、哪些帧编码。40 项 AVC、35 项 HEVC 均通过本轮全部检查。AV1 的 35 项中，29 项通过；另外 6 项混合输出可以严格解码、画面和时间也正确，但含不同 Sequence Header，不满足 Matroska 的 AV1 映射要求。默认策略会拒绝这 6 项；显式允许整段编码后，6 项均通过。

这不是通用 MKV 产品发布：研究原型仍有输入范围限制，尤其是 `CodecState` 输入、部分 HEVC 的逐行标识、亚毫秒时间精度，以及播放器覆盖。正式产品入口尚未增加 MKV 支持。

| 比较对象 | AVC | HEVC | AV1 | 合计：通过本轮检查 / 尝试 |
| --- | ---: | ---: | ---: | ---: |
| 本研究原型，开放不合规 AV1 反例输出 | 40/40 | 35/35 | 29/35 | **104/110** |
| DeepRegular SmartCut v0.8.37 官方 Rust CLI | 31/40 | 26/35 | 7/35 | **64/110** |
| skeskinen/smartcut + PyAV 16.0.1 | 25/40 | 17/35 | 5/35 | **47/110** |

表中失败并不等于文件全部无法播放。DeepRegular 的 110 个输出都通过严格解码及帧数检查；它的失败主要是时间轴、AV1 容器约束和 SAR。skeskinen 有 10 项 HEVC 在 mux 阶段退出，其余 100 项通过严格解码及帧数检查。各项目使用的 FFmpeg、编码器和默认参数不同，此表不是速度或编码质量排名。

完整证据：[汇总](evidence/summary.json)、[原型](evidence/prototype.json)、[上游](evidence/upstream.json)、[消融与策略](evidence/experiments.json)、[音频字幕](evidence/streams.json)、[边界限制](evidence/edges.json)。

## 2. MKV 与 MP4 的关键差别

| 问题 | 现有 MP4 实现 | MKV 方案 |
| --- | --- | --- |
| 视频标识 | avc1/avc3、hvc1/hev1、av01 | `V_MPEG4/ISO/AVC`、`V_MPEGH/ISO/HEVC`、`V_AV1`；没有 avc1/avc3 的标签选择 |
| 初始配置 | stsd 中的 avcC/hvcC/av1C | TrackEntry 的 CodecPrivate，编码配置结构与上述记录有对应关系 |
| 初始化更新 | NEW_EXTRADATA 可驱动 movenc 创建/选择 sample description | 规范有 CodecState/CueCodecState；不能假定 FFmpeg 实现了这条路径 |
| 视频数据位置 | 已验证范围内可按 MP4 sample 偏移读取 | `AVPacket.pos` 不保证是数据正文；有 Block 头、lacing、压缩/头移除等封装 |
| 时间 | DTS 表、PTS 偏移、edit list、轨道时钟 | Block 时间是显示时间，数据顺序承担解码顺序；不能要求输入存在 MP4 式 DTS 表 |
| 可变帧率与末帧 | sample duration、轨道时长 | BlockDuration、DefaultDuration、后续 PTS、Segment Duration 共同影响显示时长 |
| 随机跳转 | 同步样本表及描述切换 | Cues + 可独立解码的访问点 + 该点可取得的正确配置 |

Matroska 的时间和引用规则见 [RFC 9559](https://www.rfc-editor.org/rfc/rfc9559.html) 与 [Specification Notes](https://www.matroska.org/technical/notes.html)。编码映射见 [Codec Mappings](https://www.matroska.org/technical/codec_specs.html)。因此，改文件扩展名或仅把 `avformat_alloc_output_context2` 的格式名改为 `matroska`，不足以完成产品适配。

### 2.1 FFmpeg 9.0.2 的实际能力

已下载并审阅固定标签 n9.0.2 的 matroskaenc.c、matroskadec.c 和 AVC/HEVC/AV1 封装辅助代码，文件哈希见 [manifest.json](evidence/manifest.json)。关键位置：

- `mkv_check_new_extra_data`：AVC/HEVC 的新 extradata 进入忽略分支；AV1 分支主要补充最初缺失的配置，不是任意视频配置切换。
- `mkv_init`：输出轨道时基被设置为 `1/1000`；`mkv_write_header` 写入 1,000,000 ns 的 TimestampScale。
- `mkv_write_block`：在实际 duration 偏离已声明默认时长等条件下写 BlockDuration。
- Matroska demuxer 的 BlockGroup 语法表将 `MATROSKA_ID_CODECSTATE` 标为 `EBML_NONE`。

这些是本轮读取的实现事实；不能由 `AV_PKT_DATA_NEW_EXTRADATA` 是公共 API 推导出所有容器都支持相同语义。[FFmpeg n9.0.2 muxer 源码](https://github.com/FFmpeg/FFmpeg/blob/n9.0.2/libavformat/matroskaenc.c)、[demuxer 源码](https://github.com/FFmpeg/FFmpeg/blob/n9.0.2/libavformat/matroskadec.c)。

### 2.2 CodecState 的定位

Matroska 为初始化数据更新定义 CodecState，并用 CueCodecState 帮助跳转恢复。MKVToolNix 的 `cluster_helper.cpp` 确实包含写入 KaxCodecState 的分支，说明这个机制不是 MP4 的专属概念。但“容器有这个元素”“某个 muxer 能写”“FFmpeg 能消费”“播放器跳转正确”是不同能力，必须分别验证。

本原型采取 AVC/HEVC 带内参数集策略，未实现通用 CodecState 更新，也未实现 CodecState 输入的自动识别和处理。研究范围内的流保持尺寸、位深、色彩等轨道级属性稳定；Track 的初始化配置取第一个实际输出区间，后续独立访问点携带实际使用的参数集。本轮证明的是这一路径的解码与跳转兼容性，**不把它宣称为所有初始化变化都满足完整 Matroska 规范的证明**。如果产品承诺显式的初始化更替协议，需要补齐 mux/demux 的 CodecState、CueCodecState 支持和独立播放器验证；不应假装当前的带内实现已经完成了它。

## 3. 参考项目怎么做

### DeepRegular SmartCut

本轮运行的是 **v0.8.37 官方 Rust CLI**，源码固定为 `d670099e9df622b9b9120fc46411e6154985b269`，不是早期 Python 参考实现。它直接接受 MKV 输入并输出 MKV。

`rust/crates/core/src/cut.rs` 中约 9730—9900 行区分 ISO 容器与使用 NAL 长度前缀的容器；Matroska 属于后者。它为复制关键画面补源参数集，为新编码画面保留新参数集，并使数据 framing 与 CodecPrivate 一致。MP4 的 avc3/hev1 标签不会套到 MKV。`rate_to_state` 和 Grid 处理输出帧率与时间格点；另有音频、字幕和附件分支。

值得采用的部分是：**按容器选择 framing、按实际独立访问点携带配置、独立处理音频字幕、不给 MKV 塞 MP4 私有选项**。本轮不足是：按帧率格点重建显示时间会改变已有 MKV 时间轴；AV1 混合输出没有本研究的单 Sequence Header 准入检查。

源码：[固定提交 cut.rs](https://github.com/DeepRegular/SmartCut/blob/d670099e9df622b9b9120fc46411e6154985b269/rust/crates/core/src/cut.rs)。本地审阅文件哈希和官方二进制哈希已归档。

### skeskinen/smartcut

固定提交 `73821ea01c29a4dca81a8865ef7d348d04bb0810`。`video_cutter.py` 复用 PyAV 的 stream 模板、`h264_mp4toannexb` / `hevc_mp4toannexb`、边界编码与 mux；H.264 编码默认使用 `sps-id=3`，HEVC 尝试匹配部分源参数并开启 repeat headers。

它的 `_fix_packet_timestamps` 包含缺失 DTS 的兜底逻辑。在一次 HEVC Open GOP 的失败复现中，日志记录输入包 `PTS=-520, DTS=None`，修复后成为 `PTS=-520, DTS=201`，muxer 随即报 `pts (-520) < dts (201)`。这个包走的是 `DTS is None` 分支，前一个分支中的 PTS/DTS 修正没有覆盖它。**这次失败能定位到时间调度，而不是由“MKV 不支持 HEVC”解释。**

诊断仅给原函数加前后记录，未改变参数或返回结果；证据见 [trace-skeskinen.json](evidence/trace-skeskinen.json)、[复现脚本](tests/trace_skeskinen.py)。其 PyAV 16.0.1 使用 libavformat 62.3.100、libavcodec 62.11.100，不能把其表现全部归因于本轮 FFmpeg 9.0.2。

源码：[固定提交 video_cutter.py](https://github.com/skeskinen/smartcut/blob/73821ea01c29a4dca81a8865ef7d348d04bb0810/smartcut/video_cutter.py)。

### MKVToolNix / mkvmerge

采用官方 **103.0** 便携版及对应源码。`cluster_helper_c::split_if_necessary` 在不是关键画面时直接返回，分割动作只在符合条件的包上发生。它的 `p_avc.cpp` / `p_hevc.cpp` / `p_av1.cpp` 负责编码映射、元数据和 packetizer，不会替用户重新编码任意切点。

对本轮 AVC、HEVC Open GOP、AV1 各一个输入，请求保留 0.520—9.080 秒，即帧 `[13,227)`。mkvmerge 实际输出的显示帧为 **24—239，共 216 帧**，其解码像素均与原始对应帧一致。这是关键帧边界调整后的复制，不是任意帧 Smart Rendering 的同一验收目标。

其 `--timestamp-scale` 可写比 FFmpeg 默认更细的时钟；本轮用 1000 ns 制作了微秒时基的 MKV 反例输入。官方说明见 [mkvmerge 手册](https://mkvtoolnix.download/doc/mkvmerge.html)，固定源码见 [103.0 cluster_helper.cpp](https://codeberg.org/mbunkus/mkvtoolnix/src/tag/release-103.0/src/merge/cluster_helper.cpp)。

### LosslessCut 的参考范围

沿用本地已下载版本对其 FFmpeg 命令编排和拼接设计的理解；这次没有额外运行 LosslessCut GUI，也没有将历史 MP4 测试结果冒充为 MKV 测试。它可提供命令级拼接的对照思路，但本次 MKV 的量化对比以以上两个实际 Smart Rendering 实现及 mkvmerge/FFmpeg 控制实验为依据。

## 4. 本项目建议采用的方案

### 4.1 解封装和索引

1. 用 libavformat 原样解封装，消费其交付的 AVPacket。允许 MKV 的视频 DTS 缺失，保留 PTS 和物理解码顺序。
2. 将视频包正文写入临时 spool，以 spool 内偏移建立索引；Block 解压、头恢复、lacing 拆包都由 FFmpeg 完成。保留并验证 side data，不把原始 `packet.pos` 当正文地址。
3. 复用原有真实访问点检查：AVC IDR；HEVC IDR/CRA/RASL/RADL；AV1 Sequence Header 与显示 KEY_FRAME。容器 KEY 标志只作线索。
4. 按源 PTS 排序得到显示序号，以真实间隔规划选区。当前原型仍要求输入固定编码配置、单层、4:2:0 8/10 bit 等原有受支持范围。

spool 避免把全部压缩载荷驻留内存，但索引本身仍是 O(帧数)。正式实现可抽象成 `PacketSource`，用解封装重放/访问点 seek 或仅缓存相关 GOP 优化磁盘开销；未验证能定位原始正文之前，不应恢复按 MKV 文件偏移直接复制的捷径。

### 4.2 AVC / HEVC：边界算法继续复用

AVC 复制完整独立区间，切点涉及的区间重新编码；只有一个 IDR 的长 Open GOP 仍可能导致整个选区编码。HEVC 保留原有 CRA/RASL 处理：需要时先编码保留的前导画面，对符合条件、无 RADL 的非连续 CRA 重入执行 CRA→BLA，而不是任意修改所有 CRA。

新旧区间各自使用匹配的实际参数集。通过 FFmpeg BSF 规范化为 Annex B，再由 Matroska muxer 转为正确的 NAL 长度前缀。输出 header 的 extradata framing 必须与喂给 muxer 的 packet framing 配套。

**在配置切换处以及后续每一个独立访问点都恢复当前 SPS/PPS 或 VPS/SPS/PPS。** 只在拼接缝处恢复不足以支持新的解码器直接跳到该区间内部的另一个关键画面。

原型的 `filter_units` 用于先移除参数集，再精确控制恢复位置，以便做消融；正式产品可以避免不必要的重复过滤，但必须保留等价的“每个访问点可恢复配置”不变量。

### 4.3 AV1：先验证整个 Segment 的配置

Matroska 的 V_AV1 映射明确限定一个 Segment 内支持一份 Sequence Header；这个限制不同于 MP4 中通过多个 sample description 关联不同 av1C 的方式。[V_AV1 映射](https://www.matroska.org/technical/codec_specs.html#v_av1)

AV1 编码后、写输出之前，应收集所有实际输出区间的 Sequence Header：

- 只有一种：允许局部编码与复制；不能仅比较 codec id、尺寸或数值 level。
- 有不同配置：默认报错；明确请求回退时，对所有选区完整编码，并重新检查配置唯一性。
- 纯复制或全部选中内容刚好只需要一种新配置：按实际输出判定，不因为输入曾使用别的配置就拒绝。

本原型以 Sequence Header 字节一致作为保守准入条件，没有尝试证明不同字节在语义上等价。它也没有修改 OBU 的 sequence flags 来迎合旧头；这种修改可能改变后续 frame header / tile 的解释。

`--av1-policy full` 是明确的成本变化：以 SVT/rav1e 输入 `[13,227)` 为例，局部方案可复制 203 帧、编码 11 帧；配置不一致而整段回退后，复制为 0、编码为 214。报告会明确写出 `explicit-full-reencode`。这不能描述为“成功的无损智能复制”。

### 4.4 时间、时长和 Cues

输入 MKV 不保存可直接复用的 MP4 DTS 表，但 libavformat 的交织 API 仍需要合理的包调度。原型沿用显示序号与编码输出顺序建立统一解码时间槽，计算足够的重排延迟；**不会通过推高 PTS 来消除 DTS 冲突**。

MKV muxer 写的是 PTS。输出视频 duration 使用该画面的显示保持时间，而不是照搬 MP4 调度中最后一个解码包的 duration。给 muxer 提供有效 nominal rate 与实际 duration，使 FFmpeg 9.0.2 能在非默认时长处写 BlockDuration。帧率信息用于声明默认时长，不用于把 VFR 重采样成 CFR。

NTSC 输入有一个具体适配：Matroska 的 DefaultDuration 可以是纳秒值，FFmpeg 交付的 PTS 却可能为 33/34 ms 交替。沿用 MP4 的严格 `相邻 PTS 差 = 上一包 duration` 检查会误拒绝这类输入。原型只对 MKV、且差异不超过一个输入 tick 时，用下一显示 PTS 恢复上一帧间隔；更大的不连续仍拒绝。

写头后必须重新读取输出 `time_base` 再 rescale。FFmpeg 9.0.2 默认 MKV 精度为 1 ms，无法保留任意 MP4/高精度 MKV 的全部时间分辨率。本轮普通 MKV→MKV 的显示 PTS 误差均为 0；微秒输入→MKV 的最大误差为 0.5 ms。2000 fps、0.5 ms 相邻帧的测试因时间槽重叠被拒绝，不通过改成 CFR 或吞帧掩盖限制。

Cues 交给 FFmpeg 写入；以真实独立访问点设置 KEY 标志。必须用全新进程/解码器从 Cues 附近跳转，再与线性解码结果逐帧比较，不能仅从头播放一次。

## 5. 测试设计与素材身份

22 个固定输入，每个 240 帧：

| 编码 | 素材 |
| --- | --- |
| AVC，8 个 | closed_b3_id0、closed_b3_id3、open_gop、mixed_open_idr、high10、ntsc、vfr、fullrange_sar |
| HEVC，7 个 | closed、open、main10、vfr、ntsc、radl、color_sar |
| AV1，7 个 | aom、svt、main10、vfr、ntsc、rav1e、color_sar |

每个输入运行五种选区：partial `[13,227)`；multi `[13,81)+[100,227)`；aligned `[24,216)`；one `[23,24)`；tail `[213,240)`。`aligned` 只是固定用例名，对所有编码结构不意味着一定恰好是安全访问点，最终由各实现决定。

输入由历史带条码的 MP4 原包转换而来，没有重新编码。每个 MKV 都核对了压缩包、像素和 PTS；文件 SHA-256、来源路径和命令记录在 [fixtures.json](evidence/fixtures.json)。原始生成脚本及构建方式见 [README](README.md)。

检查器与原型规划器分离，检查：

- FFmpeg 严格解码是否报错、帧数、画面条码身份及实际解码显示 PTS。
- 计划复制的位置是否同时保持 VCL/AV1 画面 OBU 和像素一致；HEVC 仅允许已定义的 CRA/BLA NAL 类型差异。
- 重编码画面是否仍为正确帧，并记录 PSNR，30 dB 为合成素材的最低筛查阈值。
- 尺寸、像素格式、SAR、颜色属性、Segment 时长、Block 的显式或默认持续时间。
- AV1 实际 Sequence Header 种类数、CodecPrivate 的 av1C 必要字段是否匹配。
- partial/multi 每例进行三次全新解码器跳转，共 **132 次原型跳转检查**。

MKV 没有单独存储原始 DTS，因此这里没有把 ffprobe 推算的 DTS 当成原始容器 DTS 进行严格相等断言。无 BlockDuration 和 DefaultDuration 时，检查器从下一显示时间/Segment 末端推断保持时长，不将缺省解释为零。本轮最终上游证据使用修正后的检查器重新审计已有文件，没有重新导出以改变结果。

## 6. 主要测试结果及问题归因

### 6.1 本项目原型

AVC 40/40、HEVC 35/35 通过；复制帧的画面载荷与解码像素一致。两组各有 48、42 次跳转检查通过。编码部分最低 PSNR 分别约 39.56、39.90 dB；这些只是合成素材的对应画面检查，不是编码器质量排名。

AV1 35 项全部严格解码、条码身份、显示时间、复制画面和抽查跳转正确；其中 SVT、rav1e 各三个 mixed 用例包含不同 Sequence Header，所以只有 **29/35** 通过全部判定。rav1e 的三个 mixed 用例还存在 CodecPrivate 数值 level 与实际序列不一致。将“能解码”当成“可正式支持”会遗漏这些问题。

额外对这六个失败用例测试默认拒绝策略，**6/6 在发布前拒绝**；测试显式整段编码，**6/6 输出通过**。单帧用例只输出一种新配置、aligned 纯复制用例只输出一种源配置，均可正常输出。

典型 `[13,227)` 的工作量：

| 输入 | 复制 | 编码 | 为边界实际解码 |
| --- | ---: | ---: | ---: |
| AVC closed_b3_id0 | 192 | 22 | 35 |
| AVC open_gop | 0 | 214 | 227 |
| HEVC open | 189 | 25 | 80 |
| AV1 aom | 203 | 11 | 24 |

MKV 并不改变参考依赖。AVC Open GOP 的保守重编码范围依旧存在；不能因换容器就承诺缩成每个 I 帧附近几帧。

### 6.2 DeepRegular

- 常规 CFR AVC/HEVC、Main10、RASL/RADL 等输入表现良好，支持本研究继续采用独立访问点+正确参数集的判断。
- 三类编码的 VFR 五种选区均未通过整体时间/时长检查；主矩阵中的最大 PTS 偏移为 **36 ms**。
- NTSC 的多个用例出现 **1 ms** 级偏移。这里比较的是已量化的源 MKV 时间戳；它不是解码失败，也不能与一个新容器不可避免的半 tick 量化混为一谈。
- AV1 22 项出现不同 Sequence Header；5 项存在 CodecPrivate 字段冲突，5 项丢失源 SAR。问题有重叠，不能把分类计数相加当失败文件数。

时间问题与 `Grid` / half-frame slot、nominal rate 路径吻合；AV1 问题与单份 Track 配置和编码/复制混合输出有关。源码定位与测试一起支持这些判断，但未将所有现象归为同一个根因。

### 6.3 skeskinen

- 100 个成功导出的文件全部通过严格解码和帧数检查，源 PTS 保留较好。
- 10 个 HEVC partial/multi 导出退出。额外日志确认其中 Open GOP 复现的直接错误是 `PTS < DTS`；其他九项具有同一 mux 报错类别，未逐个进行相同插桩，不能声称已全部独立证明根因。
- 多项单帧输出的 Segment Duration 为 0 或不符合该帧保持时间；部分 AV1 输出声明了 1 ms 默认帧时长，虽有正确 PTS，持续时间仍不正确。
- AV1 有 22 项多 Sequence Header、9 项 CodecPrivate 字段冲突；两个 Main10 用例的跳转检查失败，SAR 用例也有丢失。

### 6.4 消融证明哪些处理有用

| 实验 | AVC / HEVC 结果 | 含义 |
| --- | --- | --- |
| 只通过 NEW_EXTRADATA 传配置，删除带内参数集 | 2/2 严格解码失败 | Matroska muxer 不会替这些视频写配置切换 |
| 只在拼接缝恢复参数集，后续编码关键帧不恢复 | 2/2 线性解码通过，尾部新进程跳转失败 | “接缝能播放”不够，每个可跳转点需要配置 |
| 每个独立访问点恢复当前参数集 | 上述对应 4 项均通过 | 这是本轮明确需要保留的处理 |
| FFmpeg 8.1 原包重封装本轮 VFR 输入 | AVC/HEVC 保持 PTS，但 BlockDuration 检查失败；AV1 对照通过 | 需固定并验证 muxer 版本，不能只验证压缩包 |

跳转失败的 AVC 日志有 intra4x4 解码错误，HEVC 有 `alignment_bit_equal_to_one=0`。这直接支持参数集恢复的必要性，而不是根据项目代码复杂程度作判断。

## 7. 音频、字幕、附件和章节

建议沿用用户既定产品语义：原版 FFmpeg 对整个选区复制或编码音频、处理字幕，再与智能渲染视频合流。对 MKV，辅助容器改为 MKV，避免 MP4 辅助文件无法承载 ASS、字体附件等内容。

本轮构造包含 AAC、Opus、SRT、ASS、字体附件、语言标记和两个章节的 MKV。分别测试 AAC copy、Opus copy、AAC encode、Opus encode、FLAC encode：**5/5 通过**。辅助输出与最终合流输出的非视频包 PTS、duration、压缩数据 hash、side data、音频 PCM、字幕、附件元数据、章节保持一致；Opus 的 CodecDelay/SeekPreRoll 和 DiscardPadding 可由 FFmpeg 的参数与 side data 路径继续承载。

这验证的是“第二次 MKV mux 不改变原版 FFmpeg 已做出的选择”。**没有声称音频 copy 自动具备采样级任意切点精度**：copy 仍遵循音频编码帧边界，重编码路径才由 FFmpeg 处理采样裁剪、编码延迟等问题。字幕跨越选区边界时也沿用 FFmpeg 本身的语义。

正式实现还需改变现有辅助流的身份映射：不能假定 MKV 的 TrackNumber/stream id 和 MP4 track id 有相同规则。应保存显式映射清单，并验证音轨/字幕类型、顺序和元数据。附件作为轨道 extradata 处理，不能放进普通有时间戳的视频包循环。复杂 ordered chapters、外部 Segment 链接和所有附件 MIME 类型尚未覆盖。

## 8. 有用、可复用及不必照搬的技术

| 技术 | 结论与证据 |
| --- | --- |
| 区分 IDR/CRA/RASL/RADL、真实 AV1 恢复点 | 必要；与容器无关，继续复用已有规划核心 |
| 匹配当前画面数据的真实参数集 | 必要；删除带内参数集的两个反例均失败 |
| 访问点重复参数集 | 必要；只补接缝会造成新的解码器跳转失败 |
| 源时间驱动 VFR，而非平均帧率重建 | 必要；上游 VFR 出现实测偏移，本原型保持输入 MKV PTS |
| 显式处理最后显示帧的持续时间 | 必要；单帧和 VFR 是能定位问题的反例 |
| AV1 输出配置准入检查 | MKV 特有的关键约束；不能复用多 stsd 的解法 |
| 固定 `sps-id=3` 或为所有边界分配新 ID | 本范围内不必；源 ID 0、ID 3 都通过，不靠 ID 隔离代替真实配置恢复 |
| 强行让边界编码器复制源 ref/B 帧/GOP 配置 | 本范围内不必；独立入口、实际新参数集和正确调度已足够；AV1 则另受固定 SH 约束 |
| MP4 的 avc3/hev1 标签、stsd、edit list、movie timescale、negative_cts_offsets | 不搬入 MKV 输出策略 |
| 手写完整 Matroska muxer、手写音频编码器 | 不需要；现有 FFmpeg 已完成主要工作 |
| CodecState/CueCodecState | 不能当现成 FFmpeg 功能；若产品承诺通用初始化更新，应作为额外能力实现与验证 |

“本范围内不必”不是对所有编码结构、播放器和扩展格式的普遍证明。特别是不同序列的 AV1，参数集 ID 技巧不存在可直接照搬的对应方案。

## 9. 已知不足和产品接入边界

1. **CodecState 输入仍未处理。** FFmpeg 当前解封装路径忽略它，而原型没有独立的 EBML 能力预检。正式产品必须能检测并拒绝这类输入，或扩展 FFmpeg 暴露/消费该状态；不能只靠当前包扫描宣称支持所有 MKV。
2. **部分 HEVC MKV 缺少明确逐行标识。** mkvmerge 生成的 zlib HEVC 样本可被 FFmpeg 解码，但 libavformat 交付的 field_order 未被本原型识别为 progressive，因此被现有输入契约拒绝；即使测试时显式指定 `--field-order 0:0` 仍未解决。AVC、AV1 的 zlib 输入实验通过。后续应通过可靠的码流/解码信息完善逐行判断，不能直接删除限制。
3. **时间分辨率受现有 FFmpeg MKV muxer 限制。** 微秒输入会损失最多半个毫秒量化步长；2000 fps 用例被拒绝。若要精确保留亚毫秒时钟，应扩展 FFmpeg 的 Matroska TimestampScale 支持或采用已经验证的其他 muxer，而不是修补输出文件字节。
4. **配置输入范围仍窄。** 不支持任意动态 SPS/VPS/SH、分辨率变化、加密、多层视频；复杂 HEVC/AV1 参考结构沿用既有研究范围。
5. **性能尚未做长片基准。** 当前 spool 多一次压缩载荷写入和读取；索引仍全扫描。小型合成素材只能证明剪辑正确性与复制比例，不能给出长片速度结论。
6. **播放器覆盖有限。** 132 次主矩阵跳转及额外反例用 FFmpeg 软件解码；未完成 VLC、mpv、Windows 系统解码和多家硬解器的完整矩阵，也未进行 GUI 字幕渲染验证。
7. **原型不是完整 CLI 产品。** 一个视频轨、独立音频字幕实验；完整 stream map、容器选项、附件选择、复杂元数据及异常输入安全检查仍需产品接入工作。

建议正式代码引入两个边界：`PacketSource` 负责容器无关的包取得与时间信息，`ContainerPolicy` 负责初始化配置、时钟精度、随机访问恢复和辅助流封装。`video.cpp` 的 AVC/HEVC 规划、`av1.cpp` 的 TU 规划和编码器 AVOptions 继续共享；`app.cpp` 分别选择 MP4 或 MKV 输出策略。MKV 的限制与回退不应改变现有 MP4 的多配置输出能力。

## 10. 交付与证据范围

交付包含 CMake/MSVC 原型、构建时核心适配脚本、独立检查器、22 个输入身份记录、330 次主矩阵导出尝试、33 项消融/策略/控制实验、5 项音频字幕测试、2 项额外边界测试，以及一次 skeskinen 日志诊断复跑。

实验里包含故意移除必要功能的失败反例、明确拒绝的用例和已知未解决的输入，不能把这些总数包装为“370 项全部通过”。归档保留了每项的命令、输出哈希、错误和检查结果。复现入口见 [README](README.md)，源码版本和下载哈希见 [manifest.json](evidence/manifest.json)。

视频、构建二进制、临时 spool 及下载的上游项目只保存在本地忽略目录；研究文本、代码和证据可单独进入远程仓库。本轮未改写根 README，也未提交或推送正式产品改动。
