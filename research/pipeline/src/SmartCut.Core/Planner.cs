namespace SmartCut.Core;

public enum UnitKind { Copy, Encode }

/// <param name="PacketBegin">Decode-order packet index, inclusive.</param>
/// <param name="PacketEnd">Decode-order packet index, exclusive.</param>
/// <param name="DisplayBegin">Display-frame index, inclusive.</param>
/// <param name="DisplayEnd">Display-frame index, exclusive.</param>
public sealed record PlanUnit(UnitKind Kind, int PacketBegin, int PacketEnd, long DisplayBegin, long DisplayEnd,
                              long OutputStartTicks, long DurationTicks)
{
    public int PacketCount => PacketEnd - PacketBegin;
    public long FrameCount => DisplayEnd - DisplayBegin;
    public bool IsWholeEpoch => Kind == UnitKind.Copy;
}

public sealed class VideoPlan
{
    public required IReadOnlyList<PlanUnit> Units { get; init; }
    public required IReadOnlyList<FrameRange> NormalizedKeeps { get; init; }
    public required IReadOnlyList<string> Warnings { get; init; }
    public long CopiedFrames => Units.Where(u => u.Kind == UnitKind.Copy).Sum(u => u.FrameCount);
    public long EncodedFrames => Units.Where(u => u.Kind == UnitKind.Encode).Sum(u => u.FrameCount);
    public long TotalDurationTicks => Units.Sum(u => u.DurationTicks);

    /// <summary>Expected output presentation time per kept display frame, in stream ticks.</summary>
    public IEnumerable<long> ExpectedOutputTicks(VideoIndex index)
    {
        foreach (var unit in Units)
            for (var rank = unit.DisplayBegin; rank < unit.DisplayEnd; rank++)
                yield return unit.OutputStartTicks + (index.PtsOfDisplay(rank) - index.Boundaries[(int)unit.DisplayBegin]);
    }
}

public sealed record AudioRange(long? StartSample, long? EndSample, double StartSeconds, double EndSeconds, bool Exact)
{
    public long? SampleCount => StartSample is null || EndSample is null ? null : EndSample - StartSample;
}

public sealed class AudioTrackPlan
{
    public required ProbeStream Stream { get; init; }
    /// <summary>Ordinal of this stream among the input's audio streams, as ffmpeg's [1:a:n] expects.</summary>
    public required int AudioOrdinal { get; init; }
    public required IReadOnlyList<AudioRange> Ranges { get; init; }
    public required string Label { get; init; }
    public bool AnyInexact => Ranges.Any(r => !r.Exact);
}

public static class Planner
{
    public static IReadOnlyList<FrameRange> Normalize(VideoIndex index, IReadOnlyList<FrameRange> keeps)
    {
        if (keeps.Count == 0) throw new PipelineException("at least one --keep range is required");
        var previous = -1L;
        var result = new List<FrameRange>();
        foreach (var range in keeps)
        {
            if (range.Begin < 0 || range.End <= range.Begin)
                throw new PipelineException($"invalid --keep range {range.Begin}:{range.End}");
            if (range.End > index.FrameCount)
                throw new PipelineException($"--keep {range.Begin}:{range.End} exceeds the {index.FrameCount}-frame input");
            if (range.Begin < previous)
                throw new PipelineException("--keep ranges must be ordered and non-overlapping");
            previous = range.End;
            if (result.Count > 0 && result[^1].End == range.Begin)
                result[^1] = result[^1] with { End = range.End };
            else
                result.Add(range);
        }
        return result;
    }

