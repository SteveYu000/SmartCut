namespace SmartCut.Core;

/// <summary>One --keep range over display-frame indices, inclusive start / exclusive end.</summary>
public readonly record struct FrameRange(long Begin, long End);

public sealed class Options
{
    public required string Input { get; init; }
    public required string Output { get; init; }
    public string? Report { get; init; }
    public string? FfmpegDirectory { get; init; }
    public string? WorkDirectory { get; init; }
    public IReadOnlyList<FrameRange> Keeps { get; init; } = Array.Empty<FrameRange>();
    public double Crf { get; init; } = 18;
    public string Preset { get; init; } = "medium";
    public bool IncludeAudio { get; init; } = true;
    public bool Verify { get; init; }
    public bool Analyze { get; init; }
    public string? AudioBitrate { get; init; }
    public string AudioCodec { get; init; } = "aac";
    public bool KeepWork { get; init; }
}

public sealed class PipelineException(string message) : Exception(message);
