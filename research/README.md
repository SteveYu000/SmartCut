# 研究资料索引

每个目录是一项研究：报告与对应源码、脚本、结果和本地媒体放在一起。实验日期和原始基线版本保留在各报告中。

| 目录 | 内容与入口 |
| --- | --- |
| [source-review](source-review/REPORT.md) | 开源项目源码与 issue 调查；Markdown、PDF、Word 报告，报告生成器及 `evidence/` 采集记录 |
| [h264](h264/README.md) | H.264 C++ 实现、CMake、独立验证器；`test-data/` 为本地验收及探索媒体 |
| [h265](h265/README.md) | HEVC C++ 实现、CMake、上游对比与容器/解码验证；`test-data/` 为本地媒体与证据 |
| [pipeline](pipeline/README.md) | 原始 C# / FFmpeg 进程实现、逻辑与集成测试 |
| [h264-pipeline-comparison](h264-pipeline-comparison/REPORT.md) | 效率对比、原管线完整复测、VFR 和音频诊断、测试记录 |
| [losslesscut-comparison](losslesscut-comparison/REPORT.md) | 官方 LosslessCut 同素材测试；运行脚本、独立检查器、逐例结果和本地导出 |
| [pipeline-vfr-repair](pipeline-vfr-repair/REPORT.md) | VFR 修复设计与 20 项独立检查、完整回归、单独的 `prototype/` 源码和补丁 |

下载的上游项目原样保留在根目录 `sources/`，见 [上游资料目录说明](source-review/LOCAL_SOURCES.md)；FFmpeg SDK 留在本地 `h264/deps/`，Python 比较依赖留在本地 `h265/deps/`。这些下载文件均不纳入 Git 跟踪。构建及报告渲染缓存已清理，需要使用时重新生成。

所有测试视频和音频由根 `.gitignore` 忽略，已有媒体保留在本地，新克隆须运行各项目的生成脚本。结果 JSON、日志、条码/PTS 检查代码继续跟踪。相同媒体使用本地硬链接共享数据，文件名仍可直接用于历史报告与脚本。

归档媒体作为固定证据使用；修改素材时请先复制到新的运行目录，避免改变硬链接共享的历史样本。

目录整理只更新资料位置和复测路径，不重新定义历史测试结论。旧报告中的“未提交”“未合入”描述对应当轮研究结束时；VFR 原型随本次整理归档，基础 `pipeline` 的算法仍保持独立。

目录迁移、缓存清理与验证结论见 [工作区整理记录](WORKSPACE_LAYOUT.md)，逐项操作见 [workspace-organization.json](workspace-organization.json)。
