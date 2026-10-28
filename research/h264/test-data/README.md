# 最终测试素材与输出

这里保存本次最终验收的全部 MP4 测试文件和逐次运行记录，共 304 个 MP4 文件，视频文件合计 270,887,035 字节（约 258.3 MiB）。测试视频未修改；JSON 和 concat 列表中的本机绝对路径已改为相对路径。无编译后的程序、DLL 或 SDK。

| 目录 | MP4 数量 | 内容 |
| --- | ---: | --- |
| [run-20261027-125923](run-20261027-125923) | 283 | 13 类基础源、264 个导出输出、不支持输入/文件保护负例、参数集恢复消融 |
| [experiments-20261027-130026](experiments-20261027-130026) | 20 | 中文路径与音频轨在前、简化 concat 对照、损坏输入、1080p 性能源与输出 |
| [package-check-20261027-130429](package-check-20261027-130429) | 1 | 本地运行包实际剪切的输出 |

主矩阵的 [results.json](run-20261027-125923/results.json) 记录 264 个导出、11 个拒绝用例和 1 个消融实验的结果。补充实验的 [results.json](experiments-20261027-130026/results.json) 记录 5 个补充用例、简化拼接对照与三次性能计时。完整解释见 [实现与测试报告](../docs/IMPLEMENTATION_REPORT.md)。

建议先查看以下一组：

- [输入：240 帧、闭合 GOP、B=3、源 SPS ID 0](run-20261027-125923/source-closed_b3_id0.mp4)
- [输出：保留 13:227，avc3](run-20261027-125923/closed_b3_id0-both_edges-avc3-crf18.mp4)
- [对应导出计划](run-20261027-125923/closed_b3_id0-both_edges-avc3-crf18.json)
- [相同剪切的 stsd 输出](run-20261027-125923/closed_b3_id0-both_edges-stsd-crf18.mp4)

这组输出各 214 帧，其中 192 帧复制、22 帧重编码。正确性由帧数、每帧时间戳、画面帧号、复制帧像素一致性及 VCL 哈希共同验证。

## 文件性质

`source-*.mp4` 通常为生成的源素材；`*-crf18.mp4` 为普通质量导出；`*-lossless-*-crf0.mp4` 为经过全选区 QP=0 回退的无损输出。每个主矩阵导出旁边有同名 JSON 计划。

以下文件故意不能作为正常输出示范：

- `ablation-no-parameter-restore.mp4`：人为移除恢复的源 SPS/PPS，用来证明配置恢复的作用。
- `bad-length.mp4`：NAL 长度被故意破坏。
- `existing.mp4`：用于验证不覆盖既有文件的文本哨兵，并非可播放的视频。
- `baseline-*-output.mp4`：简化 FFmpeg concat 对照的实际结果，包含报告记录的多帧/缺帧/解码问题；不是原版 LosslessCut 的直接运行结果。
- `two-video-streams.mp4`、`interlaced.mp4`、`mpeg4-part2.mp4`、`source-lossless-profile.mp4`：有效的输入约束测试素材，本 CLI 会按当前支持范围拒绝它们。

`benchmark-smart-0/1/2.mp4` 与 `benchmark-full-0/1/2.mp4` 保留三次计时对应的实际输出，内容相同也保留文件名以便对照记录。

## 复现

在 `h264` 目录完成构建后运行：

```powershell
.\build\Release\smart_render.exe -i test-data/run-20261027-125923/source-closed_b3_id0.mp4 -o demo.mp4 --keep 13:227 --report demo.json
python tests/integration.py --with-nvenc
python tests/experiments.py --suite-dir test-data/run-20261027-125923
```

没有 NVIDIA NVENC 硬件时省略 `--with-nvenc`，完整导出矩阵为 244 个。脚本生成的新结果写入已被 Git 忽略的 `test-output`，不会覆盖这里的存档。
