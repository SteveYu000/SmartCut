# SmartCut 正式 CLI 设计

## 1. 结构与数据流

实现使用公开的 libavformat、libavcodec、libavutil API，不修改 FFmpeg。C++ 直接完成视频索引、边界解码/编码、参数切换和最终 MP4 封装；原版 ffmpeg.exe 完成音频/字幕的选区处理。

```text
命令与输出流映射
  ├─ 视频：MP4 解封装 → 样本/访问点索引 → 复制/编码计划
  │         ├─ 完整独立区间：按文件偏移读取源压缩包
  │         └─ 边界：局部解码 → 用户所选编码器 → 磁盘包暂存
  └─ 音频/字幕：原版 FFmpeg → 辅助 MP4
              ↓
  参数切换 + 统一视频时间轴 + 辅助流原包重封装
              ↓
        staged MP4 → 原子发布
```

| 文件 | 职责 |
| --- | --- |
| `src/media.hpp` | FFmpeg 资源的 RAII、索引、区间、编码包及任务接口 |
| `src/cli.cpp` | FFmpeg 风格参数、输入/输出时间组、stream specifier、编码器 AVOptions、辅助命令 |
| `src/video.cpp` | avcC/hvcC 与 NAL 校验、访问点计划、边界编码、DTS/PTS 调度、压缩包读取 |
| `src/av1.cpp` | AV1 OBU/TU 校验、源配置保存、AV1 边界编码与显示序号映射 |
| `src/app.cpp` | 预检、MP4 参数配置、参数集切换、交织封装、元数据、JSON 报告与发布 |
| `src/platform.cpp` | UTF-8/Windows 路径、CreateProcessW、Job Object、取消、临时目录和原子替换 |
| `src/main.cpp` | Unicode 命令入口、帮助和错误退出 |

## 2. 访问点与复制决策

### H.264

输入包按真实 VCL NAL 分类。普通 I 帧、容器 KEY 标志不能代替 NAL type 5 的 IDR。按相邻 IDR 划分解码区间，并验证每个区间的显示 PTS 范围连续、不交错。

完整保留的区间直接复制。区间被选区边界截断时，局部解码该区间，按显示帧保留实际画面，送入一个全新编码器。新编码器必须先产生 IDR，结束时彻底 flush。中间无需强制 `-bf 0`、固定 GOP、复制源编码器的参考帧数量或反向修改 slice 的 PPS id。

这比参考依赖图的最小重编码更保守。对只有一个 IDR 的长 Open GOP 文件，切割内部片段可能重编码整个所选片段；本轮以确定性正确和沿用已验证方案为先。

### HEVC

区分 IDR、CRA、RASL 和 RADL。连续复制时保留相邻访问点的原始关系。非连续进入 CRA 区间时，重新编码仍需保留的前导 RASL，复制 CRA 以后的安全部分；无 RADL 的重入将该 CRA 的全部 slice NAL type 21 改为 BLA_N_LP type 18，阻止解码器把新接入的区间当作对已删除内容的延续。

该修改仅用于计划确认不保留 leading picture 的重入区间，不作用于任意 CRA。RADL 区间采用研究中已验证的更保守策略。对复制画面做逐帧像素比较，对 CRA→BLA 的 VCL 比较仅允许对应 NAL header 的变化，其余编码内容保持一致。

### AV1 的访问点和尾部复制

AV1 通过 `Sequence Header + shown KEY_FRAME` 确认恢复入口，不能只相信 MP4 KEY 标记。索引保留完整 TU，包括隐藏参考帧和 show_existing 指令；只检查 OBU framing 和 frame header 起始字段，不解析 tile 或重写参考槽。

起点不是恢复点时，编码到下一恢复点前；恢复后可直接复制至任意完整 TU 末端，无需对尾部再次编码。若下一恢复点已经超过选择末尾，则编码整个选择。这个优化已在正式产品的全部 AV1 矩阵中验证，具体证据见 [AV1 接入报告](../research/product-validation/AV1_REPORT.md)。

