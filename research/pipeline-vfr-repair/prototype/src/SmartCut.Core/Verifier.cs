using System.Globalization;
using System.Text.RegularExpressions;

namespace SmartCut.Core;

public sealed record VerificationReport(
    bool Passed,
    IReadOnlyList<string> Problems,
    long Frames,
    long ExpectedFrames,
    long PtsMismatches,
    long ZeroGaps,
    long CopyFramesChecked,
    long CopyFramesMatching,
    double? MinEncodedRegionPsnr,
    string? DecodeWarning,
    IReadOnlyList<AudioTrackVerification>? Audio = null)
{
    public object ToJsonPayload() => new Dictionary<string, object?>
    {
        ["passed"] = Passed,
        ["frames"] = Frames,
        ["expected_frames"] = ExpectedFrames,
        ["pts_mismatches"] = PtsMismatches,
        ["zero_gaps"] = ZeroGaps,
        ["copy_frames_checked"] = CopyFramesChecked,
        ["copy_frames_matching"] = CopyFramesMatching,
        ["min_encoded_region_psnr_db"] = MinEncodedRegionPsnr,
        ["decode_warning"] = DecodeWarning,
        ["audio"] = Audio?.Select(a => (object?)a.ToJsonPayload()).ToList() ?? new List<object?>(),
        ["problems"] = Problems.ToList(),
    };
}

