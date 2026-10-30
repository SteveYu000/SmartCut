# MKV Smart Rendering 研究与复现

结论及限制见 [研究报告](REPORT.md)，逐例记录见 [evidence/summary.json](evidence/summary.json)。研究日期为 2026-10-30 至 2026-10-31。

这是独立的视频研究原型，直接读取 MKV/MP4、输出 MKV，复用根目录 AVC、HEVC、AV1 的规划和边界编码实现。正式产品 `src/` 的行为未因这次研究改变。音频、字幕、附件和章节另外用原版 FFmpeg 验证。

## 构建

需要 Visual Studio 2026 C++ 工具链、CMake、Python 3，以及 FFmpeg **9.0.2 共享开发包**。SDK 应包含 `include/`、`lib/`、`bin/`；本次使用 Gyan full shared 包，来源及哈希见 [manifest.json](evidence/manifest.json)。Python 测试依赖 NumPy 2.3.5。

从仓库根目录运行，并替换 SDK/Python 路径：

```powershell
cmake -S research/mkv -B research/mkv/build -G "Visual Studio 18 2026" -A x64 `
  -DFFMPEG_ROOT="C:/SDK/ffmpeg-9.0.2" `
  -DPython3_EXECUTABLE="C:/Python312/python.exe"
cmake --build research/mkv/build --config Release --parallel 1 -- /nodeReuse:false
python -m pip install -r research/mkv/requirements-test.txt
$env:FFMPEG_ROOT = "C:/SDK/ffmpeg-9.0.2"
```

`FFMPEG_ROOT` 也可通过环境变量传给 CMake。输出为 `research/mkv/build/Release/mkv-smartcut.exe`，运行时保留同目录 DLL。

`prepare_core.py` 只在 **build 目录**产生原有 `video.cpp` / `av1.cpp` 的适配副本；精确匹配的替换锚点变化时配置失败，不会静默套用错误修改。生成的差异和原文件哈希已归档在 `evidence/core-*.diff`、`evidence/core-adapter.json`。这样可以审核 MKV 所需变化，同时避免维护两套复制的切点算法。

## 原型命令

```powershell
research/mkv/build/Release/mkv-smartcut.exe -y -i input.mkv `
  --keep-frames 13:227 -c:v libx264 -preset medium -crf 18 `
  --report result.json output.mkv

research/mkv/build/Release/mkv-smartcut.exe -y -i input.mkv `
  --keep-frames 13:81 --keep-frames 100:227 -c:v libx265 `
  -preset medium -crf 18 --report result.json output.mkv
```

帧范围从 0 开始，左闭右开。原型要求输入只有一个视频流；多流处理参见 `tests/streams.py`，不能把正式 CLI 的全部选项视为本原型已实现的功能。

AV1 默认 `--av1-policy reject`：局部编码后若出现不同 Sequence Header，在发布输出前报错。

```powershell
research/mkv/build/Release/mkv-smartcut.exe -y -i input.mkv `
  --keep-frames 13:227 -c:v libaom-av1 -cpu-used 6 -crf 18 `
  --av1-policy full --report result.json output.mkv
```

`full` 表示用户明确接受配置不一致时对**整个选区**重新编码；不需要回退时仍按局部方案输出。`experimental` 仅用于生成违反 AV1 单 Sequence Header 限制的反例。`--header-mode extradata/seams` 是故意移除必要参数集的消融模式，不是产品建议；默认 `all` 在独立访问点恢复当前参数集。

## 测试视频的来源

本研究复用先前的 240 帧、192×112、带帧号条码素材，再用 FFmpeg 9.0.2 原包转换为 MKV。**22 个输入均验证压缩包和解码像素与对应 MP4 一致**。NTSC 时间戳在转为默认毫秒 MKV 时最多量化 0.5 ms。

[fixtures.json](evidence/fixtures.json) 记录每个原始 MP4 的路径和 SHA-256、MKV 的 SHA-256、完整转换命令、编码类型和容器摘要。无需提交视频，也能明确本轮测试的是哪一个文件。

若本地没有原始 MP4，先使用旧研究所记录的 **8.1 SDK 及其编码器版本**恢复输入：

```powershell
$env:FFMPEG_ROOT = "C:/SDK/ffmpeg-original-8.1"
python tests/prepare_fixtures.py
python research/av1/tests/fixtures.py --names aom,svt,main10,vfr,ntsc,rav1e,color_sar
```

原始生成脚本：[AVC](../h264/tests/integration.py)、[HEVC](../h265/tests/common.py)、[AV1](../av1/tests/fixtures.py)。原始版本记录见对应项目报告。不同编码器构建生成的文件可能有不同哈希；它是新的素材版本，不能冒充本轮固定输入。MKV 生成脚本会校验历史源文件哈希。

## 运行验证

恢复素材后切回 9.0.2 SDK：

```powershell
$env:FFMPEG_ROOT = "C:/SDK/ffmpeg-9.0.2"
python research/mkv/tests/fixtures.py
python research/mkv/tests/matrix.py --projects prototype --seek
python research/mkv/tests/experiments.py
python research/mkv/tests/streams.py
python research/mkv/tests/edge_cases.py
```

`matrix.py` 默认使用 `experimental` 策略，目的是把 AV1 的 6 个不合规混合输出交给独立检查器识别。因此本轮矩阵应显示 **104/110**，而非全部通过。`experiments.py` 另证实这 6 个用例在默认策略下均被拒绝、在 `full` 策略下均通过整段编码；实验中的消融及旧 FFmpeg 对照也有预期失败。

上游对照还需要本地下载的：

- DeepRegular SmartCut v0.8.37 官方 Windows CLI，放在 `sources/DeepRegular-SmartCut-0.8.37-win64/smartcut/`。
- skeskinen/smartcut 固定提交 `73821ea01c29a4dca81a8865ef7d348d04bb0810`，放在 `sources/skeskinen-smartcut/`；PyAV 16.0.1 及依赖位于历史研究的 `research/h265/deps/python/`。
- MKVToolNix 103.0 官方便携版，放在 `sources/mkvtoolnix-103.0-win64/mkvtoolnix/`；其下载地址和校验值见证据清单。

```powershell
python research/mkv/tests/matrix.py --projects deepregular,skeskinen --seek
python research/mkv/tests/trace_skeskinen.py
```

`recheck.py <results.json>` 可对已有导出重新运行检查器，不重新编码、不修改媒体。`archive.py --help` 列出证据归档参数。检查器允许无 `DefaultDuration`/`BlockDuration` 时从后续显示时间和 Segment 末端推算保持时长，不把“未声明”误判成“声明为零”。

所有构建物、视频、临时压缩包 spool 和实验运行目录都被忽略；报告、研究代码及 `evidence/` 中的文本证据保留。下载的上游代码仅留在本地 `sources/`。
