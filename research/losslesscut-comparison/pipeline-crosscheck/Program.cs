// SPDX-License-Identifier: GPL-3.0-or-later
// Reuse the unchanged Pipeline Verifier on the official LosslessCut outputs.
using System.Text.Json;
using SmartCut.Core;

var root = Path.GetFullPath(args[0]);
var tools = FFmpegTools.Locate(args[1]);
using var saved = JsonDocument.Parse(File.ReadAllText(Path.Combine(root,"results.json")));
var sources = saved.RootElement.GetProperty("fixtures");
var cache = new Dictionary<string, VideoIndex>();
var rows = new List<Dictionary<string,object?>>();
foreach (var row in saved.RootElement.GetProperty("cases").EnumerateArray())
{
    if (row.GetProperty("codec").GetString() != "h264") continue;
    var id = row.GetProperty("id").GetString()!;
    var fixture = row.GetProperty("fixture").GetString()!;
    var result = new Dictionary<string,object?> { ["id"] = id, ["passed"] = false };
    try
    {
        if (!row.TryGetProperty("output",out var outputValue))
            throw new InvalidOperationException("LosslessCut did not return a completed output");
        var output = outputValue.GetString()!;
        var source = sources.GetProperty("h264/"+fixture).GetProperty("path").GetString()!;
        if (!cache.TryGetValue(fixture,out var index))
        {
            index = VideoIndex.Build(tools,source,new FFprobeReader(tools).Read(source));
            cache.Add(fixture,index);
        }
        var keeps = row.GetProperty("ranges").EnumerateArray()
                       .Select(r => new FrameRange(r[0].GetInt64(),r[1].GetInt64())).ToArray();
        var plan = Planner.PlanVideo(index,keeps);
        var verification = Verifier.Verify(tools,source,output,index,plan,Array.Empty<AudioTrackPlan>());
        result["verification"] = verification.ToJsonPayload();
        result["baseline_copied_frames"] = plan.CopiedFrames;
        // Same additional copy coverage assertion as PipelineIntegrationTests.
        var covered = verification.CopyFramesChecked == plan.CopiedFrames &&
                      verification.CopyFramesMatching == verification.CopyFramesChecked;
        result["copy_coverage_passed"] = covered;
        result["passed"] = verification.Passed && covered;
    }
    catch(Exception ex)
    {
        result["error"] = ex.Message;
    }
    rows.Add(result);
    var report = new {
        description = "Unchanged SmartCut.Core.Verifier plus the original pipeline integration test's copy-coverage assertion, applied to saved official LosslessCut 3.70.2 outputs. No re-export.",
        ffmpeg = tools.Version,
        core_dll = typeof(Verifier).Assembly.Location,
        total = rows.Count,
        passed = rows.Count(r => (bool)r["passed"]!),
        cases = rows,
    };
    File.WriteAllText(Path.Combine(root,"pipeline-crosscheck.json"),JsonSerializer.Serialize(report,new JsonSerializerOptions { WriteIndented=true }));
    Console.WriteLine($"{id}: {result["passed"]}");
}
Console.WriteLine($"Passed {rows.Count(r => (bool)r["passed"]!)}/{rows.Count}");
