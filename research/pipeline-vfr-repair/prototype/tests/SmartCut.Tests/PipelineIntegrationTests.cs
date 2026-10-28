using System.Text.Json;
using SmartCut.Core;

namespace SmartCut.Tests;

/// <summary>End-to-end runs against the repository's fixture suite, judged by the built-in
/// Verifier and cross-checked against the C++ engine's own per-case report. Needs ffmpeg and the
/// fixtures; both are located from the environment so the suite runs anywhere the tests ship.</summary>
public class PipelineIntegrationTests
{
    static readonly Dictionary<string, (string Label, FrameRange[] Keeps)> Cuts = new()
    {
        ["full"] = ("full", [new FrameRange(0, 240)]),
        ["aligned"] = ("aligned", [new FrameRange(24, 216)]),
        ["both_edges"] = ("both_edges", [new FrameRange(13, 227)]),
        ["head_only"] = ("head_only", [new FrameRange(13, 216)]),
        ["tail_only"] = ("tail_only", [new FrameRange(24, 227)]),
        ["single_frame"] = ("single_frame", [new FrameRange(23, 24)]),
        ["cross_idr"] = ("cross_idr", [new FrameRange(23, 25)]),
        ["multiple"] = ("multiple", [new FrameRange(1, 29), new FrameRange(47, 121), new FrameRange(191, 240)]),
        ["adjacent"] = ("adjacent", [new FrameRange(0, 24), new FrameRange(24, 48)]),
        ["adjacent_inside"] = ("adjacent_inside", [new FrameRange(0, 13), new FrameRange(13, 24)]),
    };

    static readonly string[] Fixtures =
    [
        "closed_b3_id0", "closed_b3_id3", "baseline", "open_gop", "mixed_open_idr", "high10",
        "multi_slice", "ntsc", "vfr", "offset", "fragmented", "fullrange_sar", "nvenc",
    ];

    public static IEnumerable<object[]> Cases =>
        (from fixture in Fixtures from cut in Cuts.Keys select new object[] { fixture, cut });

    static string? Suite =>
        Directory.Exists(Environment.GetEnvironmentVariable("SMARTCUT_FIXTURE_DIR") ?? "")
            ? Environment.GetEnvironmentVariable("SMARTCUT_FIXTURE_DIR")!
            : FindSuite();

    static string? FindSuite()
    {
        var directory = new DirectoryInfo(AppContext.BaseDirectory);
        while (directory is not null)
        {
            var candidate = Path.Combine(directory.FullName, "h264", "test-data");
            if (Directory.Exists(candidate))
            {
                var runs = Directory.GetDirectories(candidate)
                                   .Where(run => File.Exists(Path.Combine(run, "source-closed_b3_id0.mp4")))
                                   .OrderBy(run => run)
                                   .ToArray();
                if (runs.Length > 0) return runs[^1];
            }
            directory = directory.Parent;
        }
        return null;
    }

    /// <summary>Missing tooling fails the case: a silently returning integration test is a false green.</summary>
    static (FFmpegTools Tools, string Suite, string Source) Require(string fixture)
    {
        var suite = Suite;
        FFmpegTools? tools = null;
        try { tools = FFmpegTools.Locate(Environment.GetEnvironmentVariable("SMARTCUT_FFMPEG_DIR")); }
        catch (PipelineException) { }
        if (tools is null)
            throw new SkipOrFail("ffmpeg/ffprobe not found; set SMARTCUT_FFMPEG_DIR");
        if (suite is null)
            throw new SkipOrFail("fixture suite not found; set SMARTCUT_FIXTURE_DIR");
        var source = Path.Combine(suite, $"source-{fixture}.mp4");
        if (!File.Exists(source)) throw new SkipOrFail($"missing fixture {source}");
        return (tools, suite, source);
    }

    internal sealed class SkipOrFail(string message) : Exception("integration prerequisites: " + message);

