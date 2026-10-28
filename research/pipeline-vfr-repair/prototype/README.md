# SmartCut Pipeline

C#/.NET 10 的 FFmpeg 进程管线剪辑实现。索引、切分决策、时间轴换算与验收判据在本目录的 C# 代码里；解码、编码、拼接、封装由官方 ffmpeg/ffprobe 子进程完成，不链接 FFmpeg 库。

## 目录

| 路径 | 内容 |
| --- | --- |
| [src/SmartCut.Core/Probe.cs](src/SmartCut.Core/Probe.cs) | ffprobe JSON 读取 |
| [src/SmartCut.Core/VideoIndex.cs](src/SmartCut.Core/VideoIndex.cs) | 显示帧索引、真 IDR 判定、IDR 区间切分、输入合同检查 |
| [src/SmartCut.Core/Planner.cs](src/SmartCut.Core/Planner.cs) | keep 区间归一化、复制/编码单元决策、输出时间轴、音频窗口换算 |
| [src/SmartCut.Core/PipelineRunner.cs](src/SmartCut.Core/PipelineRunner.cs) | 段进程调用、concat 拼接、音频 filter_graph、临时目录与改名 |
| [src/SmartCut.Core/Verifier.cs](src/SmartCut.Core/Verifier.cs) | 视频与音频验收判据 |
| [src/SmartCut.Cli/Program.cs](src/SmartCut.Cli/Program.cs) | 命令行入口，stdout 输出 JSON |
| [tests/SmartCut.Tests/](tests/SmartCut.Tests/) | xUnit 用例：纯逻辑 + 端到端 |
| [tests/perf_1080p.py](tests/perf_1080p.py) | 两臂三次计时，落盘 JSON |

## 处理流程

```text
ffprobe 全样本 → 读样本字节判 VCL NAL type 5 → 按 PTS 建显示序 → 按真 IDR 切区间
  → keep 区间与 IDR 区间求交
      完整覆盖的区间：copy（整段包搬运）
      不完整的区间：encode（select 显示帧 + libx264 closed GOP、B=0）
  → 相邻 copy 区间合并成一个段
  → 每段一个 ffmpeg 进程 → concat demuxer 拼接 → -tag:v avc3
  → 音频：与视频同一批窗口，可整除时用 atrim=start_sample/end_sample，否则按秒
  → 全部成功后临时目录改名输出
```

copy 段一律读一个先行的视频-only 解包文件（`source-video.mp4`），不直接对原输入用 `-ss`。

## 命令行

```powershell
dotnet run --project src/SmartCut.Cli -- -i input.mp4 -o output.mp4 --keep 13:227 --verify
```

| 参数 | 作用 |
| --- | --- |
| `-i`, `-o` | 输入、输出 |
| `--keep A:B` | 零基显示帧，`A` 含、`B` 不含；可重复，须有序不重叠 |
| `--crf N` | 边界质量，0–51，默认 18 |
| `--preset NAME` | 边界 libx264 preset，默认 medium |
| `--no-audio` | 只输出视频 |
| `--audio-codec NAME` | 音频重编码编码器，默认 aac |
| `--audio-bitrate N` | 音频码率，如 192k |
| `--ffmpeg-dir DIR` | ffmpeg.exe 与 ffprobe.exe 所在目录 |
| `--work-dir DIR` | 段文件根目录，默认系统临时目录 |
| `--keep-work` | 保留段文件 |
| `--verify` | 跑验收判据 |
| `--analyze` | 只出计划，不写输出 |
| `--report FILE` | 另存 JSON |

stdout 是 JSON（状态、输入帧数、IDR 区间数、key-flag 非 IDR 包数、time_base、每单元决策、复制/编码帧数、总时长 tick、音频窗口与对齐读数、耗时、警告）；stderr 是诊断与错误。退出码 0 成功，1 为错误或验证不通过。既有输出与报告文件不覆盖。

## 验收判据（`--verify`）

