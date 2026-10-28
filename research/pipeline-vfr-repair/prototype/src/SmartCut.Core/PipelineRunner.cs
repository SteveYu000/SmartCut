using System.Diagnostics;
using System.Globalization;
using System.Text;
using System.Text.Json;

namespace SmartCut.Core;

public sealed record Segment(PlanUnit Unit, string Path, IReadOnlyList<string> Arguments);

public sealed class RunResult
{
    public required string Output { get; init; }
    public required string WorkDirectory { get; init; }
    public required IReadOnlyList<Segment> Segments { get; init; }
    public required VideoPlan Plan { get; init; }
    public required IReadOnlyList<AudioTrackPlan> Audio { get; init; }
    public required IReadOnlyList<string> Warnings { get; init; }
    public required double ElapsedSeconds { get; init; }
    public VerificationReport? Verification { get; set; }

    public string ToJson(VideoIndex index, string ffmpegVersion, string toolVersion)
    {
        var units = Plan.Units.Select(u => new Dictionary<string, object?>
        {
            ["mode"] = u.Kind == UnitKind.Copy ? "copy" : "encode",
            ["begin"] = u.DisplayBegin,
            ["end"] = u.DisplayEnd,
            ["packet_begin"] = u.PacketBegin,
            ["packet_end"] = u.PacketEnd,
            ["output_start_ticks"] = u.OutputStartTicks,
            ["duration_ticks"] = u.DurationTicks,
        });
        var payload = new Dictionary<string, object?>
        {
            ["status"] = Verification is null ? "completed" : Verification.Passed ? "verified" : "verification_failed",
            ["tool"] = "smartcut-pipeline",
            ["tool_version"] = toolVersion,
            ["ffmpeg"] = ffmpegVersion,
            ["input"] = index.InputPath,
            ["output"] = Output.Length == 0 ? null : Output,
            ["input_frames"] = index.FrameCount,
            ["idr_epochs"] = index.Epochs.Count,
            ["non_idr_key_packets"] = index.NonIdrKeySamples,
            ["time_base"] = new[] { index.TimeBaseNumerator, index.TimeBaseDenominator },
            ["segments"] = Segments.Count,
            ["copied_frames"] = Plan.CopiedFrames,
            ["encoded_frames"] = Plan.EncodedFrames,
            ["expected_frames"] = Plan.CopiedFrames + Plan.EncodedFrames,
            ["duration_ticks"] = Plan.TotalDurationTicks,
            ["audio_tracks"] = Audio.Select(a => new Dictionary<string, object?>
            {
                ["stream_index"] = a.Stream.Index,
                ["codec"] = a.Stream.CodecName,
                ["ranges"] = a.Ranges.Select(r => new Dictionary<string, object?>
                {
                    ["start_sample"] = r.StartSample,
                    ["end_sample"] = r.EndSample,
                    ["start_seconds"] = Math.Round(r.StartSeconds, 6),
                    ["end_seconds"] = Math.Round(r.EndSeconds, 6),
                    ["sample_exact"] = r.Exact,
                }).ToList(),
            }).ToList(),
            ["elapsed_seconds"] = Math.Round(ElapsedSeconds, 3),
            ["warnings"] = Warnings.ToList(),
            ["units"] = units,
            ["verification"] = Verification?.ToJsonPayload() ?? "not_requested",
        };
        return JsonSerializer.Serialize(payload, new JsonSerializerOptions { WriteIndented = true });
    }
}

/// <summary>Executes a plan with stock ffmpeg only: one child process per unit, the concat demuxer
/// for the join, and an atrim/asplit/concat graph for audio. Copy segments are whole IDR epochs, so
/// their packets are contiguous in decode order and -frames:v counts exactly the wanted display frames.</summary>
public sealed class PipelineRunner(Options options, FFmpegTools tools)
{
    const string X264Params = "scenecut=0:open-gop=0:bframes=0:keyint=100000";
    string _work = "";

    /// <summary>Copy segments always read a video-only demux of the input, never the input itself.
    /// Measured here: an input that carries an audio stream makes ffmpeg's seek discard 24 leading
    /// video packets, and those discards are charged against -frames:v, so a whole GOP goes missing.
    /// A plain -map 0:v:0 -c copy demux re-bases presentation time to zero, which is exactly the
    /// relative addressing the plan already uses, so segment arguments do not change.</summary>
    string DemuxVideoSource(string input)
    {
        var path = Path.Combine(_work, "source-video.mp4");
        tools.FFmpeg(["-v", "error", "-y", "-i", input, "-map", "0:v:0", "-c", "copy", "-an", path]);
        return path;
    }

