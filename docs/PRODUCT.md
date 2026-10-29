# SmartCut CLI 使用与构建

本目录对应根目录 `src/` 中的正式 CLI。历史研究报告与原型继续保留在 `research/`；根 README 的内容不作修改。

SmartCut 对 MP4 中的一路 H.264 或 HEVC 视频执行逐帧切割：安全的完整访问点区间复制压缩视频，仅重编码边界。音频、字幕交给原版 FFmpeg 处理整个选区，再由 libavformat 合成输出。

## 构建

已验证环境：Windows x64、Visual Studio 2026 Professional / MSVC 19.51、CMake 4.3.1、FFmpeg 8.1 系列共享 GPL SDK。预设使用 Visual Studio 18 2026 生成器，需 CMake 4.2 或更高版本。源代码采用 C++20。

SDK 需要提供 `include/`、`lib/`、`bin/`，其中包括 avformat、avcodec、avutil 的头文件、导入库与 DLL，以及原版 `ffmpeg.exe`。测试另需 `ffprobe.exe`。

```powershell
cmake --preset msvc -DFFMPEG_ROOT="C:/path/to/ffmpeg-shared-sdk"
cmake --build --preset release --parallel 1 -- /nodeReuse:false
ctest --preset release
```

如未指定 SDK，CMake 会在根 `deps/ffmpeg-*` 和现有 `research/h264/deps/` 下寻找唯一的匹配目录。生成的 `build/Release/smartcut.exe` 旁会复制运行所需的 DLL 和原版 FFmpeg。SDK 与构建产物不纳入 Git。

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

### 参数范围

- `-ss/-t/-to` 可置于 `-i` 前或后；输入、输出时间组分别处理，`-t` 优先于同组的 `-to`。
- `-map` 支持类型、输出序号、可选流 `?` 与排除映射。可选一路视频以及多路音频/字幕，绝对输出序号按映射顺序解析。
- 视频使用 FFmpeg 编码器 AVOptions，如 `-preset:v:0`、`-crf`、`-b:v`、`-bf`、`-g`、`-profile:v`、`-x264-params`、`-x265-params`。本轮正式验证的输出编码器为 libx264/libx265。
- 音频支持 FFmpeg 原有的编码、复制、码率、采样率、声道和简单音频滤镜参数；字幕支持原版 FFmpeg 在 MP4 下允许的复制或编码。格式本身不兼容时由 FFmpeg 报错。
- 保留常用元数据、语言和 disposition；支持全局 `-map_metadata 0/-1`、`-map_chapters 0/-1`。重排选区时不生成新的章节时间线。
- 支持 MP4 muxer AVOptions，如 `-movflags +faststart`。默认根据轨道时钟提高 movie timescale；起始重排需要时启用有符号 CTS 偏移。选区起点转换到源视频时间基，不能表达的部分按源 tick 精度量化；显式修改时间刻度等封装参数也可能降低精度。
- 默认以 `avc3` / `hev1` 写入流内参数集；可用 `-tag:v avc1` / `-tag:v hvc1` 改用多个 sample description。两种模式均已测试。

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

输入需为本地、未加密、可直接寻址的 MP4。视频须从真实 IDR 开始，固定参数配置，逐行扫描，4:2:0 8/10 bit；H.264 支持 Baseline/Main/High/High 10，HEVC 支持 Main/Main 10。跨 sample description 的输入配置变化、隔行、扩展分层视频等会拒绝。

H.264 采用保守的 IDR 区间划分；很长的 Open GOP 区间可能需要重编码较多帧。HEVC 按 CRA/RASL/RADL 的实际关系处理。仅支持某编码格式，不等于支持其所有 profile、元数据和播放器组合。

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

没有 NVIDIA 硬件时，去掉生成命令的 `--with-nvenc`，并对视频矩阵加 `--skip-hardware`。已有测试媒体会直接复用；生成脚本不会覆盖它们。参数测试中的 NTFS 硬链接用例需要允许创建本地硬链接的运行环境；受限沙箱可能拒绝此操作。

测试从 CMake 缓存或 `FFMPEG_ROOT` 环境变量定位 SDK。`streams.py` 应在 `parameters.py` 之前运行，为其 Windows 引号测试提供音视频素材。媒体、二进制和每次完整运行目录均只保留在本地，最终书面结论与结果快照放在 `research/product-validation/`。

- [实现设计](DESIGN.md)
- [验收报告](../research/product-validation/REPORT.md)
- [命令解析与映射源码](../src/cli.cpp)
- [视频核心源码](../src/video.cpp)
- [封装与任务调度源码](../src/app.cpp)
