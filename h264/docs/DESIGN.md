# 实现设计与不变量

版本：0.1.0，2026-10-27。完整论证与实验见 [IMPLEMENTATION_REPORT.md](IMPLEMENTATION_REPORT.md)。

## 输入与剪辑语义

输入为本地、单视频轨、固定配置、逐行 H.264 MP4。支持 4:2:0 的 8/10 bit，输入 profile 限于 Baseline / Main / High / High 10；AVCC 长度前缀样本，每个样本是一幅逐行图像，可有多个 slice。要求首个样本为真实 IDR、DTS 严格递增、PTS 唯一，最后一帧具有正时长。动态配置、隔行及缺失时间戳不自动猜测修复。High 4:4:4 Predictive 等无损源配置即使像素为 4:2:0 也拒绝，以免与新的有损边界混合。

`--keep A:B` 是显示帧区间 `[A,B)`，按 PTS 排序定义。多个有序不重叠区间顺序连接，相邻区间先合并。删除的时段不保留空洞。

## 数据流

```text
libavformat 解复用
  -> 样本索引 (pos / size / PTS / DTS / duration / IDR)
  -> 显示顺序与 IDR 区间
  -> keep 区间求交、COPY / ENCODE 计划
      COPY   -> AVIO 读取原始样本 -> BSF 或原始 AVCC
      ENCODE -> 从该 IDR 解码 -> 筛选显示帧 -> libx264
  -> 统一输出 PTS/DTS + 参数集承载
  -> libavformat MP4 muxer -> 成功后改名
```

索引只保存元数据，不缓存全片压缩包。先顺序扫描一次，再通过 AVIO 按已验证的样本文件位置读取。FFmpeg 负责 fMP4 的解析，因此不必自行解析 moof/trun。输入在整个处理期间必须保持不变。

## 复制安全性

真实 IDR 由 VCL NAL type 5 判定；容器 key flag 或普通 I 帧均不足以恢复复制。两个真实 IDR 之间构成一个区间，最后一个区间延伸至文件结尾。检查每个区间的显示帧集合连续且不跨 IDR 重排序。

完整选中的区间可以复制；不完整区间的选中部分全部重新编码。解码从区间的原始 IDR 开始，达到所需末帧即可停止输入；重排/线程缓冲可能多返回少量画面。编码器只接收选中的画面。每个编码单元从独立 IDR 开始，B 帧为 0，closed GOP；较长单元允许编码器再插入 IDR。

复制区间保持原始解码顺序和 VCL 字节。不改写 POC、frame_num、MMCO、参考列表或参数集 ID。Open GOP 内的跨 GOP 参考依然存在，但被保留在同一个完整复制区间内。若没有后续 IDR，就不能在中途恢复复制。

## 两种参数集承载

`avc3`：输出 sample entry 为 avc3。编码器提供匹配自身切片的 SPS/PPS；每个复制单元建立新的 `h264_mp4toannexb` 上下文，从源 avcC 恢复 SPS/PPS。MP4 muxer 将 Annex B 转成长度前缀形式。允许 IDR 边界前切换同一个 ID 的定义。

`stsd`：输出 sample entry 为 avc1。每个单元第一包携带 `AV_PKT_DATA_NEW_EXTRADATA`；FFmpeg 8.1 movmuxer 建立/选择对应 sample description，`stsc` 关联样本块与配置。复制包仍是原始 AVCC，编码包的 Annex B 转换交给 muxer。

两者都不强行把新 SPS/PPS 替换成旧字节。参数集要与实际切片编码方式一致。

## 时间轴

记输入显示边界为 `t[0..N]`，前 N 个为排序后 PTS，最后一个为末帧 PTS 加其时长。对计划单元 `[a,b)`，其输出起点为 `O`：

```text
output_pts = O + input_pts - t[a]
unit_duration = t[b] - t[a]
next_O = O + unit_duration
```

按单元原始解码顺序，第 `j` 包获得递增解码时隙 `s[j] = O + t[a+j] - t[a]`。统一延迟为所有复制包的 `max(0, s[j]-output_pts[j])`。输出 DTS 为 `s[j]-delay`。B=0 的编码单元按显示顺序使用同一套时隙。

因此相邻单元 DTS 严格递增，所有 DTS 不晚于 PTS；输出 PTS 精确对应保留的显示帧。初始 DTS 可为负，交给 MP4 的 ctts/edit list 表达。全部使用整数 tick 和 FFmpeg 有理数换算，不用平均帧率估算 VFR 边界，也不靠给非单调 DTS 加 1 修补。

这是容器时间轴重建，不承诺保留原 HRD/CPB 时序。原码流的解码顺序不变，但 DTS 数值可能不同于原文件。

## 编码与质量

默认 libx264 medium、CRF 18、4 线程、B=0，沿用尺寸、像素格式、色彩字段和 SAR，并为通常的有损编码设置源 profile 对应的限制。不追求原始码率匹配，不做像素转换。

新编码帧上的源侧 side data 清除，避免把旧编码过程的 SEI/HRD 等误认为新编码结果。本版不保证附属元数据完整保留。

若 `--crf 0` 且计划含编码单元，则所有选中单元改为 QP=0 编码；显式输出提示和 `lossless_full_encode_fallback=true`。这是经过实验后采用的安全回退，不声称此时仍是最少重编码。若原计划全为复制，则不触发回退。

## 正确性约束

- 输入不在支持范围内即报错；不按关键帧静默移动用户切点。
- 解码得到的 PTS 必须逐一匹配样本索引；被选帧数和编码输出包数必须相等。
- 每次写包验证 DTS 单调且不大于 PTS；写完验证总样本数与计划一致。
- 输出仅真实 IDR 标为同步样本；集成测试进一步从这些点 seek 验证。
- 复制画面“无损”的证据是 VCL NAL 哈希和解码像素同时一致，不能只看播放成功。
- 输出临时文件仅在成功写完 trailer 后改名；不覆盖用户既有文件。

这些检查不是完整 H.264 合规验证器；复制部分不会全量解码，损坏码流检测和播放器兼容性仍有边界。
