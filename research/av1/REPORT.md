# MP4 / AV1 Smart Rendering：源码对比、方案与验证

研究日期：2026-10-30。范围：MP4 / AV1 视频；音频、字幕及杜比技术不在本轮研究中。源码、生成脚本与证据保存在同一研究目录，媒体和上游下载文件只保留本地。

## 结论

**DeepRegular SmartCut 的 AV1 核心思路值得采用：在可靠的随机访问点恢复原始 Sequence Header 和参考帧状态，重编码受切点影响的部分，直接复制其余 Temporal Unit。AV1 不需要照搬 H.264 / HEVC 的 SPS/PPS ID 分配、POC 改写或 CRA→BLA 处理。** 但“码流可以拼接”没有自动解决 MP4 的配置描述、源时间轴和显示属性保存。

本轮先运行未修改的上游实现，再实现 C++ / FFmpeg 原型。11 类相同输入、8 种剪切模式的完整检查通过数如下：

| 实现 | 全部检查通过 | 结果含义 |
| --- | ---: | --- |
| DeepRegular SmartCut **官方 Rust CLI v0.8.37** | **60/88** | 原始压缩区间确实被复制；失败集中在 VFR 时间轴、部分 av1C 字段和 SAR |
| skeskinen/smartcut | **35/88** | 原始压缩区间同样被复制；末帧时长、配置和显示属性存在问题 |
| 本研究 C++ 原型，libaom 编码边界 | **88/88** | 通过选帧、严格解码、精确时间、复制区间、显示属性与容器检查 |
| 本研究 C++ 原型，SVT-AV1 编码边界 | **32/32** | 在 SVT、Main10、VFR、NVENC 四类输入上通过同样的 8 种模式 |

两套上游的 88 个输出均能通过最终的 dav1d 严格解码和帧数检查，且完整可复制区间的包与像素一致。**60/88 和 35/88 不是播放成功率**；它们是把时间戳、时长、SAR、颜色及 MP4 配置一致性也计入后的通过数。不能据此宣称其他真实视频的总体成功率。

原型用 FFmpeg 公共 API 完成解码、编码与封装，只自行实现轻量 OBU 检查、切段规划、源时间轴映射及配置切换通知。没有修改 FFmpeg，也没有引入完整 AV1 解码器或参考帧依赖图。

另有一个实际有效的简化：**已从有效随机访问点开始复制后，AV1 的结束点可以直接截在完整 Temporal Unit 边界**。11 类素材的组件实验全部通过，无需重编码尾部。主原型目前保留“两端重编码”的保守基线，后续产品方案应采用这项已验证的优化。

## 研究对象与版本

不能将 DeepRegular 旧 Python 参考实现的结果当作当前 Rust 引擎的结果。本次检出的 `v0.8.37` 与所测官方发行包相对应，源码和 skeskinen 工作树均没有修改。

