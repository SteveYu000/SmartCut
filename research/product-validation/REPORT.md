# SmartCut 正式 CLI 实现与验收报告

日期：2026-10-29，Asia/Shanghai。源码入口为根目录 `src/`，使用 C++20、CMake、MSVC。该报告对应本轮正式产品实现，历史 h264/h265/pipeline 报告保持原内容。

## 结论

已实现独立的 `smartcut.exe`，采用已研究的原生 H.264/HEVC 视频方案；音频、字幕交给原版 FFmpeg 处理整个编辑区间。FFmpeg 风格的视频编码参数用于边界编码，编码格式与源视频不一致时预先报错。

最终验收共 **551/551 项通过**。其中，视频矩阵是 **24 份历史原始素材 × 10 个选区 × 2 种参数集封装方式，共 480 次导出**，不能把两种封装方式重复计算为不同素材。源文件 SHA-256 全部与历史研究记录一致。

| 验证组 | 通过 / 总数 | 内容 |
| --- | ---: | --- |
| H.264 视频矩阵 | 260 / 260 | 13 份素材、10 个选区、avc3 与 avc1/stsd |
| HEVC 视频矩阵 | 220 / 220 | 11 份素材、10 个选区、hev1 与 hvc1/stsd |
| 音频、字幕及辅助输出 | 16 / 16 | 与原版 FFmpeg 比较包、时间戳、PCM 和流属性 |
| 编码参数、错误与文件保护 | 38 / 38 | 17 组编码参数、17 组拒绝路径及 4 组其他行为 |
| 小数时间、VFR、小时级命令 | 15 / 15 | 包/显示帧 PTS、时间选区、leading gap、原命令 |
| CTest | 2 / 2 | CLI 帮助、参数解析与基本拒绝行为 |

全部结果快照记录同一个最终可执行文件 SHA-256：

```text
7073f11cde9fe4654ee36e53ffd1b744698b624f4f8aad0a58194cf9e4ba4700
```

构建环境为 Windows x64、Visual Studio 2026 / MSVC 19.51、CMake 4.3.1、FFmpeg `n8.1.3-24-g92611900bb-20261025`、Python 与 NumPy 2.3.5。具体版本、源文件校验和与原始结果见 [验收环境](evidence/environment.json) 和 [源码校验和](evidence/source-checksums.json)。测试媒体与二进制只保留在本地，不纳入 Git。

## 1. 产品实现

详细设计见 [DESIGN.md](../../docs/DESIGN.md)，构建和参数说明见 [PRODUCT.md](../../docs/PRODUCT.md)。

视频使用公开 libavformat/libavcodec/libavutil API。索引实际 NAL 与显示时间，完整安全区间直接读取并复制源压缩包；切点区间局部解码，再使用用户选择的编码器与 AVOptions。原版 ffmpeg.exe 只承担音频、字幕任务。最终 MP4 由公共 muxer API 写出，没有修改 FFmpeg 源码。

示例命令：

```powershell
smartcut.exe -hide_banner -y -i input.mp4 -ss 1:0:1 -to 2:0:1 `
  -map 0:v:0? -map 0:a:0? `
  -c:v:0 libx264 -preset:v:0 slower `
  -c:a:0 aac -b:a:0 192k output.mp4
