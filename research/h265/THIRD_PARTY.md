# 许可证与来源

本目录新增的 C++ 实现和 Python 测试采用 **GPL-3.0-or-later**，许可证全文见 [LICENSE](LICENSE)。测试生成的图案、帧号条码及视频可随本目录用于复现和再分发。

实现复用了本仓库 H.264 实验的资源管理、MP4 输出、时间轴与 CLI 框架。未复制上游 smartcut、DeepRegular 或 MediaMolder 的实现源码；参考算法的出处及固定 revision 见研究报告。没有把测试用的上游源码或二进制依赖加入仓库。

构建调用 FFmpeg 的公开 `libavformat`、`libavcodec`、`libavutil` API 和其 `libx265` 封装。使用的 SDK 是 BtbN 的 GPL shared 构建，固定版本 `n8.1.3-24-g92611900bb-20261025`，下载地址和 SHA-256 位于 [scripts/ffmpeg-sdk.json](scripts/ffmpeg-sdk.json)。本次提交仅含自身源码、文档、测试脚本和测试媒体，不分发 SDK、DLL、EXE、LIB。

- [FFmpeg 源码](https://github.com/FFmpeg/FFmpeg/tree/92611900bb)：GPL/LGPL 条件取决于所选构建组件；本次使用 GPL 构建。
- [BtbN 构建脚本与固定发布](https://github.com/BtbN/FFmpeg-Builds/releases/tag/autobuild-2026-10-25-13-00)。
- [x265 源码与许可证](https://github.com/videolan/x265)：GPL-2.0-or-later，可与本目录采用 GPLv3 条件组合。
- NumPy 2.3.5 仅用于测试；PyAV 16.0.1、tqdm 4.67.1 仅用于运行上游 Python 对比。

若另外发布含 FFmpeg/x265 的二进制包，应同时处理所用构建的对应源码与许可证交付；本目录的源代码许可不替代第三方组件许可。