    /// <summary>Whole IDR epochs copy; anything partial re-encodes. Adjacent copy units are merged
    /// into one segment: measured on these fixtures, each extra concat join costs one frame period of
    /// drift on variable-frame-rate material, because a segment's declared duration need not equal the
    /// distance to the next kept frame.</summary>
    public static VideoPlan PlanVideo(VideoIndex index, IReadOnlyList<FrameRange> keeps)
    {
        var normalized = Normalize(index, keeps);
        var units = new List<PlanUnit>();
        var output = 0L;
        foreach (var range in normalized)
        {
            foreach (var epoch in index.Epochs)
            {
                var a = Math.Max(epoch.DisplayBegin, range.Begin);
                var b = Math.Min(epoch.DisplayEnd, range.End);
                if (a >= b) continue;
                var copy = a == epoch.DisplayBegin && b == epoch.DisplayEnd;
                var duration = index.Boundaries[(int)b] - index.Boundaries[(int)a];
                var unit = new PlanUnit(copy ? UnitKind.Copy : UnitKind.Encode,
                                        copy ? epoch.PacketBegin : PacketRangeFor(index, a, b).Begin,
                                        copy ? epoch.PacketEnd : PacketRangeFor(index, a, b).End,
                                        a, b, output, duration);
                if (copy && units.Count > 0 && units[^1].Kind == UnitKind.Copy && units[^1].DisplayEnd == a &&
                    units[^1].PacketEnd == epoch.PacketBegin)
                {
                    var previous = units[^1];
                    units[^1] = previous with { PacketEnd = unit.PacketEnd, DisplayEnd = unit.DisplayEnd, DurationTicks = previous.DurationTicks + duration };
                    output += duration;
                    continue;
                }
                units.Add(unit);
                output += duration;
            }
        }
        if (units.Count == 0) throw new PipelineException("empty plan");
        var warnings = new List<string>();
        var copied = units.Where(u => u.Kind == UnitKind.Copy).Sum(u => u.FrameCount);
        var encoded = units.Where(u => u.Kind == UnitKind.Encode).Sum(u => u.FrameCount);
        if (encoded > copied)
            warnings.Add($"more than half of the selection needs encoding under the IDR-only safety policy ({encoded} of {copied + encoded})");
        return new VideoPlan { Units = units, NormalizedKeeps = normalized, Warnings = warnings };
    }

    static (int Begin, int End) PacketRangeFor(VideoIndex index, long displayBegin, long displayEnd)
    {
        var packets = new List<int>();
        for (var epoch = 0; epoch < index.Epochs.Count; epoch++)
        {
            var e = index.Epochs[epoch];
            if (e.DisplayEnd <= displayBegin || e.DisplayBegin >= displayEnd) continue;
            for (var p = e.PacketBegin; p < e.PacketEnd; p++)
                if (index.DecodeOrder[p].DisplayRank >= displayBegin && index.DecodeOrder[p].DisplayRank < displayEnd)
                    packets.Add(p);
        }
        return (packets.Min(), packets.Max() + 1);
    }

    /// <summary>Audio windows follow the video cut points. Bounds are sample-exact whenever the video
    /// time base divides evenly into the audio sample rate; otherwise atrim falls back to seconds.</summary>
    public static IReadOnlyList<AudioTrackPlan> PlanAudio(ProbeResult probe, VideoIndex index, IReadOnlyList<FrameRange> keeps)
    {
        var plans = new List<AudioTrackPlan>();
        var ordinal = 0;
        foreach (var stream in probe.Streams.Where(s => s.CodecType == "audio"))
        {
            var current = ordinal++;
            if (!long.TryParse(stream.SampleRate, out var rate) || rate <= 0)
            {
                plans.Add(new AudioTrackPlan
                {
                    Stream = stream,
                    AudioOrdinal = current,
                    Ranges = Array.Empty<AudioRange>(),
                    Label = $"a{current}",
                });
                continue;
            }
            var ranges = new List<AudioRange>();
            foreach (var range in keeps)
            {
                var startTicks = index.Boundaries[(int)range.Begin] - index.FirstPts;
                var endTicks = index.Boundaries[(int)range.End] - index.FirstPts;
                var startSeconds = (double)startTicks * index.TimeBaseNumerator / index.TimeBaseDenominator;
                var endSeconds = (double)endTicks * index.TimeBaseNumerator / index.TimeBaseDenominator;
                var (startSample, endSample, exact) = ToSamples(startTicks, endTicks, index.TimeBaseNumerator,
                                                                index.TimeBaseDenominator, rate);
                ranges.Add(new AudioRange(startSample, endSample, startSeconds, endSeconds, exact));
            }
            plans.Add(new AudioTrackPlan { Stream = stream, AudioOrdinal = current, Ranges = ranges, Label = $"a{current}" });
        }
        return plans;
    }

    static (long? Start, long? End, bool Exact) ToSamples(long startTicks, long endTicks, long tbNum, long tbDen, long rate)
    {
        var denominator = (decimal)tbDen;
        var start = (decimal)startTicks * rate * tbNum;
        var end = (decimal)endTicks * rate * tbNum;
        if (start % denominator != 0 || end % denominator != 0 || start > long.MaxValue || end > long.MaxValue)
            return (null, null, false);
        return ((long)(start / denominator), (long)(end / denominator), true);
    }
}
