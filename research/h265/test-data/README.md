# 测试媒体与证据

这些视频由本项目测试脚本生成，带有可机器读取的逐帧条码。与合成图像一起随项目发布，用于复现、定位和后续回归；不是生产素材质量或播放器兼容性认证。

| 目录 | 内容 |
|---|---|
| `fixtures` | 11 种基础源素材，每份 240 帧，192×112；文件名标识编码/封装变化 |
| `matrix` | 最终 220 个正确导出，附每个导出的 JSON 计划、stderr 日志与结果集 |
| `lossless` | 18 个无损边界输出；整段像素等于输入解码像素 |
| `idr-baseline` | 12 个保守方案输出；正确但本组 open-GOP 部分剪切为全量重编码 |
| `experiments` | 800 帧输入、320×192 Main10 硬解输入、正确输出、消融和负向样本 |
| `upstream-skeskinen` | 16 个原版 skeskinen 输出与日志 |
| `upstream-deepregular-python` | 16 个原版 DeepRegular Python 输出与日志；验收均存在 decoded PTS 问题 |
| `upstream-copy-gap` | 两个原版实现对 800 帧素材进行跳接的结果 |

每个目录的 `results.json` 是原始验收记录。原始绝对路径中 `test-output/<运行名>` 对应这里的目录，映射及所有媒体/JSON/log 的 SHA-256 见 [manifest.json](manifest.json)。JSON 不是在新目录重新执行得到的，路径保留用于追溯。

本次清单含 879 个媒体/JSON/log 文件，其中 314 个使用 `.mp4` 扩展名（含下述负向样本）；媒体总计 101,529,670 字节。除清单外另附本说明。

## 预期失败文件

以下文件故意保留，不能把它们当成可正常播放的交付视频：

- `experiments/copy-gap-*-bla0.mp4`：省去 CRA→BLA；图像数量正确但有 3 帧次序和 PTS 错配。
- `experiments/ablation-missing-parameter-sets.mp4`：删除接入处参数集，验证 decoder 错误、丢帧与错图。
- `experiments/negative-invalid-nal-length.mp4`、`negative-invalid-hvcc-array.mp4`：损坏的结构性输入。
- `experiments/negative-existing.mp4`：用于验证禁止覆盖的哨兵文本，扩展名虽为 MP4，内容不是视频。
- `upstream-deepregular-python` 中的输出：保留其时间轴/帧选择问题，便于与原日志及测试报告交叉验证。

`wrap-*-bla0.mp4` 是另一组关闭 BLA 的对照，在这组混合编码场景中通过；它与上面的失败案例同时保留，避免选择性报告。

正确视频可优先查看 `matrix/open-partial-hev1.mp4`、`matrix/open-partial-stsd.mp4`，以及 `experiments/copy-gap-*-bla1.mp4`。播放画面本身不能发现所有时间戳问题，应使用测试脚本验收。

只收录研究源码、日志、计划和测试媒体；没有 SDK、EXE、DLL、LIB 或构建中间文件。复现测试命令见 [上级 README](../README.md)。
