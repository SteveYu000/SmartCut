using System.Buffers.Binary;

namespace SmartCut.Core;

public sealed record VideoSample(int Packet, long Pts, long Dts, long Duration, long Pos, long Size, bool Idr, int DisplayRank);

public sealed record Epoch(int PacketBegin, int PacketEnd, int DisplayBegin, int DisplayEnd);

/// <summary>Display-frame index of one H.264 video track. Random access points come from reading
/// sample bytes for a VCL NAL type 5, never from the container key flag: measured on this
/// repository's own fixtures, key-flagged non-IDR packets exist (up to 9 per file).</summary>
public sealed class VideoIndex
{
    public required ProbeStream Stream { get; init; }
    public required long TimeBaseNumerator { get; init; }
    public required long TimeBaseDenominator { get; init; }
    public required IReadOnlyList<VideoSample> DecodeOrder { get; init; }
    public required IReadOnlyList<int> DisplayToPacket { get; init; }
    public required IReadOnlyList<Epoch> Epochs { get; init; }
    /// <summary>N+1 presentation boundaries in stream ticks.</summary>
    public required IReadOnlyList<long> Boundaries { get; init; }
    public required int FrameCount { get; init; }
    public required long FirstPts { get; init; }
    public required long NonIdrKeySamples { get; init; }
    public required int NalLengthSize { get; init; }
    public required string InputPath { get; init; }

    public long PtsOfDisplay(long rank) => Boundaries[(int)rank];

    public static VideoIndex Build(FFmpegTools tools, string path, ProbeResult probe)
    {        var videos = probe.Streams.Where(s => s.CodecType == "video").ToArray();
        if (videos.Length == 0) throw new PipelineException("no video stream");
        if (videos.Length > 1) throw new PipelineException("exactly one video stream is supported (including cover art)");
        var stream = videos[0];
        if (stream.CodecName != "h264")
            throw new PipelineException($"video codec must be H.264, got {stream.CodecName}");
        if (stream.CodecTag is not ("avc1" or "avc3"))
            throw new PipelineException($"only unencrypted avc1/avc3 input is supported, got {stream.CodecTag}");
        if (stream.FieldOrder is not null and not ("progressive" or "unknown"))
            throw new PipelineException($"only progressive video is supported, got field_order={stream.FieldOrder}");
        var (tbNum, tbDen) = stream.TimeBaseRatio();
        if (tbNum <= 0 || tbDen <= 0) throw new PipelineException("invalid stream time base");

        var packets = probe.Packets.Where(p => p.StreamIndex == stream.Index).ToArray();
        if (packets.Length == 0) throw new PipelineException("no video samples");
        var nalLength = ReadNalLengthSize(path);
        var idr = new bool[packets.Length];
        using (var file = File.OpenRead(path))
        {
            var buffer = new byte[64 * 1024];
            for (var i = 0; i < packets.Length; i++)
            {
                var p = packets[i];
                if (p.Pos < 0 || p.Size <= 0)
                    throw new PipelineException($"sample {i} is not directly addressable in the local MP4");
                idr[i] = ContainsIdrAccessUnit(file, p, ref buffer, nalLength);
            }
        }

        var samples = new List<VideoSample>(packets.Length);
        for (var i = 0; i < packets.Length; i++)
        {
            var p = packets[i];
            if (i > 0 && p.Dts <= packets[i - 1].Dts)
                throw new PipelineException($"input DTS must be strictly increasing (sample {i})");
            samples.Add(new VideoSample(i, p.Pts, p.Dts, p.Duration, p.Pos, p.Size, idr[i], 0));
        }

        var display = Enumerable.Range(0, samples.Count).OrderBy(i => samples[i].Pts).ToArray();
        for (var rank = 0; rank < display.Length; rank++)
            samples[display[rank]] = samples[display[rank]] with { DisplayRank = rank };
        for (var rank = 1; rank < display.Length; rank++)
            if (samples[display[rank]].Pts == samples[display[rank - 1]].Pts)
                throw new PipelineException("duplicate presentation timestamps are unsupported");

        if (!samples[display[0]].Idr)
            throw new PipelineException("input must start with a real IDR access unit");
        var last = samples[display[^1]];
        if (last.Duration <= 0) throw new PipelineException("last sample has no positive duration");

        var boundaries = new List<long>(samples.Count + 1);
        for (var rank = 0; rank < display.Length; rank++) boundaries.Add(samples[display[rank]].Pts);
        boundaries.Add(boundaries[^1] + last.Duration);

        var epochs = new List<Epoch>();
        var start = 0;
        for (var end = 1; end <= samples.Count; end++)
        {
            if (end != samples.Count && !idr[end]) continue;
            var lo = int.MaxValue; var hi = -1;
            for (var i = start; i < end; i++)
            {
                lo = Math.Min(lo, samples[i].DisplayRank);
                hi = Math.Max(hi, samples[i].DisplayRank);
            }
            if (lo != samples[start].DisplayRank || hi - lo + 1 != end - start)
                throw new PipelineException("presentation reordering crosses an IDR boundary");
            epochs.Add(new Epoch(start, end, lo, hi + 1));
            start = end;
        }

        return new VideoIndex
        {
            InputPath = path,
            Stream = stream,
            TimeBaseNumerator = tbNum,
            TimeBaseDenominator = tbDen,
            DecodeOrder = samples,
            DisplayToPacket = display,
            Epochs = epochs,
            Boundaries = boundaries,
            FrameCount = samples.Count,
            FirstPts = samples[display[0]].Pts,
            NonIdrKeySamples = packets.Count(p => p.Key) - samples.Count(s => s.Idr),
            NalLengthSize = nalLength,
        };
    }

