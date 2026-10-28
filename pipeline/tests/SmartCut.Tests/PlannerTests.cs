using SmartCut.Core;

namespace SmartCut.Tests;

/// <summary>Indexes that exist only in memory: the planner maths is tested without ffmpeg.</summary>
internal static class SyntheticIndex
{
    public static VideoIndex Build(int epochCount = 10, int framesPerEpoch = 24, long tickStep = 512,
                                   long timeBaseNumerator = 1, long timeBaseDenominator = 12800,
                                   bool bFrameOrder = false, long[]? unevenSteps = null)
    {
        var total = epochCount * framesPerEpoch;
        var steps = unevenSteps ?? Enumerable.Repeat(tickStep, total).ToArray();
        var pts = new long[total];
        pts[0] = 0;
        for (var i = 1; i < total; i++) pts[i] = pts[i - 1] + steps[i - 1];

        // Decode order: with B frames each group of four is P,B,B,B in the file but P,B,B,B in time.
        var packetOrder = new List<int>();
        for (var group = 0; group < total / 4; group++)
        {
            if (bFrameOrder)
            {
                packetOrder.Add(group * 4 + 3);
                for (var k = 0; k < 3; k++) packetOrder.Add(group * 4 + k);
            }
            else
            {
                for (var k = 0; k < 4; k++) packetOrder.Add(group * 4 + k);
            }
        }

        var displayToPacket = new int[total];
        var decodeOrder = new VideoSample[total];
        for (var packet = 0; packet < total; packet++)
        {
            var rank = bFrameOrder ? packetOrder[packet] : packet;
            displayToPacket[rank] = packet;
            decodeOrder[packet] = new VideoSample(packet, pts[rank], packet, steps[rank], 1000 + packet * 100, 50,
                                                  packet % framesPerEpoch == 0, rank);
        }

        var epochs = new List<Epoch>();
        for (var e = 0; e < epochCount; e++)
        {
            var begin = e * framesPerEpoch;
            epochs.Add(new Epoch(begin, begin + framesPerEpoch, begin, begin + framesPerEpoch));
        }

        var boundaries = pts.Concat([pts[^1] + steps[^1]]).ToList();
        return new VideoIndex
        {
            InputPath = "synthetic.mp4",
            Stream = new ProbeStream { Index = 0, CodecName = "h264", CodecType = "video", PixFmt = "yuv420p" },
            TimeBaseNumerator = timeBaseNumerator,
            TimeBaseDenominator = timeBaseDenominator,
            DecodeOrder = decodeOrder,
            DisplayToPacket = displayToPacket,
            Epochs = epochs,
            Boundaries = boundaries,
            FrameCount = total,
            FirstPts = 0,
            NonIdrKeySamples = 3,
            NalLengthSize = 4,
        };
    }

    public static ProbeResult ProbeWithAudio(string sampleRate) =>
        new([
            new ProbeStream { Index = 0, CodecType = "video", CodecName = "h264", TimeBase = "1/12800" },
            new ProbeStream { Index = 1, CodecType = "audio", CodecName = "aac", SampleRate = sampleRate, TimeBase = "1/48000" },
        ], []);
}

public class PlannerTests
{
    [Fact]
    public void BothEdgesProducesEncodeCopyEncodeAndMergesTheCopyEpochs()
    {
        var index = SyntheticIndex.Build();
        var plan = Planner.PlanVideo(index, [new FrameRange(13, 227)]);

        Assert.Equal(3, plan.Units.Count);
        Assert.Equal(UnitKind.Encode, plan.Units[0].Kind);
        Assert.Equal(UnitKind.Copy, plan.Units[1].Kind);
        Assert.Equal(UnitKind.Encode, plan.Units[2].Kind);
        Assert.Equal(192, plan.Units[1].FrameCount);
        Assert.Equal(11, plan.Units[0].FrameCount);
        Assert.Equal(11, plan.Units[2].FrameCount);
        Assert.Equal(22, plan.EncodedFrames);
        Assert.Equal(192, plan.CopiedFrames);
        // One concat join per unit boundary: the merged copy epoch is a single segment.
        Assert.Equal(24, plan.Units[1].PacketBegin);
        Assert.Equal(216, plan.Units[1].PacketEnd);
    }

