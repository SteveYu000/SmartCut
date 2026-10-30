# AV1 正式产品接入与验收

日期：2026-10-30。基线提交：`1e98f0888b806e281624aecfeca455bbd79cb619`。本报告对应根目录 `src/` 中的正式 CLI；此前 [AV1 研究报告](../av1/REPORT.md) 保留其当时的独立原型结论。

## 结果

正式产品已支持 MP4 / AV1 Main 4:2:0 8/10 bit Smart Rendering，并接入已验证的尾部直接复制优化。**新增 AV1 验收 172/172，原有功能回归 551/551，合计 723/723。** 每组记录使用同一 EXE，SHA-256 为 `f54faec076631e59fc6c956f85feaf399960c7925e5fa35ad7e4be8af2e3deb6`。

| 验证组 | 通过 / 总数 | 主要内容 |
| --- | ---: | --- |
| AV1 / libaom-av1 边界编码 | 88/88 | 11 类固定输入 × 8 种剪切模式 |
| AV1 / libsvtav1 边界编码 | 32/32 | SVT、Main10、VFR、NVENC 输入 × 8 种模式 |
| AV1 / librav1e 边界编码 | 12/12 | AOM、Main10、VFR 输入，部分区间、多段、单帧、跨访问点两帧 |
| AV1 / av1_nvenc 边界编码 | 8/8 | NVENC 输入 × 8 种模式 |
| AV1 产品附加检查 | 32/32 | 时间选区、seek、第二解码器、参数一致性、错误处理、音频字幕、NVDEC |
| H.264 / HEVC 视频矩阵 | 480/480 | 原有 AVC 260 项、HEVC 220 项，带内及多 description 两种模式 |
| 原有音频 / 字幕 | 16/16 | 与原版 FFmpeg 对照 |
| 原有参数与保护 | 38/38 | 编码参数、错误、硬链接别名、Unicode 和原子替换 |
| 原有时间轴 | 15/15 | 小数切点、NTSC、VFR、偏移、两组 seek 等 |
| CTest | 2/2 | CLI 启动与参数解析 |

这是一组限定输入的功能验收，不是所有 AV1 文件或播放器的兼容认证。媒体和二进制仍只留本地，远程证据记录了素材生成方法、SHA-256、逐例命令、计划、检查结果及源码哈希。

## 接入方案

新增 [src/av1.cpp](../../src/av1.cpp)，使用 FFmpeg 解复用、解码、编码和 MP4 muxer，只自行处理 OBU framing、TU 显示标志、源索引和编码后的序号映射。公共的选区、映射、参数处理、磁盘暂存、交织、JSON 报告和原子发布继续复用产品流程。

AV1 索引逐包读取压缩数据，保存位置、大小、PTS、duration 和实际访问点，不解码全片。入口要求 Sequence Header 出现在 shown KEY_FRAME 前；拒绝将 show_existing、INTRA_ONLY 或容器 KEY 标志直接当作独立恢复点。复制整个 TU，保留其中隐藏参考帧和 show_existing 指令。

规划规则为：

1. 选择从有效恢复点开始时，直接复制到所需结束 TU。
2. 起点位于恢复区间内部时，编码起点至下一个恢复点之前，再复制后面的 TU。
3. 选择在下一个恢复点前已经结束时，编码整个选择。
4. 多段选择分别判断入口；时间、元数据和配置切换沿用公共流程。

与研究原型两端编码相比，SVT 输入的 `[13,227)` 现在复制 **203/214 帧**，只编码 11 帧。AOM 主矩阵同例只解码首个区间的 24 张显示画面；原型原先编码两端共 22 帧、解码 48 帧。主矩阵 10,901 张输出画面中，产品复制 **10,376 帧**、编码 **525 帧**；研究保守矩阵编码 909 帧。这里比较的是规划数量，未将不同参数的运行时间用于性能排名。

编码器由用户指定，使用既有 FFmpeg AVOptions 解析与 codec ID 校验。编码格式不一致、无效参数、像素格式转换等，即使选区原本全可复制，也会报错。省略编码参数时使用编码器默认值，不注入研究原型的 CRF、preset、lookahead 或编码线程数。

AV1 编码器内部使用显示序号和 nominal rate；输出 packet 按序号映射回源显示帧的整数 PTS/duration。最终 `DTS = PTS`，解码延迟为零，不使用 `ctts`；VFR 不经过平均帧率重建。时间选区保留首帧 leading gap，并按 `-to/-t` 缩短最后一个 TU 的持续时间。

GLOBAL_HEADER 可能让编码器把 Sequence Header 放进 extradata。产品在同步 sample 前补回实际头，恢复源复制段时使用源头。输出始终使用 `av01`，通过 `AV_PKT_DATA_NEW_EXTRADATA` 让 FFmpeg 创建、选择或复用 av1C / sample description；不自行写 MP4 box。编码器与源的 level 或工具位不同时，分别使用对应配置；相同配置可以共用一个条目。SAR、SDR 颜色与 full-range 属性同时传递给编码和输出。

## 测试依据

主素材逐字节复用研究的 11 个输入：AOM、SVT、rav1e、Main10、VFR、NTSC、非零起点、分片 MP4、full-range/SAR、tile、NVENC。240 帧、192×112，含逐帧条码及隐藏帧 / show_existing。八种选择为整片、两端部分、多段、名义对齐、单帧、跨点两帧、间隔复制和尾段。实际访问点来自 OBU，不按 `-g 24` 推算。

独立验证在研究检查器上增加了“从恢复点复制到尾部 TU”的位置和像素断言。原检查器计算完整区间的复制量，单靠那个数量不足以验证新增尾部优化；产品检查器另外要求每个新增复制位置的源压缩包与像素都相等，并与产品计划的复制/编码数对应。

