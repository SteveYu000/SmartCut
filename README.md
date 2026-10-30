# SmartCut
视频智能局部重编码的剪辑程序。
## 前言
根据对现有的开源项目的搜索和调查，我发现这些项目各有不足，且对视频格式的支持有限。因此，我提出本项目，一方面对现有项目的实现方法进行测试，另一方面根据测试结果判断本项目应该采用什么办法比较好，最终做出一个支持格式全面，鲁棒性良好的程序。

## 调查结果
[Smart_Rendering源码研究报告](research/source-review/REPORT.md)

## 寻找共同开发者
我深知，本项目会是一个很大的工程，需要经过繁复的测试和验证，因此，我期待各位有意愿的开发者的参与。

**贡献要求**：
1. 项目采用GPL-3.0许可证，您的提交应该符合许可证要求。
2. 项目包含 C++ 与 CMake 实现的 `research/h264/`、`research/h265/`，直接链接 FFmpeg 库；`research/pipeline/` 用 C# 与 dotnet CLI，调用官方 ffmpeg/ffprobe 进程。
3. 您应该首先对现已有项目的实现方法进行对比测试之后决定采用哪一种方法作为本项目的实现，对于自己原创的方案，应该与现有方案进行对比，说明原创方案的必要性。总的来说，必须以测试结果作为依据，不可盲目采用某一种方案。
4. 本项目遵循“不重复造轮子”的原则，尽量复用ffmpeg已有的组件，自己只写最核心的部分。

## 项目阶段
- [x] 项目已支持mp4 with avc/hevc/av1。
- [x] ~~mp4/avc 的第二套实现：C# + 官方 FFmpeg 进程管线，含音频轨，165 个端到端与逻辑用例通过[实现说明](research/pipeline/README.md)。（方案已抛弃）~~
- [ ] 正在研究mkv容器方案。

## 构建

安装 Visual Studio 2026 的“使用 C++ 的桌面开发”和 CMake 4.2 或更高版本；预设使用 Windows x64、C++20。自行下载包含 libx264/libx265 编码器的 FFmpeg 共享开发包，SDK 根目录应包含 `include/` 头文件、`lib/` 下 MSVC 可链接的导入库，以及 `bin/` 下的 DLL 和 `ffmpeg.exe`。视频测试另需 `ffprobe.exe`，已验证的 SDK 为 FFmpeg 8.1 系列。

在仓库根目录执行，将示例路径替换为实际解压出的 SDK 根目录：

```powershell
cmake --preset msvc -DFFMPEG_ROOT="C:/SDK/ffmpeg"
cmake --build --preset release
ctest --preset release
```

也可在首次配置时设置环境变量：

```powershell
$env:FFMPEG_ROOT = "C:/SDK/ffmpeg"
cmake --preset msvc
```

命令行或 CMake 缓存中的非空 `FFMPEG_ROOT` 优先于环境变量。更换 SDK 时重新执行带 `-DFFMPEG_ROOT` 的配置命令并构建，头文件、导入库和运行库应来自同一套 SDK。

构建输出为 `build/Release/smartcut.exe`，所选 SDK 的 DLL 和原版 `ffmpeg.exe` 会复制到同一目录。详细参数和测试方法见 [CLI 使用与构建](docs/PRODUCT.md)；历史原型的构建步骤见 [H.264 README](research/h264/README.md) 和 [H.265 README](research/h265/README.md)。