    [Theory]
    [MemberData(nameof(Cases))]
    [Trait("Category", "Integration")]
    public void PipelineMatchesItsOwnPlanAndTheEngineCounts(string fixture, string cut)
    {
        var (tools, suite, source) = Require(fixture);
        using var work = new Workspace();
        var output = Path.Combine(work.Root, "final.mp4");
        var options = new Options
        {
            Input = source,
            Output = output,
            WorkDirectory = work.Root,
            Keeps = Cuts[cut].Keeps,
            IncludeAudio = false,
        };
        var probe = new FFprobeReader(tools).Read(source);
        var index = VideoIndex.Build(tools, source, probe);
        var keeps = Planner.Normalize(index, options.Keeps);
        var plan = Planner.PlanVideo(index, keeps);
        var result = new PipelineRunner(options, tools).Run(index, plan, probe);
        var verification = Verifier.Verify(tools, source, output, index, plan, result.Audio);

        Assert.True(verification.Passed, string.Join("; ", verification.Problems));
        Assert.Equal(verification.CopyFramesChecked, verification.CopyFramesMatching);
        Assert.Equal(plan.CopiedFrames, verification.CopyFramesChecked);

        var engineReport = Path.Combine(suite, $"{fixture}-{cut}-avc3-crf18.json");
        if (File.Exists(engineReport))
        {
            using var document = JsonDocument.Parse(File.ReadAllText(engineReport));
            var root = document.RootElement;
            Assert.Equal(root.GetProperty("copied_frames").GetInt64(), plan.CopiedFrames);
            Assert.Equal(root.GetProperty("encoded_frames").GetInt64(), plan.EncodedFrames);
            Assert.Equal(root.GetProperty("duration_ticks").GetInt64(), plan.TotalDurationTicks);
            Assert.Equal(root.GetProperty("idr_epochs").GetInt32(), index.Epochs.Count);
        }
    }

    static readonly (string Fixture, int Tracks)[] AudioVariants =
    [
        ("closed_b3_id0", 1), ("ntsc", 1), ("vfr", 1), ("closed_b3_id0", 2),
    ];

    public static IEnumerable<object[]> AudioCases =>
        from fixture in AudioVariants
        from cut in new[] { "full", "both_edges", "multiple", "head_only", "adjacent", "tail_only" }
        select new object[] { fixture.Fixture, fixture.Tracks, cut };

    [Theory]
    [MemberData(nameof(AudioCases))]
    [Trait("Category", "Integration")]
    public void AudioTrackIsTrimmedToTheSameWindowsAsTheVideo(string fixture, int tracks, string cut)
    {
        var (tools, _, source) = Require(fixture);
        using var work = new Workspace();

        // Impulse every 0.16 s: a seam that lands on the wrong audio sample moves an impulse.
        var wav = Path.Combine(work.Root, "click.wav");
        WriteImpulseWav(wav, 13.0, 48_000, 0.16);
        var withAudio = Path.Combine(work.Root, "with-audio.mp4");
        var arguments = new List<string> { "-v", "error", "-y", "-i", source, "-i", wav };
        if (tracks == 2) arguments.AddRange(["-i", wav]);
        arguments.AddRange(["-map", "0:v"]);
        for (var index = 0; index < tracks; index++) arguments.AddRange([$"-map", $"{index + 1}:a"]);
        arguments.AddRange(["-c:v", "copy", "-c:a", "aac", "-b:a", "192k"]);
        var videoEnd = VideoEndSeconds(tools, source);
        arguments.Add("-t");
        arguments.Add(videoEnd.ToString(System.Globalization.CultureInfo.InvariantCulture));
        arguments.Add(withAudio);
        tools.FFmpeg(arguments);

        var output = Path.Combine(work.Root, "final.mp4");
        var options = new Options { Input = withAudio, Output = output, WorkDirectory = work.Root,
                                    Keeps = Cuts[cut].Keeps, Crf = 18 };
        var probe = new FFprobeReader(tools).Read(withAudio);
        var audioIndex = VideoIndex.Build(tools, withAudio, probe);
        var plan = Planner.PlanVideo(audioIndex, Planner.Normalize(audioIndex, options.Keeps));
        var result = new PipelineRunner(options, tools).Run(audioIndex, plan, probe);
        var verification = Verifier.Verify(tools, withAudio, output, audioIndex, plan, result.Audio);

        Assert.True(verification.Passed, string.Join("; ", verification.Problems));
        Assert.Equal(tracks, verification.Audio!.Count);
        foreach (var audio in verification.Audio)
        {
            Assert.Equal(audio.EventsExpected, audio.EventsFound);
            Assert.True(audio.EventsExpected > 0, "the kept window must contain at least one impulse");
            Assert.True(audio.MaxResidualMs < 20, $"impulse drift {audio.MaxResidualMs} ms");
        }

        var streams = new FFprobeReader(tools).Read(output).Streams;
        Assert.Equal(1, streams.Count(s => s.CodecType == "video"));
        Assert.Equal(tracks, streams.Count(s => s.CodecType == "audio"));
    }

