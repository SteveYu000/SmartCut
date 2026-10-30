# SmartCut CLI 使用与构建

本文对应根目录 `src/` 中的正式 CLI。历史研究报告与原型继续保留在 `research/`。

SmartCut 对 MP4 中的一路 H.264、HEVC 或 AV1 视频执行逐帧切割：复制依赖关系安全的压缩区间，仅重编码必要边界。音频、字幕交给原版 FFmpeg 处理整个选区，再由 libavformat 合成输出。

## 构建

已验证环境：Windows x64、Visual Studio 2026 Professional / MSVC 19.51、CMake 4.3.1、FFmpeg 8.1 系列共享 GPL SDK。预设使用 Visual Studio 18 2026 生成器，需 CMake 4.2 或更高版本。源代码采用 C++20。

SDK 需要提供 `include/`、`lib/`、`bin/`，其中包括 avformat、avcodec、avutil 的头文件、导入库与 DLL，以及原版 `ffmpeg.exe`。测试另需 `ffprobe.exe`。AV1 需要可用的 AV1 解码器（优先使用 libdav1d）与所选编码器；本轮验证 libaom-av1、libsvtav1、librav1e 和 av1_nvenc。NVENC 另需支持 AV1 的 NVIDIA GPU。

```powershell
cmake --preset msvc -DFFMPEG_ROOT="C:/path/to/ffmpeg-shared-sdk"
cmake --build --preset release --parallel 1 -- /nodeReuse:false
ctest --preset release
```

开发者自行下载共享 SDK，通过 `-DFFMPEG_ROOT="SDK根目录"` 指定位置；也可在首次配置或缓存变量为空时使用 `FFMPEG_ROOT` 环境变量。命令行/缓存中的非空设置优先于环境变量。未指定时，CMake 会给出配置提示。生成的 `build/Release/smartcut.exe` 旁会复制所选 SDK 的 DLL 和原版 FFmpeg。SDK 与构建产物不纳入 Git。

更换 SDK 后重新执行上述配置命令，头文件与导入库会全部从新位置查找，避免沿用旧路径缓存。此设置仅选择编译使用的 SDK；更换 ABI 主版本后仍需重新构建并验证。根项目与 `research/h264`、`research/h265` 使用相同的 `FFMPEG_ROOT` 配置方式。

不同版本的 Visual Studio 可另建构建目录，手动选择对应生成器；上述预设验证的是 VS 2026。POSIX 平台保留了平台封装，但本轮未在 Linux/macOS 构建验收。

## 命令

```powershell
.\build\Release\smartcut.exe -hide_banner -y -i input.mp4 `
  -ss 1:0:1 -to 2:0:1 `
  -map 0:v:0? -map 0:a:0? `
  -c:v:0 libx264 -preset:v:0 slower `
  -c:a:0 aac -b:a:0 192k `
  --report output.json output.mp4
```

此命令保留第 3601 秒至第 7201 秒的选区。`libx264`、`slower` 只用于需要编码的视频区间；AAC、192 kbit/s 用于整个选区的所选音轨。没有指定 CRF、B 帧、GOP、线程数时，使用所选 FFmpeg 编码器的默认值。

如果指定了编码器，指定的编码器与源视频格式要求一致，否则会报错，包括整个选区原本恰好可复制的情况。省略 `-c:v` 时按 FFmpeg 的 MP4 默认视频编码器选择规则处理。

AV1 输入示例：

```powershell
.\build\Release\smartcut.exe -hide_banner -y -i input-av1.mp4 -ss 1 -to 8 `
  -map 0:v:0? -map 0:a:0? `
  -c:v:0 libsvtav1 -preset:v:0 8 -crf:v:0 24 `
  -c:a:0 copy output-av1.mp4
```

也可显式选 `libaom-av1` 并指定其 `-cpu-used` 等参数。程序不注入 AV1 preset、CRF、lookahead 或编码线程数；没有提供的编码参数使用 FFmpeg 编码器默认值。MP4 默认编码器通常不是 AV1，所以 AV1 重编码应显式选择匹配编码器。

### 参数范围

