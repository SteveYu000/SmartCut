# H.264 / MP4 Smart Rendering CLI

C++20 实现，使用 FFmpeg 公开 API。完整的 IDR 区间直接复制，剪切点涉及的不完整区间用 libx264 重新编码；按显示帧编号精确剪切，两端均处理。项目目标是可检查、可复现的实现与实验，不是通用视频编辑器。

**完整设计、项目比较与实测结论：[实现与测试报告](docs/IMPLEMENTATION_REPORT.md)。**

本目录包含源码、构建配置、测试脚本、报告、JSON 证据和 [最终测试视频](test-data/README.md)。不包含编译后的 EXE、DLL、构建目录或 FFmpeg SDK。测试视频保留原始字节；JSON 中的本机绝对路径已转换为项目相对路径。

## 使用

先按下方“MSVC 构建”编译，再从 `h264` 目录执行以下命令。运行时需要 Microsoft Visual C++ x64 运行库；CMake 会把所需 FFmpeg DLL 复制到构建输出目录。实际构建环境安装了 Visual Studio 2026 Professional。

```powershell
.\build\Release\smart_render.exe -i input.mp4 -o output.mp4 --keep 13:227 --report plan.json
.\build\Release\smart_render.exe -i input.mp4 -o selected.mp4 --keep 13:100 --keep 150:227
.\build\Release\smart_render.exe -i input.mp4 --keep 13:227 --analyze
.\build\Release\smart_render.exe -i input.mp4 -o output-stsd.mp4 --keep 13:227 --ps-mode stsd
```

`--keep A:B` 使用从零开始的**显示帧序号**，包含 A，不包含 B。例如 `13:227` 输出 214 帧。多个区间须有序且不重叠，相邻区间自动合并。它不是秒数，也不是解码顺序中的包编号。

默认 `--crf 18`。复制帧的画面压缩数据保持不变；重编码帧存在有损压缩。`--crf 0` 请求 QP=0 无损编码：**只要有边界需要编码，就明确回退为整个选区重新编码**，以规避已观察到的无损/有损配置混合问题；若选区本来就能全部复制，则仍全部复制。“无损”以源文件解码像素为参照。

默认 `--ps-mode avc3` 通过带内 SPS/PPS 切换配置；`stsd` 通过 MP4 的多个 sample description 切换。两者都已通过本项目 FFmpeg 解码及随机访问测试，尚未验证各播放器/硬件解码器的互操作性。

标准输出是 JSON，诊断写到标准错误。`--report` 另存 JSON。既有输出和报告不覆盖；导出先写临时 MP4，成功后改名，失败时清理临时文件。输入不能在处理期间被外部修改。

仓库中已保存用于演示的原始测试视频：

```powershell
.\build\Release\smart_render.exe -i test-data/run-20261027-125923/source-closed_b3_id0.mp4 -o demo.mp4 --keep 13:227 --report demo.json
```

预期 214 帧，其中 192 帧复制、22 帧重编码。已通过验证的参考输出是 [closed_b3_id0-both_edges-avc3-crf18.mp4](test-data/run-20261027-125923/closed_b3_id0-both_edges-avc3-crf18.mp4)。

## 支持范围

| 项目 | 当前行为 |
| --- | --- |
| 输入 | 本地、未加密 MP4，恰好一个 H.264 视频轨；可含其他轨道 |
| 输出 | MP4，只有视频；音频、字幕及其他轨道不输出 |
| 视频 | 逐行，固定格式，4:2:0 的 8-bit / 10-bit；CFR / VFR；输入 profile 限于 Baseline / Main / High / High 10 |
| 输入配置 | avc1/avc3；配置固定，必须从真实 IDR 开始，时间戳完整 |
| Open GOP | 可以处理，但只在真实 IDR 恢复复制，可能重编码整个选区 |
| 元数据 | 处理色彩字段与 SAR；不保证保留任意 SEI、HRD、旋转、章节或其他附属信息 |
| 主动拒绝 | 隔行/未知扫描方式、多视频轨、其他编码、动态参数集、缺失或异常时间戳等 |

当前输入约束不支持重读包含参数集切换的混合导出文件，再次剪切应使用原始输入。High 4:4:4 Predictive 等无损源配置即使像素为 4:2:0，也会拒绝，避免再次形成无损/有损配置混合。不是所有合法 H.264 MP4 都在本版支持范围内；详见报告中的限制。

## MSVC 构建

准备 Visual Studio 的“使用 C++ 的桌面开发”、CMake 与 Python 3.12。项目不需要 FFmpeg 源码编译，也不需要 x264 的独立开发包：通过 FFmpeg 的 `libx264` 编码器封装调用。

```powershell
python scripts/fetch_ffmpeg.py
cmake --preset msvc
cmake --build --preset release
ctest --preset release
```

下载脚本固定 BtbN FFmpeg 8.1 SDK 的 URL、大小和 SHA-256，支持续传。`CMakePresets.json` 使用本机实际验证的 `Visual Studio 18 2026`；也可另行指定生成器：

```powershell
cmake -S . -B build-vs2022 -G "Visual Studio 17 2022" -A x64 -DFFMPEG_ROOT="C:/sdk/ffmpeg"
cmake --build build-vs2022 --config Release
```

VS 2022 命令是配置示例，未在本次机器上实测。依赖需要 `include/`、MSVC 可链接的 `lib/*.lib` 和 `bin/*.dll`。`stsd` 路线依赖本次使用的 FFmpeg 8.1 movmuxer 行为，不能假定任意旧版本都等价。

## 重现测试

```powershell
python -m pip install -r requirements-test.txt
python tests/integration.py --quick
python tests/integration.py
# 有可用 NVIDIA NVENC 硬件时：
python tests/integration.py --with-nvenc
# 替换为上一命令打印的结果目录：
python tests/experiments.py --suite-dir test-output/run-YYYYMMDD-HHMMSS
```

`ctest` 只运行 CLI 帮助的启动检查；视频正确性由上述 Python 集成测试完成。默认完整集成测试不依赖 GPU，含 244 个导出用例；启用 NVENC 为 264 个。每次测试新建结果目录，不删除既有数据。测试会生成素材并消耗磁盘空间。

测试独立验证严格解码、帧数、每帧 PTS、单调 DTS、复制帧逐像素一致性、VCL NAL SHA-256、重编码帧 PSNR 与画面帧号、sample entry 和 sample description，以及错误输入与参数集恢复的破坏性对照。

最终实测证据在 [docs/evidence](docs/evidence)。264 个导出、11 个拒绝用例、1 个破坏性对照和 5 个补充用例全部达到各自预期。破坏性对照的“通过”指成功检出了人为破坏，不能理解为损坏文件可正常播放。

## 文件布局

| 文件 | 内容 |
| --- | --- |
| `src/main.cpp` | 参数解析、UTF-8 Windows 路径入口 |
| `src/engine.cpp` | 索引、规划、编解码桥接、参数集承载、时间轴与复用 |
| `src/engine.hpp` | 配置结构与入口 |
| `docs/DESIGN.md` | 实现不变量和数据流 |
| `docs/IMPLEMENTATION_REPORT.md` | 书面研究、实验结果、设计取舍和不足 |
| `tests/integration.py` | 带帧号素材、集成矩阵、失败输入及消融实验 |
| `tests/experiments.py` | 中文路径、随机访问、简化 concat 对照与性能实验 |

依赖说明及许可证原文见 [THIRD_PARTY.md](THIRD_PARTY.md)。
