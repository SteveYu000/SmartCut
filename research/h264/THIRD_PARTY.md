# 第三方组件

本项目调用 FFmpeg 的公开 C API，使用其 libavformat、libavcodec、libavutil 以及 libx264 编码器封装。没有将所比较项目的源代码复制到 CLI。

实际构建使用 [BtbN FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds) 的 Windows x64 GPL shared 构建，FFmpeg 版本为 `n8.1.3-24-g92611900bb-20261025`。固定下载地址与 SHA-256 记录于 `scripts/ffmpeg-sdk.json`；随 SDK 提供的许可证原文保留于 `licenses/FFmpeg-GPL-3.0.txt`。

源代码与构建资料：

- [FFmpeg 源码](https://github.com/FFmpeg/FFmpeg)，该构建标记的 revision 为 `92611900bb`。
- [BtbN 构建脚本](https://github.com/BtbN/FFmpeg-Builds)。
- [x264 源码](https://code.videolan.org/videolan/x264)。
- [固定 SDK 发布记录](https://github.com/BtbN/FFmpeg-Builds/releases/tag/autobuild-2026-10-25-13-00)。

交付目录内的 DLL 来自该 GPL 构建，原有许可证不因本项目封装而改变。对外发布衍生产品时，需要依据最终选用的 FFmpeg/x264 构建安排对应源码和许可证交付。

测试代码使用 NumPy 2.3.5；运行 CLI 不依赖 Python 或 NumPy。MSVC 运行库由 Microsoft Visual C++ Redistributable 提供，不包含在本次运行包内。