/// <summary>Independent acceptance: frame count, per-frame presentation time, positional identity of
/// copied pictures, PSNR of re-encoded regions, and a strict decode that must stay silent. A zero exit
/// code alone is not evidence: measured here, a file with 600 frames and duplicate presentation times
/// still decoded with exit code 0.</summary>
public static class Verifier
{
    public static VerificationReport Verify(FFmpegTools tools, string source, string output,
                                            VideoIndex index, VideoPlan plan,
                                            IReadOnlyList<AudioTrackPlan> audioPlans)
    {
        var problems = new List<string>();
        var probe = new FFprobeReader(tools);
        var outputProbe = probe.Read(output);
        var stream = outputProbe.Streams.First(s => s.CodecType == "video");
        var packets = outputProbe.Packets.Where(p => p.StreamIndex == stream.Index).ToArray();
        var (outNum, outDen) = stream.TimeBaseRatio();

        foreach (var (key, before, after) in new[]
                 {
                     ("color_range", index.Stream.ColorRange, stream.ColorRange),
                     ("color_space", index.Stream.ColorSpace, stream.ColorSpace),
                     ("color_primaries", index.Stream.ColorPrimaries, stream.ColorPrimaries),
                     ("color_transfer", index.Stream.ColorTransfer, stream.ColorTransfer),
                     ("sample_aspect_ratio", index.Stream.SampleAspectRatio, stream.SampleAspectRatio),
                 })
        {
            if (before is null || before == "unknown") continue;
            if (after != before) problems.Add($"metadata {key}: input {before} became output {after ?? "unset"}");
        }
        var actualSeconds = packets.Select(p => (decimal)p.Pts * outNum / outDen).OrderBy(x => x).ToArray();

        var expectedTicks = plan.ExpectedOutputTicks(index).ToArray();
        if (actualSeconds.Length != expectedTicks.Length)
            problems.Add($"frame count {actualSeconds.Length} != planned {expectedTicks.Length}");

        var expectedSeconds = expectedTicks
            .Select(t => (decimal)t * index.TimeBaseNumerator / index.TimeBaseDenominator).ToArray();
        long ptsMismatch = 0;
        for (var i = 0; i < Math.Min(actualSeconds.Length, expectedSeconds.Length); i++)
            if (actualSeconds[i] != expectedSeconds[i]) ptsMismatch++;
        if (ptsMismatch > 0) problems.Add($"{ptsMismatch} presentation times differ from the plan");

        long zeroGaps = 0;
        for (var i = 1; i < actualSeconds.Length; i++)
            if (actualSeconds[i] == actualSeconds[i - 1]) zeroGaps++;
        if (zeroGaps > 0) problems.Add($"{zeroGaps} duplicate presentation times");

        var sourceRows = FrameHashes(tools, source);
        var outputRows = FrameHashes(tools, output);
        var wantedRanks = plan.Units.SelectMany(u => Enumerable.Range((int)u.DisplayBegin, (int)u.FrameCount)).ToArray();
        var copyRanks = plan.Units.Where(u => u.Kind == UnitKind.Copy)
                                 .SelectMany(u => Enumerable.Range((int)u.DisplayBegin, (int)u.FrameCount))
                                 .ToHashSet();
        long copyChecked = 0, copyMatched = 0;
        for (var i = 0; i < Math.Min(outputRows.Count, wantedRanks.Length); i++)
        {
            if (!copyRanks.Contains(wantedRanks[i])) continue;
            copyChecked++;
            if (wantedRanks[i] < sourceRows.Count && outputRows[i] == sourceRows[wantedRanks[i]]) copyMatched++;
        }
        if (copyMatched != copyChecked)
            problems.Add($"copied pictures differ at {copyChecked - copyMatched}/{copyChecked} positions");

        var strict = tools.FFmpeg(["-v", "error", "-xerror", "-err_detect", "explode", "-i", output,
                                   "-map", "0:v:0", "-fps_mode", "passthrough", "-f", "null", "-"], expectSuccess: false);
        var warning = strict.StdErr.Trim();
        if (strict.ExitCode != 0 || warning.Length > 0)
            problems.Add($"strict decode reported: {Shorten(warning)}");

        double? minPsnr = null;
        var offset = 0;
        foreach (var unit in plan.Units)
        {
            var count = (int)unit.FrameCount;
            if (unit.Kind == UnitKind.Encode && count > 0)
            {
                var value = RegionPsnr(tools, output, source, offset, offset + count - 1,
                                       (int)unit.DisplayBegin, (int)unit.DisplayEnd - 1);
                if (value is not null && (minPsnr is null || value < minPsnr)) minPsnr = value;
            }
            offset += count;
        }

        if (minPsnr is < 30)
            problems.Add($"re-encoded region PSNR {minPsnr.Value.ToString("F2", CultureInfo.InvariantCulture)} dB is below 30 dB");

        var audio = AudioVerifier.Verify(tools, source, output, index, plan.NormalizedKeeps, audioPlans);
        foreach (var track in audio)
        {
            if (Math.Abs(track.SampleDelta) > 4800)
                problems.Add($"audio stream {track.StreamIndex}: {track.SampleDelta:+#;-#;0} samples versus the planned " +
                             $"{track.ExpectedSamples}");
            if (track.EventsExpected > 0 && track.EventsFound != track.EventsExpected)
                problems.Add($"audio stream {track.StreamIndex}: {track.EventsFound} impulses, expected {track.EventsExpected}");
            if (track.EventsExpected > 0 && double.IsPositiveInfinity(track.MaxResidualMs))
                problems.Add($"audio stream {track.StreamIndex}: impulse count differs, position check inconclusive");
            else if (track.MaxResidualMs > 20)
                problems.Add($"audio stream {track.StreamIndex}: impulse position deviates up to {track.MaxResidualMs:F1} ms " +
                             $"(median offset {track.MedianOffsetMs:F1} ms)");
        }

        return new VerificationReport(problems.Count == 0, problems, actualSeconds.Length, expectedTicks.Length,
                                      ptsMismatch, zeroGaps, copyChecked, copyMatched, minPsnr,
                                      warning.Length == 0 ? null : Shorten(warning), audio);
    }

    static List<string> FrameHashes(FFmpegTools tools, string path)
    {
        var run = tools.FFmpeg(["-v", "error", "-i", path, "-map", "0:v:0", "-fps_mode", "passthrough", "-f", "framemd5", "-"]);
        var rows = new List<string>();
        foreach (var line in run.StdOut.Split('\n'))
        {
            var trimmed = line.Trim();
            if (trimmed.Length == 0 || trimmed.StartsWith('#')) continue;
            var comma = trimmed.LastIndexOf(',');
            if (comma < 0) continue;
            rows.Add(trimmed[(comma + 1)..]);
        }
        return rows;
    }

