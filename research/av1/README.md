# MP4 / AV1 Smart Rendering 研究

结论、项目源码对比、反例和局限见 [研究报告](REPORT.md)。本目录包含一个 C++20 / FFmpeg 研究 CLI，以及素材生成器和独立验证器；正式产品根目录的 `src/` 尚未加入 AV1。

已验证：原型主矩阵 **88/88**，另用 SVT-AV1 编码边界 **32/32**；同一组输入上，未修改的 DeepRegular SmartCut v0.8.37 官方 Rust CLI 为 **60/88**，skeskinen/smartcut 为 **35/88**。这是本报告规定的全部检查通过数，不是“可以播放”的数量。完整证据在 [evidence/](evidence/)。

## 构建

安装 Visual Studio 2026 的“使用 C++ 的桌面开发”和 CMake 4.2 或更高版本。自行下载 FFmpeg **共享开发包**，指定包含 `include/`、`lib/`、`bin/` 的 SDK 根目录。`lib/` 必须提供 MSVC 能链接的导入库；运行库、头文件和导入库来自同一套 SDK。

本实验实际使用 FFmpeg `n8.1.3-24-g92611900bb-20261025`，直接链接 `avformat`、`avcodec`、`avutil`。需要 `libdav1d` 解码器、`libaom-av1` 编码器；第二组测试另需 `libsvtav1`。完整素材集还使用 `librav1e`、`av1_nvenc`，验证器需要 `ffmpeg.exe` 和 `ffprobe.exe`。不保证旧版 MOV muxer具有相同的动态 extradata / sample description 行为。

在本目录执行，替换示例路径：

```powershell
$env:FFMPEG_ROOT = "C:/SDK/ffmpeg"
cmake --preset msvc -DFFMPEG_ROOT="$env:FFMPEG_ROOT"
cmake --build --preset release --parallel 1 -- /nodeReuse:false
ctest --preset release
```

也可只通过 `-DFFMPEG_ROOT=...` 配置；非空 CMake 缓存优先于环境变量。测试脚本使用环境变量选择 SDK。构建输出为 `build/Release/av1_smart_render.exe`，所选 SDK 的 DLL 会复制到同目录。使用其他生成器时可自行 `cmake -S . -B ... -G ... -DFFMPEG_ROOT=...`，本轮只验证了上述 MSVC 配置。

## 使用和边界

```powershell
./build/Release/av1_smart_render.exe -i input.mp4 -o output.mp4 --keep 13:227 --report plan.json
./build/Release/av1_smart_render.exe -i input.mp4 -o joined.mp4 --keep 13:81 --keep 100:227 --encoder libsvtav1 --crf 18
./build/Release/av1_smart_render.exe -i input.mp4 --keep 13:227 --analyze
```

帧号从 0 开始，`A:B` 包含 A、不包含 B。区间必须有序且不重叠，相邻区间会合并。CLI 拒绝覆盖已有输出、临时输出和报告。`--crf 0` 未实现专门的无损模式，不应视为无损承诺。

支持范围是本地可寻址、未加密 MP4 中的一条 AV1 Main 4:2:0 8/10 bit 视频，固定尺寸、固定输入 Sequence Header、单层、每个 sample 显示一帧、连续的 PTS/duration，文件以验证过的随机访问点开始。已覆盖隐藏参考帧、show_existing_frame、VFR、非零起点、分片 MP4 输入、多 tile 和 SAR / SDR 色彩信息。输出为普通 MP4，只输出视频；音频和字幕不在本轮测试中。

当前原型保守地重编码两个不完整边界区间。另有 [尾部直接复制实验](evidence/tail-copy.json)，证明在限定条件下可以进一步去掉尾部重编码；该优化尚未接入此 CLI。多层、动态分辨率、动态输入配置、film grain、HDR、加密、4:2:2/4:4:4、实际长录像及跨播放器兼容性未完成验证。原型生成的混合 Sequence Header 输出，目前也会被其固定输入配置检查拒绝再次剪切。

`--experimental-single-config` 和 `--experimental-drop-copy-sequence` 是故意构造错误输出的消融开关，只供 `tests/experiments.py` 使用。

## 素材身份和复现

所有视频、SDK、构建产物和下载的上游项目只保留本地。远程文件仍能确认当时用了什么：

- [fixtures.json](evidence/fixtures.json)：11 个原始输入的 SHA-256、大小、完整生成命令、原始 RGB 哈希、真实随机访问点、隐藏帧数量。
- [audit.json](evidence/audit.json)：源码提交、官方发行包及 EXE/DLL 哈希、运行环境、检查器源码哈希、几个反例的实际值。
- 每组结果 JSON：每次剪切的输入哈希、输出哈希、区间、命令和检查结果。`${REPO}` 表示仓库根目录。
- [experiments.json](evidence/experiments.json)：额外 192×128 硬解输入的生成命令、原始 RGB 哈希及输入哈希。