    static bool ContainsIdrAccessUnit(FileStream file, ProbePacket packet, ref byte[] scratch, int nalLength)
    {
        var size = (int)packet.Size;
        if (size > scratch.Length) Array.Resize(ref scratch, size);
        file.Seek(packet.Pos, SeekOrigin.Begin);
        try
        {
            file.ReadExactly(scratch.AsSpan(0, size));
        }
        catch (EndOfStreamException)
        {
            throw new PipelineException("short sample read (input changed during processing?)");
        }
        var pos = 0;
        var idr = false;
        while (pos + nalLength <= size)
        {
            var length = nalLength switch
            {
                1 => scratch[pos],
                2 => BinaryPrimitives.ReadUInt16BigEndian(scratch.AsSpan(pos)),
                3 => (scratch[pos] << 16) | (scratch[pos + 1] << 8) | scratch[pos + 2],
                _ => (int)BinaryPrimitives.ReadUInt32BigEndian(scratch.AsSpan(pos)),
            };
            pos += nalLength;
            if (length <= 0 || pos + length > size) throw new PipelineException("invalid NAL length in sample");
            if ((scratch[pos] & 31) == 5) idr = true;
            pos += length;
        }
        return idr;
    }

    static int ReadNalLengthSize(string path)
    {
        var length = new FileInfo(path).Length;
        if (length < 9) return 4;
        using var file = File.OpenRead(path);
        // avcC sits in moov, which is at the front for faststart files and at the end otherwise.
        foreach (var start in new[] { 0L, Math.Max(0, length - (1 << 20)) })
        {
            var window = new byte[Math.Min(1 << 20, length - start)];
            file.Position = start;
            file.ReadExactly(window);
            for (var i = 0; i + 9 <= window.Length; i++)
                if (window.AsSpan(i, 4).SequenceEqual("avcC"u8))
                    return (window[i + 8] & 3) + 1;
        }
        return 4;
    }

    public string Describe() =>
        $"{FrameCount} frames, {Epochs.Count} IDR epochs, {NonIdrKeySamples} key-flag non-IDR samples, " +
        $"time_base {TimeBaseNumerator}/{TimeBaseDenominator}, {Stream.PixFmt}, profile {Stream.Profile}, tag {Stream.CodecTag}";
}
