using System.Text.Json;
using System.Text.Json.Serialization;

namespace SmartCut.Core;

public sealed record ProbeStream
{
    [JsonPropertyName("index")] public int Index { get; init; }
    [JsonPropertyName("codec_name")] public string? CodecName { get; init; }
    [JsonPropertyName("codec_type")] public string? CodecType { get; init; }
    [JsonPropertyName("codec_tag_string")] public string? CodecTag { get; init; }
    [JsonPropertyName("time_base")] public string? TimeBase { get; init; }
    [JsonPropertyName("sample_rate")] [JsonConverter(typeof(FlexStringConverter))] public string? SampleRate { get; init; }
    [JsonPropertyName("pix_fmt")] public string? PixFmt { get; init; }
    [JsonConverter(typeof(FlexLongConverter))] public long? Width { get; init; }
    [JsonConverter(typeof(FlexLongConverter))] public long? Height { get; init; }
    [JsonPropertyName("field_order")] public string? FieldOrder { get; init; }
    [JsonPropertyName("profile")] public string? Profile { get; init; }
    [JsonPropertyName("color_range")] public string? ColorRange { get; init; }
    [JsonPropertyName("color_space")] public string? ColorSpace { get; init; }
    [JsonPropertyName("color_primaries")] public string? ColorPrimaries { get; init; }
    [JsonPropertyName("color_transfer")] public string? ColorTransfer { get; init; }
    [JsonPropertyName("sample_aspect_ratio")] public string? SampleAspectRatio { get; init; }
    [JsonConverter(typeof(FlexLongConverter))] public long? Duration { get; init; }

    public (long Numerator, long Denominator) TimeBaseRatio() => ParseRational(TimeBase);
    public static (long Numerator, long Denominator) ParseRational(string? text)
    {
        if (string.IsNullOrWhiteSpace(text)) return (0, 1);
        var parts = text.Split('/');
        if (parts.Length == 1) return (long.Parse(parts[0]), 1);
        return (long.Parse(parts[0]), long.Parse(parts[1]));
    }
}

public sealed record ProbePacket
{
    [JsonPropertyName("stream_index")] public int StreamIndex { get; init; }
    [JsonConverter(typeof(FlexLongConverter))] public long Pts { get; init; }
    [JsonConverter(typeof(FlexLongConverter))] public long Dts { get; init; }
    [JsonConverter(typeof(FlexLongConverter))] public long Duration { get; init; }
    [JsonConverter(typeof(FlexLongConverter))] public long Size { get; init; }
    [JsonConverter(typeof(FlexLongConverter))] public long Pos { get; init; }
    [JsonPropertyName("flags")] public string? Flags { get; init; }
    public bool Key => Flags is not null && Flags.Contains('K');
}

public sealed record ProbeResult(IReadOnlyList<ProbeStream> Streams, IReadOnlyList<ProbePacket> Packets);

/// <summary>ffprobe -of json reader. pos/size arrive quoted from some builds, so numbers and
/// strings both have to be tolerated.</summary>
public sealed class FFprobeReader(FFmpegTools tools)
{
    static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNameCaseInsensitive = true,
        AllowTrailingCommas = true,
    };

    public ProbeResult Read(string path)
    {
        var result = tools.FFprobe([
            "-v", "error",
            "-show_entries",
            "stream=index,codec_name,codec_type,codec_tag_string,time_base,sample_rate,pix_fmt,width,height,field_order,profile,color_range,color_space,color_primaries,color_transfer,sample_aspect_ratio,duration"
            + ":packet=stream_index,pts,dts,duration,size,pos,flags",
            "-of", "json", path,
        ]);
        using var document = JsonDocument.Parse(result.StdOut);
        var streams = new List<ProbeStream>();
        if (document.RootElement.TryGetProperty("streams", out var streamArray))
            foreach (var element in streamArray.EnumerateArray())
                streams.Add(element.Deserialize<ProbeStream>(JsonOptions)!);
        var packets = new List<ProbePacket>();
        if (document.RootElement.TryGetProperty("packets", out var packetArray))
            foreach (var element in packetArray.EnumerateArray())
                packets.Add(element.Deserialize<ProbePacket>(JsonOptions)!);
        return new ProbeResult(streams, packets);
    }
}

public sealed class FlexLongConverter : JsonConverter<long>
{
    public override long Read(ref Utf8JsonReader reader, Type typeToConvert, JsonSerializerOptions options) =>
        reader.TokenType switch
        {
            JsonTokenType.Number => reader.GetInt64(),
            JsonTokenType.String => long.TryParse(reader.GetString(), out var value) ? value : 0,
            JsonTokenType.Null => 0,
            _ => throw new JsonException($"cannot read a long from {reader.TokenType}"),
        };

    public override void Write(Utf8JsonWriter writer, long value, JsonSerializerOptions options) => writer.WriteNumberValue(value);
}

public sealed class FlexStringConverter : JsonConverter<string>
{
    public string? ReadAsNullable(ref Utf8JsonReader reader, Type typeToConvert, JsonSerializerOptions options) => Read(ref reader, typeToConvert, options);

    public override string Read(ref Utf8JsonReader reader, Type typeToConvert, JsonSerializerOptions options) =>
        reader.TokenType switch
        {
            JsonTokenType.String => reader.GetString() ?? string.Empty,
            JsonTokenType.Number => reader.GetInt64().ToString(),
            JsonTokenType.Null => string.Empty,
            _ => string.Empty,
        };

    public override void Write(Utf8JsonWriter writer, string value, JsonSerializerOptions options) => writer.WriteStringValue(value);
}