    static readonly Regex PsnrLine = new(@"average:(\d+\.?\d*)", RegexOptions.Compiled | RegexOptions.CultureInvariant);

    static double? RegionPsnr(FFmpegTools tools, string output, string source,
                              int outputBegin, int outputEnd, int sourceBegin, int sourceEnd)
    {
        // psnr aligns its two inputs by timestamp, so both windows get the same synthetic cadence:
        // without this the comparison pairs frame i against a shifted frame and reports junk.
        var graph = $"[0:v]select='between(n\\,{outputBegin}\\,{outputEnd})',setpts=N/TB[c];" +
                    $"[1:v]select='between(n\\,{sourceBegin}\\,{sourceEnd})',setpts=N/TB[r];[c][r]psnr[psnrout]";
        var run = tools.FFmpeg(["-v", "info", "-i", output, "-i", source, "-filter_complex", graph,
                                "-map", "[psnrout]", "-fps_mode", "passthrough", "-f", "null", "-"], expectSuccess: false);
        var match = PsnrLine.Match(run.StdErr);
        return match.Success && double.TryParse(match.Groups[1].Value, CultureInfo.InvariantCulture, out var value)
            ? value
            : null;
    }

    static string Shorten(string text) => text.Length <= 220 ? text : text[..220];
}

public sealed record AudioTrackVerification(
    int StreamIndex,
    string Codec,
    long ExpectedSamples,
    long DecodedSamples,
    long EventsExpected,
    long EventsFound,
    double MedianOffsetMs,
    double MaxResidualMs,
    string Note)
{
    public long SampleDelta => DecodedSamples - ExpectedSamples;

    public object ToJsonPayload() => new Dictionary<string, object?>
    {
        ["stream_index"] = StreamIndex,
        ["codec"] = Codec,
        ["expected_samples"] = ExpectedSamples,
        ["decoded_samples"] = DecodedSamples,
        ["sample_delta"] = SampleDelta,
        ["impulses_expected"] = EventsExpected,
        ["impulses_found"] = EventsFound,
        ["median_offset_ms"] = Math.Round(MedianOffsetMs, 3),
        ["max_residual_ms"] = double.IsFinite(MaxResidualMs) ? Math.Round(MaxResidualMs, 3) : "inconclusive",
        ["note"] = Note,
    };
}

/// <summary>Audio acceptance: sample count against the planned windows, and impulse positions.
/// The impulse train is read out of the source, mapped through the same keep windows the plan used,
/// and matched against the output; a seam that lands on the wrong audio position shows up as a
/// residual jump at that point rather than as a duration error.</summary>
public static class AudioVerifier
{
    const double ThresholdRatio = 0.5;