同一生成命令在不同编码器、驱动和版本下不保证得到同一二进制视频。必须比较哈希，不能仅凭文件名宣称“与本报告同一测试集”。`fixtures.py` 会更新素材清单；在新环境复现时先保存原始清单，再比较新清单。已存在的视频不会被生成器覆盖。

```powershell
python -m pip install -r requirements-test.txt
$env:FFMPEG_ROOT = "C:/SDK/ffmpeg"
New-Item -ItemType Directory -Force local | Out-Null
Copy-Item evidence/fixtures.json local/reference-fixtures.json
python tests/fixtures.py
python tests/integration.py
python tests/integration.py --names svt,main10,vfr,nvenc --encoder libsvtav1
```

生成全部 11 类输入需要支持 AV1 编码的 NVIDIA GPU。没有该 GPU 时，给 `fixtures.py` 和 `integration.py` 都加 `--names aom,svt,rav1e,main10,vfr,ntsc,offset,fragmented,color_sar,tiles`；这只能得到 80 项子集，不能声称复现完整 88 项。

脚本打印新建的 `test-data/matrix-时间/` 路径。将主矩阵路径传给附加实验；这组实验还引用历史 H.264 素材 `../h264/test-data/run-20261027-125923/source-closed_b3_id0.mp4` 作为非 AV1 拒绝测试，需先按 H.264 研究说明恢复该素材。硬解部分需要本机可用的 `av1_cuvid`。

```powershell
python tests/experiments.py --matrix test-data/matrix-实际时间
python tests/tail_copy.py
```

`tail_copy.py` 是从随机访问点开始的 FFmpeg stream-copy 组件实验，不是任意起点的完整 Smart Rendering 工具。生成、导出结果均使用新目录。`seconds` 字段包含独立验证的耗时，不能作为各项目导出性能排名。

## 上游对比

本轮运行的是 DeepRegular **官方 Windows Rust CLI**，不是仓库的旧 Python 参考实现。新克隆环境需准备以下本地目录；已有目录应核对版本后复用，不要覆盖原资料：

1. [DeepRegular v0.8.37 源码](https://github.com/DeepRegular/SmartCut/tree/v0.8.37) 放在 `../../sources/DeepRegular-SmartCut-v0.8.37/`。提交为 `d670099e9df622b9b9120fc46411e6154985b269`。
2. 下载 [官方 portable x64 发行包](https://github.com/DeepRegular/SmartCut/releases/download/v0.8.37/smartcut-portable-x64-0.8.37.zip)，SHA-256 为 `6e0e9a787149ed6b0391c524c7bdd23582697e1d94893b14f17af211af423d2f`，大小 70,179,456 字节。解压到 `../../sources/DeepRegular-SmartCut-0.8.37-win64/`，最终应有 `smartcut/smartcut-cli.exe` 及其原配 DLL。
3. [skeskinen/smartcut](https://github.com/skeskinen/smartcut/tree/73821ea01c29a4dca81a8865ef7d348d04bb0810) 放在 `../../sources/skeskinen-smartcut/`，检出提交 `73821ea01c29a4dca81a8865ef7d348d04bb0810`。

```powershell
python -m pip install --target ../h265/deps/python av==16.0.1 tqdm==4.67.1
python tests/upstream.py --names aom,svt,rav1e,main10,vfr,ntsc,offset,fragmented,color_sar,tiles,nvenc
python tests/upstream.py --only deepregular --names vfr --index scan
```

两者均在原源码 / 官方二进制上运行，包装脚本只转换区间表示并独立检查输出。DeepRegular 通过秒区间指定切点，skeskinen 通过包含末帧的帧区间指定，转换记录在逐例命令中。PyAV 和 DeepRegular 自带的 FFmpeg 版本不同，不能把正确性对比解释为受控编码速度或画质排名。

最终逐例结果由以下脚本重新检查媒体并复制非空日志；`--output` 指定要更新的证据文件，请勿把开发试验混入正式矩阵：

```powershell
python tests/archive.py --runs test-data/matrix-实际时间 --output matrix.json
python tests/archive.py --runs test-data/第二编码器矩阵时间 --output svt-boundaries.json
python tests/archive.py --runs test-data/upstream-实际时间 --output upstream.json
python tests/archive.py --runs test-data/VFR扫描实验时间 --output deepregular-vfr-scan.json
```

新增源码按本仓库 GPL-3.0-or-later 标记。依赖与上游项目保留各自许可证；本目录没有复制其完整源码或分发二进制。
