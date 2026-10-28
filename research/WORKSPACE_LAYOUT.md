# 工作区整理记录

2026-10-29。Git 仓库由 `publish-SmartCut/` 移到当前根目录，继续使用原 `main` 分支和 `origin`，保留提交历史。

根目录 `README.md` 与已有研究文档按用户要求保留原文，仅更新搬迁后的路径和链接。原报告直接归档在 `research/`，不以占位入口替换报告内容。

## 当前布局

```text
SmartCut/
  README.md / LICENSE / global.json / .gitignore
  research/
    source-review/              源码调查报告、Word、PDF、生成器和 issue 证据
    h264/                       原生 H.264 实现、报告、测试代码和本地媒体
    h265/                       原生 HEVC 实现、报告、测试代码和本地媒体
    pipeline/                   原始 C# 管线
    h264-pipeline-comparison/    效率和覆盖对照
    losslesscut-comparison/      官方产品测试、脚本、结果和本地导出
    pipeline-vfr-repair/         独立修复原型、报告、补丁和本地媒体
  sources/                      上游仓库、官方程序、参考源码，仅本地保留
  tools/                        工作区验证脚本
  local/                        与研究无关的本地资料，不提交
```

研究入口见 [索引](README.md)。VFR 原型与基础管线分开归档，未将原型算法合入基础实现。

## 保留与清理

- 测试视频、音频及下载的上游项目、官方程序、SDK 均保留本地，由 `.gitignore` 排除。原来跟踪的测试视频从当前版本删除跟踪，不改写历史提交。
- 7 个原有上游仓库及原下载文件、LosslessCut 3.70.2 源码和官方程序、FFmpeg 参考源码、FFmpeg SDK 共 5 组文件树校验全部一致，见 [preserved-downloads.json](preserved-downloads.json)。
- 已清理约 1146.6 MiB 的重复工程副本、交付压缩包、解压后的重复运行包、报告预览 PNG、旧 PDF 渲染版本、编译与初始化缓存；最终报告 PDF、Word 和 Markdown 均保留。
- 4225 个媒体路径对应 1340 份不同内容，2885 个重复路径改为 NTFS 硬链接，约减少 1869.8 MiB 重复数据。路径不变，见 [media-deduplication.json](media-deduplication.json)。历史媒体按固定证据使用，修改时先复制到新运行目录。
- 与视频研究无关的个人资料不纳入版本跟踪。
- 报告与脚本中的迁移路径已更新。HEVC 原始清单保持原文，受路径改写影响的 JSON 新旧校验值另存至 [layout-checksums.json](layout-checksums.json)；测试结果和视频字节未改变。

操作记录见 [workspace-organization.json](workspace-organization.json)，路径更新列表见 [layout-path-updates.json](layout-path-updates.json)。

## 整理后的验证

- H.264、HEVC 两个项目均从新路径用 CMake / MSVC 重新构建成功。
- 原始管线、VFR 原型、LosslessCut 对照检查程序均用固定 .NET SDK 10.0.403 构建成功，0 警告、0 错误。
- 原生 H.264、原生 HEVC、原始管线、VFR 原型各进行一次实际剪切，均通过原有独立检查器的严格解码、帧数、PTS、画面和复制量检查。
- 31 个研究 Python 脚本通过语法检查，2600 份 JSON/LLC 通过解析检查，Markdown 内部文件链接无失效项。
- VFR 归档输出的 MP4 时长表检查仍为 20/20；历史完整回归计数仍为 157/165，无新增失败。本次未重跑整套 165 项，未将路径整理描述为新的算法验证。

验证结果见 [workspace-validation.json](workspace-validation.json)。验证时临时生成的程序和构建目录已再次清理，不提交二进制。