编码器可能因 GLOBAL_HEADER 将 Sequence Header 放入 extradata，产品在同步 sample 前恢复实际头；恢复原始复制段时使用其源头。`av01` 始终使用 FFmpeg NEW_EXTRADATA 路径创建或复用 av1C / sample description。源输入仍要求固定 Sequence Header；会拒绝动态输入配置，不把一份文件级配置用于解释多种输入序列。

## 3. 编码器参数与参数集

使用 `avcodec_find_encoder_by_name` / codec descriptor 定位编码器，在扫描全部样本前检查其 codec id 与源视频一致。设置源视频的尺寸、像素格式、时间基、有效 SAR 和色彩描述，再把用户的 AVOptions 原样交给 `avcodec_open2`。选区中即使全可复制，也预检显式编码器及参数，避免悄悄接受格式不一致的请求。

不注入研究原型的 CRF 18、medium、B 帧关闭、固定 GOP 或编码线程数。实际编码包必须与送入的源帧一一对应，不能丢帧、重复或改成 CFR。AV1 用 nominal rate 和显示序号驱动编码器，再从源索引恢复输出 PTS/duration；该内部序号不作为最终时间轴。源解码器线程数只是内部解码设置，与用户编码器参数无关。

局部编码得到的 VPS/SPS/PPS 必须随局部编码数据使用，不能用旧参数集解释新 slice。参数集 id 只是引用标识，不保证配置等价；新旧参数集在明确的独立访问点切换并恢复，可以复用 id。

提供两种封装方式：

1. 默认 `avc3/hev1`：通过 FFmpeg 的 mp4toannexb BSF 转换，进入新配置以及后续真正关键访问点时补齐当前参数集。最终由 MP4 muxer 写样本。
2. `avc1/hvc1`：在配置切换处使用 `AV_PKT_DATA_NEW_EXTRADATA`，让 libavformat 建立相应 stsd/stsc 描述，而非手写 MP4 box。

AV1 的 `av01` 同样使用第 2 条公共 API 路径，但传递的是 AV1 Sequence Header，而非 NAL 参数集。不照搬解码器的数值 level，不分配 SPS ID。full-range 输入的 YUVJ420P 仅规范化布局枚举为 YUV420P，保留范围、SAR 和颜色描述。

输出头采用首个实际输出区间的配置。对 NVENC 等输入，用 `av_guess_sample_aspect_ratio` 选择有效 SAR，避免只读 AVStream SAR 而遗漏 codecpar 中的值。

H.264 无损 profile 变化仍采用研究的完整选区编码回退。此时用户请求的 CRF/QP/preset 不变，报告明确说明复制帧数变为零。

## 4. 显示与解码时间

保留源显示时间的非均匀间隔；时间选择按实际 PTS 查找，帧选择按 PTS 排序序号查找。首个显示帧可晚于时间选区起点，这段 leading gap 保留。最后一帧的区间可以按 `-to/-t` 缩短。

编码器可输出 B 帧，因此先把编码包写入任务私有的磁盘 spool，记录实际编码顺序与源 PTS。完整计划形成后，对每个区间用源显示时间格点构造解码顺序槽，并计算统一的足够重排延迟，使预封装 DTS 严格递增且不大于 PTS。没有把所有压缩帧载荷放进内存，也没有把 VFR 重新采样为 CFR。

MP4 还有两项与视频编码不同的时间约束：

- 起始重排可能触发 movenc 对负 DTS 的 start_cts 推断，造成媒体时长和 edit list 的舍入问题。只有在确会触发、且首个解码包也是最早显示帧时，启用其 `negative_cts_offsets`。HEVC 的 IDR+RADL 可能先解码后显示，不能一律开启，否则 leading picture 会被编辑列表排除。
- empty edit 使用 movie timescale，默认 1000 会把 NTSC 的非毫秒首帧位置量化。默认选用可覆盖视频时钟的 movie timescale，在范围允许时与辅助轨道分母取 LCM。用户仍可显式设置 muxer 时间参数，并承担该设置本身的量化结果。