    public static IReadOnlyList<AudioTrackVerification> Verify(FFmpegTools tools, string source, string output,
                                                               VideoIndex index, IReadOnlyList<FrameRange> keeps,
                                                               IReadOnlyList<AudioTrackPlan> plans)
    {
        var results = new List<AudioTrackVerification>();
        var outputProbe = new FFprobeReader(tools).Read(output);
        var audioOut = outputProbe.Streams.Where(s => s.CodecType == "audio").ToArray();
        var keptPlans = plans.Where(p => p.Ranges.Count > 0).ToArray();
        if (keptPlans.Length != audioOut.Length)
            throw new PipelineException($"output carries {audioOut.Length} audio stream(s) but the plan wrote {keptPlans.Length}");

        for (var position = 0; position < keptPlans.Length; position++)
        {
            var plan = keptPlans[position];
            var outStream = audioOut[position];
            if (!long.TryParse(plan.Stream.SampleRate, out var rate) || rate <= 0)
            {
                results.Add(new AudioTrackVerification(plan.Stream.Index, plan.Stream.CodecName ?? "?",
                                                       0, 0, 0, 0, 0, 0, "input has no usable sample rate"));
                continue;
            }
            var expectedSamples = plan.Ranges.Sum(r => r.SampleCount ?? (long)Math.Round((r.EndSeconds - r.StartSeconds) * rate));
            var mapping = BuildMapping(index, keeps);

            var sourcePcm = DecodeMono(tools, source, plan.Stream.Index, rate);
            // One absolute yardstick for both sides: a threshold relative to each file's own peak
            // would flag the loudest leftover of a window that holds no impulse at all.
            var threshold = Peak(sourcePcm) * ThresholdRatio;
            var sourceImpulses = Impulses(sourcePcm, rate, threshold);
            var expectedTimes = sourceImpulses
                .Select(t => MapToOutput(t, mapping))
                .Where(v => v.HasValue)
                .Select(v => v!.Value)
                .ToArray();

            var outputPcm = DecodeMono(tools, output, outStream.Index, rate);
            var found = Impulses(outputPcm, rate, threshold);

            double median = 0, worst = 0;
            var note = "";
            if (expectedTimes.Length == 0)
            {
                note = found.Length == 0
                    ? "no impulses in the kept audio; position check skipped"
                    : $"source kept window has no impulses but the output has {found.Length}";
            }
            else if (found.Length != expectedTimes.Length)
            {
                note = $"impulse count differs";
                worst = double.PositiveInfinity;
            }
            else
            {
                var residuals = found.Select((t, i) => t - expectedTimes[i]).ToArray();
                var sorted = (double[])residuals.Clone();
                Array.Sort(sorted);
                median = sorted[sorted.Length / 2];
                worst = residuals.Max(r => Math.Abs(r - median));
            }
            results.Add(new AudioTrackVerification(plan.Stream.Index, plan.Stream.CodecName ?? "?", expectedSamples,
                                                   outputPcm.Length, expectedTimes.Length, found.Length,
                                                   median * 1000, worst * 1000, note));
        }
        return results;
    }

    /// <summary>(source seconds start, source seconds end, output seconds start) per kept range.</summary>
    static (double SourceStart, double SourceEnd, double OutputStart)[] BuildMapping(VideoIndex index, IReadOnlyList<FrameRange> keeps)
    {
        var list = new List<(double, double, double)>();
        double cursor = 0;
        foreach (var range in keeps)
        {
            var start = (double)(index.Boundaries[(int)range.Begin] - index.FirstPts) * index.TimeBaseNumerator / index.TimeBaseDenominator;
            var end = (double)(index.Boundaries[(int)range.End] - index.FirstPts) * index.TimeBaseNumerator / index.TimeBaseDenominator;
            list.Add((start, end, cursor));
            cursor += end - start;
        }
        return list.ToArray();
    }

    static double? MapToOutput(double sourceSeconds, (double SourceStart, double SourceEnd, double OutputStart)[] mapping)
    {
        foreach (var (a, b, o) in mapping)
            if (sourceSeconds >= a - 1e-9 && sourceSeconds < b - 1e-9) return o + (sourceSeconds - a);
        return null;
    }

    static float[] DecodeMono(FFmpegTools tools, string path, long streamIndex, long rate)
    {
        var data = tools.FFmpegBytes(["-v", "error", "-i", path, "-map", $"0:{streamIndex}", "-ac", "1",
                                      "-ar", rate.ToString(CultureInfo.InvariantCulture), "-f", "f32le", "-"]);
        var samples = new float[data.Length / 4];
        if (data.Length % 4 != 0) throw new PipelineException("partial PCM frame from ffmpeg");
        Buffer.BlockCopy(data, 0, samples, 0, samples.Length * 4);
        return samples;
    }

    static float Peak(float[] samples)
    {
        float peak = 0;
        foreach (var value in samples) peak = Math.Max(peak, Math.Abs(value));
        return peak;
    }

    static double[] Impulses(float[] samples, long rate, double threshold)
    {
        if (samples.Length == 0 || threshold <= 0) return Array.Empty<double>();
        var result = new List<double>();
        var i = 0;
        while (i < samples.Length)
        {
            if (Math.Abs(samples[i]) < threshold) { i++; continue; }
            var best = i;
            var j = i;
            while (j < samples.Length && Math.Abs(samples[j]) >= threshold * 0.25)
            {
                if (Math.Abs(samples[j]) > Math.Abs(samples[best])) best = j;
                j++;
            }
            result.Add((double)best / rate);
            i = j + 1;
        }
        return result.ToArray();
    }
}