    public RunResult Run(VideoIndex index, VideoPlan plan, ProbeResult probe)
    {
        var started = Stopwatch.StartNew();
        var warnings = new List<string>(plan.Warnings);
        _work = CreateWorkDirectory();
        var segments = new List<Segment>();
        try
        {
            var videoSource = DemuxVideoSource(index.InputPath);
            for (var i = 0; i < plan.Units.Count; i++)
                segments.Add(BuildSegment(index, plan.Units[i], i, videoSource));

            var listPath = Path.Combine(_work, "list.txt");
            var builder = new StringBuilder();
            long previousBoundaryUs = 0;
            foreach (var segment in segments)
            {
                builder.Append("file '").Append(Path.GetFileName(segment.Path)).Append("'\n");
                // Concat offsets the next file by this value. Round absolute
                // boundaries, so fractional microseconds do not accumulate.
                var nextBoundaryUs = ToMicroseconds(segment.Unit.OutputStartTicks + segment.Unit.DurationTicks, index);
                var duration = (nextBoundaryUs - previousBoundaryUs) / 1_000_000m;
                builder.Append("duration ").Append(duration.ToString("0.000000", CultureInfo.InvariantCulture)).Append('\n');
                previousBoundaryUs = nextBoundaryUs;
            }
            File.WriteAllText(listPath, builder.ToString(), new UTF8Encoding(false));
            var timelinePath = WriteTimelineFilter(index, plan);

            var videoOnly = Path.Combine(_work, "video.mp4");
            tools.FFmpeg(["-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", listPath,
                          "-c", "copy", "-tag:v", "avc3", "-an", "-/bsf:v", timelinePath, "-video_track_timescale", index.TimeBaseDenominator.ToString(CultureInfo.InvariantCulture),
                          "-movflags", "+faststart", videoOnly]);

            IReadOnlyList<AudioTrackPlan> audioTracks = Array.Empty<AudioTrackPlan>();
            var current = videoOnly;
            if (options.IncludeAudio)
            {
                audioTracks = Planner.PlanAudio(probe, index, plan.NormalizedKeeps);
                var usable = audioTracks.Where(t => t.Ranges.Count > 0).ToArray();
                if (usable.Length < audioTracks.Count)
                    warnings.Add($"{audioTracks.Count - usable.Length} audio track(s) lack a usable time base and were dropped");
                if (usable.Length == 0)
                    warnings.Add("no audio track in the input; output contains video only");
                else
                {
                    var withAudio = Path.Combine(_work, "with-audio.mp4");
                    MuxAudio(index.InputPath, videoOnly, usable, withAudio, warnings, index, timelinePath);
                    current = withAudio;
                }
            }
            else
            {
                warnings.Add("--no-audio: output contains video only");
            }

            var staged = Path.Combine(_work, "output.mp4");
            if (!ReferenceEquals(current, staged))
            {
                File.Copy(current, staged, overwrite: false);
                File.Delete(current);
            }
            MoveIntoPlace(staged, options.Output);
            if (!options.KeepWork) TryDelete(_work);

            return new RunResult
            {
                Output = options.Output,
                WorkDirectory = _work,
                Segments = segments,
                Plan = plan,
                Audio = audioTracks,
                Warnings = warnings,
                ElapsedSeconds = started.Elapsed.TotalSeconds,
            };
        }
        catch
        {
            if (!options.KeepWork) TryDelete(_work);
            throw;
        }
        finally
        {
            started.Stop();
        }
    }

