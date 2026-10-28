# MP4 / H.265 Smart Rendering 实现与测试报告

交付日期：2026-10-28；实验记录日期：2026-10-27。范围：单视频轨 MP4/HEVC；音频和杜比技术不在本次实现中。

## 1. 测试结论

**方案在本报告列出的输入范围内可行，C++ CLI 已完成 CMake/MSVC 构建和实际验证。**

- 最终常规矩阵 **220/220** 通过：11 种素材 × 10 种剪切 × `hev1` / `hvc1` 两种参数集传递方式。
- 无损边界模式 **18/18** 通过，2460 个输出帧全部与对应输入解码像素一致。
- 保守 IDR 方案对照 **12/12** 通过，但 open-GOP 部分剪切需要把整个选择区间重编码。
- 补充正向实验 **7/7**、随机定位 **24/24**、NVIDIA 硬解 **2/2**、错误输入/输出保护 **8/8** 通过。
- 关闭 BLA 的纯复制拼接反例在两种容器路径下均失败；删掉切换处参数集的反例也失败。另两项关闭 BLA 的混合编码对照没有触发故障，不能把“某一个片段能播”当成充分证据。

上述计数不含开发中间运行，也不把预期失败的反例算作普通成功用例。常规矩阵共验收 **22,374 帧**：19,820 帧保留原压缩图像负载，其中 19,724 帧 VCL 全字节一致、96 帧只发生 CRA→BLA NAL 头修改；其余 2,554 帧重编码。复制区像素不一致数量为 0。默认 CRF 18 下，非像素一致帧的最低逐帧 YUV PSNR 为 **41.06 dB**；这个数字仅描述合成测试图案，不代表所有真实视频的主观质量。

## 2. 如何遵守贡献要求

| 仓库要求 | 本次做法 |
|---|---|
| GPL-3.0 兼容 | 本目录代码 GPL-3.0-or-later，可按 GPLv3 条件使用，附许可证及第三方说明 |
| 语言与构建约定 | 本轮沿用 C++20/CMake；MSVC 实测；Python 仅负责测试和证据整理 |
| 先比较再选型 | 先运行两个未修改的上游 Python 实现，共 32 个常规对比；发现 POC 反例后再补 2 个上游对比 |
| 原创调整需要对比依据 | 比较 IDR 与 CRA 复制比例；针对 CRA→BLA 和参数集恢复做消融；展示少编码前导区间的收益 |
| 尽量复用 FFmpeg | 解封装、解码、编码、BSF、参数配置切换和 MP4 写入全部调用 FFmpeg 公开 API |

本目录 C++ 共 609 行，其中 engine 513 行；没有引入完整 HEVC 语法重写器。详细设计见 [DESIGN.md](DESIGN.md)，源码见 [engine.cpp](../src/engine.cpp)、[main.cpp](../src/main.cpp)。

交付前同步了远端 `3badab9`：保留新加入的 C# `pipeline/` 和根目录 GPL-3.0 许可；贡献要求已按最新 README 更新。本次结果不包含该 AVC/音频管线的新测试，也不将其已有测试数计入 HEVC 结论。

## 3. 环境与固定版本

| 项目 | 实际版本 |
|---|---|
| Windows / CPU | Windows build 26200 / Intel Core Ultra 9 275HX |
| GPU | NVIDIA GeForce RTX 5060 Laptop GPU，驱动 617.42 |
| C++ 编译器 | MSVC 19.51.36260，VS 18/2026，x64 Release，SDK 10.0.26100 |
| FFmpeg SDK / 外部 ffmpeg、ffprobe | `n8.1.3-24-g92611900bb-20261025`，BtbN GPL shared |
| SDK SHA-256 | `7416150e7dbab588c54e8aa38d4fa2f93adcfd8d0498a88c829e4ea236e442f5` |
| SDK 内 x265 | `4.3+49-020d7054`，8/10/12-bit build |
| 测试脚本 | Python 3.12.14，NumPy 2.3.5 |
| skeskinen 的依赖 | PyAV 16.0.1、tqdm 4.67.1；PyAV 自带 avcodec 62.11.100 / avformat 62.3.100 / avutil 60.8.100 |
| 最终测试 EXE SHA-256 | `a5236d81782866a092b546451589dab8b1357723b86ba1cddd97bf7df6600630` |