```

其中视频的 `libx264/slower` 用于重编码区间；音频的 `aac/192k` 用于完整选区。没有注入研究原型的 CRF 18、B=0、固定 GOP 或编码线程数。不同编码格式的请求不会自动全片转码来掩盖错误。

H.264 基于真实 IDR 划分独立区间。HEVC 区分 IDR、CRA、RASL、RADL，沿用研究中的 CRA 重入与 BLA 处理。默认输出 avc3/hev1 流内参数集；显式 avc1/hvc1 时通过 NEW_EXTRADATA 交给 FFmpeg 建立多个 sample description。

音频、字幕的参数与输出序号转换后传给原版 FFmpeg。辅助 MP4 完成编码或复制后，保留其包与时间戳重封装。空音轨使用稳定 track id 识别，避免 FFmpeg 省略空轨后把后续音轨错配到错误输出位置。

## 2. 视频矩阵的方法与结果

测试直接复用历史独立检查器 [run_suite.py](../losslesscut-comparison/run_suite.py)。本轮没有修改其断言；复用的是其输出检查函数，不启动 LosslessCut，不通过 LosslessCut 生成视频。

复制/编码数量的期望值取自历史 h264/h265 原生实现的已归档结果，不从本产品自己的计划生成期望值。24 份输入的 SHA-256 逐一与历史结果核对，见 [source-fixtures.json](evidence/source-fixtures.json)。

H.264 素材包括闭合 B3、SPS id 0/3、Baseline、Open GOP、Open/IDR 混合、High 10、多 slice、NTSC、VFR、偏移时间戳、fragmented MP4、全范围色彩/SAR、NVENC 输入。HEVC 包括闭合与开放 GOP、Main 10、VFR、NTSC、偏移、fragmented、多 slice、色彩/SAR、RADL 和 NVENC 输入。

每次输出分别验证：

- 严格 FFmpeg 解码，错误识别开启，不接受解码器报错或损坏帧。
- 包数量、实际解码帧数、画面条码顺序和选区帧身份。
- 包 PTS 和解码显示帧 PTS 按有理数精确匹配源显示时间间隔，覆盖 VFR。
- 解码顺序 DTS 严格递增，测试中的解封装结果 DTS 不大于对应 PTS。
- 流时长及末帧边界，误差不超过 1 微秒。
- H.264 复制区间 VCL 字节一致；HEVC 仅允许计划中的 CRA→BLA NAL header 变化，其余 VCL 保持一致。
- 实际复制画面的解码像素逐帧一致；重编码边界检查帧身份与质量，本组 CRF 18 的最低单帧 PSNR 要求为 30 dB。
- SAR、色彩信息、位深以及两种参数集封装方式。

**结果：480/480 通过，复制效率与历史计划一致。** 完整逐例结果见 [matrix.json](evidence/matrix.json)。

例如 25 fps、保留显示帧 `[13,227)` 时：

| 输入 | 输出帧 | 复制帧 | 编码帧 | 复制比例 |
| --- | ---: | ---: | ---: | ---: |
| H.264 closed_b3_id0 | 214 | 192 | 22 | 89.72% |
| HEVC open | 214 | 189 | 25 | 88.32% |

这些 192×112、240 帧的素材面向精确性和边界覆盖，不能据此宣称 1080p/4K 长片的速度上限。报告的 `decoded_frames` 统计本项目边界解码器输出，不包含 FFmpeg 探测流信息时的内部工作。

## 3. 编码参数是否真正沿用 FFmpeg

对照采用原版 FFmpeg，给它相同源文件、相同的首个独立边界帧区间、相同的编码参数及时间基，比较实际生成的逐帧 VCL 字节。没有用“整段转码”的结果作编码字节期望，因为整段转码与独立边界的码率控制历史不同。

17 组编码设置包括：

- H.264：默认值、slower 且省略 CRF、ultrafast+CRF 12、8 B 帧、短 GOP、Open GOP、SPS id 3、Baseline、目标码率、QP 0、CRF 0。
- HEVC：默认 CRF、slower、ultrafast+CRF 12、8 B 帧、短 Open GOP、lossless。

其中 **15 组没有整体回退的设置，首个边界的全部 VCL 均与原版 FFmpeg 完全一致**。这说明本轮测试的默认值和显式参数没有被本产品改成研究原型参数。

H.264 QP 0 与 CRF 0 分别触发研究中已有的 High 4:4:4 Predictive profile 混接回退，整个选区使用用户原参数编码，**214/214 帧解码像素与源画面一致**。HEVC lossless 可继续复制中间区间，**214/214 帧像素同样一致**。没有把 `crf=0` 改写为另一个质量模式。

还验证了格式不匹配、未知编码器/选项、错误 preset、不完整区间使用 `copy`、必选映射缺失、视频滤镜、改帧率、改像素格式、错误 tag、越界/重叠区间和多输入等拒绝路径。失败时已有目标文件逐字节保持不变。

中文与空格路径、输入硬链接别名、原子覆盖和包含双引号/末尾反斜杠的 FFmpeg 子进程参数也通过测试。硬链接测试首次在受限沙箱下被操作系统拒绝创建，随后在正常本机权限下完成；这不是 SmartCut 输出错误。

**结果：38/38 通过。** 见 [parameters.json](evidence/parameters.json)。

## 4. 音频与字幕的原版 FFmpeg 对照

使用含两路 AAC 音频、语言标记和 mov_text 字幕的源文件，并补充 HEVC、VFR、延迟音轨和极短区间。

对照项包括音频编码与复制、多音轨、输出顺序重排、绝对流序号、AAC 192k、ALAC、44.1 kHz 单声道、简单音频滤镜、字幕编码/复制、输入与输出 seek、单帧选区和空音轨。另验证纯音频导出的 `+faststart` 和 `movie_timescale=90000` 实际生效。

逐流比较 codec、time base、采样率、声道、语言、disposition，以及每个包的 SHA-256、PTS、DTS、duration、skip_samples 侧数据。音频再解码为 f32 PCM 比较字节数和 SHA-256。

**结果：16/16 通过，与原版 FFmpeg 一致。** 本产品没有声称压缩音频复制可以在任意采样点无损截断；复制模式的边界仍采用原版 FFmpeg 语义。字幕事件跨边界的处理也同理。

完整流、包与 PCM 对照见 [streams.json](evidence/streams.json)。

## 5. 小数时间与原示例

新增 15 项时间轴验证，包括 H.264/HEVC 的 CFR、NTSC、VFR、初始时间偏移、小数 `-ss/-to`、输入和输出两组 `-ss`、`-t` 与 `-to` 同时出现、输入侧 seek。

这些测试独立计算保留帧与输出 PTS，检查解码显示 PTS、leading gap、活动视频时长与选区终点，不仅比较播放器显示的总时长。

原命令用一个四小时时间轴、240 个编码帧的稀疏视频验证，并在第 3600 秒附近加入音频。执行 `-ss 1:0:1 -to 2:0:1` 后：

- 选区长度 3600 秒。
- 输出保留源显示帧 61 至 120，共 60 帧，首帧 PTS 为 59 秒；此前是正确保留的选区空白时间。
- 视频帧顺序与时间戳正确，复制画面像素一致。
- 音频包及解码 PCM 与原版 FFmpeg 相同参数输出完全一致。

这是长时间数值与参数语义测试，不是处理一小时高帧率实拍素材的吞吐或稳定性测试。

**结果：15/15 通过。** 见 [timestamps.json](evidence/timestamps.json)。

## 6. 本轮发现并修复的问题

| 问题 | 原因与解决方式 | 复验 |
| --- | --- | --- |
| NVENC 输入的 SAR 丢失 | 只取 AVStream SAR，遗漏 codecpar 中的有效值；改用 FFmpeg `av_guess_sample_aspect_ratio`，同时用于编码器与输出流 | H.264/HEVC NVENC、两种封装均通过 |
| NTSC 末尾时长被舍短 | 首个边界 B 帧触发 movenc 负 DTS 的 start_cts 推断；必要时启用现有 signed CTS 选项 | NTSC 所有帧选区与小数时间通过 |
| 一律 signed CTS 影响 HEVC 短片段/RADL | 初始重排信息和先解码后显示的 leading picture 不适合统一平移策略；按实际包顺序判断，并保留 IDR+RADL 的原无符号 CTS 调度 | tiny_disjoint、RADL、CRA leading 回归通过 |
| 小数 NTSC 起点被量化到毫秒 | empty edit 使用默认 1000 movie timescale；按轨道时钟提高 movie timescale | 首帧从错误的 0.003 秒恢复为 116/30000 秒 |
| 空音轨导致输出位置错配 | 辅助 FFmpeg 会省略没有包的流；使用稳定 track id 关联，空轨提示后跳过 | 空轨在视频前后两种映射顺序均通过 |
| 编码器 KEY 标记含普通恢复点 I 帧 | 检查实际 VCL NAL；H.264 仅 IDR 标为独立访问点，流内参数集在后续真正访问点补齐 | 短 GOP、Open GOP 与完整矩阵通过 |
| 纯音频输出漏传封装参数 | 视频流程的辅助 muxer 参数应留给最终 mux，但无视频时就是最终输出；此时直接传给原版 FFmpeg | faststart box 顺序及指定 timescale 通过 |

因此，“调用 FFmpeg”本身既不能保证失败，也不能保证成功。关键在于调用粒度、访问点判断、参数切换、真实显示时间和 MP4 编辑列表的处理。本实现复用 FFmpeg 组件，同时控制上述核心边界。

## 7. 哪些复杂处理有用、哪些本轮无需采用

有用且保留的部分是实际 NAL/访问点识别、HEVC leading picture 分类、局部解码所需的前置引用、编码后参数集切换、显示与解码时间分离，以及独立的严格解码/PTS/复制像素验证。

在本轮限定输入下，**全局重新分配 SPS/PPS id、把新 slice 强行改为使用旧参数集、关闭所有 B 帧、固定边界 GOP、把所有视频解码后再挑帧，以及自行编写音频剪切编码器**都不是必要条件。参数对照和同 id/不同 id 的双封装矩阵给出了实际证据。

这不是对其他项目全部设计的否定。DeepRegular 等项目若要处理更复杂的参数变化、跨片源拼接、更多编码格式和设备，相关重写、依赖分析和兼容层仍可能必要。本轮也没有重新运行 LosslessCut 后据此重写其历史结论；与它的区别是本产品直接控制安全复制单元、配置切换与时间轴，而不是仅依赖编码小段后 concat 的推断。

## 8. 剩余边界与不足

- H.264 仍按保守 IDR 区间划分，没有构建任意参考图；长 Open GOP 的重编码量不一定最小。
- 输入限定固定配置、逐行、4:2:0 8/10 bit。动态参数集、多层、隔行、加密或损坏输入没有通用修复方案。
- 多个帧区间目前限视频；音频/字幕多段拼接尚未实现，明确拒绝。
- 独立 CLI 仅支持文档列出的 FFmpeg 风格参数范围，没有实现完整 ffmpeg.c 命令前端。
- 输入时间基不能精确表达的选区起点按源 tick 精度量化；显式更改封装时间参数也可能降低精度。
- H.264 某些无损参数需要整体选区编码回退，不能同时保证最小重编码量。
- 不保证任意 HDR 动态元数据、旋转/显示矩阵、完整 SEI 与所有硬件播放器的行为；本轮不处理杜比实现。
- 输出与报告分别原子发布；尚未实现跨文件事务、断电持久性与对恶意并发源改写的快照保护。
- 本轮没有完成真实长片、4K、异常退出压力、全部硬件编码器及硬件播放器的产品兼容认证。

这些限制已在使用文档公开。对本轮支持范围，所有最终验收项通过；超出范围时应明确报错或进行后续专项验证，不能由小尺寸矩阵外推通用兼容性。

## 9. 复现与证据

```powershell
cmake --preset msvc -DFFMPEG_ROOT="C:/path/to/ffmpeg-shared-sdk"
cmake --build --preset release --parallel 1 -- /nodeReuse:false
ctest --preset release
python -m pip install -r tests/requirements.txt
python tests/prepare_fixtures.py --with-nvenc
python tests/integration.py
python tests/streams.py
python tests/parameters.py
python tests/timestamps.py
python tests/archive_results.py
```

没有 NVIDIA 编码设备时可跳过硬件输入，视频矩阵将变为 440 次软件素材导出；本报告的正式验收包含两份 NVENC 输入，因此为 480 次。它们的输出边界仍使用 libx264/libx265，不等于验收了 NVENC 输出编码。

`archive_results.py` 会核对所有测试完整通过、最终程序哈希相同、24 份历史素材哈希相同后再生成固定证据快照。原始每次运行的媒体、日志和逐例计划留在本项目本地时间戳目录中。最终证据目录内不包含程序、DLL 或测试视频。

- [完整视频矩阵](evidence/matrix.json)
- [音频与字幕原版对照](evidence/streams.json)
- [参数、拒绝路径和文件保护](evidence/parameters.json)
- [精确时间轴验证](evidence/timestamps.json)
- [CTest 原始日志](evidence/ctest.log)
- [环境与汇总](evidence/environment.json)
- [历史素材校验和](evidence/source-fixtures.json)
- [本轮源码校验和](evidence/source-checksums.json)