    static double VideoEndSeconds(FFmpegTools tools, string source)
    {
        var run = tools.FFprobe(["-v", "error", "-select_streams", "v:0", "-show_entries",
                                 "packet=pts,duration", "-show_entries", "stream=time_base", "-of", "json", source]);
        using var document = JsonDocument.Parse(run.StdOut);
        var (numerator, denominator) = ProbeStream.ParseRational(document.RootElement.GetProperty("streams")[0]
                                                                     .GetProperty("time_base").GetString());
        double best = 0;
        foreach (var packet in document.RootElement.GetProperty("packets").EnumerateArray())
        {
            var duration = packet.TryGetProperty("duration", out var value) && value.ValueKind != JsonValueKind.Null
                ? value.GetInt64() : 0;
            best = Math.Max(best, (packet.GetProperty("pts").GetInt64() + duration) * numerator / (double)denominator);
        }
        return best;
    }

    [Fact]
    [Trait("Category", "Integration")]
    public void ExistingOutputIsNeverOverwritten()
    {
        var (tools, _, source) = Require("closed_b3_id0");
        using var work = new Workspace();
        var output = Path.Combine(work.Root, "taken.mp4");
        File.WriteAllText(output, "SENTINEL");
        var options = new Options { Input = source, Output = output, WorkDirectory = work.Root,
                                    Keeps = [new FrameRange(0, 24)], IncludeAudio = false };
        var probe = new FFprobeReader(tools).Read(source);
        var index = VideoIndex.Build(tools, source, probe);
        var plan = Planner.PlanVideo(index, Planner.Normalize(index, options.Keeps));

        var failure = Assert.Throws<PipelineException>(() => new PipelineRunner(options, tools).Run(index, plan, probe));
        Assert.Contains("already exists", failure.Message);
        Assert.Equal("SENTINEL", File.ReadAllText(output));
    }

    [Fact]
    [Trait("Category", "Integration")]
    public void NonH264InputIsRejected()
    {
        var (tools, _, source) = Require("closed_b3_id0");
        using var work = new Workspace();
        var mpeg4 = Path.Combine(work.Root, "mpeg4.mp4");
        tools.FFmpeg(["-v", "error", "-y", "-i", source, "-c:v", "mpeg4", "-q:v", "2", "-an", mpeg4]);
        var probe = new FFprobeReader(tools).Read(mpeg4);
        var error = Assert.Throws<PipelineException>(() => VideoIndex.Build(tools, mpeg4, probe));
        Assert.Contains("H.264", error.Message);
    }

    [Fact]
    [Trait("Category", "Integration")]
    public void SecondVideoStreamIsRejected()
    {
        var (tools, _, source) = Require("closed_b3_id0");
        using var work = new Workspace();
        var dual = Path.Combine(work.Root, "two-video.mp4");
        tools.FFmpeg(["-v", "error", "-y", "-i", source, "-map", "0:v:0", "-map", "0:v:0", "-c", "copy", dual]);
        var probe = new FFprobeReader(tools).Read(dual);
        var error = Assert.Throws<PipelineException>(() => VideoIndex.Build(tools, dual, probe));
        Assert.Contains("exactly one video stream", error.Message);
    }

    /// <summary>Temporary directory for one case, removed with the case.</summary>
    internal sealed class Workspace : IDisposable
    {
        public string Root { get; } = Path.Combine(Path.GetTempPath(), "smartcut-tests", Guid.NewGuid().ToString("N"));

        public Workspace() => Directory.CreateDirectory(Root);

        public void Dispose()
        {
            try { Directory.Delete(Root, recursive: true); }
            catch (IOException) { }
            catch (UnauthorizedAccessException) { }
        }
    }

    static void WriteImpulseWav(string path, double seconds, int rate, double period)
    {
        var samples = (int)(seconds * rate);
        using var file = File.Create(path);
        using var writer = new BinaryWriter(file);
        var dataBytes = samples * 4;
        Span<byte> header = stackalloc byte[44];
        System.Text.Encoding.ASCII.GetBytes("RIFF").CopyTo(header);
        Write(header, 4, dataBytes + 36);
        System.Text.Encoding.ASCII.GetBytes("WAVEfmt ").CopyTo(header.Slice(8));
        Write(header, 16, 16);
        header[20] = 1; header[22] = 2;
        Write(header, 24, rate);
        Write(header, 28, rate * 4);
        header[32] = 4; header[34] = 16;
        System.Text.Encoding.ASCII.GetBytes("data").CopyTo(header.Slice(36));
        Write(header, 40, dataBytes);
        writer.Write(header);
        var interval = (int)Math.Round(period * rate);
        for (var i = 0; i < samples; i++)
        {
            var value = i % interval == 0 ? (short)29445 : (short)0;
            writer.Write(value);
            writer.Write(value);
        }
    }

    static void Write(Span<byte> buffer, int offset, int value) =>
        BitConverter.TryWriteBytes(buffer.Slice(offset), value);
}