检查包括 dav1d 严格解码、帧数、条码身份、精确显示 PTS、尾端时长、压缩包和复制像素、PSNR 门槛、SAR / 色彩、实际同步帧、av1C 字段、尺寸、nclx、stsd/stsc 映射，以及无 composition offset。附加音视频文件使用只选视频 track 的 box 检查，避免音频/字幕的 stsc 覆盖视频映射；该扩展与原单轨检查器在 140 个视频矩阵输出上的表解析结果一致。

四种边界编码器的矩阵最低重编码画面 PSNR 分别为：AOM **42.498 dB**、SVT **42.637 dB**、rav1e **40.970 dB**、NVENC **47.947 dB**，均高于本组 30 dB 门槛。复制画面要求像素完全一致。这些小型合成素材数值不构成真实内容的主观画质排名。

附加 32 项分组如下：

| 检查 | 项数 | 结果 |
| --- | ---: | --- |
| seek 与第二解码器 | 5 | SVT、rav1e、Main10、VFR、NVENC 输出；每个文件 3 个定位点，共 15 次；libaom 与 dav1d 像素一致 |
| 非帧对齐时间选区 | 4 | CFR、VFR、NTSC、偏移输入；精确核对 leading gap、PTS 和尾端持续时间 |
| 无效命令与已有输出保护 | 7 | codec、默认 codec 不匹配，错误 tag、参数、像素格式，失败保留目标，copy 非安全起点 |
| 不用编码器的尾部复制 | 1 | rav1e `[24,227)`，203 帧复制、0 帧编码 |
| 中文空格路径与相邻区间 | 1 | Unicode 与合并后的复制/编码计划正确 |
| FFmpeg 编码参数一致性 | 4 | AOM 未指定 CRF、AOM lookahead、AOM 全关键帧、SVT 显式参数；11 个边界画面的编码 OBU 数据与原版 FFmpeg 逐包相同 |
| 动态输入配置拒绝 | 1 | 将混合配置输出再次剪切，明确失败且不发布输出 |
| 音频字幕 | 6 | CFR / VFR 各 3 项：复制、AAC 192k/字幕编码、音频先于视频的输出映射 |
| NVDEC | 3 | 单独生成 192×128 AOM 8-bit、AOM 10-bit、NVENC 输入，交叉使用 SVT/AOM 边界；硬解像素与 dav1d 相同 |

音频字幕对照保留原 FFmpeg 的整个选区语义，比较音频 / 字幕包哈希、PTS/DTS、duration、skip_samples、格式、顺序、disposition 和解码 PCM。没有增加音频局部 GOP 算法。

开发复核时修正了测试命令的 `0.xxx` 时间写法和一个重复区间参数；参数回归的错误信息检查改为通用前缀。NTFS 硬链接检查因沙箱拒绝创建而在允许该操作的环境重跑，最终 38 项全部通过。正式快照只归档最终成功运行，未将这些环境或脚本错误统计为产品通过。

## 环境、限制与复现

构建使用 Windows x64、MSVC 19.51.36260、CMake 4.3.1-msvc1、FFmpeg `n8.1.3-24-g92611900bb-20261025` 共享 SDK。GPU 为 RTX 5060 Laptop，驱动 617.42；Python 3.12.14、NumPy 2.3.5。新增文件直接编入 `smartcut_core`，没有添加新的链接库或固定 SDK 路径。

输入仍限定本地未加密 MP4、单路所选 AV1 Main 4:2:0 8/10 bit、固定尺寸与固定 Sequence Header、单层、一 TU 一显示帧、连续的 PTS/duration、首帧为有效恢复点。包含动态 Sequence Header 的混合输出目前不能再次输入；重复编辑应使用原始素材。多层、film grain、HDR、动态分辨率、加密、所有硬件播放器和长视频吞吐 / 内存峰值未完成验证。

现有多段选择仍只支持视频；带音频字幕的单段选择继续使用原 FFmpeg 流程。更换 SDK 后需要重新构建并验证其 MOV muxer 的动态 extradata 行为。本次使用的是原版组件，未修改 FFmpeg。

构建、AV1 命令和复现测试见 [PRODUCT.md](../../docs/PRODUCT.md)。生成原始 AV1 素材见 [研究 README](../av1/README.md)。没有 NVIDIA 设备可以运行 CPU 子集，但不能将其记录为本报告完整验收。

证据位于 [evidence/av1/](evidence/av1/)：

- [环境与汇总](evidence/av1/environment.json)、[源码校验](evidence/av1/source-checksums.json)、[AV1 输入身份](evidence/av1/fixtures.json)、[AVC/HEVC 输入身份](evidence/av1/regression-fixtures.json)。源码校验同时记录本地原始字节和 LF 规范化哈希，以便 Git 换行规范化后的检查。
- [AOM 88 项](evidence/av1/libaom-av1.json)、[SVT 32 项](evidence/av1/libsvtav1.json)、[rav1e 12 项](evidence/av1/librav1e.json)、[NVENC 8 项](evidence/av1/av1_nvenc.json)、[附加 32 项](evidence/av1/features.json)。
- [原视频矩阵](evidence/av1/regression-matrix.json)、[音频字幕](evidence/av1/regression-streams.json)、[参数](evidence/av1/regression-parameters.json)、[时间轴](evidence/av1/regression-timestamps.json)、[CTest](evidence/av1/ctest.log)。

源输入 SHA-256 与研究档案逐项相同；每个 AV1 矩阵导出保存输出哈希、命令和计划。附加音视频与 192×128 硬解素材另记生成命令和输入哈希。报告未上传媒体、SDK 或构建产物。