    Segment BuildSegment(VideoIndex index, PlanUnit unit, int sequence, string videoSource)
    {
        var path = Path.Combine(_work, $"unit{sequence:D3}.mp4");
        var args = new List<string> { "-v", "error", "-y" };
        if (unit.Kind == UnitKind.Copy)
        {
            var seek = (decimal)(index.DecodeOrder[unit.PacketBegin].Pts - index.FirstPts)
                       * index.TimeBaseNumerator / index.TimeBaseDenominator;
            args.Add("-ss");
            args.Add(seek.ToString(CultureInfo.InvariantCulture));
            args.AddRange(["-i", videoSource, "-frames:v", unit.PacketCount.ToString(CultureInfo.InvariantCulture),
                           "-c", "copy"]);
        }
        else
        {
            args.AddRange(["-i", videoSource, "-vf",
                           $"select='between(n\\,{unit.DisplayBegin}\\,{unit.DisplayEnd - 1})'",
                           "-fps_mode", "passthrough", "-enc_time_base", $"{index.TimeBaseNumerator}/{index.TimeBaseDenominator}",
                           "-c:v", "libx264", "-preset", options.Preset,
                           "-crf", options.Crf.ToString(CultureInfo.InvariantCulture),
                           "-bf", "0", "-g", "100000", "-x264-params", X264Params,
                           "-pix_fmt", EncoderPixelFormat(index.Stream.PixFmt)]);
            AppendColorArguments(args, index.Stream);
        }
        args.AddRange(["-video_track_timescale", index.TimeBaseDenominator.ToString(CultureInfo.InvariantCulture), "-an", path]);
        tools.FFmpeg(args);
        return new Segment(unit, path, args);
    }

    void MuxAudio(string source, string video, IReadOnlyList<AudioTrackPlan> tracks, string destination, List<string> warnings, VideoIndex index, string timelinePath)
    {
        var graph = new StringBuilder();
        var maps = new List<string>();
        foreach (var track in tracks)
        {
            var ranges = track.Ranges;
            if (track.AnyInexact)
                warnings.Add($"audio track {track.Stream.Index}: cut points are not on sample boundaries; trimmed by seconds");
            var outLabel = $"aout{track.Stream.Index}";
            var selector = $"[1:a:{track.AudioOrdinal}]";
            var parts = new List<string>();
            for (var i = 0; i < ranges.Count; i++)
            {
                var range = ranges[i];
                var input = ranges.Count == 1 ? selector : $"[spk{track.Stream.Index}_{i}]";
                var trim = range.Exact && range.StartSample is not null && range.EndSample is not null
                    ? $"atrim=start_sample={range.StartSample}:end_sample={range.EndSample}"
                    : $"atrim=start={Num(range.StartSeconds)}:end={Num(range.EndSeconds)}";
                var label = $"[at{track.Stream.Index}_{i}]";
                if (ranges.Count > 1 && i == 0)
                    graph.Append(selector).Append("asplit=").Append(ranges.Count)
                         .Append(':').Append(string.Join("", Enumerable.Range(0, ranges.Count)
                             .Select(k => $"[spk{track.Stream.Index}_{k}]"))).Append(';');
                graph.Append(input).Append(trim).Append(",asetpts=N/SR/TB").Append(label).Append(';');
                parts.Add(label);
            }
            if (parts.Count == 1)
            {
                maps.Add(parts[0]);
                continue;
            }
            graph.Append(string.Concat(parts)).Append($"concat=n={parts.Count}:v=0:a=1[{outLabel}];");
            maps.Add($"[{outLabel}]");
        }
        if (graph.Length > 0) graph.Length--;

        var args = new List<string> { "-v", "error", "-y", "-i", video, "-i", source,
                                      "-filter_complex", graph.ToString(), "-map", "0:v:0" };
        foreach (var map in maps) args.AddRange(["-map", map]);
        args.AddRange(["-c:v", "copy", "-c:a", options.AudioCodec]);
        if (options.AudioBitrate is not null) args.AddRange(["-b:a", options.AudioBitrate]);
        args.AddRange(["-/bsf:v", timelinePath, "-video_track_timescale", index.TimeBaseDenominator.ToString(CultureInfo.InvariantCulture), "-movflags", "+faststart", destination]);
        tools.FFmpeg(args);
    }

    static string Num(double value) => value.ToString("G17", CultureInfo.InvariantCulture);