skeskinen 运行的是其要求的 PyAV wheel，并非强行替换为本项目 SDK。输入文件和最终验收 decoder 相同，但内部库版本与默认边界质量参数不同。因此本报告比较**正确性、复制比例及功能路径**，不据此给出运行速度或编码质量排名。EXE/DLL/SDK 不提交到 Git，仅记录哈希供本地复现核对。

上游源码固定为：

- [skeskinen/smartcut `73821ea0`](https://github.com/skeskinen/smartcut/tree/73821ea01c29a4dca81a8865ef7d348d04bb0810)：实际运行原版 CLI。
- [DeepRegular/SmartCut `84252659`](https://github.com/DeepRegular/SmartCut/tree/84252659110ad34e1af02689f3013badc1561a1e)：实际运行 **Python 参考实现**；没有编译、测试 Rust 主引擎。
- [MediaMolder `9e51f8f7`](https://github.com/MediaMolder/mediamolder/tree/9e51f8f761588e2e3c5ef87e9f11dfd5c091ac58)：阅读 CRA→BLA 与参数集重编号源码；未运行其 Go 产品，因此不作产品通过率比较。

## 4. 先做的上游比较

初始输入为 closed GOP、open GOP、Main10 open GOP、VFR open GOP，各 240 帧。每种分别测试 `[13,227)`、`[22,74)`、`[13,81)+[100,227)`、全片，共 16 项/项目。

| 被测实现 | 完整验收结果 | 观察 |
|---|---:|---|
| skeskinen Python CLI | 16/16 | 所选画面、逐帧 PTS、复制区像素均正确 |
| DeepRegular Python 参考实现 | 0/16 | CFR 也存在解码帧与 PTS 的配对错误；VFR 另有时间间隔、总时长及部分帧选择错误 |

**这里的失败不是“全部画面无法解码”。** DeepRegular 的 CFR 用例大多能解出正确次序的图像，但关联到图像的时间戳不正确；仅对 packet PTS 排序检查，可能看不出问题。全片复制甚至可以保持全部像素，仍未通过显示时间戳验收。VFR 的部分区间则出现条码帧号不符。

源码中的相关路径：

- [`renderer.py`](https://github.com/DeepRegular/SmartCut/blob/84252659110ad34e1af02689f3013badc1561a1e/smartcut/renderer.py#L250) 把区间导出成裸 HEVC、拼接，再按平均帧率重新封装。这与丢失原 MP4 显示时间关系的观察一致；本项目直接保存并映射源 PTS。
- [`probe.py`](https://github.com/DeepRegular/SmartCut/blob/84252659110ad34e1af02689f3013badc1561a1e/smartcut/probe.py#L221) / [`bitstream.py`](https://github.com/DeepRegular/SmartCut/blob/84252659110ad34e1af02689f3013badc1561a1e/smartcut/bitstream.py#L127) 根据前导图像的 reference 标记判定是否可删。在本组 RASL_R 素材上返回不可删，open-GOP 部分剪切回退为全量编码。对 HEVC 来说，“是参考图像”不等于“移除所有 RASL 后 trailing pictures 仍然依赖它”。
- [`skeskinen/video_cutter.py`](https://github.com/skeskinen/smartcut/blob/73821ea01c29a4dca81a8865ef7d348d04bb0810/smartcut/video_cutter.py#L538) 的 hybrid CRA 路径在原上下文解码前导帧，只编码必要区域，然后继续 remux。这条思路被本项目采纳。

这些结论限定于给出的版本、入口、参数及样本；不能据此推断 DeepRegular Rust 引擎具有相同问题。测试最初有沙箱临时目录权限和相对路径问题，修正环境后重新运行；环境失败记录不进入上面的通过率。归档的 `results.json` 是最终验证结果，日志保留了每次有效运行的原始命令输出。

## 5. 复制比例比较与方案选择

以下来自同一份 open-GOP 输入；“保留”指原压缩图像负载，C++ 的非连续 CRA 入口可能仅修改 NAL 类型位。

| 场景 | 输出帧数 | skeskinen 保留 / 编码 | DeepRegular Python 保留 / 编码 | C++ CRA 方案保留 / 编码 | C++ IDR 对照 |
|---|---:|---:|---:|---:|---:|
| `[13,227)` | 214 | 189 / 25 | 0 / 214 | **189 / 25** | 0 / 214 |
| `[22,74)` | 52 | 21 / 31 | 0 / 52 | **45 / 7** | 未在 IDR 对照组单独执行 |
| `[13,81)+[100,227)` | 195 | 138 / 57 | 0 / 195 | **138 / 57** | 0 / 195 |
| 800 帧素材中的 `[24,93)+[312,429)` | 186 | 138 / 48 | 0 / 186 | **186 / 0** | 未在 IDR 对照组单独执行 |

最后一行是发现 POC 拼接反例后新增的原版上游对比：skeskinen 验收通过，DeepRegular Python 仍在 decoded PTS 项失败。原版上游总计 34 项：skeskinen **17/17**，DeepRegular Python **0/17**。

收益来自更细的接入判定：当切点落在 RASL 区域，或恰好从 CRA 自身开始时，无需把其后完整 trailing 区域一起编码。并不是所有 open GOP 都必须整体重编码，也不是所有 I 帧都能直接作为接入点。

## 6. 必要性实验

### 6.1 BLA 不是装饰性修补

对 800 帧 open-GOP 输入剪取 `[24,93)` 和 `[312,429)`。两个区间全部保留压缩图像负载，编码帧数为 0，参数集也不发生编码器切换。

| 路径 | 结果 |
|---|---|
| `hev1`，CRA→BLA 开启 | 186 帧，图像次序、decoded PTS、复制像素全部正确 |
| `hvc1` 多 description 路径，开启 | 同样通过 |
| `hev1`，关闭 | 3 帧次序和 decoded PTS 错配 |
| `hvc1` 路径，关闭 | 同样 3 帧错配 |

预期源帧 `91,92,312`，关闭 BLA 后实际输出为 `312,91,92`。压缩负载没有改变，文件包数和排序后的 PTS 仍可正确。说明容器时钟不能替代解码器 POC/CVS 状态处理。

另一个跨回绕、但包含边界重编码的混合实验中，关闭 BLA 的两项用例仍通过。此结果也保留在证据中：不能宣称每次缺少 BLA 都会立刻失败。真正区分方案的是参数集不切换的纯复制跳接反例。

该处理参考了 [MediaMolder 的 `hevcCRAToBLA`](https://github.com/MediaMolder/mediamolder/blob/9e51f8f761588e2e3c5ef87e9f11dfd5c091ac58/job/smartcopy_nal.go#L339)，本项目只实现所需的 NAL 头修改，并用上述实验验证其必要性。

### 6.2 恢复参数集必要，重编号并非本方案必需

在正确的混合 `hev1` 输出中，删除第一个复制接入处恢复的 3 个 VPS/SPS/PPS NAL，替换为**相同长度的 filler**，保持 sample 位置、长度、MP4 表不变。

结果：输出从应有的 214 帧变为 211 帧，decoder 报错，183 个与预期位置匹配的复制 VCL 对应像素不一致。说明错误来自配置切换，而不是简单损坏文件偏移。

正常 `hev1` 带内配置和 `hvc1` 多 sample description 两条路径都通过常规矩阵、无损边界、硬解与定位实验。因此当前范围内无需实现 MediaMolder 的 VPS/SPS/PPS ID 重新分配及 slice header 位级重写。其设计在要求单一 sample description、不同 muxer 或其他兼容性目标下可能仍有价值，本报告没有测试或否定这些场景。

## 7. 常规矩阵与验收方法

11 种素材：closed GOP、open GOP、Main10、VFR、30000/1001、起始时间偏移、fragmented MP4、2-slice、全范围色彩/SAR、IDR/RADL、NVENC。每种 240 帧，基础尺寸 192×112；硬解专用素材为 320×192 Main10。长 POC 实验为 800 帧。均为脚本生成的移动图案和逐帧二进制条码，允许随项目分享。

10 种剪切包括首尾非关键帧、前导帧区域、多区间、全片、单帧、从 CRA 自身开始、尾部、小段离散拼接、相邻区间合并、CRA 前截断。具体范围在 [integration.py](../tests/integration.py) 的 `CUTS` 中。

验收不使用 CLI 自报“成功”作为唯一依据：

1. 独立 ffprobe 检查包数、PTS/DTS、时间基、总时长及色彩/SAR/像素格式。
2. ffprobe 解码得到**显示顺序的逐帧 PTS**，逐项和源时间映射比较，而不是只排序 packet PTS。
3. FFmpeg 解码原始 YUV，检查错误日志和帧数；条码检验实际帧号。
4. 根据 MP4 sample 文件位置读取 VCL，SHA-256 比较源与输出。只允许 CRA→BLA 的头部类型位归一化；不忽略其他差异。
5. 复制区逐像素比较，包括 B 帧；编码区计算逐帧 YUV PSNR，要求 ≥30 dB。
6. 无损模式要求全帧像素完全一致；随机定位从新的 decoder 开始；硬解结果与软件解码逐像素对照。

像素提取阶段使用独立的顺序时钟，避免把 rawvideo 输出端的时间戳告警误称为 HEVC 解码错误；真实的图像时间戳通过第 2 项另行严格检查。验收器不解析 HEVC 参考图，仍依赖 FFmpeg 解码；NVIDIA 对照提高了独立性，但没有覆盖所有解码器。

## 8. 哪些设计有用，哪些本轮不需要

| 处理 | 实测判断 |
|---|---|
| 识别真实 NAL 类型，区分 IDR/CRA/RASL/RADL | 必须；决定可复制范围及 decoder priming |
| 前导帧局部编码、后续 trailing 复制 | 有效；52 帧案例由 31 帧编码降到 7 帧 |
| 非连续 CRA→BLA | 有反例证明；纯复制跳接出现 POC 输出次序错误 |
| 恢复与当前码流对应的 VPS/SPS/PPS | 有消融证明；删除后出现丢帧与错误画面 |
| 保存并重建源 PTS/DTS，而非裸流按帧率再猜 | 必须；CFR 的图像/PTS 配对及 VFR 都要检查 |
| 参数集 ID 重编号、完整 slice header 重写 | 当前方案不必；两种配置传递路径均通过测试 |
| 重写 POC LSB、手动构造完整 RPS/DPB 依赖图 | 当前受限输入不必；采用 IRAP 约束和 BLA，依赖 FFmpeg 解码器 |
| 将所有 open GOP 当不可复制 | 正确但代价过高；IDR 对照部分剪切 100% 编码 |
| 沿用 H.264 的无损整段回退 | 本次 HEVC 不需要；18 项混合无损边界测试全部像素一致 |
| 只检查能打开、包数和排序后的 PTS | 不够；BLA 与裸流封装反例都可漏检 |

## 9. 设计不足与处理情况

已解决本轮发现的问题：CRA 接入前导帧缺依赖、POC 回绕跳接、参数集切回原流、VFR 显示时间保持、显示帧 PTS 与 packet PTS 排序假阳性、独立定位时配置恢复。

当前通过约束而非通用算法处理：固定输入参数集和尺寸；非连续 CRA 若含 RADL 则整组编码；仅 Main/Main10 逐行 4:2:0；拒绝无法可靠建立一 sample/一显示帧索引的结构。输入整体必须是有效码流，复制负载并未全部事先解码。

尚未解决或未完成验证：

- **再次剪切混合输出**：当前动态参数集输入会拒绝，需要将配置版本纳入索引，并按配置创建 decoder/BSF/encoder。
- **播放器兼容**：FFmpeg 和本机 NVIDIA 通过，不代表 Apple、Media Foundation、浏览器或电视支持 `hev1`/多 description `hvc1` 的所有切换。
- **其他 HEVC 结构**：长期参考、BLA 原始输入、场编码、稀有编码器和非连续 CRA+RADL 组合缺少系统覆盖；多层和非 4:2:0 未实现。
- **元数据**：色彩标记与 SAR 已验证；重编码边界不承诺所有 SEI/HDR 信息保留，也未验证广播 HRD 时序。
- **效率和工程化**：边界 decoder/encoder 可复用；完整扫描与内存索引在长文件上仍有成本；尚无取消、进度、缓存索引、崩溃恢复及多平台构建验证。
- **真实素材库**：本轮是可控合成输入与 NVENC 输入，不是摄像机、手机、流媒体和广播样本的兼容性认证。

音频按仓库既定方向留待下一阶段；此 CLI 遇到音频轨只选择视频，输出不含音频。

## 10. 复现与证据

构建和本项目测试命令见 [README](../README.md)。上游比较示例（源码应 checkout 到第 3 节固定 revision）：

```powershell
python -m pip install --target deps/python av==16.0.1 tqdm==4.67.1
python tests/compare_upstream.py --sources C:/research/sources --reuse-fixtures test-data/fixtures
python tests/compare_upstream.py --sources C:/research/sources --long
```

`sources` 下目录名应为 `skeskinen-smartcut` 和 `DeepRegular-SmartCut`；脚本为上游进程设置 SDK 的 PATH、PyAV 的 PYTHONPATH 以及项目内 TMP/TEMP。不会修改上游源码。skeskinen 的 `--frames` 终点是包含式，脚本将本实验 `[A,B)` 转换为 `A,B-1`；DeepRegular 则使用源帧时刻换算秒区间。

| 归档 | 内容 |
|---|---|
| [evidence/matrix.json](evidence/matrix.json) | 最终 220 项、逐项指标、输入/EXE 哈希、命令及计划 |
| [evidence/lossless.json](evidence/lossless.json) | 18 项无损边界 |
| [evidence/idr-baseline.json](evidence/idr-baseline.json) | 12 项保守 IDR 对照 |
| [evidence/experiments.json](evidence/experiments.json) | 7 项正向、5 项消融/对照、24 次定位、2 次硬解、8 项负向 |
| [evidence/upstream-skeskinen.json](evidence/upstream-skeskinen.json) | 原版 skeskinen 16 项 |
| [evidence/upstream-deepregular-python.json](evidence/upstream-deepregular-python.json) | 原版 DeepRegular Python 16 项 |
| [evidence/upstream-copy-gap.json](evidence/upstream-copy-gap.json) | 长素材纯复制拼接的 2 项上游比较 |
| [test-data](../test-data/README.md) | 输入、输出、命令日志、计划、预期失败视频和 SHA-256 清单 |

原始 JSON 中保留实验时的绝对路径和运行目录名，作为历史记录；交付目录映射见 test-data 说明。无损/IDR 对照完成于最终输入校验收紧之前：编码、复制和时间轴逻辑未变，原 EXE 哈希各自保留；最终 220 项矩阵和补充实验使用第 3 节最终 EXE。