| 项目 | 固定版本 / 提交 | 本轮工作 |
| --- | --- | --- |
| [DeepRegular/SmartCut](https://github.com/DeepRegular/SmartCut/tree/d670099e9df622b9b9120fc46411e6154985b269) | `v0.8.37` / `d670099e9df622b9b9120fc46411e6154985b269` | 重点源码分析；官方 Windows Rust CLI 88 项；另强制扫描索引复测 VFR 8 项 |
| [skeskinen/smartcut](https://github.com/skeskinen/smartcut/tree/73821ea01c29a4dca81a8865ef7d348d04bb0810) | `73821ea01c29a4dca81a8865ef7d348d04bb0810` | Python / PyAV 源码分析；原实现 88 项 |
| [MediaMolder/mediamolder](https://github.com/MediaMolder/mediamolder/tree/9e51f8f761588e2e3c5ef87e9f11dfd5c091ac58) | `9e51f8f761588e2e3c5ef87e9f11dfd5c091ac58` | AV1 smartcopy 分支源码分析，未做运行评分 |
| [mifi/lossless-cut](https://github.com/mifi/lossless-cut/tree/3ef357d88d51d8a2a36e5bf3c041b3a269bfbc5a) | `3ef357d88d51d8a2a36e5bf3c041b3a269bfbc5a` | 当前 Smart Cut 源码分析，未做 AV1 GUI 运行评分 |

DeepRegular 官方发行包 SHA-256 为 `6e0e9a787149ed6b0391c524c7bdd23582697e1d94893b14f17af211af423d2f`；CLI SHA-256 为 `8ccd600af97d5f1f8ae9ad029624126540c2ba8556158af4efd79dc3b2386175`。它使用原配 avcodec 61.19.101 / avformat 61.7.100 / avutil 59.39.100，日志显示 SVT-AV1 `3.0.0-18-gd653d6c1`。

本研究原型与检查器使用 FFmpeg `n8.1.3-24-g92611900bb-20261025`；第二编码器为 SVT-AV1 `4.2.0-151-g5ed9ffbcb`。skeskinen 使用 PyAV 16.0.1，内含 avcodec 62.11.100 / avformat 62.3.100。编译环境为 Windows 10.0.26200、MSVC 19.51.36260、CMake 4.3.1-msvc1、Windows SDK 10.0.26100.0；Python 3.12.14、NumPy 2.3.5。设备为 Core Ultra 9 275HX、RTX 5060 Laptop、驱动 617.42。

这些环境不同，因此本轮比较**功能正确性和复制行为**，没有控制到足以比较各项目的编码速度、压缩率或最终画质。逐例 `seconds` 包含独立检查耗时，不能作为导出性能数据。完整版本、源码及二进制哈希见 [audit.json](evidence/audit.json)。

## AV1 的剪切单位，以及与 H.264 / H.265 的区别

本研究把两个经过检查的随机访问点之间称为一个“可独立复制区间”。代码中的 `keys` / 测试里的 GOP 指这个区间，不能直接套用 H.264 的 I/P/B 帧含义。

| 对象 | 在此方案中的用途 |
| --- | --- |
| OBU | AV1 的结构单元；读取类型、大小和少量头字段，视频 tile 数据原样保留 |
| Sequence Header | 保存当前编码序列的配置；新编码器与源编码器可以生成不同配置 |
| Temporal Unit（TU） | 本轮 MP4 sample / AVPacket 的处理单位；一个 TU 可含隐藏帧和一张显示画面 |
| shown KEY_FRAME | 作为复制入口；同时要求其前有对应 Sequence Header |
| hidden frame / show_existing_frame | 前者提前建立参考画面，后者显示此前保存的画面；必须保留所在 TU 的完整内容 |

本次素材并非只有顺序显示的简单帧：AOM 输入有 71 个隐藏帧、54 次 show_existing；SVT 和 NVENC 均有 110 个隐藏帧和 110 次 show_existing；rav1e 为 109 / 109。直接把“一个编码帧”等同于“一个显示帧”会造成错误计数或剪切。

AV1 的 MP4 绑定以 TU 组织 sample；依赖来自当前 TU 较早的帧或之前的 TU。shown key frame 提供随机访问恢复点。`av01` sample entry 可以有多个，所关联的 av1C 必须适用于对应 samples；配置中的 profile、level、位深等字段须与有效 Sequence Header 一致。AV1 track 不使用 composition offset。上述约束分别见 [AV1 ISOBMFF v1.3.0 §1、§2.2–2.4](https://aomediacodec.github.io/av1-isobmff/v1.3.0.html)。

由此得到本研究采用的限制：只从 **Sequence Header + shown KEY_FRAME** 开始一个复制区间，不把普通 INTRA_ONLY、SWITCH_FRAME 或 show_existing 当作恢复入口；输入输出的时间单位是 TU，输出使用 `DTS = PTS`。隐藏帧已经封装在 TU 内，不需要另外为它们发明一条 H.264 式重排序时间轴。

## DeepRegular SmartCut 的实现

### 1. 编码器选择和边界编码

[`encoders_for`](https://github.com/DeepRegular/SmartCut/blob/d670099e9df622b9b9120fc46411e6154985b269/rust/crates/core/src/cut.rs#L4971) 明确按 `libsvtav1 → librav1e → libaom-av1` 顺序尝试，而不是只按 AV1 codec ID 取第一个编码器。实际打开失败还能换下一种。这解决了“库里存在编码器”和“编码器能处理这类输入”不相等的问题。

[`open_encoder_as`](https://github.com/DeepRegular/SmartCut/blob/d670099e9df622b9b9120fc46411e6154985b269/rust/crates/core/src/cut.rs#L5012) 传递尺寸、像素格式和颜色等参数，设置适合短边界编码的速度选项。AV1 特别跳过直接复制解码器的 `level`：解码侧的 `seq_level_idx` 与 SVT 编码选项的数值约定不同，照搬可能连编码器都无法打开。这个处理有实际价值，原型同样不照搬该字段。

边界通过 [`reencode_segment`](https://github.com/DeepRegular/SmartCut/blob/d670099e9df622b9b9120fc46411e6154985b269/rust/crates/core/src/cut.rs#L5711) 解码到选择的显示画面，再送入新的编码器；完整区间交给 [`copy_segment`](https://github.com/DeepRegular/SmartCut/blob/d670099e9df622b9b9120fc46411e6154985b269/rust/crates/core/src/cut.rs#L4142)。AV1 没有走 AVC/HEVC 那套 NAL 参数集、POC、参考标记重写。

### 2. 为什么通用复制路径能工作

新的边界序列以自己的 Sequence Header 和 key frame 开始。恢复源片段时，源 key frame 前的 Sequence Header 再次建立原配置，并由随机访问点恢复参考状态。因此新旧编码器生成的不同工具选项不必被强制改成一样，也不需要像 SPS ID 那样分配另一组编号。

这与项目自己的 [AV1 / VP9 设计说明](https://github.com/DeepRegular/SmartCut/blob/d670099e9df622b9b9120fc46411e6154985b269/docs/technical/rust-core.md#L694) 一致。它的结论应理解为“AV1 的这条码流拼接路径无需 AVC/HEVC 式修补”；本轮反例表明，MP4 配置和时间轴仍需单独检查。

本次全部 88 项 DeepRegular 输出都保留了应复制的完整区间，复制画面逐像素一致。这个结果直接支持其基本架构，而不是因为部分检查失败就否定整套通用路径。

### 3. 时间轴机制与 VFR 漏检

DeepRegular 使用 [`Grid`](https://github.com/DeepRegular/SmartCut/blob/d670099e9df622b9b9120fc46411e6154985b269/rust/crates/core/src/cut.rs#L5293) 表示通用显示时间。检测到 VFR 后会采用更细的网格；没有检测到则按名义帧率的半帧网格放置。最终 [`Writer::emit_one`](https://github.com/DeepRegular/SmartCut/blob/d670099e9df622b9b9120fc46411e6154985b269/rust/crates/core/src/cut.rs#L1375) 用显示位置乘 `field_ticks` 写出 PTS。

本轮 VFR 输入的帧间隔为 40 / 40 / 80 ms，平均帧率 `6000/319 ≈ 18.809`，`r_frame_rate = 25`。初始判断 [`frame_rate > base_rate * 1.0001`](https://github.com/DeepRegular/SmartCut/blob/d670099e9df622b9b9120fc46411e6154985b269/rust/crates/core/src/lib.rs#L2182) 不成立；扫描判断 [`varies`](https://github.com/DeepRegular/SmartCut/blob/d670099e9df622b9b9120fc46411e6154985b269/rust/crates/core/src/index.rs#L387) 的阈值是大于典型间隔的 1.75 倍或小于 0.5 倍，这组素材也未触发。

于是即使是整段复制，240 个压缩包和画面都未改变，PTS 仍被量化：

| 前 9 帧 PTS，秒 | 数值 |
| --- | --- |
| 源视频 | 0, 0.04, 0.08, 0.16, 0.20, 0.24, 0.32, 0.36, 0.40 |
| DeepRegular 输出 | 0, 0.0531667, 0.07975, 0.1595, 0.2126667, 0.23925, 0.319, 0.3721667, 0.39875 |

最大显示 PTS 误差为 **13.25 ms**。这不是严格意义上的“全部变成固定帧率”，而是**误判后落入较粗时间网格**。VFR 的 8 种剪切都失败：7 种出现 PTS 差异；单帧用例虽 PTS 都为零，持续时间也不同。另加 `--index scan` 仍为 0/8，因此不能通过强制扫描索引解决此反例。证据见 [VFR 扫描复测](evidence/deepregular-vfr-scan.json) 和 [实际 PTS](evidence/audit.json)。

改进方向不是继续调整启发式阈值，而是在 MP4/AV1 这条路径上直接保留输入 sample 的整数 PTS 和 duration。原型采用这种方式，不依赖先判断 CFR / VFR。

### 4. MP4 av1C：可以解码不等于描述一致

rav1e 和 NVENC 输入的 `seq_level_idx_0` 为 **31**；DeepRegular 重编码边界生成的 Sequence Header 对应值为 **0**。以 `13:227` 为例，输出仍只有一个 sample description，av1C 保留 31，边界 sample 却使用 0：

| 输入 | 出错 sample（输出从 0 编号） | av1C / 实际 Sequence Header |
| --- | --- | --- |
| rav1e | 0、211 | `seq_level_idx_0: 31 / 0` |
| NVENC | 0、203 | `seq_level_idx_0: 31 / 0` |

这两个例子均能正常解码，显示帧和时间也正确，但容器配置不一致。rav1e 的 7 项、NVENC 的 5 项因此未通过，共 12 项。对齐模式在 rav1e 上并不一定真的对齐：它的实际随机访问点受编码器行为影响，不能假定 `-g 24` 就固定每 24 帧。

**不能只因为两份 Sequence Header 字节不同就判错。** 本验证器另行列出 `config_sequence_differences` 作为诊断信息，只有受检查的 av1C 字段不一致才记为 `av1C_field_mismatch`。例如编码工具开关变化与 level / 位深变化不是同一问题。本轮 SVT 输入本身就存在初始配置 OBU 与首个实际 OBU 的工具字段差异，不应误判所有此类文件。

原型让每个配置区间绑定自己的 sample description，具体实现见下文。原型的 rav1e 反向消融关闭此功能后，仍可解码，但相同字段检查立即失败，见 [experiments.json 的 ablation-rav1e](evidence/experiments.json)。

### 5. SAR 保存

`color_sar` 输入包含 full-range BT.709 和 **4:3 SAR**。DeepRegular 的 8 项输出都变成 **1:1 SAR**，包括纯复制；画面像素一致不代表显示比例一致。

源码从 codec parameters 读取并复制 SAR，而本例容器流层的信息需要一并考虑。原型改用 `av_guess_sample_aspect_ratio` 得到有效 SAR，同时写入编码器、输出 codec parameters 和 `AVStream::sample_aspect_ratio`。8 项由此通过。这里属于元数据来源和传递层的问题，不需要任何 AV1 tile 数据改写。

### 6. 上游已有测试能证明什么

[`run_vp9_av1_tests.sh`](https://github.com/DeepRegular/SmartCut/blob/d670099e9df622b9b9120fc46411e6154985b269/tests/run_vp9_av1_tests.sh) 包含 3 项 VP9、2 项 AV1，检查帧数、独立解码和复制比例；其 AV1 用例使用 MKV，不能覆盖本次 MP4 av1C、SAR 和 VFR 反例。

该脚本已经说明：show_existing 的短命令可能重复，按“某个输出包是否在源包集合中出现过”计数会高估复制量。本轮进一步使用**对应源位置、确定可复制的区间和像素一致性**联合判断。作者并非没有意识到这个问题；本研究增加的是更强的定位式检查和 MP4 覆盖。

## 其他项目与 DeepRegular 的对照

| 项目 | 复用的共同部分 | 主要设计差别 | 本轮可以下的结论 |
| --- | --- | --- | --- |
| DeepRegular | 边界解码/编码、整段压缩包复制、FFmpeg 封装 | Rust 内部统一多编解码器路径；显式编码器候选和通用显示网格 | AV1 基础复制正确；MP4 配置、VFR 判定和 SAR 有实测缺口 |
| skeskinen | PyAV 内部处理 packet/frame；切换前 flush 编码器 | 分离输出流和边界 codec context；通用时间戳补偿、典型 duration 回填 | 末帧时长与部分边界格式不一致，不能仅靠 DTS 单调修补 |
| MediaMolder | 编排框架中的 copy/recode；FFmpeg 后端 | AV1 进入 passthrough，关闭 global header，依赖带内配置；NAL 参数集策略留给 AVC/HEVC | 说明“AV1 不做参数集 ID 改写”是有意设计；未做运行评分 |
| LosslessCut | FFmpeg 子进程、前部编码后拼接复制段 | 查找后续 keyframe，生成编码前缀，再 concat；AV1 明确映射到 libsvtav1 | 当前源码有 AV1 分支；不能拿先前 H.264/H.265 GUI 结果推断本轮 AV1 成败 |

skeskinen 的 [`_fix_packet_timestamps`](https://github.com/skeskinen/smartcut/blob/73821ea01c29a4dca81a8865ef7d348d04bb0810/smartcut/video_cutter.py#L356) 修补 DTS、PTS 和缺失 duration；[`_ensure_enc_codec`](https://github.com/skeskinen/smartcut/blob/73821ea01c29a4dca81a8865ef7d348d04bb0810/smartcut/video_cutter.py#L398) 从输出 codec context 建边界编码器。这些通用处理没有保证本次 AV1 的最后一个 sample 使用源帧持续时间，也没有完整保存颜色、容器 SAR 和所有输入位深配置。

其典型 `aom / partial` 输出应为 **8.56 秒**，实际 **8.520078125 秒**；46 项有持续时间差异。Main10 的 5 项编码边界产生 8-bit Sequence Header，而唯一 av1C 仍声明 10-bit，合计与 level 问题形成 17 项配置失败。`color_sar` 的 8 项丢失 SAR，其中 5 项还改变了解码报告的 range / colorspace。这些计数允许重叠，不可直接相加作为失败文件数。逐例值见 [upstream.json](evidence/upstream.json) 与 [audit.json](evidence/audit.json)。

本轮还修正了验证器自身的一个陷阱：FFmpeg 默认在格式 / 颜色改变时重建滤镜，导致 `setpts=N/...` 的 N 重置，rawvideo 输出层报非单调 DTS。单独解码证实这不是上游码流解析失败。最终像素提取设置 `-reinit_filter 0`，原始 PTS 通过另一路 ffprobe 检查，并重新审核全部输出。因此最终结论没有把这些工具链报错记为“严格解码失败”。

MediaMolder 的关键证据是 [`planSmartCopyVideo`](https://github.com/MediaMolder/mediamolder/blob/9e51f8f761588e2e3c5ef87e9f11dfd5c091ac58/job/smartcopy_plan.go#L214)：AV1 在前置分支直接返回 passthrough 计划，并设置 `GlobalHeader=false`。仓库里有 AV1 CBS 解析模块，不代表这条导出路径进行了全参考槽依赖分析；本研究只按实际调用路线评价。

LosslessCut 的 [`smartcut.ts`](https://github.com/mifi/lossless-cut/blob/3ef357d88d51d8a2a36e5bf3c041b3a269bfbc5a/src/renderer/src/smartcut.ts) 对应思想是头部重编码、后部复制；其 [`useFfmpegOperations.ts`](https://github.com/mifi/lossless-cut/blob/3ef357d88d51d8a2a36e5bf3c041b3a269bfbc5a/src/renderer/src/hooks/useFfmpegOperations.ts) 负责编码段与 concat。这里需要独立检查的是配置恢复、sample description 和时间映射，而不是仅凭没有 `sps-id` 就判设计缺陷。AV1 根本没有可直接对应的 SPS ID 命名空间。

## 本研究的 C++ / FFmpeg 方案

实现见 [src/main.cpp](src/main.cpp)。编解码及 MP4 读写使用 FFmpeg 公共接口；仅测试器解析 MP4 表和更完整的 Sequence Header 字段，避免生产核心重复实现 muxer。

```text
FFmpeg demux + 轻量 OBU 检查
    ↓
源 sample 索引：位置、大小、整数 PTS、duration、真实随机访问点
    ↓
帧区间规划
    ├─ 完整区间：按原位置读取压缩 sample
    └─ 不完整区间：从前一恢复点解码 → 选显示帧 → FFmpeg 编码
    ↓
恢复源时间轴 + 对配置切换发送 NEW_EXTRADATA
    ↓
FFmpeg MOV muxer：av1C / stsd / stsc / stts / stss
```

**输入和规划。** 索引阶段遍历压缩包但不解码全部画面，保存文件位置和时间，不把整部视频的压缩数据常驻内存。轻量检查读取 OBU framing 和 frame header 的起始标志；验证一 sample 显示一帧、关键入口、固定输入配置、PTS 严格增长及 duration 连续。区间使用半开帧范围，合并相邻选择；只将完整安全区间作为主矩阵的复制候选。

**边界。** 从此前验证过的随机访问点解码覆盖区间，把所选画面送给 libaom 或 SVT；不删除隐藏参考帧包来“凑帧数”。边界编码器生成自己的真实 Sequence Header，不沿用不匹配的旧头，也不通过修改 level 或工具位伪装成原始编码器。首个输出必须为带 Sequence Header 的 shown key frame；后续 packet 必须按显示序号返回，每个 TU 显示一帧。

**时间。** 编码器内部可以使用便于工作的序号 / nominal timebase，但这些序号只用于匹配源显示帧，不作为最终 PTS。对每个保留范围 `[a,b)`：

```text
output_pts(i) = 已输出范围的总持续时间 + source_pts(i) - source_pts(a)
duration(i)   = source_pts(i+1) - source_pts(i)
末端 source_pts(N) = 最后一个源 sample 的 pts + duration
```

计算使用源整数时间刻度，最终统一转换到输出流时基。VFR、NTSC 和非零源起点使用同一算法；不会用 `最后 DTS + 1` 代替真实画面持续时间。输出 DTS 与 PTS 相同，不生成 `ctts`。

**配置。** 使用实际编码包中的 Sequence Header 管理当前配置。源输入首个真实头优先于可能尚未定稿的 extradata OBU；后续变化属于本原型暂不支持的动态输入配置。写出时保留各区间的带内 Sequence Header，切换配置前给 packet 附加 `AV_PKT_DATA_NEW_EXTRADATA`。

FFmpeg [`movenc.c`](https://github.com/FFmpeg/FFmpeg/blob/n8.1/libavformat/movenc.c#L6863) 已有比较 / 保存 extradata、选择 sample description 的逻辑。原型交给它写 `av1C`、`stsd` 和 `stsc`，自身不写 MP4 box。典型混合输出得到两个 description；切回原配置时复用原条目。一次出现三次配置切换，不代表最终有三个不同 description。

当前实现以完整 Sequence Header 字节作为配置身份，属于保守做法；有些工具位变化并不需要新增 description。以后可先按 av1C 必需字段及实际解码配置做兼容判断，减少冗余条目，但不能把“始终沿用一个 av1C”当作普遍优化。一次 SVT→SVT 的部分剪切中，头完全相同，实际只有一个 description、零次配置切换。

**显示属性。** 将有效 SAR、范围、颜色原色、传递函数、矩阵、chroma location 显式传递到边界编码器和输出。对 FFmpeg 的 YUVJ420P 表示仅规范化像素布局枚举，同时保留 full-range 属性。位深或布局不受支持时拒绝继续，避免静默把 Main10 的边界变成 8-bit。

**故障处理。** 已有输出不覆盖；先写同目录临时 MP4，完成后改名；错误清理自己创建的临时输出。帧顺序、输出数量和编码器格式不满足约束时停止。CLI 的 `--analyze` 输出复制 / 重编码计划，`--report` 保存实际统计。

## 测试素材、检查标准与结果

素材是与既有研究同类的移动图案和逐帧条码，共 240 帧，主矩阵分辨率 192×112。使用 AV1 重新编码生成，因此不能称为与 H.264 / HEVC 相同的压缩视频。每次上游与原型比较使用**完全相同哈希的 AV1 文件**。

| 素材 | 变化点 | DeepRegular | skeskinen | 原型 libaom 边界 |
| --- | --- | ---: | ---: | ---: |
| aom | AOM、隐藏帧与 show_existing | 8/8 | 4/8 | 8/8 |
| svt | SVT-AV1 输入 | 8/8 | 4/8 | 8/8 |
| rav1e | 不同 keyframe 间隔、level index 31 | 1/8 | 1/8 | 8/8 |
| main10 | 10-bit | 8/8 | 3/8 | 8/8 |
| vfr | 40 / 80 ms 持续时间 | 0/8 | 4/8 | 8/8 |
| ntsc | 30000/1001 | 8/8 | 4/8 | 8/8 |
| offset | 源起点偏移 2 秒 | 8/8 | 4/8 | 8/8 |
| fragmented | 分片 MP4 输入 | 8/8 | 4/8 | 8/8 |
| color_sar | full-range BT.709、SAR 4:3 | 0/8 | 0/8 | 8/8 |
| tiles | 2×1 tile | 8/8 | 4/8 | 8/8 |
| nvenc | 硬件编码、level index 31 | 3/8 | 3/8 | 8/8 |

素材完整命令、真实访问点和 SHA-256 见 [fixtures.json](evidence/fixtures.json)。不能根据文件名或 `-g 24` 推导真正的复制边界。

| 模式 | 保留的源显示帧，半开区间 | 输出帧数 |
| --- | --- | ---: |
| full | `[0,240)` | 240 |
| partial | `[13,227)` | 214 |
| multi | `[13,81)` + `[100,227)` | 195 |
| aligned | `[24,216)`；名称不保证对所有编码器实际对齐 | 192 |
| one | `[23,24)` | 1 |
| tiny | `[23,25)`，跨边界 | 2 |
| copy_gap | `[24,72)` + `[120,192)` | 120 |
| tail | `[213,240)` | 27 |

独立检查包括：dav1d 严格解码；条码确认选中哪一显示帧；输出 packet / decoded frame 数；以有理数比较显示 PTS 和尾端持续时间；复制区间对应位置的压缩包和解码像素相等；重编码画面 PSNR 至少 30 dB；SAR / 颜色保存；sync sample 的实际头；stsd/stsc 映射后的 av1C 字段、尺寸和 nclx 颜色；DTS=PTS 且无 `ctts`。这不是完整 AV1/ISOBMFF 一致性认证器。

主矩阵累计输出 **10,901 帧**，确认复制 **9,992 帧**，其余 909 帧重编码；复制部分逐像素一致，重编码画面实测最低 PSNR 为 **42.879 dB**。第二编码器矩阵输出 3,964 帧，复制 3,648 帧，最低 PSNR **42.637 dB**。这些数值只是小型合成素材的失真门槛验证，不代表实际视频的主观质量。

以 SVT 输入 `13:227` 为例：主原型复制 **192/214 帧**，重编码 22 帧，仅解码两个边界共 48 张显示画面；输出两种配置时生成两个 sample description。rav1e 同一范围因实际访问点不同，复制 200 帧、重编码 14 帧、解码 40 帧。数据见 [主矩阵](evidence/matrix.json)、[SVT 边界矩阵](evidence/svt-boundaries.json)。

### 附加验证与消融

| 检查 | 结果 | 说明 |
| --- | ---: | --- |
| 第二软件解码器 libaom | 8/8 | 与 dav1d 解出的完整像素一致 |
| 随机定位到输出开头、首个复制入口、后部重编码入口 | 15/15 | seek 后序列与顺序解码对应后缀一致 |
| 关闭配置切换 | 反例成立 | rav1e 输出可解码，但 av1C 字段不一致 |
| 同时关闭配置切换并去掉复制段的 Sequence Header | 反例成立 | SVT 复制段无法由 dav1d 严格解码，报 frame header / TU 解析错误 |
| 修改源 stss，把一个同步标记指向错误位置 | 通过恢复检查 | FFmpeg 解析后规范化标记，原型按 OBU 验证入口，输出正确；不能把它记为“拒绝了坏文件” |
| 动态输入配置、非法范围、重叠、非 AV1、错误边界编码器等拒绝检查 | 7/7 | 未成功生成最终输出 |
| 现有输出 / 临时输出 / 报告保护 | 3/3 | 原内容哈希不变 |
| 中文空格路径与相邻区间合并 | 1/1 | 选帧、时间、配置检查通过 |
| 192×128 AV1 NVDEC | 3/3 | AOM 8-bit、AOM 10-bit、NVENC 输入，交叉使用 SVT/AOM 编码边界；硬解像素与 dav1d 一致 |

附加实验共 43 项，其中 **40 项通过，3 项因设备输入尺寸限制未完成**。这三项是先尝试 192×112 主素材时，NVDEC 报高度必须至少 128；没有把它们计为通过，也没有解释为拼接算法故障。随后另行生成上述 192×128 素材并通过三项硬解检查，其身份单独记录，未偷换主矩阵输入。全部记录见 [experiments.json](evidence/experiments.json)。

CTest 的 CLI help 检查为 1/1；它只验证可执行文件启动，不能替代上述视频矩阵。

## 哪些处理有必要，哪些可省

| 处理 | 本轮结论及依据 |
| --- | --- |
| 新边界携带自己的 Sequence Header；恢复复制时带回源配置 | **必要**。删除复制段头的消融产生实际解码错误 |
| AVC/HEVC 式 SPS/PPS/VPS ID 分配、slice ID / POC 改写、CRA/RASL/BLA 修补 | **本方案不需要**。AV1 恢复入口和结构不同，全部主矩阵不依赖这些处理 |
| 解析完整参考槽、order_hint、frame_id 依赖图 | **本轮安全边界策略不需要**。只有想在没有完整恢复点的位置继续复制时，才可能需要更复杂分析 |
| 按实际编码能力尝试编码器、选择短片段速度参数 | **有用**。参考 DeepRegular；本轮分别验证了 AOM 和 SVT 边界 |
| 复制解码器的数值 level 给 AV1 编码器 | **应避免直接照搬**。保留 DeepRegular 的规避；容器使用实际生成的配置 |
| 保存源 sample 的 PTS/duration | **必要**。VFR 整段复制反例表明，内容完全不变仍可能被时间网格改坏 |
| 在所需配置不同的区间切换 sample description | **有实证必要**。level / 位深反例；由 FFmpeg 完成，不自行写 box |
| 仅因整个 Sequence Header 字节不同就宣称文件不合规 | **不成立**。本次将完整字节差异和受约束字段差异分开记录 |
| 所有情况强制生成不同 stsd | **不需要**。相同配置可复用；原型 SVT→SVT 实测只用一个条目 |
| 两端一律重编码 | **保守但非最优**。尾部直接复制组件实验 11/11，见下文 |
| 全片解码后决定能否复制 | **不需要**。主原型仅扫描压缩包，按边界解码；长文件索引仍有优化空间 |

### 尾部不重编码的实证与后续方案

[`tests/tail_copy.py`](tests/tail_copy.py) 从实际访问点 24 开始，直接复制到显示帧 227 之前，刻意在最后一个独立区间中间结束。11 类输入均输出 **203 帧，203 个压缩包逐一相同，所有像素相同，PTS/duration 和配置检查通过**。结果见 [tail-copy.json](evidence/tail-copy.json)。

这是有理论依据的简化：一旦前面所需状态完整，从流中保留 TU 前缀不会删除该前缀已经依赖的未来包；输入的隐藏参考画面已经在保留的 TU 中。结束点必须落在完整 TU 边界，不能在 OBU 或 tile 中间截断。本轮对单层、一 TU 一显示帧的范围作了实测，不扩展到任意畸形或不受支持输入。

因此产品方案可采用：

1. 起点已是有效恢复点时，直接复制到所需末尾 TU。
2. 起点落在区间中间时，只编码起点到下一个有效恢复点之前的所选画面；随后复制到末尾 TU。
3. 下一个恢复点已超过所选末尾时，整个选择仍需要编码。
4. 每个拼接段重新判断入口状态，继续保留时间映射、源 Sequence Header 恢复及配置切换。

对通常的 `13:227`，这个规划可把主基线的“22 帧编码 + 192 帧复制”减少为“11 帧编码 + 203 帧复制”。其中 203 帧的尾部复制已单独验证；**完整任意起点、多段拼接的优化规划尚未接入并重跑主矩阵**，不能把推导的最终方案写成已有 88 项测试结果。当前报告交付的是已通过的保守原型和有证据支持的进一步设计。

## 尚存不足与产品接入建议

最先应补的是**动态输入配置和重复剪切**：目前原型接受固定输入 Sequence Header，能生成多配置输出，却拒绝把这种输出再次当输入。产品应把输入也建模为配置区间，按 sample 实际使用的 description / 有效 Sequence Header 管理解码状态，不应只保存文件级一份配置。

随后把已验证的尾部复制规则纳入规划，并扩展任意起点、多段、同一源区间多次选择的测试。边界解码现在保守解码整个覆盖区间；编码器在每个边界重新创建，扫描索引与随机读取也尚未针对长录像优化。正确性已经有基线，但本轮没有 1080p/4K 长片、内存峰值或吞吐测试，不能给出最大速度结论。

输入检查仍是针对限定场景的轻量解析，不是完整 AV1 验证器。多 operating point / 多层、film grain、HDR 元数据、动态分辨率、4:2:2/4:4:4、加密轨道、特殊 edit list、损坏媒体与分片输出还需独立设计和测试。硬解只证明本机 FFmpeg/NVDEC 路径，Apple/QuickTime、Media Foundation、浏览器、电视以及编辑器二次导入均未验证。

根目录产品可复用已有 stream 路由、FFmpeg 参数处理和音频/字幕流程；AV1 增加自己的 TU 索引和恢复点判定。编码器 codec ID 必须仍与复制视频一致。参数冲突应在计划阶段报错；本研究 CLI 只在实际打开边界编码器时检查，不是正式产品的完整参数兼容层。

本目录没有修改正式产品 `src/`。下一阶段宜先补动态输入配置与优化规划的完整回归，再接入产品；不要把只在这组受限素材上通过的研究实现等同于已经完成通用 AV1 支持。

## 证据和复现入口

构建、下载版本、依赖安装和命令见 [README.md](README.md)。远程即使没有媒体，仍可通过以下文件明确定位本次每个输入与输出：

- [fixtures.json](evidence/fixtures.json)：生成方法、输入 SHA-256、尺寸、真实访问点和隐藏帧统计。
- [audit.json](evidence/audit.json)：环境、发行包、源码 / 工具哈希、反例实际值和汇总。
- [upstream.json](evidence/upstream.json)、[deepregular-vfr-scan.json](evidence/deepregular-vfr-scan.json)：未修改上游的逐项结果、命令和日志链接。
- [matrix.json](evidence/matrix.json)、[svt-boundaries.json](evidence/svt-boundaries.json)：原型逐项结果及实际复制 / 编码计划。
- [experiments.json](evidence/experiments.json)、[tail-copy.json](evidence/tail-copy.json)：解码、定位、消融、硬件、保护与尾部优化证据。

生成脚本提供逻辑复现；只有输入 SHA-256 相同才是同一二进制素材。FFmpeg、编码器或驱动改变后应生成新的实验记录，保留与本报告的区别。研究没有把推测根因写成已经修复的上游 issue，也未向上游提交消息或修改。