| 判据 | 判据来源 |
| --- | --- |
| 帧数、逐帧 PTS 与计划精确相等 | ffprobe 样本表 |
| 无重复 PTS | ffprobe 样本表 |
| 复制帧与源同位置 framemd5 相同 | ffmpeg 解码 |
| 编码区间 PSNR 记录并设 30 dB 下限 | ffmpeg `psnr` 滤镜 |
| 严格解码零告警 | ffmpeg `-xerror -err_detect explode` |
| color_range / color_space / color_primaries / color_transfer / SAR 与输入一致 | ffprobe 流元数据 |
| 音频样本数对计划窗口、冲击位置逐段映射比对 | ffmpeg 解码 PCM |

音频判据用源文件自身的冲击序列作参照：先从源音频检出冲击时刻，按同一批 keep 窗口映射到输出时间轴，再与输出解码结果比对；阈值取源侧峰值的一半，两侧共用。

## 构建与测试

```powershell
dotnet build pipeline/SmartCut.Pipeline.slnx -c Release
dotnet test pipeline/tests/SmartCut.Tests                       # 全部，含端到端
dotnet test pipeline/tests/SmartCut.Tests --filter "Category!=Integration"   # 只跑纯逻辑
```

端到端用例需要两个环境变量：`SMARTCUT_FFMPEG_DIR`（ffmpeg 目录）与 `SMARTCUT_FIXTURE_DIR`（夹具目录，默认在仓库内向上查找 `h264/test-data/run-*`）。缺依赖时用例失败，不静默跳过。

## 实测

本机 Windows x64，FFmpeg `git-2026-10-24-d8990137ee`（`C:\PLAN\FFmpeg`），.NET 10。

| 项目 | 结果 |
| --- | --- |
| 端到端套件 | 165/165 通过 |
| 13 份素材 × 10 种剪辑 | 130/130 通过 |
| 其中复制帧逐帧对位 | 每例 `copy_frames_matching == copy_frames_checked` |
| 音频用例（4 变体 × 6 剪辑） | 24/24 通过，样本偏差 0，冲击位移中位偏差 0 ms，最大残差 0 ms |
| 拒绝用例（非 H.264、双视频轨、既有输出保护） | 3/3 |
| 纯逻辑用例 | 8/8 |

计划计数与 `h264/` C++ 引擎落盘的同一夹具报告逐例比对：`copied_frames`、`encoded_frames`、`duration_ticks`、`idr_epochs` 相同。例：`closed_b3_id0` 保留 `13:227` → 214 帧、192 复制、22 重编、109568 tick。

1080p/30 fps/600 帧源保留 `13:587` → 574 帧，480 复制、94 重编、3 段；管线三次 `2.754 / 2.602 / 2.710` 秒（中位 2.710），同选区全选区重编对照 `4.603 / 4.830 / 4.545` 秒（中位 4.603），比值 1.70。原始数据见 [docs/evidence/perf-1080p.json](docs/evidence/perf-1080p.json)。

## 输入合同

单视频轨 H.264（`avc1`/`avc3`）、逐行、首帧为真实 IDR、DTS 严格递增、PTS 不重复、末帧时长为正。拒绝：多视频轨、非 H.264、隔行。容器的 key flag 不作为随机访问点依据。

## 限制

1. 复制边界只到真实 IDR，长 Open GOP 素材整段重编。
2. 复制区间不做全量解码校验，不是坏码流验证器，也不分析 HRD/CPB 合规。
3. 已验证 ffmpeg 软件解码与 seek，未验证 Windows Media Foundation、浏览器、VLC 与硬件解码器。
4. 单进程顺序执行，先全片扫描，无索引缓存、无并行、无 GUI。
5. 边界编码固定 B=0 closed GOP。
6. 音频重编码为 AAC（可换 `--audio-codec`），整段复制音频未实现。
7. 素材库为可复现合成夹具加一种硬件编码来源，不含相机、广播与商用编码器的真实文件。