这些问题用公开 muxer 选项解决，不修改 movenc，不对完成后的 MP4 做二进制修补。

AV1 的压缩包以 TU 顺序输出，`DTS = PTS`，解码延迟为零，不生成 `ctts`。尾部持续时间仍使用源 sample 的 duration，时间选区的最后一个 sample 可以按 `-to/-t` 缩短。该流程与既有音频/字幕交织封装共用。

## 5. 音频与字幕

FFmpeg 子命令保持用户的两组 `-ss/-t/-to`、选中源流和音频/字幕参数。为移除视频后变化的流序号重新映射输出 stream specifier，例如原输出 `-c:3` 会转换为辅助文件内的对应序号。

子命令处理整个选区，可复制或重编码音频，也可应用 FFmpeg 自己的简单音频滤镜。字幕继承 FFmpeg 对事件边界和 MP4 编码格式的原有语义，不另建“音频 GOP”或字幕切割算法。

使用固定 track id 关联所请求输出与辅助 MP4 的实际流；因为 FFmpeg 可能省略选区内没有包的音轨，不能简单按位置配对。空轨道被明确提示并跳过。音频/字幕压缩包、PTS/DTS、duration 和 skip_samples 侧数据交由 libavformat 重封装，测试以原版 FFmpeg 输出为独立对照。

## 6. 失败、取消与资源

输入格式、流布局、编码格式、参数、选区在写入最终目标前验证。私有工作区用随机名称并由任务持有；源压缩数据通过只读 AVIO 和已验证的样本偏移读取。发布前复核输入大小与修改时间，防止普通的处理中源文件变化。

Windows 子进程使用 CreateProcessW 参数向量转义，不经过 shell；用 Job Object 约束其生命周期。取消会结束子进程。临时输出位于目标卷，完成 trailer 并关闭文件后再通过 MoveFileExW 原子发布。失败不会把半成品覆盖到已有输出。

大小与 mtime 检查不是抵御恶意并发改写的文件快照机制。视频与侧车报告也不是跨文件事务。完整断电持久性、多进程抢占同一个输出和所有硬件播放器兼容性仍需产品化后续专项验证。

## 7. 对研究项目技术的取舍

| 技术部分 | 本实现的选择与证据 |
| --- | --- |
| 完整区间复制、边界编码 | 所有实现共同的有效核心；沿用原生 h264/h265 方案 |
| 把容器 KEY/I 帧直接当随机访问点 | 对 H.264 不充分；读取真实 IDR，对 HEVC 细分访问点 |
| 每种编码格式都要独立的前导帧处理 | HEVC 确实需要；本轮 CRA、RASL、RADL 用例验证了边界差异 |
| 匹配几项 profile/level 后直接 concat | 无法代替参数集切换、时间轴和访问点验证；本实现直接控制每个视频包 |
| 全局重写 SPS/PPS id | 本轮固定配置输入不需要；同 id 与显式 id=3、两种参数集封装都进行了验证 |
| 强制边界 B=0 / 固定 GOP | 不是正确性的必需条件；8 B 帧、短 GOP、Open GOP 的用户编码参数与 stock FFmpeg 边界 VCL 一致 |
| 所有时间戳归零或按平均帧率重建 | 不适合 VFR；保留源 PTS 间隔和选区 leading gap |
| 全部视频流程调用 ffmpeg.exe | 可行但不属于本实现；直接用公共库避免多次临时视频封装、concat 的时间轴推断 |
| 所有音频都自己写边界算法 | 当前需求不需要；原版 FFmpeg 执行整个选区，按其原有边界语义验收 |
| HEVC、H.264 无损及任意元数据都同一路径 | 不充分；H.264 无损 profile 回退仍必要，未验证的动态元数据不能宣称无损保留 |

以上结论限定于本项目的输入契约及测试集，不能推导为其他项目所有 SPS id、参考关系分析或重写逻辑都多余。更宽的输入范围、多段音频拼接及跨 profile/hardware 兼容目标可能需要额外处理。
