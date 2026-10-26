# 视频 Smart Rendering 源码研究报告

实现机制 技术共性 独有设计与问题定位

交付日期：2026 年 10 月 26 日　源码与 issue 核查截止：2026 年 10 月 25 日

## 一 主要结论

这些项目采用同一个基本思路：把保留区间拆成可以复制的压缩数据和必须重新编码的边界，再将两者放回同一条输出时间轴。真正拉开差距的是四件事：怎样证明复制区间仍可解码，怎样让新旧参数集共存，怎样在 B 帧重排序下重建时间戳，以及怎样独立处理音频边界。

**DeepRegular SmartCut 的工程覆盖最广，MediaMolder 对参数集共存的处理最有比较价值。** 前者把参考帧分析、场级时间轴、音频边界补丁和广播容器处理做成专门模块；后者把智能复制嵌入处理图，为 H.264 和 HEVC 分配互不冲突的参数集 ID，尽量保留原来的 MP4 sample entry。这里评价的是可见设计范围，不是未经实测的可靠性排名。 [D1](https://github.com/DeepRegular/SmartCut/blob/84252659110ad34e1af02689f3013badc1561a1e/rust/crates/core/src/plan.rs#L624) [D2](https://github.com/DeepRegular/SmartCut/blob/84252659110ad34e1af02689f3013badc1561a1e/rust/crates/core/src/leadrefs.rs#L1) [D3](https://github.com/DeepRegular/SmartCut/blob/84252659110ad34e1af02689f3013badc1561a1e/rust/crates/core/src/audio.rs#L1695) [D4](https://github.com/DeepRegular/SmartCut/blob/84252659110ad34e1af02689f3013badc1561a1e/rust/crates/core/src/cut.rs#L667) [M1](https://github.com/MediaMolder/mediamolder/blob/9e51f8f761588e2e3c5ef87e9f11dfd5c091ac58/job/handlers_smartcopy.go#L1) [M2](https://github.com/MediaMolder/mediamolder/blob/9e51f8f761588e2e3c5ef87e9f11dfd5c091ac58/job/smartcopy_plan.go#L214)

**skeskinen smartcut 适合阅读紧凑的算法实现，LosslessCut 和 VidCutter 更接近 FFmpeg 任务编排。** Python smartcut 已下沉到包、NAL 类型和 CRA 前导帧层面，但时间戳与参数集处理仍有启发式成分。LosslessCut 的 Smart Cut 主要重新编码开头；VidCutter 则生成“头部重编码—中部复制—尾部重编码”三个临时文件。调用同一种编码器并不能自动解决新旧码流的兼容性。 [P1](https://github.com/skeskinen/smartcut/blob/73821ea01c29a4dca81a8865ef7d348d04bb0810/smartcut/smart_cut.py#L48) [P2](https://github.com/skeskinen/smartcut/blob/73821ea01c29a4dca81a8865ef7d348d04bb0810/smartcut/video_cutter.py#L150) [L1](https://github.com/mifi/lossless-cut/blob/3ef357d88d51d8a2a36e5bf3c041b3a269bfbc5a/src/renderer/src/smartcut.ts#L1) [L2](https://github.com/mifi/lossless-cut/blob/3ef357d88d51d8a2a36e5bf3c041b3a269bfbc5a/src/renderer/src/hooks/useFfmpegOperations.ts#L500) [V1](https://github.com/ozmartian/vidcutter/blob/62d9b0657fd841c74a8d5bb184512ddaddacbce2/vidcutter/libs/videoservice.py#L261)

**视频逐帧精确，不等于音频逐采样精确，也不等于容器与原文件等价。** 大多数项目复制完整音频包；DeepRegular 提供压缩音频边界重编码，MediaMolder 的音频 smartcopy 则明确仅支持 PCM。字幕、音轨映射、MP4 edit list、TS 节目表都可能在视频像素正确时单独出问题。 [D3](https://github.com/DeepRegular/SmartCut/blob/84252659110ad34e1af02689f3013badc1561a1e/rust/crates/core/src/audio.rs#L1695) [M3](https://github.com/MediaMolder/mediamolder/blob/9e51f8f761588e2e3c5ef87e9f11dfd5c091ac58/docs/smartcopy.md#L1) [P3](https://github.com/skeskinen/smartcut/blob/73821ea01c29a4dca81a8865ef7d348d04bb0810/smartcut/track_cutters.py#L35) [V1](https://github.com/ozmartian/vidcutter/blob/62d9b0657fd841c74a8d5bb184512ddaddacbce2/vidcutter/libs/videoservice.py#L261)

issue 的状态必须结合评论与当前源码解释。LosslessCut 多个问题是归入实验功能总帖后关闭；Python smartcut 的部分修复只进入商业产品；MediaMolder 的关键缺陷已由合并 PR 修复；avcut 的个别开放问题已有对应防御代码。不能把“关闭”当成已修复，也不能把“开放”直接当成当前版本仍能复现。

## 二 范围与证据口径

本报告实际阅读七个项目的核心路径，覆盖视频、音频及容器层。SmartCut64 与商业闭源产品没有取得对应发行版本的源码，因而不对其内部算法作源码级判断；Smart Media Cutter 的商业更新也不倒推为 skeskinen 开源版本已经具备的能力。按要求，不研究杜比技术相关实现。

VirtualDub 使用的是可获取的 **1.9.1 历史源码镜像**。官方列有 1.10.4 源码下载，但本次未取得可用归档。因此下文的代码结论仅适用于历史镜像，不混用 VirtualDub2 的实现或 issue。 [X1](https://www.virtualdub.org/download.html) [X2](https://documentation.help/VirtualDub/p-smartrendering.html)

| 项目 | 本次源码快照 | 主要阅读对象 |
| --- | --- | --- |
| DeepRegular SmartCut | 84252659110a　2026-10-23　0.8.31 | Rust core 为主，Python 参考架构用于对照 |
| skeskinen smartcut | 73821ea01c29　2026-02-18 | Python 与 PyAV 核心，README 标记停止持续更新 |
| LosslessCut | 3ef357d88d51　2026-10-25 | smartcut.ts 与 FFmpeg 导出流程 |
| VidCutter | 62d9b0657fd8　2026-10-23 | videoservice.py 与编码配置 |
| MediaMolder | 9e51f8f76158　2026-10-20 | smartcopy 节点、码流重写与音频裁剪 |
| avcut | 7704c26c95a4　2026-07-15　0.8 | avcut.c 的包缓存、解码与混合输出 |
| VirtualDub 历史镜像 | 388a9e98ff08　2009-04-19　1.9.1 | 帧映射、DirectWrite 与 AVI 输出路径 |

文中“源码确认”指可在固定提交的函数或数据流中直接找到；“维护者确认”指 issue 或 PR 的明确说明；“推断”指症状与代码机制相符，但没有完成原始素材复现。正文引用链接指向固定提交或原始讨论，完整提交号位于文末。

**本次没有编译这些项目，没有运行其测试套件，也没有下载 issue 附件复现或测量性能。** 上游测试只作为实现者提供的证据，不冒充本次验证结果。issue 核查集中在 Smart Rendering、剪切边界、音画同步和封装相关记录，并非穷尽每个仓库的全部问题。

## 三 共同处理链与关键概念

一次可靠的智能剪切可以分为六步。

| 阶段 | 必须解决的问题 | 典型产物 |
| --- | --- | --- |
| 建立索引 | 显示顺序与解码顺序不同，关键帧标志可能不够 | 帧 PTS、包 DTS、访问点与前导帧范围 |
| 制定计划 | 删除参考帧后，剩余帧是否仍能正确解码 | 丢弃区、重编码区、复制区与解码预热区 |
| 生成边界 | 从可解码位置开始解码，只输出所需帧 | 新编码的头部或尾部包 |
| 修补码流衔接 | 新旧参数集、POC 与参考状态能否共存 | 参数集配置、NAL 封装及头部重写 |
| 重建时间轴 | B 帧、场、VFR 与音频包时长如何衔接 | 合法的 PTS、DTS、duration 与跨轨偏移 |
| 封装与验证 | 容器声明、轨道映射和播放器是否理解输出 | 可解码文件及帧数、顺序、同步检查 |

GOP 是便于组织处理的单位，不是所有编码格式中都天然封闭的依赖边界。H.264 的普通 I 图像未必刷新解码器状态；HEVC 的 CRA 可能带有显示时间在它之前的 RASL 前导图像。“找到 K 标志后开始复制”与“证明从此处复制安全”是强度不同的判断。 [D2](https://github.com/DeepRegular/SmartCut/blob/84252659110ad34e1af02689f3013badc1561a1e/rust/crates/core/src/leadrefs.rs#L1) [P4](https://github.com/skeskinen/smartcut/blob/73821ea01c29a4dca81a8865ef7d348d04bb0810/smartcut/nal_tools.py#L106)

“保留原始画质”也有不同层次：压缩包逐字节相同；修改 NAL 或 slice header 但不重新量化像素；重新编码少量边界画面。MediaMolder 在某些接缝修改复制包的 POC、frame_num 或 CRA 类型，因此其不重编码区间不能一概理解为每个包逐字节不变。 [M1](https://github.com/MediaMolder/mediamolder/blob/9e51f8f761588e2e3c5ef87e9f11dfd5c091ac58/job/handlers_smartcopy.go#L1) [M4](https://github.com/MediaMolder/mediamolder/blob/9e51f8f761588e2e3c5ef87e9f11dfd5c091ac58/job/smartcopy_hevc_ids.go#L1)

PTS 决定显示时间，DTS 决定解码时间，duration 决定样本占用时长。把 DTS 改为大于前一个值，只解决单调性约束，并不能保证恒定帧率、正确的合成偏移或音画同步。几个项目的 issue 恰好集中在这些约束之间。

## 四 DeepRegular SmartCut

### 1 架构与剪切计划

主要实现位于 Rust 的 plan.rs、cut.rs、bitstream.rs、leadrefs.rs 和 audio.rs。仓库还保留 Python 参考路径；Python 侧的外部 FFmpeg 与临时码流拼接限制，不能直接套到当前 Rust 的包级混合封装路径上。 [D1](https://github.com/DeepRegular/SmartCut/blob/84252659110ad34e1af02689f3013badc1561a1e/rust/crates/core/src/plan.rs#L624) [D4](https://github.com/DeepRegular/SmartCut/blob/84252659110ad34e1af02689f3013badc1561a1e/rust/crates/core/src/cut.rs#L667) [D5](https://github.com/DeepRegular/SmartCut/blob/84252659110ad34e1af02689f3013badc1561a1e/docs/technical/rust-core.md#L1)

plan_range 不只是取最近关键帧：它读取访问点的前导图像起点与可丢弃性，选择可以开始复制的位置，并在下一访问点的前导显示范围之前结束复制。默认还要求复制区间具有一定长度，阈值为两帧和 0.5 秒中的较大值；收益过小或找不到安全区间时，整段重编码。这是以额外编码换取明确边界的保守决策。 [D1](https://github.com/DeepRegular/SmartCut/blob/84252659110ad34e1af02689f3013badc1561a1e/rust/crates/core/src/plan.rs#L624)

### 2 视频参考关系与参数集

最独特的模块是 leadrefs.rs。对于 H.264 中“前导 B 图像也是参考图像”的情况，它继续追踪短期参考帧缓冲、frame_num 间隙、部分 MMCO 操作和各 slice 的参考列表，判断后续保留图像是否真的需要这些前导图像。参考标志为真，并不必然意味着前导图像不可删除；只看普通 B 帧标签也不足以证明可以删除。 [D2](https://github.com/DeepRegular/SmartCut/blob/84252659110ad34e1af02689f3013badc1561a1e/rust/crates/core/src/leadrefs.rs#L1)

这种分析有明确边界：长期参考、pic_order_cnt_type 1、无法读取的参数集或 slice、分析窗口耗尽等情况，均按“仍然需要”处理。源码设置了有界分析容量。因此它是针对实际码流的保守模型，不是完整软件解码器的替代品。 [D2](https://github.com/DeepRegular/SmartCut/blob/84252659110ad34e1af02689f3013badc1561a1e/rust/crates/core/src/leadrefs.rs#L1)

H.264 接缝处理还会把重编码头部的 POC 调整到后续复制区间所需的状态；必要时扩展 SPS 中 POC 字段的位宽，并照顾 CABAC 字节对齐。这里重点修改新生成的头部，使复制区间维持原来的语义。另有 clean_join 路径，通过延长重编码区寻找更干净的接缝。 [D4](https://github.com/DeepRegular/SmartCut/blob/84252659110ad34e1af02689f3013badc1561a1e/rust/crates/core/src/cut.rs#L667)

对 H.264 和 HEVC，新旧编码器参数集可能不同。Rust 输出采用参数集随样本带内切换的路径：适合的 MP4 输出分别声明 avc3 或 hev1，并在回到复制区间时恢复源参数集；NAL 在 Annex B 和长度前缀之间按容器要求转换。它没有把“尺寸、profile、level 相同”当作 SPS/PPS 已相同。 [D4](https://github.com/DeepRegular/SmartCut/blob/84252659110ad34e1af02689f3013badc1561a1e/rust/crates/core/src/cut.rs#L667)

需要纠正一个常见表述：avcC/hvcC 并非只能装一套参数集。MediaMolder 实际采用了在其中放入多套不同 ID 参数集的办法。真正要解决的是参数集 ID 冲突、活动配置切换和 sample entry 的约束。DeepRegular 的带内策略与 MediaMolder 的配置记录合并，是不同实现路线。 [D4](https://github.com/DeepRegular/SmartCut/blob/84252659110ad34e1af02689f3013badc1561a1e/rust/crates/core/src/cut.rs#L667) [M2](https://github.com/MediaMolder/mediamolder/blob/9e51f8f761588e2e3c5ef87e9f11dfd5c091ac58/job/smartcopy_plan.go#L214)

AV1 与 VP9 复用总体的“边界编码加内部复制”调度，重点依赖边界关键图像及编码器输出的必要头部，而不是套用 H.264 的 SPS/PPS 重写逻辑。当前代码优先尝试 SVT-AV1、rav1e、libaom；VP9 使用 libvpx-vp9，并避免直接照搬两端表示方式不同的 level 数值。这些是实际的编码器适配，不能扩大解释为所有 profile、参考结构与容器组合均已验证。 [D6](https://github.com/DeepRegular/SmartCut/blob/84252659110ad34e1af02689f3013badc1561a1e/rust/crates/core/src/cut.rs#L4727)

另一个少见设计是自带 VC-1 帧内编码器，用来填补切点附近需要编码、而常用 FFmpeg 路径缺少对应编码器的空白。其适用范围受自身实现的 profile 和图像结构限制，不能只凭“支持 VC-1”推导出完整覆盖。 [D7](https://github.com/DeepRegular/SmartCut/blob/84252659110ad34e1af02689f3013badc1561a1e/rust/crates/vc1/src/lib.rs#L1)

### 3 视频时间轴

Writer 将待写包按解码顺序排队，同时用显示位置的最小堆安排 DTS。显示位置以场级单位表达，并为 VFR 等情况引入更细刻度；输出包的 PTS、DTS 和 duration 由统一时间轴生成，再换算为容器 time base。 [D4](https://github.com/DeepRegular/SmartCut/blob/84252659110ad34e1af02689f3013badc1561a1e/rust/crates/core/src/cut.rs#L667)

这使隔行、半帧场图像、pulldown 和重排序具有统一处理入口，但也有容错取舍：代码对 DTS 做下界约束并以 PTS 为上界；如果受损素材导致两者无法同时满足，会跳过该图像并计数。因此“能完成导出”不自动等于“保留了损坏源中的每个图像”。 [D4](https://github.com/DeepRegular/SmartCut/blob/84252659110ad34e1af02689f3013badc1561a1e/rust/crates/core/src/cut.rs#L667)

### 4 音频

音频以实际输出的视频区间和统一拼接时间为基准，没有简单跟随视频头中尾切成三份。copy 模式选择邻近的完整压缩帧，并根据累计已写终点控制取舍，降低多次剪接的累计漂移。以每帧 1024 个采样、48 kHz 的 AAC 为例，一个压缩帧约 21.33 ms；就近选帧仍是音频帧粒度，不是采样粒度。 [D3](https://github.com/DeepRegular/SmartCut/blob/84252659110ad34e1af02689f3013badc1561a1e/rust/crates/core/src/audio.rs#L1695)

默认 smart 模式对边界音频建立补丁：解码邻域，把保留范围外的采样静音并施加很短的渐变，再编码替换受影响的包；中间完整包继续复制。AAC 还考虑变换窗重叠，增加相邻保护帧，通过编码器预热和输出对齐处理延迟。普通边界通常只动少量帧，但较长淡入淡出或其他修整会扩大范围，不能固定承诺“永远只重编码一帧”。 [D3](https://github.com/DeepRegular/SmartCut/blob/84252659110ad34e1af02689f3013badc1561a1e/rust/crates/core/src/audio.rs#L1695)

reencode 模式对保留音频整体执行采样裁剪和编码；重采样、混音等也会进入这类路径。没有合适编码器或不支持某种位宽时，部分边界处理会退回复制并提示。源码对 FLAC/ALAC 边界补丁支持的位宽也有选择，说明无损音频同样需要专门适配。 [D3](https://github.com/DeepRegular/SmartCut/blob/84252659110ad34e1af02689f3013badc1561a1e/rust/crates/core/src/audio.rs#L1695)

### 5 容器与验证设计

一般输出由 libavformat 封装；广播 TS 则增加普通 remux 之外的工作。si.rs 在输出后重建或恢复所需的节目和服务描述，把相关流放回适当 PID，处理节目表与连续计数。restamp.rs 针对同一录制文件中多段时钟重启，在读取时按字节区间修正 PES 时间戳和 PCR，维持可寻址的统一时钟。前者是容器内容保留，后者是输入时间基准修复，两者不应混为视频 GOP 重编码。 [D8](https://github.com/DeepRegular/SmartCut/blob/84252659110ad34e1af02689f3013badc1561a1e/rust/crates/core/src/si.rs#L1) [D9](https://github.com/DeepRegular/SmartCut/blob/84252659110ad34e1af02689f3013badc1561a1e/rust/crates/core/src/restamp.rs#L1)

仓库具备视频逐帧验证、真实素材检查、VFR 和音频边界测试入口。验证设计区分复制画面的相等性与重编码画面的质量误差，也关注音画对齐。这比“FFmpeg 成功退出”提供更强证据；本报告未执行这些测试，也不引用其性能数字作为独立测量。 [D10](https://github.com/DeepRegular/SmartCut/blob/84252659110ad34e1af02689f3013badc1561a1e/rust/crates/core/src/verify.rs#L1)

## 五 skeskinen smartcut

### 1 索引与混合输出

MediaContainer 扫描视频包，建立帧时间戳、GOP 的 PTS/DTS 范围和 HEVC 前导图像信息；音频压缩包和字幕包保存在内存中。make_cut_segments 将保留区间与 GOP 求交：完整落入保留区间的 GOP 复制，部分重叠的 GOP 重编码，不相交的 GOP 丢弃。时间计算大量使用 Fraction，避免早期就转成浮点秒。 [P1](https://github.com/skeskinen/smartcut/blob/73821ea01c29a4dca81a8865ef7d348d04bb0810/smartcut/smart_cut.py#L48) [P5](https://github.com/skeskinen/smartcut/blob/73821ea01c29a4dca81a8865ef7d348d04bb0810/smartcut/media_container.py#L80)

VideoCutter 分别提供 recode_segment、remux_segment 和 hybrid_recode_cra_segment。最后一个路径针对 HEVC CRA 接缝：从前一 GOP 预热解码器，重编码需要保留的前导图像，再复制 CRA 及其后续图像。这比“所有 K 包都直接复制”更深入，但尚未达到 DeepRegular 的 H.264 参考列表模拟程度。 [P2](https://github.com/skeskinen/smartcut/blob/73821ea01c29a4dca81a8865ef7d348d04bb0810/smartcut/video_cutter.py#L150)

NAL 检查优先识别实际图像类型；H.264 以 IDR 为主要安全入口，HEVC 区分 CRA、RASL、RADL。仍有宽松兜底：无法识别 NAL 类型时返回允许，某些只读到参数集或 SEI 的情况也会接受。因此它是带格式启发式的安全检查，不是完整依赖证明。 [P4](https://github.com/skeskinen/smartcut/blob/73821ea01c29a4dca81a8865ef7d348d04bb0810/smartcut/nal_tools.py#L106)

### 2 编码参数与时间戳

H.264 边界编码设置固定 sps-id=3，目的在于降低与源参数集冲突的概率。这与扫描源 ID 后动态分配不同：源流若已占用该 ID，就不能仅靠常量证明安全。HEVC 则尝试读取源数据中的 x265 options 信息并复用，同时控制 repeat-headers 和 info；这依赖具体编码器留下的信息，不是对任意 HEVC 参数集的完整反向构造。 [P2](https://github.com/skeskinen/smartcut/blob/73821ea01c29a4dca81a8865ef7d348d04bb0810/smartcut/video_cutter.py#L150)

输出继承分辨率、像素格式、宽高比等基础参数，通过 bitstream filter 处理必要的码流封装。HEVC 的 MP4/MOV 输出显式使用 hev1，允许参数集随样本携带。与 MediaMolder 保留 hvc1 的路线相比，这降低了部分配置共存的实现复杂度，但播放器兼容性仍须单独验证。 [P2](https://github.com/skeskinen/smartcut/blob/73821ea01c29a4dca81a8865ef7d348d04bb0810/smartcut/video_cutter.py#L150) [M2](https://github.com/MediaMolder/mediamolder/blob/9e51f8f761588e2e3c5ef87e9f11dfd5c091ac58/job/smartcopy_plan.go#L214)

_fix_packet_timestamps 会清理异常 DTS，把不递增的 DTS 推到上一个值之后，并在 PTS 小于 DTS 时调整 PTS，必要时补 duration。它是局部修正器。复制与重编码区间仍分别通过源时间偏移映射到输出，因而在极短边界、编码延迟和 B 帧重排序共同出现时，可能形成非均匀时间间隔；这与 #28 的报告相符，但本次没有复现。 [P2](https://github.com/skeskinen/smartcut/blob/73821ea01c29a4dca81a8865ef7d348d04bb0810/smartcut/video_cutter.py#L150)

AV1 和 VP9 也有编码器选择路径。当前代码明确拒绝 VP9 profile 2/3 的相应编码场景，并提示使用关键帧剪切；不能把仓库支持列表理解为所有 VP9 变体都能局部重编码。 [P2](https://github.com/skeskinen/smartcut/blob/73821ea01c29a4dca81a8865ef7d348d04bb0810/smartcut/video_cutter.py#L150)

### 3 音频 字幕与容器

PassthruAudioCutter 按音频包起始 PTS 二分查找区间，复制整个包并偏移时间。它没有解码音频、截取包内采样或处理压缩音频变换窗，边界精度受包粒度限制。全文件音频包驻留内存，也意味着长文件、多音轨时的内存消耗不只取决于当前 GOP。 [P3](https://github.com/skeskinen/smartcut/blob/73821ea01c29a4dca81a8865ef7d348d04bb0810/smartcut/track_cutters.py#L35) [P5](https://github.com/skeskinen/smartcut/blob/73821ea01c29a4dca81a8865ef7d348d04bb0810/smartcut/media_container.py#L80)

SubtitleCutter 明示朴素策略的不足：只按字幕包开始时间筛选，跨越剪切起点的字幕可能遗漏，跨越终点的字幕可能保留过长 duration。容器通过 PyAV 模板流和 mux 输出，保留部分元数据、disposition 与适用的附件，但没有发现类似 DeepRegular 的广播节目表重建层。 [P1](https://github.com/skeskinen/smartcut/blob/73821ea01c29a4dca81a8865ef7d348d04bb0810/smartcut/smart_cut.py#L48) [P3](https://github.com/skeskinen/smartcut/blob/73821ea01c29a4dca81a8865ef7d348d04bb0810/smartcut/track_cutters.py#L35)

README 已声明后续改进转入 Smart Media Cutter。源码仍可用于学习与二次开发，但不能用商业产品更新日志证明当前开源版本已经修复相同问题。 [P6](https://github.com/skeskinen/smartcut/blob/73821ea01c29a4dca81a8865ef7d348d04bb0810/README.md#L1)

## 六 MediaMolder

### 1 图节点与 GOP 运行段

MediaMolder 将 SmartCopy 做成位于 source 与 sink 之间的图节点。source 仍传递压缩包，sink 从源注册输出流，双方共享规划好的编码配置。输出节点的 codec_video: smartcopy 可以展开为隐式节点，也允许显式连接节点；裁剪时间由输出窗口传给智能复制节点。 [M1](https://github.com/MediaMolder/mediamolder/blob/9e51f8f761588e2e3c5ef87e9f11dfd5c091ac58/job/handlers_smartcopy.go#L1) [M2](https://github.com/MediaMolder/mediamolder/blob/9e51f8f761588e2e3c5ef87e9f11dfd5c091ac58/job/smartcopy_plan.go#L214) [M3](https://github.com/MediaMolder/mediamolder/blob/9e51f8f761588e2e3c5ef87e9f11dfd5c091ac58/docs/smartcopy.md#L1)

节点缓冲 GOP，并根据其最早和最晚显示 PTS 判断与保留窗口的关系。完整内部 GOP 复制；连续边界 GOP 合并成一次重编码运行段，从前一 GOP 预热解码器。紧随头部的复制 GOP 若含应保留的前导图像，会把这些图像并入重编码段，再用已输出时间水位避免重复。 [M1](https://github.com/MediaMolder/mediamolder/blob/9e51f8f761588e2e3c5ef87e9f11dfd5c091ac58/job/handlers_smartcopy.go#L1)

这是一种比简单按解码顺序切 GOP 更细的规划，但它没有实现 DeepRegular 那样的 H.264 参考列表追踪。尤其对 H.264，不能把“前导图像一定不被后续图像引用”当成普遍成立的性质。

### 2 参数集共存与接缝修补

**H.264 加 libx264 路径会动态选择源流未使用的 SPS/PPS ID。** 它试开边界编码器，把新参数集加入原 avcC，实际编码后再核对生成配置与计划是否一致。这样可继续使用 avc1，并避免源和新编码器都从 ID 0 开始造成冲突。 [M2](https://github.com/MediaMolder/mediamolder/blob/9e51f8f761588e2e3c5ef87e9f11dfd5c091ac58/job/smartcopy_plan.go#L214)

**HEVC 采用更深的码流重写。** x265 不能直接按所需方式选择全部参数集 ID，因此代码重编号 VPS、SPS、PPS，并修改每个重编码 slice 的 PPS 引用；还要处理 Exp-Golomb 长度变化、尾部填充和 slice data 字节对齐。合并后的参数集写入 hvcC，尽量保留源 hvc1。对不支持的 hvcC 重编号情况，代码报错，而不是静默改成 hev1。 [M2](https://github.com/MediaMolder/mediamolder/blob/9e51f8f761588e2e3c5ef87e9f11dfd5c091ac58/job/smartcopy_plan.go#L214) [M4](https://github.com/MediaMolder/mediamolder/blob/9e51f8f761588e2e3c5ef87e9f11dfd5c091ac58/job/smartcopy_hevc_ids.go#L1)

非 libx264 的部分 H.264 路径使用带内参数集和 avc3；源为 Annex B 的情况也有相应带内路径。因此该项目并非只有一种配置策略，而是根据源封装与编码器能力选择。 [M2](https://github.com/MediaMolder/mediamolder/blob/9e51f8f761588e2e3c5ef87e9f11dfd5c091ac58/job/smartcopy_plan.go#L214)

Open GOP 接缝另有两类修补：HEVC 首个复制 CRA 可改为 BLA；H.264 的复制 slice 可调整 POC 和 frame_num，持续到后续 IDR 或状态重置点。这里会修改复制包头部，不重新量化其像素数据。与 DeepRegular 主要移动重编码头部 POC 的做法相比，二者修改的接缝一侧不同。 [M1](https://github.com/MediaMolder/mediamolder/blob/9e51f8f761588e2e3c5ef87e9f11dfd5c091ac58/job/handlers_smartcopy.go#L1) [M2](https://github.com/MediaMolder/mediamolder/blob/9e51f8f761588e2e3c5ef87e9f11dfd5c091ac58/job/smartcopy_plan.go#L214) [M4](https://github.com/MediaMolder/mediamolder/blob/9e51f8f761588e2e3c5ef87e9f11dfd5c091ac58/job/smartcopy_hevc_ids.go#L1) [M8](https://github.com/MediaMolder/mediamolder/blob/9e51f8f761588e2e3c5ef87e9f11dfd5c091ac58/job/smartcopy_h264poc.go#L1)

### 3 时间戳 音频与容器

sendRun 从显示 PTS 推导重编码段的 DTS，结合源和编码器的重排序深度安排提前量；复制区关键帧也可能因前导图像已被重编码而重新定时。源码会探测源首个 GOP 的重排序深度，并限制边界编码器的 B 帧布局。探测范围与后续素材结构变化仍有边界；无法正常衔接时也存在警告与 DTS 单调修正，不能保证所有变化码流均形成理想 CFR 时间表。 [M1](https://github.com/MediaMolder/mediamolder/blob/9e51f8f761588e2e3c5ef87e9f11dfd5c091ac58/job/handlers_smartcopy.go#L1) [M2](https://github.com/MediaMolder/mediamolder/blob/9e51f8f761588e2e3c5ef87e9f11dfd5c091ac58/job/smartcopy_plan.go#L214)

音频 smartcopy **仅支持 PCM**：内部包直接复制，跨边界包按采样大小切字节，无需解码或处理编码延迟。AAC、Opus、FLAC 等压缩音频会被明确拒绝，用户需选择整包 copy 或编码器路径。重编码音频自动插入 atrim，并在相应条件下通过 asetpts 重置时间，保证 priming 包与包含首批有效采样的包送达 muxer。 [M3](https://github.com/MediaMolder/mediamolder/blob/9e51f8f761588e2e3c5ef87e9f11dfd5c091ac58/docs/smartcopy.md#L1) [M5](https://github.com/MediaMolder/mediamolder/blob/9e51f8f761588e2e3c5ef87e9f11dfd5c091ac58/job/handlers_graph_build.go#L379) [MediaMolder PR #79](https://github.com/MediaMolder/mediamolder/pull/79)

容器适配与处理图的时序密切相关：sink 必须知道某路音频已经内部裁剪，避免重复执行起始偏移、停止或丢包；输出分段重新锚定和 shortest 判断也要使用一致时间。这解释了为什么 AAC 开头缺失的修复不仅修改编码器，还涉及图与 sink 的时间逻辑。 [M5](https://github.com/MediaMolder/mediamolder/blob/9e51f8f761588e2e3c5ef87e9f11dfd5c091ac58/job/handlers_graph_build.go#L379) [M7](https://github.com/MediaMolder/mediamolder/blob/9e51f8f761588e2e3c5ef87e9f11dfd5c091ac58/job/handlers_sink.go#L58) [MediaMolder PR #79](https://github.com/MediaMolder/mediamolder/pull/79)

其回归测试检查边界可解码性、帧数顺序、内部数据保留、DTS 步长和部分原生解码器结果。它们提供的是特定夹具上的证据，不能覆盖所有 POC 类型、参考结构和播放器。 [M6](https://github.com/MediaMolder/mediamolder/blob/9e51f8f761588e2e3c5ef87e9f11dfd5c091ac58/job/handlers_smartcopy_cut_test.go#L1) [MediaMolder PR #77](https://github.com/MediaMolder/mediamolder/pull/77) [MediaMolder PR #79](https://github.com/MediaMolder/mediamolder/pull/79)

## 七 LosslessCut

### 1 视频路径

smartcut.ts 在切入点附近查询关键帧，窗口不足时再扩大。切入点恰好对齐关键帧时直接复制；否则找到后续关键帧，把切入点到该关键帧附近的头部重新编码，再复制其后部分。若选区短到没有可用复制区间，就整体编码。 [L1](https://github.com/mifi/lossless-cut/blob/3ef357d88d51d8a2a36e5bf3c041b3a269bfbc5a/src/renderer/src/smartcut.ts#L1) [L2](https://github.com/mifi/lossless-cut/blob/3ef357d88d51d8a2a36e5bf3c041b3a269bfbc5a/src/renderer/src/hooks/useFfmpegOperations.ts#L500)

**该路径不是对称的首尾 GOP 重编码。** 尾端主要仍由 stream copy 的截断行为处理。头部末端还减去一个帧时长，用来缓解重叠帧问题。这是命令级边界修正，没有自建 H.264/HEVC 参考依赖分析。 [L2](https://github.com/mifi/lossless-cut/blob/3ef357d88d51d8a2a36e5bf3c041b3a269bfbc5a/src/renderer/src/hooks/useFfmpegOperations.ts#L500)

编码设置从源读取码率、time base 等；源码率缺失时会用文件大小与时长估算，并乘系数。当前 profile/level 匹配代码未启用，也没有看到参数集 ID 分配或 POC 连续性处理。存在 AV1 到 libsvtav1 的映射，因此前次“未确认 AV1”的结论应细化为“存在编码器路径，通用可靠性未获证明”。 [L1](https://github.com/mifi/lossless-cut/blob/3ef357d88d51d8a2a36e5bf3c041b3a269bfbc5a/src/renderer/src/smartcut.ts#L1)

### 2 音频与容器路径

Smart Cut 把临时片段交给 FFmpeg concat demuxer 合并，最终继续以 copy 输出，并处理流映射、元数据、章节和容器参数。该模式只处理一个主要视频流，对来自外部文件的轨道组合有限制。 [L2](https://github.com/mifi/lossless-cut/blob/3ef357d88d51d8a2a36e5bf3c041b3a269bfbc5a/src/renderer/src/hooks/useFfmpegOperations.ts#L500)

重编码头部时，非视频轨通常仍按 copy 处理；中部复制也携带各轨道。音频由临时文件中的完整压缩包组成，缺乏独立连续的采样域边界计划。字幕事件则还受持续时间与 concat 时间偏移影响。由此产生的首包或字幕同步问题，不能仅调整视频码率解决。 [L2](https://github.com/mifi/lossless-cut/blob/3ef357d88d51d8a2a36e5bf3c041b3a269bfbc5a/src/renderer/src/hooks/useFfmpegOperations.ts#L500)

一个薄弱点是临时文件的流拓扑必须一致。当前流程依据复制部分读回流信息再合并；如果很短的编码头部实际没有输出视频，concat 使用预期视频加音频映射时就可能失败。#2494 中的日志和复现解释直接落在这一层。 [L2](https://github.com/mifi/lossless-cut/blob/3ef357d88d51d8a2a36e5bf3c041b3a269bfbc5a/src/renderer/src/hooks/useFfmpegOperations.ts#L500) [LosslessCut #2494](https://github.com/mifi/lossless-cut/issues/2494)

## 八 VidCutter

### 1 三段式视频剪切

VidCutter 用 ffprobe 包的关键帧标志建立索引，将选区拆成头部编码、中部复制和尾部编码。多个 QProcess 任务通过 smartcheck 顺序推进，再进入 smartjoin。配置预设使用 libx264 或 libx265 等，并设置 closed GOP 等参数，但没有完整恢复源编码器的参数集与参考结构。 [V1](https://github.com/ozmartian/vidcutter/blob/62d9b0657fd841c74a8d5bb184512ddaddacbce2/vidcutter/libs/videoservice.py#L261) [V2](https://github.com/ozmartian/vidcutter/blob/62d9b0657fd841c74a8d5bb184512ddaddacbce2/vidcutter/libs/config.py#L1)

与 LosslessCut 相比，它明确处理头尾两端；与包级引擎相比，它把跨段兼容性大量交给临时文件、FFmpeg 与拼接容器。这两项差别都应保留，不能简单把二者归为同一种算法。

### 2 音频与容器

即使边界视频重新编码，cut 仍使用 -c:a copy 和 -c:s copy，因此音频、字幕没有同步获得采样或事件级精度。失败重试会移除全流映射，改用默认选择；这能让某些文件成功输出，也可能改变输出轨道集合。 [V1](https://github.com/ozmartian/vidcutter/blob/62d9b0657fd841c74a8d5bb184512ddaddacbce2/vidcutter/libs/videoservice.py#L261)

对于适合的编码，smartjoin 尝试把各段转成 MPEG-TS，进行 Annex B 等转换后通过 concat 协议拼接，再回到目标容器；失败时还存在其他 join 路径。TS 中转可缓解一些头部封装问题，但不能自动消除参数集语义冲突，也不能保证所有源音频和字幕都能经过 TS 中转。 [V1](https://github.com/ozmartian/vidcutter/blob/62d9b0657fd841c74a8d5bb184512ddaddacbce2/vidcutter/libs/videoservice.py#L261)

流程成功判断主要依赖子进程状态和输出文件大小，没有逐帧或采样级结果验证。cmdExec 内的无限期 waitForFinished(-1) 与多级回调，使“没有前进”可能来自子进程等待、异常中断或拼接阶段。具体根因应由日志区分，不能把所有卡死归给编码器。 [V1](https://github.com/ozmartian/vidcutter/blob/62d9b0657fd841c74a8d5bb184512ddaddacbce2/vidcutter/libs/videoservice.py#L261)

## 九 avcut

### 1 GOP 缓存与重编码

avcut 使用 C 和 libavformat/libavcodec，在同一流程中保存压缩包与解码帧，将缓冲区分类为整段复制、整段丢弃或部分重编码。它按图像类型与对应包的位置处理边界，用 opaque_ref 标识关联包和帧，旧接口则更多依赖 PTS/DTS 回退匹配。 [A1](https://github.com/anyc/avcut/blob/7704c26c95a4540a43e5c9ce9c6470595a78eddc/avcut.c#L248)

一个容易忽略的性能区别是：主读取循环会把选中的视频流送入解码器，即使某个 GOP 最终走压缩包复制，也已参与解码分析。因此“只有局部重新编码”不意味着“只有局部解码”。这与以包扫描为主、只解码边界的设计成本不同。 [A2](https://github.com/anyc/avcut/blob/7704c26c95a4540a43e5c9ce9c6470595a78eddc/avcut.c#L1079)

边界编码继承尺寸、像素格式、色彩等参数，默认避免生成 B 帧以减轻解码时间衔接压力，在边界任务之间排空或重开编码器。复制部分结合 I/P/B 图像与包映射寻找范围，没有看到完整 H.264 参考列表或 HEVC CRA 依赖模型。 [A1](https://github.com/anyc/avcut/blob/7704c26c95a4540a43e5c9ce9c6470595a78eddc/avcut.c#L248)

### 2 音频 时间戳与容器

音频主要按包时间判断保留与删除，再复制整包；它不是独立的音频局部重编码器。视频、音频各自累计丢弃时间或下一个输出时间，再换算 time base。这样可以修复部分输入时间异常，但场时长、VFR、音频包取整与多次剪接仍是应重点验证的交界处。 [A1](https://github.com/anyc/avcut/blob/7704c26c95a4540a43e5c9ce9c6470595a78eddc/avcut.c#L248)

当前代码含 H.264 Annex B 与 AVCC 转换，以及在复制关键帧时恢复源参数集的处理；没有看到与 MediaMolder 相当的参数集 ID 规划。跨容器输出会清理 codec_tag，并考虑 muxer 初始化后 time base 改变。旧 issue 报告的错误 tag，不能直接认定仍存在于本次快照。 [A1](https://github.com/anyc/avcut/blob/7704c26c95a4540a43e5c9ce9c6470595a78eddc/avcut.c#L248)

README 仍将项目称为实验性且非积极开发，但仓库在 2026 年存在 FFmpeg API 适配与 0.8 发布提交。正确结论是“仍有维护动作，成熟度承诺有限”，不是“多年没有更新”。 [A3](https://github.com/anyc/avcut/blob/7704c26c95a4540a43e5c9ce9c6470595a78eddc/README.md#L1)

## 十 VirtualDub 历史实现

### 1 逐输出帧决策

历史源码中的 VDRenderFrameMap::Init 逐个输出帧建立源帧映射，询问滤镜系统是否允许直接映射，并结合最近关键帧、已连续复制到的位置和可丢弃帧信息判断能否 DirectWrite。需要依赖但已跳过的帧，会使当前输出回到完整处理路径。 [X3](https://github.com/fishman/virtualdub/blob/388a9e98ff08b9b546e4c053c3781a760f5ff976/VirtualDub/source/DubUtils.cpp#L194)

这个设计不局限于三个剪切片段：滤镜能够说明某段画面是否未经改变，未受影响的帧可进入复制通道。它展示了 Smart Rendering 与时间线、滤镜依赖共同规划的经典做法。 [X3](https://github.com/fishman/virtualdub/blob/388a9e98ff08b9b546e4c053c3781a760f5ff976/VirtualDub/source/DubUtils.cpp#L194)

### 2 编码器 音频与 AVI

DirectWrite 将原压缩数据交给输出流，其他帧则经解码和压缩器；切换模式时处理编码器延迟输出和重启。系统依赖所选 VFW 压缩器接受源格式，要求编码结果适合既有流描述，而不是为现代 H.264/HEVC 建立通用参数集共存系统。 [X4](https://github.com/fishman/virtualdub/blob/388a9e98ff08b9b546e4c053c3781a760f5ff976/VirtualDub/source/DubProcessVideo.cpp#L345) [X5](https://github.com/fishman/virtualdub/blob/388a9e98ff08b9b546e4c053c3781a760f5ff976/VirtualDub/source/Dub.cpp#L766)

音频走独立的 AudioSubset 与音频流水线，可按模式进行转换、重采样、增益和压缩，也包含部分 MP3 延迟处理。这里没有发现与视频 DirectWrite 同构的通用压缩音频边界重编码调度。AVI 的样本、块和交错规则承担主要封装工作，不能用来说明 MP4 edit list 或 TS 节目表的解决情况。 [X5](https://github.com/fishman/virtualdub/blob/388a9e98ff08b9b546e4c053c3781a760f5ff976/VirtualDub/source/Dub.cpp#L766)

最值得借鉴的是“可直接映射的帧”这一抽象。旧式 VFW/AVI 接口本身不能充当现代多编码格式的兼容性方案。

## 十一 跨项目对照

### 1 视频与码流

| 项目 | 边界判定 | 新旧码流衔接 | 突出设计 |
| --- | --- | --- | --- |
| DeepRegular | 访问点、前导图像、部分参考列表 | 带内参数集、NAL 转换、重编码头部 POC 修补 | 场级时间轴与保守依赖分析 |
| skeskinen | GOP 求交、NAL 类型、CRA 特例 | 固定 SPS ID、部分 x265 信息、BSF | 紧凑 PyAV 与 CRA 混合输出 |
| MediaMolder | GOP 显示范围、前导图像并入边界 | 参数集 ID 分配、hvcC 合并、复制头部重写 | 保留 avc1/hvc1 的配置共存 |
| LosslessCut | ffprobe 关键帧及时间窗口 | 临时文件 concat、近似匹配编码设置 | 主要只补编码头部 |
| VidCutter | ffprobe 关键帧索引 | 头中尾文件、TS 中转与 concat | 对称头尾任务编排 |
| avcut | 解码帧类型与包对应关系 | 编码器刷新、参数集恢复、NAL 转换 | 全程解码分析下的 GOP 缓存 |
| VirtualDub 历史 | 帧映射、可丢弃帧与关键帧 | VFW 状态与 DirectWrite 切换 | 滤镜可表达帧能否直接复制 |

### 2 音频与容器

| 项目 | 音频边界 | 容器层工作与主要边界 |
| --- | --- | --- |
| DeepRegular | 整包复制、边界包重编码或全段重编码可选 | 常规 mux 外有 TS 服务表与时钟专项处理 |
| skeskinen | 按起始 PTS 选完整包 | PyAV mux、标签与 BSF；字幕跨界处理朴素 |
| MediaMolder | PCM 按采样切字节；压缩音频复制或重编码 | 图节点共享计划，处理 AAC priming 与时间重置 |
| LosslessCut | 临时片段内复制音频包 | concat 流拓扑、起始偏移、edit list 相互影响 |
| VidCutter | 头尾视频编码时仍复制音频包 | TS 中转、流映射回退可能影响非视频轨 |
| avcut | 包级删除与复制 | 各轨时间重映射、codec_tag 与 time base 适配 |
| VirtualDub 历史 | 独立音频复制或处理流水线 | AVI 块与交错，不是现代 MP4/TS 实现样本 |

### 3 重复技术与可复用边界

各项目反复实现的基础工作包括：有理数时间换算、帧与包对应、GOP 缓存、解码预热、编码器 drain/flush、NAL 封装转换、流参数继承、输出时间偏移和轨道映射。上述“重复”指解决同类问题，不表示这些仓库互相复制代码，也不证明彼此实现等价。

适合共享的是明确的接口和不变量。例如，规划器应给出复制起点安全的理由；边界编码器应返回实际输出帧数、重排序深度和参数集；时间轴模块应同时检查 PTS/DTS/duration；音频模块应报告真实采样跨度与延迟。单纯共享一组 FFmpeg 参数无法表达这些信息。

至少四类逻辑应保留格式专属实现：参考图像可丢弃性；参数集及 slice header 重写；压缩音频 priming 与重叠窗；容器的配置记录、时间表和节目服务信息。它们不能由一个“找到最近关键帧”的公共函数代替。

## 十二 issue 与功能模块的对应

以下状态以本次取得的公开记录为准。“相关代码仍在”比单纯开放状态更强，但仍不替代运行复现。

### 1 skeskinen smartcut

**#27 输出时长少一帧，开放。** 报告称帧数和 stts 看似正确，而轨道时长少一个帧周期。优先检查最后一个样本的 duration、轨道终点与 muxer 收尾，不应直接诊断为解码丢帧。对应 video_cutter 的 duration 填补、时间偏移及 PyAV 封装结束路径；验证时应分别核对帧数、最后 PTS 加 duration、mdhd/tkhd 与 edit list。 [smartcut #27](https://github.com/skeskinen/smartcut/issues/27) [P2](https://github.com/skeskinen/smartcut/blob/73821ea01c29a4dca81a8865ef7d348d04bb0810/smartcut/video_cutter.py#L150)

**#28 极短边界导致 CFR 变为非均匀时间表，开放。** 症状出现在只重编码一两帧的边界。局部 DTS 递增修正、编码器延迟与 duration 继承是最相关模块。维护者指出商业产品使用了不同时间戳代码，开源仓库不再更新；不能用商业修复消除这里的风险。 [smartcut #28](https://github.com/skeskinen/smartcut/issues/28) [P2](https://github.com/skeskinen/smartcut/blob/73821ea01c29a4dca81a8865ef7d348d04bb0810/smartcut/video_cutter.py#L150)

**#23 与 #24 都关闭，但含义不同。** #23 的特定 HEVC 素材问题得到维护者“v1.7 已修复”的确认；当前 CRA 专项处理与该问题类型相关，但本次未追溯到唯一修复提交。#24 涉及缺失 ctts 的 B 帧源文件，维护者后续说修复进入 Smart Media Cutter 2.0.5，且说明开源库不再维护，不能记为开源版本已修好。 [smartcut #23](https://github.com/skeskinen/smartcut/issues/23) [smartcut #24](https://github.com/skeskinen/smartcut/issues/24)

### 2 LosslessCut

**#2494 合并时找不到预期流，开放。** 后续评论给出非零 video start_time 与极短头部重编码的组合：编码临时文件可能只有音频，没有视频，concat 随后按另一片段的拓扑映射而失败。日志和当前导出路径支持“起始偏移处理加临时流拓扑假设”这一定位，证据较强；仍需用原始复现命令确认不同 FFmpeg 版本的行为。 [LosslessCut #2494](https://github.com/mifi/lossless-cut/issues/2494) [L2](https://github.com/mifi/lossless-cut/blob/3ef357d88d51d8a2a36e5bf3c041b3a269bfbc5a/src/renderer/src/hooks/useFfmpegOperations.ts#L500)

**#2050、#2049、#2013 与 #2912 的关闭不代表通用修复。** 它们分别涉及 H.264 加 AAC 接缝音频拉伸、字幕不同步、特定 HEVC 输出损坏和接缝音频重复；维护者将相关报告归入实验性总帖 #126。对应层次可能是音频包时间与 concat 偏移、字幕事件时间、码流参数兼容，以及音频包衔接。公开记录不足以给每项指定唯一根因。 [LosslessCut #2050](https://github.com/mifi/lossless-cut/issues/2050) [LosslessCut #2049](https://github.com/mifi/lossless-cut/issues/2049) [LosslessCut #2013](https://github.com/mifi/lossless-cut/issues/2013) [LosslessCut #2912](https://github.com/mifi/lossless-cut/issues/2912) [LosslessCut #126](https://github.com/mifi/lossless-cut/issues/126)

优先验证方法是保存临时文件，比较轨道数量、extradata、起始 PTS/DTS 与首尾 packet duration，再检查 concat 后样本表，区分“头部已坏”与“拼接才坏”。

### 3 VidCutter

**#402 Smart Join 失败，开放，源码定位较明确。** 堆栈落在 codecs() 对 Audio 正则匹配结果调用 .group(1)；当前代码第 268 行仍未检查匹配是否为 None。无音轨或没有成功产生预期音轨的临时片段可触发这条失败路径。日志前面的 GPU 初始化报错不能据此被当成最终根因。 [VidCutter #402](https://github.com/ozmartian/vidcutter/issues/402) [V1](https://github.com/ozmartian/vidcutter/blob/62d9b0657fd841c74a8d5bb184512ddaddacbce2/vidcutter/libs/videoservice.py#L261)

**#258 无音轨时卡在 80%，开放。** 触发条件与上述空音轨假设高度吻合，但这是另一版本的报告，本次没有证明两者是同一缺陷。应先增加 codecs(source) 的缺失轨道测试，再检查异常是否中断 smartcheck 到 smartjoin 的完成通知。 [VidCutter #258](https://github.com/ozmartian/vidcutter/issues/258) [V1](https://github.com/ozmartian/vidcutter/blob/62d9b0657fd841c74a8d5bb184512ddaddacbce2/vidcutter/libs/videoservice.py#L261)

**#175 导出缺音频，开放；#368 保存冻结，开放。** 前者与 -map 回退、TS 中转及默认流选择存在合理关联；评论中用户的分离音视频方案不等于上游已合并修复。后者只有现象，不能排除子进程等待、异常或素材问题，应保留为低置信度定位。 [VidCutter #175](https://github.com/ozmartian/vidcutter/issues/175) [VidCutter #368](https://github.com/ozmartian/vidcutter/issues/368) [V1](https://github.com/ozmartian/vidcutter/blob/62d9b0657fd841c74a8d5bb184512ddaddacbce2/vidcutter/libs/videoservice.py#L261)

### 4 MediaMolder

**PR #77 与 #79 已合并并包含在本次快照中，不应列为当前未解决 issue。** #77 修复长度前缀与 Annex B 混用、错误参数集及 Open GOP 前导帧丢失；#79 继续修复 CFR 接缝 DTS 不均、重编码 AAC 开头丢失，以及 hvc1 输出变为 hev1 后的特定原生播放问题。它们给出了较完整的症状、模块、修复和测试链条。 [MediaMolder PR #77](https://github.com/MediaMolder/mediamolder/pull/77) [MediaMolder PR #79](https://github.com/MediaMolder/mediamolder/pull/79)

当前仍可从源码与文档识别边界条件：H.264 POC type 1 或不兼容布局不能按现有方法重编号；靠近非 IDR 接缝可能出现 MMCO 警告；首 GOP 的重排序探测不天然代表整条变化码流；某些编码器回退的 avc3 路径仍需目标播放器验证。压缩音频 smartcopy 被明确拒绝，是尚未实现的能力。 [M1](https://github.com/MediaMolder/mediamolder/blob/9e51f8f761588e2e3c5ef87e9f11dfd5c091ac58/job/handlers_smartcopy.go#L1) [M2](https://github.com/MediaMolder/mediamolder/blob/9e51f8f761588e2e3c5ef87e9f11dfd5c091ac58/job/smartcopy_plan.go#L214) [M3](https://github.com/MediaMolder/mediamolder/blob/9e51f8f761588e2e3c5ef87e9f11dfd5c091ac58/docs/smartcopy.md#L1)

#79 对其测试夹具完成了 AVAssetReader 验证，不能扩展为所有 Apple 播放器与所有 hev1 文件的普遍结论。

### 5 avcut

**#9 灰屏或伪影、#19 音画不同步，均仍开放。** 前者与边界参考依赖、参数集恢复及解码帧到包映射相关；后者更应检查场时长、源时间戳及各轨丢弃时长累计。讨论跨度很长，缺少当前 0.8 上的统一复测，因此这里只列候选模块，不认定当前版本稳定复现。 [avcut #9](https://github.com/anyc/avcut/issues/9) [avcut #19](https://github.com/anyc/avcut/issues/19) [A1](https://github.com/anyc/avcut/blob/7704c26c95a4540a43e5c9ce9c6470595a78eddc/avcut.c#L248)

**#21 codec_tag 不兼容、#27 FFmpeg API 兼容，开放状态需要降权。** 当前代码已有 codec_tag 清零措施；#27 的维护者评论也说明已做较新 FFmpeg 环境适配。剩余工作是旧样本或构建环境复测后的闭环，不宜把它们当成完全未动过的缺陷。 [avcut #21](https://github.com/anyc/avcut/issues/21) [avcut #27](https://github.com/anyc/avcut/issues/27) [A1](https://github.com/anyc/avcut/blob/7704c26c95a4540a43e5c9ce9c6470595a78eddc/avcut.c#L248)

**#25 自动选到不可用硬件编码器，开放。** 这主要对应编码器发现和打开失败的回退策略，属于边界编码后端选择问题，与“GOP 应该复制还是重编码”的规划正确性不同。 [avcut #25](https://github.com/anyc/avcut/issues/25)

### 6 DeepRegular 与 VirtualDub

DeepRegular 的开放 #9 包含 AAC global_gain 域的无损淡化、自动音画同步修复和硬件边界编码等功能诉求。当前 smart 模式的 PCM 处理加重编码，不等价于压缩域无损淡化；时间轴重建也不等价于自动修复所有已错位的源文件。#8 的 GUI 问题有维护者明确版本修复说明，不应归到 GOP 核心缺陷中。 [DeepRegular #9](https://github.com/DeepRegular/SmartCut/issues/9) [DeepRegular #8](https://github.com/DeepRegular/SmartCut/issues/8)

本次没有足以宣称 DeepRegular 当前核心“无已知缺陷”的证据。较少的 issue、丰富的测试或大段技术文档都不能替代跨素材验证。VirtualDub 只做历史源码分析，不使用 VirtualDub2 的现行 tracker 给原项目标注缺陷状态。

## 十三 对后续开发与验证的启示

若要把这项能力整合进自己的系统，可以借鉴 skeskinen 的分段接口、DeepRegular 的安全规划与独立音频时间线，比较 MediaMolder 的参数集命名空间和图节点集成，再用 LosslessCut、VidCutter 与 avcut 的问题记录建立负例。不能直接把这些实现拼装成已验证的通用引擎；它们对时间轴和码流状态的假设并不相同。

输出计划最好记录每段为何复制、为何重编码、从何处预热、实际写出多少帧或采样，以及是否修改压缩包头部。这样既能展示重编码范围，也能在“画面正确但时长不对”时找到负责模块。

| 验证维度 | 应覆盖的素材或操作 | 要检查的结果 |
| --- | --- | --- |
| 参考依赖 | Closed/Open GOP、IDR/CRA、参考 B 帧、场图像 | 无缺帧重复帧，顺序正确，复制画面正确解码 |
| 极短边界 | 只保留 1 至 2 帧、同 GOP 内剪切、紧邻访问点 | 不产生空视频片段，首尾 duration 正确 |
| 参数集 | 源占用候选 ID、多套 SPS/PPS、不同边界编码器 | 配置记录与样本一致，目标播放器可解码 |
| 时间轴 | CFR、VFR、非零起点、缺失时间表、多次拼接 | PTS/DTS 合法，CFR 不异常变化，总时长正确 |
| 音频 | AAC、PCM、无音轨、多音轨、不对齐音频帧的剪切 | 采样范围符合声明，无累计漂移，priming 正确 |
| 容器与其他轨道 | MP4、MKV、TS 跨封装，跨界字幕、附件 | 轨道未意外减少，字幕时长和元数据合理 |
| 独立播放验证 | FFmpeg 与目标系统原生解码器 | 不把单个播放器容错当成文件正确性 |

验证应同时保存压缩包统计和解码结果。复制区的“包相同”与“画面相同”分别检查；重编码区允许质量误差但不允许帧序漂移；音频比较解码后的时间位置和边界波形；容器比较样本表与节目描述。上述矩阵是后续验证建议，并非本次已完成的测试清单。

## 附录 源码阅读索引与版本标识

正文引用指向固定源码提交或原始讨论。以下索引给出进一步阅读入口；issue 状态以核查截止日期为准。原始 API 响应保存在工作区 research 目录。

### DeepRegular SmartCut

版本：84252659110ad34e1af02689f3013badc1561a1e

-  [D1](https://github.com/DeepRegular/SmartCut/blob/84252659110ad34e1af02689f3013badc1561a1e/rust/crates/core/src/plan.rs#L624) plan.rs 的区间与访问点规划。
-  [D2](https://github.com/DeepRegular/SmartCut/blob/84252659110ad34e1af02689f3013badc1561a1e/rust/crates/core/src/leadrefs.rs#L1) leadrefs.rs 的 H.264 前导参考分析。
-  [D3](https://github.com/DeepRegular/SmartCut/blob/84252659110ad34e1af02689f3013badc1561a1e/rust/crates/core/src/audio.rs#L1695) audio.rs 的音频边界补丁。
-  [D4](https://github.com/DeepRegular/SmartCut/blob/84252659110ad34e1af02689f3013badc1561a1e/rust/crates/core/src/cut.rs#L667) cut.rs 的 Writer、poc_seam 与输出配置。
-  [D5](https://github.com/DeepRegular/SmartCut/blob/84252659110ad34e1af02689f3013badc1561a1e/docs/technical/rust-core.md#L1) Rust 技术文档及参考架构。
-  [D6](https://github.com/DeepRegular/SmartCut/blob/84252659110ad34e1af02689f3013badc1561a1e/rust/crates/core/src/cut.rs#L4727) cut.rs 的 AV1 与 VP9 编码器适配。
-  [D7](https://github.com/DeepRegular/SmartCut/blob/84252659110ad34e1af02689f3013badc1561a1e/rust/crates/vc1/src/lib.rs#L1) 自带 VC-1 编码器。
-  [D8](https://github.com/DeepRegular/SmartCut/blob/84252659110ad34e1af02689f3013badc1561a1e/rust/crates/core/src/si.rs#L1) si.rs 的广播表与 PID 处理。
-  [D9](https://github.com/DeepRegular/SmartCut/blob/84252659110ad34e1af02689f3013badc1561a1e/rust/crates/core/src/restamp.rs#L1) restamp.rs 的输入时钟修正。
-  [D10](https://github.com/DeepRegular/SmartCut/blob/84252659110ad34e1af02689f3013badc1561a1e/rust/crates/core/src/verify.rs#L1) verify.rs 的输出验证。

### skeskinen smartcut

版本：73821ea01c29a4dca81a8865ef7d348d04bb0810

-  [P1](https://github.com/skeskinen/smartcut/blob/73821ea01c29a4dca81a8865ef7d348d04bb0810/smartcut/smart_cut.py#L48) smart_cut.py 的分段规划与调度。
-  [P2](https://github.com/skeskinen/smartcut/blob/73821ea01c29a4dca81a8865ef7d348d04bb0810/smartcut/video_cutter.py#L150) video_cutter.py 的编码、复制、CRA 与时间戳。
-  [P3](https://github.com/skeskinen/smartcut/blob/73821ea01c29a4dca81a8865ef7d348d04bb0810/smartcut/track_cutters.py#L35) track_cutters.py 的音频与字幕。
-  [P4](https://github.com/skeskinen/smartcut/blob/73821ea01c29a4dca81a8865ef7d348d04bb0810/smartcut/nal_tools.py#L106) nal_tools.py 的 NAL 识别。
-  [P5](https://github.com/skeskinen/smartcut/blob/73821ea01c29a4dca81a8865ef7d348d04bb0810/smartcut/media_container.py#L80) media_container.py 的扫描与索引。
-  [P6](https://github.com/skeskinen/smartcut/blob/73821ea01c29a4dca81a8865ef7d348d04bb0810/README.md#L1) README 的维护状态。

### MediaMolder

版本：9e51f8f761588e2e3c5ef87e9f11dfd5c091ac58

-  [M1](https://github.com/MediaMolder/mediamolder/blob/9e51f8f761588e2e3c5ef87e9f11dfd5c091ac58/job/handlers_smartcopy.go#L1) handlers_smartcopy.go 的 GOP 与边界运行段。
-  [M2](https://github.com/MediaMolder/mediamolder/blob/9e51f8f761588e2e3c5ef87e9f11dfd5c091ac58/job/smartcopy_plan.go#L214) smartcopy_plan.go 的参数集与输出配置。
-  [M3](https://github.com/MediaMolder/mediamolder/blob/9e51f8f761588e2e3c5ef87e9f11dfd5c091ac58/docs/smartcopy.md#L1) smartcopy 文档的流程、音频能力与边界。
-  [M4](https://github.com/MediaMolder/mediamolder/blob/9e51f8f761588e2e3c5ef87e9f11dfd5c091ac58/job/smartcopy_hevc_ids.go#L1) smartcopy_hevc_ids.go 的 HEVC ID 重写。
-  [M5](https://github.com/MediaMolder/mediamolder/blob/9e51f8f761588e2e3c5ef87e9f11dfd5c091ac58/job/handlers_graph_build.go#L379) 音频裁剪与 sink 修复的代码改动。
-  [M6](https://github.com/MediaMolder/mediamolder/blob/9e51f8f761588e2e3c5ef87e9f11dfd5c091ac58/job/handlers_smartcopy_cut_test.go#L1) handlers_smartcopy_cut_test.go 的回归夹具。
-  [M7](https://github.com/MediaMolder/mediamolder/blob/9e51f8f761588e2e3c5ef87e9f11dfd5c091ac58/job/handlers_sink.go#L58) handlers_sink.go 的已裁剪音频时间处理。
-  [M8](https://github.com/MediaMolder/mediamolder/blob/9e51f8f761588e2e3c5ef87e9f11dfd5c091ac58/job/smartcopy_h264poc.go#L1) smartcopy_h264poc.go 的 H.264 接缝头部重写。

### LosslessCut

版本：3ef357d88d51d8a2a36e5bf3c041b3a269bfbc5a

-  [L1](https://github.com/mifi/lossless-cut/blob/3ef357d88d51d8a2a36e5bf3c041b3a269bfbc5a/src/renderer/src/smartcut.ts#L1) smartcut.ts 的边界探测与编码器选择。
-  [L2](https://github.com/mifi/lossless-cut/blob/3ef357d88d51d8a2a36e5bf3c041b3a269bfbc5a/src/renderer/src/hooks/useFfmpegOperations.ts#L500) useFfmpegOperations.ts 的导出与 concat。

### VidCutter

版本：62d9b0657fd841c74a8d5bb184512ddaddacbce2

-  [V1](https://github.com/ozmartian/vidcutter/blob/62d9b0657fd841c74a8d5bb184512ddaddacbce2/vidcutter/libs/videoservice.py#L261) videoservice.py 的剪切、拼接、流映射和探测。
-  [V2](https://github.com/ozmartian/vidcutter/blob/62d9b0657fd841c74a8d5bb184512ddaddacbce2/vidcutter/libs/config.py#L1) config.py 的边界编码参数。

### avcut

版本：7704c26c95a4540a43e5c9ce9c6470595a78eddc

-  [A1](https://github.com/anyc/avcut/blob/7704c26c95a4540a43e5c9ce9c6470595a78eddc/avcut.c#L248) avcut.c 的缓冲、复制、重编码与封装。
-  [A2](https://github.com/anyc/avcut/blob/7704c26c95a4540a43e5c9ce9c6470595a78eddc/avcut.c#L1079) avcut.c 的解码函数与主循环。
-  [A3](https://github.com/anyc/avcut/blob/7704c26c95a4540a43e5c9ce9c6470595a78eddc/README.md#L1) README 的实验性定位。

### VirtualDub 历史镜像

版本：388a9e98ff08b9b546e4c053c3781a760f5ff976

-  [X1](https://www.virtualdub.org/download.html) VirtualDub 官方下载页。
-  [X2](https://documentation.help/VirtualDub/p-smartrendering.html) 原作者 Smart Rendering 帮助文档的镜像。
-  [X3](https://github.com/fishman/virtualdub/blob/388a9e98ff08b9b546e4c053c3781a760f5ff976/VirtualDub/source/DubUtils.cpp#L194) DubUtils.cpp 的 VDRenderFrameMap。
-  [X4](https://github.com/fishman/virtualdub/blob/388a9e98ff08b9b546e4c053c3781a760f5ff976/VirtualDub/source/DubProcessVideo.cpp#L345) DubProcessVideo.cpp 的直接输出与编码切换。
-  [X5](https://github.com/fishman/virtualdub/blob/388a9e98ff08b9b546e4c053c3781a760f5ff976/VirtualDub/source/Dub.cpp#L766) Dub.cpp 的压缩器和音频流水线。
