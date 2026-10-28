using System.Diagnostics;

namespace SmartCut.Core;

public sealed record ToolRunResult(int ExitCode, string StdOut, string StdErr);

/// <summary>Locates and runs the ffmpeg/ffprobe binaries. No native bindings: the pipeline
/// drives official builds as child processes so any stock ffmpeg works.</summary>
public sealed class FFmpegTools
{
    public string FfmpegPath { get; }
    public string FfprobePath { get; }
    public string Version { get; private set; } = "unknown";

    FFmpegTools(string ffmpeg, string ffprobe)
    {
        FfmpegPath = ffmpeg;
        FfprobePath = ffprobe;
    }

    public static FFmpegTools Locate(string? directory)
    {
        var names = OperatingSystem.IsWindows() ? new[] { "ffmpeg.exe", "ffprobe.exe" } : new[] { "ffmpeg", "ffprobe" };
        string? ffmpeg = null, ffprobe = null;
        var roots = new List<string>();
        if (!string.IsNullOrWhiteSpace(directory)) roots.Add(directory!);
        var pathEnv = Environment.GetEnvironmentVariable("PATH") ?? string.Empty;
        roots.AddRange(pathEnv.Split(Path.PathSeparator, StringSplitOptions.RemoveEmptyEntries));
        foreach (var root in roots)
        {
            if (!Directory.Exists(root)) continue;
            ffmpeg ??= FirstExisting(root, names[0]);
            ffprobe ??= FirstExisting(root, names[1]);
            if (ffmpeg is not null && ffprobe is not null) break;
        }
        if (ffmpeg is null || ffprobe is null)
            throw new PipelineException(
                $"ffmpeg/ffprobe not found (looked in {(string.IsNullOrWhiteSpace(directory) ? "PATH" : directory)}). Pass --ffmpeg-dir DIR.");
        var tools = new FFmpegTools(ffmpeg, ffprobe);
        tools.Version = tools.Run(tools.FfmpegPath, ["-version"]).StdOut.Split('\n')[0].Trim();
        return tools;
    }

    static string? FirstExisting(string directory, string name)
    {
        try
        {
            var candidate = Path.Combine(directory, name);
            return File.Exists(candidate) ? candidate : null;
        }
        catch (ArgumentException)
        {
            return null;
        }
    }

    public ToolRunResult Run(string executable, IReadOnlyList<string> arguments, bool expectSuccess = true)
    {
        var start = new ProcessStartInfo
        {
            FileName = executable,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
            CreateNoWindow = true,
            StandardOutputEncoding = System.Text.Encoding.UTF8,
            StandardErrorEncoding = System.Text.Encoding.UTF8,
        };
        foreach (var a in arguments) start.ArgumentList.Add(a);
        using var process = Process.Start(start) ?? throw new PipelineException($"cannot start {executable}");
        // Both pipes must drain concurrently: ffmpeg writes its report on stderr and the payload on
        // stdout, so reading one to EOF before the other deadlocks once either buffer fills.
        var stdout = process.StandardOutput.ReadToEndAsync();
        var stderr = process.StandardError.ReadToEndAsync();
        process.WaitForExit();
        var result = new ToolRunResult(process.ExitCode, stdout.Result, stderr.Result);
        if (expectSuccess && result.ExitCode != 0)
            throw new PipelineException($"{Path.GetFileName(executable)} exit {result.ExitCode}: " +
                                        $"{string.Join(' ', arguments)}\n{Tail(result.StdErr)}");
        return result;
    }

    public ToolRunResult FFprobe(IReadOnlyList<string> arguments) => Run(FfprobePath, arguments);
    public ToolRunResult FFmpeg(IReadOnlyList<string> arguments, bool expectSuccess = true) => Run(FfmpegPath, arguments, expectSuccess);

    /// <summary>Runs ffmpeg and returns raw stdout bytes. Decoded samples and framemd5 payloads go
    /// through here: reading them as text would corrupt the binary.</summary>
    public byte[] FFmpegBytes(IReadOnlyList<string> arguments)
    {
        var start = new ProcessStartInfo
        {
            FileName = FfmpegPath,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
            CreateNoWindow = true,
        };
        foreach (var a in arguments) start.ArgumentList.Add(a);
        using var process = Process.Start(start) ?? throw new PipelineException($"cannot start {FfmpegPath}");
        var stderrTask = process.StandardError.ReadToEndAsync();
        using var memory = new MemoryStream();
        process.StandardOutput.BaseStream.CopyTo(memory);
        var stderr = stderrTask.Result;
        process.WaitForExit();
        if (process.ExitCode != 0)
            throw new PipelineException($"ffmpeg exit {process.ExitCode}: {string.Join(' ', arguments)}\n{Tail(stderr)}");
        return memory.ToArray();
    }

    static string Tail(string text)
    {
        text = text.Trim();
        return text.Length <= 1200 ? text : text[^1200..];
    }
}