    // Keep the VCL packet order and presentation times. Give every packet a
    // monotonically increasing decode slot and enough common reorder delay.
    // This is the same scheduling policy as h264/src/engine.cpp, expressed
    // through stock setts rather than a native packet writer.
    string WriteTimelineFilter(VideoIndex index, VideoPlan plan)
    {
        var slots = new List<long>();
        var durations = new List<long>();
        long delay = 0;
        foreach (var unit in plan.Units)
        {
            var begin = index.Boundaries[(int)unit.DisplayBegin];
            for (var n = 0; n < unit.FrameCount; n++)
            {
                var slot = unit.OutputStartTicks + index.Boundaries[(int)unit.DisplayBegin + n] - begin;
                var rank = unit.Kind == UnitKind.Copy
                    ? index.DecodeOrder[unit.PacketBegin + n].DisplayRank
                    : (int)unit.DisplayBegin + n;
                var pts = unit.OutputStartTicks + index.Boundaries[rank] - begin;
                delay = Math.Max(delay, slot - pts);
                slots.Add(slot);
                durations.Add(index.Boundaries[rank + 1] - index.Boundaries[rank]);
            }
        }
        var end = (int)plan.Units[^1].DisplayEnd;
        durations[^1] = index.Boundaries[end] - index.Boundaries[end - 1];
        var expression = $"setts=time_base={index.TimeBaseNumerator}/{index.TimeBaseDenominator}:prescale=1:pts=PTS:dts='({Lookup(slots)})-{delay}':duration='{Lookup(durations)}'";
        var path = Path.Combine(_work, "timeline.bsf");
        File.WriteAllText(path, expression, new UTF8Encoding(false));
        return path;
    }

    // Collapse arithmetic runs, then use a balanced lookup tree. Loading the
    // filter from a file avoids the Windows command-line length limit.
    static string Lookup(IReadOnlyList<long> values)
    {
        var runs = new List<(int Begin, int End, long Value, long Step)>();
        for (var begin = 0; begin < values.Count;)
        {
            var step = begin + 1 < values.Count ? values[begin + 1] - values[begin] : 0;
            var end = Math.Min(begin + 2, values.Count);
            while (end < values.Count && values[end] - values[end - 1] == step) end++;
            runs.Add((begin, end, values[begin], step));
            begin = end;
        }
        string Tree(int lo, int hi)
        {
            if (hi - lo == 1)
            {
                var r = runs[lo];
                return r.Step == 0 ? r.Value.ToString(CultureInfo.InvariantCulture)
                    : $"({r.Value}+(N-{r.Begin})*{r.Step})";
            }
            var mid = (lo + hi) / 2;
            return $"if(lt(N,{runs[mid].Begin}),{Tree(lo, mid)},{Tree(mid, hi)})";
        }
        return Tree(0, runs.Count);
    }

    static long ToMicroseconds(long ticks, VideoIndex index) => checked((long)decimal.Round(
        (decimal)ticks * index.TimeBaseNumerator * 1_000_000m / index.TimeBaseDenominator,
        0, MidpointRounding.AwayFromZero));

    static void AppendColorArguments(List<string> args, ProbeStream stream)
    {
        Add(args, "-color_range", stream.ColorRange);
        Add(args, "-colorspace", stream.ColorSpace);
        Add(args, "-color_primaries", stream.ColorPrimaries);
        Add(args, "-color_trc", stream.ColorTransfer);
        Add(args, "-sar", stream.SampleAspectRatio);
    }

    static void Add(List<string> args, string flag, string? value)
    {
        if (!string.IsNullOrWhiteSpace(value) && value != "0:1") { args.Add(flag); args.Add(value!); }
    }

    static string EncoderPixelFormat(string? source) => source switch
    {
        "yuvj420p" => "yuv420p",
        null or "" => "yuv420p",
        _ => source,
    };

    void MoveIntoPlace(string staged, string target)
    {
        if (File.Exists(target))
            throw new PipelineException($"output already exists; nothing was overwritten: {target}");
        try
        {
            File.Move(staged, target, overwrite: false);
        }
        catch (IOException)
        {
            File.Copy(staged, target, overwrite: false);
            File.Delete(staged);
        }
    }

    string CreateWorkDirectory()
    {
        var root = options.WorkDirectory is null
            ? Path.Combine(Path.GetTempPath(), "smartcut-pipeline")
            : options.WorkDirectory;
        Directory.CreateDirectory(root);
        var work = Path.Combine(root, "run-" + DateTime.UtcNow.ToString("yyyyMMdd-HHmmss-fffffff"));
        Directory.CreateDirectory(work);
        return work;
    }

    static void TryDelete(string path)
    {
        try { Directory.Delete(path, recursive: true); }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }
    }
}