- `-ss/-t/-to` 可置于 `-i` 前或后；输入、输出时间组分别处理，`-t` 优先于同组的 `-to`。
- `-map` 支持类型、输出序号、可选流 `?` 与排除映射。可选一路视频以及多路音频/字幕，绝对输出序号按映射顺序解析。
- 视频使用 FFmpeg 编码器 AVOptions，如 `-preset:v:0`、`-crf`、`-b:v`、`-bf`、`-g`、`-profile:v`、`-x264-params`、`-x265-params`、`-cpu-used`、`-svtav1-params`。参数必须是所选编码器实际支持的选项。
- 音频支持 FFmpeg 原有的编码、复制、码率、采样率、声道和简单音频滤镜参数；字幕支持原版 FFmpeg 在 MP4 下允许的复制或编码。格式本身不兼容时由 FFmpeg 报错。
- 保留常用元数据、语言和 disposition；支持全局 `-map_metadata 0/-1`、`-map_chapters 0/-1`。重排选区时不生成新的章节时间线。
- 支持 MP4 muxer AVOptions，如 `-movflags +faststart`。默认根据轨道时钟提高 movie timescale；起始重排需要时启用有符号 CTS 偏移。选区起点转换到源视频时间基，不能表达的部分按源 tick 精度量化；显式修改时间刻度等封装参数也可能降低精度。
- 默认以 `avc3` / `hev1` 写入流内参数集；可用 `-tag:v avc1` / `-tag:v hvc1` 改用多个 sample description。两种模式均已测试。
- AV1 使用 `av01`，保留带内 Sequence Header，并通过 FFmpeg 的 `NEW_EXTRADATA` 在需要时创建、切换 av1C / sample description；不能设置为 `avc1`、`hev1` 等其他编码的 tag。

这是受支持参数范围明确的独立 CLI。它没有嵌入完整 ffmpeg.c 命令前端。视频滤镜、缩放、帧率转换、强制关键帧、任意 bitstream filter、`copyts`、`shortest`、复杂滤镜、多输入、多输出等不受支持。需要改变复制区间画面或时间线的操作会报错，应使用完整转码。

### 分析与精确帧区间

```powershell
.\build\Release\smartcut.exe -i input.mp4 -ss 1 -to 8 `
  -c:v libx264 --analyze --report plan.json

.\build\Release\smartcut.exe -i input.mp4 -an -sn -c:v libx264 `
  --keep-frames 13:80 --keep-frames 120:227 output.mp4
```

帧索引是按源视频显示 PTS 排序的零起始序号，右端不包含在内。多个区间必须有序且不重叠。重复帧区间的合并目前限视频；多段音频、字幕拼接尚未实现，会明确报错。帧区间不能和时间选区参数混用。

`--analyze` 验证输入、视频编码设置并生成计划，不写视频，也不会运行音频/字幕编码任务。实际编码完成后才能确认编码器输出、无损回退和辅助流处理结果。

### 文件与诊断

- 未给 `-y` 时拒绝覆盖；`-n` 显式拒绝覆盖。
- 输出先在目标卷的私有目录中完成，成功后原子替换目标。编码失败保留已有输出，拒绝输入与输出同文件或硬链接别名。
- `--report FILE` 记录选区、参数、复制/编码/解码帧数、时间基、CTS 策略及无损回退。
- `--ffmpeg FILE` 指定原版 FFmpeg，默认取程序旁的可执行文件。
- `--work-dir DIR` 设置临时目录父目录；`--keep-work` 保留该次私有工作目录用于诊断。
- Ctrl-C 取消任务并终止 FFmpeg 子进程；普通任务结束时清理其私有目录。
- 视频输出与 JSON 报告分别原子发布，不是跨文件事务。视频发布成功后若报告写入失败，视频可能已存在，CLI 仍会报告失败。

## 支持边界

输入需为本地、未加密、可直接寻址的 MP4，4:2:0 8/10 bit、固定配置。H.264 支持 Baseline/Main/High/High 10，HEVC 支持 Main/Main 10，须从真实 IDR 开始且为逐行视频。AV1 支持 Main，须从带 Sequence Header 的 shown KEY_FRAME 开始，单层、一 TU 显示一帧、PTS= DTS 且 sample duration 连续。跨 sample description 的输入配置变化、隔行、扩展分层视频等会拒绝。

