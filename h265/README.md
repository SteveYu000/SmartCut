# MP4 / HEVC Smart Rendering 实验

Windows、C++20、CMake + MSVC。对指定的显示帧区间精确剪切：保留完整、依赖关系安全的压缩区间，重编码边界；在不连续的 CRA 入口处理 RASL 并设置 BLA。仅输出一条视频流。

详细结论见 [实现与测试报告](docs/IMPLEMENTATION_REPORT.md)，技术方案见 [设计说明](docs/DESIGN.md)，测试视频说明见 [test-data/README.md](test-data/README.md)。本实验遵循仓库要求，先运行未修改的上游实现，再实现和验证 C++ 方案。

## 构建

安装 Visual Studio 2026 的 C++ 工具；使用 VS 2022 时将生成器改为 `Visual Studio 17 2022`，该版本未在本机验证。安装 Python 仅为下载 SDK 和运行测试，CLI 本身不依赖 Python。

```powershell
python scripts/fetch_ffmpeg.py
cmake --preset msvc
cmake --build --preset release
ctest --preset release
```

已有 SDK 时可直接指定：

```powershell
cmake -S . -B build -G "Visual Studio 18 2026" -A x64 -DFFMPEG_ROOT="C:/SDK/ffmpeg"
cmake --build build --config Release
```

`FFMPEG_ROOT` 应含 `include/`、`lib/` 和 `bin/`。构建后 DLL 会复制到本地 EXE 目录，二者均被 Git 忽略。

## 使用

```powershell
./build/Release/smart_render.exe -i input.mp4 -o output.mp4 --keep 13:227
./build/Release/smart_render.exe -i input.mp4 -o joined.mp4 --keep 13:81 --keep 100:227 --ps-mode stsd
./build/Release/smart_render.exe -i input.mp4 -o lossless.mp4 --keep 13:227 --lossless
./build/Release/smart_render.exe -i input.mp4 --keep 13:227 --analyze
```

帧编号从 0 开始，`A:B` 表示包含 A、不包含 B。区间必须有序且不重叠，相邻区间合并。不会覆盖已有输出；JSON 计划打印到 stdout，可用 `--report plan.json` 保存。

| 选项 | 含义 |
|---|---|
| `--ps-mode hev1` | 默认；VPS/SPS/PPS 带内传递，在切换前恢复对应参数集 |
| `--ps-mode stsd` | 使用 `hvc1`，由 FFmpeg 通过 `NEW_EXTRADATA` 创建/切换 sample description |
| `--crf 18` | 边界编码质量；0 仍不是 x265 无损模式 |
| `--lossless` | x265 `lossless=1`，只对需要编码的边界使用无损模式 |
| `--access idr` | 用于比较的保守 IDR 方案；open GOP 文件可能近乎全量编码 |

## 支持范围

当前输入必须是本地、可寻址、未加密的 MP4，且只有一条视频流；HEVC Main/Main10、逐行、4:2:0、8/10 bit、固定分辨率及一组固定 VPS/SPS/PPS，首个 AU 为真实 IDR。支持已测试的 B 帧、CRA/RASL、IDR/RADL、多 slice、VFR、分片 MP4、非零起始时间。

可包含音频等其他轨道，但当前 CLI **只输出视频**。音频后续按仓库既定方案处理。不支持多层 HEVC、动态参数集输入、隔行、4:2:2/4:4:4、加密视频；不保证所有 SEI/HDR 元数据在重编码边界保留。混合输出包含动态参数集，因此目前不能再次作为本 CLI 的输入。无杜比相关实现。

`hev1` 和多 description `hvc1` 已通过 FFmpeg 软件解码与本机 NVIDIA 硬解实验；尚未验证 Apple/QuickTime、Media Foundation、浏览器、电视的兼容性。

## 复现测试

```powershell
python -m pip install -r requirements-test.txt
$env:FFMPEG_ROOT = "C:/SDK/ffmpeg"
python tests/integration.py --reuse-fixtures test-data/fixtures
python tests/integration.py --fixtures open,main10,vfr --cuts partial,multi,one --reuse-fixtures test-data/fixtures --lossless
python tests/integration.py --fixtures open,main10,vfr --cuts partial,multi --reuse-fixtures test-data/fixtures --access idr
python tests/experiments.py
```

`experiments.py` 的硬件检查需要可用的 NVIDIA HEVC 解码器；常规矩阵使用已有 NVENC 生成样本时无需 NVIDIA 编码器。重新生成全部样本则需要 NVENC。脚本每次创建新目录，不覆盖上次结果。

上游对比的固定源码版本、安装命令、命令日志及失败解释详见报告。`--experimental-no-bla` 只用于构造消融反例，禁止用于正常导出。
