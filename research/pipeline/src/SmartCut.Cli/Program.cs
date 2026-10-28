using System.Globalization;
using System.Text.Json;
using SmartCut.Core;

const string ToolVersion = "0.1.0";

try
{
    var options = Parse(args);
    var tools = FFmpegTools.Locate(options.FfmpegDirectory);
    var probe = new FFprobeReader(tools).Read(options.Input);
    var index = VideoIndex.Build(tools, options.Input, probe);
    var keeps = Planner.Normalize(index, options.Keeps);
    var plan = Planner.PlanVideo(index, keeps);

    if (options.Analyze)
    {
        var preview = new RunResult
        {
            Output = options.Output ?? "",
            WorkDirectory = "",
            Segments = Array.Empty<Segment>(),
            Plan = plan,
            Audio = options.IncludeAudio ? Planner.PlanAudio(probe, index, keeps) : Array.Empty<AudioTrackPlan>(),
            Warnings = plan.Warnings,
            ElapsedSeconds = 0,
        };
        Emit(preview.ToJson(index, tools.Version, ToolVersion), options.Report);
        return 0;
    }

    var result = new PipelineRunner(options, tools).Run(index, plan, probe);
    if (options.Verify)
        result.Verification = Verifier.Verify(tools, options.Input, result.Output, index, plan, result.Audio);
    Console.Error.WriteLine($"smartcut-pipeline: {index.Describe()}");
    foreach (var warning in result.Warnings) Console.Error.WriteLine($"warning: {warning}");
    if (result.Verification is { } verification)
    {
        foreach (var problem in verification.Problems) Console.Error.WriteLine($"verify: {problem}");
        Console.Error.WriteLine(verification.Passed
            ? $"verify: pass ({verification.CopyFramesMatching}/{verification.CopyFramesChecked} copied frames bit-exact, " +
              $"{verification.Frames} frames, min encoded PSNR {verification.MinEncodedRegionPsnr?.ToString("F2", CultureInfo.InvariantCulture) ?? "n/a"} dB)"
            : "verify: fail");
    }
    Emit(result.ToJson(index, tools.Version, ToolVersion), options.Report);
    return result.Verification is null or { Passed: true } ? 0 : 1;
}
catch (Exception exception)
{
    Console.Error.WriteLine($"ERROR: {exception.Message}");
    return 1;
}

static void Emit(string json, string? report)
{
    Console.Out.WriteLine(json);
    if (report is not null)
    {
        if (File.Exists(report)) throw new PipelineException($"report already exists: {report}");
        File.WriteAllText(report, json);
    }
}

static Options Parse(string[] args)
{
    string? input = null, output = null, report = null, ffmpegDirectory = null, workDirectory = null,
        audioBitrate = null, audioCodec = null, preset = null;
    var keeps = new List<FrameRange>();
    var crf = 18.0;
    var includeAudio = true;
    var verify = false;
    var analyze = false;
    var keepWork = false;

    for (var i = 0; i < args.Length; i++)
    {
        string Value()
        {
            if (++i == args.Length) throw new PipelineException($"missing value for {args[i - 1]}");
            return args[i];
        }

        switch (args[i])
        {
            case "--help" or "-h":
                Console.WriteLine(
                    "Smart-cut an H.264/MP4 video with stock ffmpeg plus an audio track.\n" +
                    "smartcut -i input.mp4 -o output.mp4 --keep 13:227 [--keep A:B ...]\n" +
                    "  --keep A:B          zero-based display frames, A inclusive, B exclusive; repeatable\n" +
                    "  --crf N             boundary quality, 0..51 (default 18)\n" +
                    "  --preset NAME       libx264 preset for boundary units (default medium)\n" +
                    "  --no-audio          write a video-only output\n" +
                    "  --audio-codec NAME  re-encoded audio codec (default aac)\n" +
                    "  --audio-bitrate N   e.g. 192k; omit to let the encoder choose\n" +
                    "  --ffmpeg-dir DIR    folder holding ffmpeg.exe and ffprobe.exe\n" +
                    "  --work-dir DIR      root for the per-run segment directory (default system temp)\n" +
                    "  --keep-work         keep segments after the run\n" +
                    "  --verify            check frames, times, copied-picture hashes, PSNR, strict decode\n" +
                    "  --analyze           print the plan and exit without writing output\n" +
                    "  --report FILE       also write the JSON report to FILE\n" +
                    "Existing outputs and reports are never overwritten.");
                Environment.Exit(0);
                break;
            case "-i" or "--input": input = Value(); break;
            case "-o" or "--output": output = Value(); break;
            case "--report": report = Value(); break;
            case "--ffmpeg-dir": ffmpegDirectory = Value(); break;
            case "--work-dir": workDirectory = Value(); break;
            case "--audio-codec": audioCodec = Value(); break;
            case "--audio-bitrate": audioBitrate = Value(); break;
            case "--preset": preset = Value(); break;
            case "--keep": keeps.Add(ParseRange(Value())); break;
            case "--crf":
                if (!double.TryParse(Value(), CultureInfo.InvariantCulture, out crf) || crf < 0 || crf > 51)
                    throw new PipelineException("--crf must be a number within 0..51");
                break;
            case "--no-audio": includeAudio = false; break;
            case "--verify": verify = true; break;
            case "--analyze": analyze = true; break;
            case "--keep-work": keepWork = true; break;
            default: throw new PipelineException($"unknown argument: {args[i]}");
        }
    }

    if (input is null || keeps.Count == 0 || (output is null && !analyze))
        throw new PipelineException("-i, -o and at least one --keep are required; see --help");
    if (!File.Exists(input)) throw new PipelineException($"input does not exist: {input}");
    if (output is not null && string.Equals(Path.GetFullPath(output), Path.GetFullPath(input), StringComparison.OrdinalIgnoreCase))
        throw new PipelineException("output must differ from input");

    return new Options
    {
        Input = input,
        Output = output ?? "",
        Report = report,
        FfmpegDirectory = ffmpegDirectory,
        WorkDirectory = workDirectory,
        Keeps = keeps,
        Crf = crf,
        Preset = preset ?? "medium",
        IncludeAudio = includeAudio,
        Verify = verify,
        Analyze = analyze,
        AudioBitrate = audioBitrate,
        AudioCodec = audioCodec ?? "aac",
        KeepWork = keepWork,
    };
}

static FrameRange ParseRange(string text)
{
    var parts = text.Split(':');
    if (parts.Length != 2 || !long.TryParse(parts[0], out var begin) || !long.TryParse(parts[1], out var end))
        throw new PipelineException($"--keep expects A:B, got {text}");
    return new FrameRange(begin, end);
}