H.264 采用保守的 IDR 区间划分；很长的 Open GOP 区间可能需要重编码较多帧。HEVC 按 CRA/RASL/RADL 的实际关系处理。仅支持某编码格式，不等于支持其所有 profile、元数据和播放器组合。

AV1 在起点到下一个有效随机访问点之间编码，后续直接复制到结束 TU，尾部无需再次重编码；选择在同一恢复区间中间开始、结束时仍会编码该选择。源 TU 内的隐藏参考帧和 show_existing 指令随 sample 完整复制。VFR 的源 PTS、duration 在编码后恢复，不按平均帧率生成最终时间轴。包含动态 Sequence Header 的混合输出，目前不能再次作为本程序输入；需要再次剪辑时使用原始素材。AV1 多层、film grain、HDR 和动态分辨率未完成验证。

H.264 的 `-crf 0` / `-qp 0` 可能生成 High 4:4:4 Predictive 参数集。与原有有损区间混接时，沿用研究结果，回退为整个选区按用户的原参数编码，日志与报告会明确标记。没有把 CRF 改成 QP 或降低无损要求。

本轮验证了 FFmpeg 软件解码和文件时间轴，未完成全部硬件播放器认证。任意 HDR 动态元数据、旋转/显示矩阵、完整 SEI 保留和损坏源修复不属于当前保证范围。本轮不实现杜比相关处理。

## 测试与设计文档

Python 测试需要 `tests/requirements.txt`。先构建，再生成未跟踪的测试媒体：

```powershell
python -m pip install -r tests/requirements.txt
python tests/prepare_fixtures.py --with-nvenc
python tests/integration.py
python tests/streams.py
python tests/parameters.py
python tests/timestamps.py
python tests/archive_results.py
```

AV1 测试复用 [AV1 研究素材](../research/av1/README.md) 中的 11 类固定输入；先按该说明生成媒体并核对 SHA-256，再运行：

```powershell
python tests/av1.py
python tests/av1.py --encoder libsvtav1 --names svt,main10,vfr,nvenc
python tests/av1.py --encoder librav1e --names aom,main10,vfr --cuts partial,multi,one,tiny
python tests/av1.py --encoder av1_nvenc --names nvenc
python tests/av1_features.py
```

无 NVIDIA 环境时从主矩阵 `--names` 中去掉 nvenc，并给 `av1_features.py` 加 `--skip-hardware`；这属于完整验收的子集。AV1 验证覆盖逐帧身份、显示 PTS、duration、压缩包和复制像素、av1C/stsd、SAR/颜色、seek、编码参数与原版 FFmpeg 一致性，以及音频/字幕的包、时间和 PCM 对照。

AV1 脚本打印各次 `research/product-validation/av1-时间/` 目录。完整矩阵、附加测试与原有回归都完成后，可将上述五个 AV1 运行目录传给 `python tests/archive_av1.py --runs <AOM目录> <SVT目录> <rav1e目录> <NVENC目录> <features目录>`。脚本确认所有记录来自当前同一 EXE、全部通过，再归档到 `research/product-validation/evidence/av1/`；不会将子集误记为完整验收，也不覆盖原有历史验收快照。

没有 NVIDIA 硬件时，去掉生成命令的 `--with-nvenc`，并对视频矩阵加 `--skip-hardware`。已有测试媒体会直接复用；生成脚本不会覆盖它们。参数测试中的 NTFS 硬链接用例需要允许创建本地硬链接的运行环境；受限沙箱可能拒绝此操作。

测试从 CMake 缓存或 `FFMPEG_ROOT` 环境变量定位 SDK。`streams.py` 应在 `parameters.py` 之前运行，为其 Windows 引号测试提供音视频素材。媒体、二进制和每次完整运行目录均只保留在本地，最终书面结论与结果快照放在 `research/product-validation/`。

- [实现设计](DESIGN.md)
- [验收报告](../research/product-validation/REPORT.md)
- [AV1 接入验收报告](../research/product-validation/AV1_REPORT.md)
- [命令解析与映射源码](../src/cli.cpp)
- [视频核心源码](../src/video.cpp)
- [封装与任务调度源码](../src/app.cpp)