    [Fact]
    public void AdjacentKeepsMergeBeforePlanning()
    {
        var index = SyntheticIndex.Build();
        var plan = Planner.PlanVideo(index, [new FrameRange(0, 24), new FrameRange(24, 48)]);

        Assert.Single(plan.Units);
        Assert.Equal(UnitKind.Copy, plan.Units[0].Kind);
        Assert.Equal(48, plan.Units[0].FrameCount);
    }

    [Fact]
    public void OutputTimeIsTheSourceIntervalShiftedByTheAccumulatedUnitDuration()
    {
        var index = SyntheticIndex.Build(unevenSteps: Enumerable.Range(0, 240).Select(i => i % 3 == 2 ? 1024L : 512L).ToArray());
        var plan = Planner.PlanVideo(index, [new FrameRange(1, 29), new FrameRange(47, 121)]);

        var ticks = plan.ExpectedOutputTicks(index).ToArray();
        Assert.Equal(102, ticks.Length);
        Assert.Equal(0, ticks[0]);
        Assert.True(ticks.Zip(ticks.Skip(1), (a, b) => b > a).All(x => x));
        // Inside a unit the source spacing survives unchanged, which is what makes VFR output correct.
        Assert.Equal(index.Boundaries[2] - index.Boundaries[1], ticks[1] - ticks[0]);
        // The second keep starts exactly where the first one ends, on the accumulated duration.
        Assert.Equal(index.Boundaries[29] - index.Boundaries[1], ticks[28]);
    }

    [Theory]
    [InlineData(0, 0)]
    [InlineData(5, 5)]
    [InlineData(0, 241)]
    [InlineData(100, 50)]
    [InlineData(0, 100, 50, 150)]
    public void InvalidKeepRangesAreRejected(params long[] flat)
    {
        var index = SyntheticIndex.Build();
        var keeps = flat.Chunk(2).Select(c => new FrameRange(c[0], c[1])).ToArray();
        Assert.Throws<PipelineException>(() => Planner.Normalize(index, keeps));
    }

    [Fact]
    public void AudioWindowsAreSampleExactWhenTheVideoGridDividesIntoTheSampleRate()
    {
        var index = SyntheticIndex.Build();
        var plans = Planner.PlanAudio(SyntheticIndex.ProbeWithAudio("48000"), index, [new FrameRange(13, 227)]);

        var ranges = Assert.Single(plans).Ranges;
        Assert.True(ranges.Single().Exact);
        Assert.Equal(24960, ranges.Single().StartSample);
        Assert.Equal(435840, ranges.Single().EndSample);
        Assert.Equal(410880, ranges.Single().SampleCount);
    }

    [Fact]
    public void AudioWindowsFallBackToSecondsWhenTheSampleArithmeticDoesNotDivide()
    {
        // 30000/1001 material: frame 13 lands on 13.013013.. seconds, which is not a whole sample.
        var index = SyntheticIndex.Build(tickStep: 1001, timeBaseDenominator: 30000);
        var plans = Planner.PlanAudio(SyntheticIndex.ProbeWithAudio("48000"), index, [new FrameRange(13, 24)]);

        var range = Assert.Single(plans).Ranges.Single();
        Assert.False(range.Exact);
        Assert.Null(range.StartSample);
        Assert.True(range.EndSeconds - range.StartSeconds > 0);
    }

    [Fact]
    public void AudioTrackWithoutASampleRateIsPlannedAsNoRange()
    {
        var index = SyntheticIndex.Build();
        var plans = Planner.PlanAudio(SyntheticIndex.ProbeWithAudio(null!), index, [new FrameRange(0, 24)]);

        Assert.Empty(Assert.Single(plans).Ranges);
    }

    [Fact]
    public void EncodingUnitsCoverSelectedFramesEvenWhenDecodeOrderIsReordered()
    {
        var index = SyntheticIndex.Build(bFrameOrder: true);
        var plan = Planner.PlanVideo(index, [new FrameRange(13, 227)]);

        var encode = plan.Units.Single(u => u.Kind == UnitKind.Encode && u.DisplayBegin == 13);
        Assert.True(encode.PacketBegin <= 13);
        Assert.True(encode.PacketEnd >= 14);
        Assert.Equal(11, encode.FrameCount);
    }
}
