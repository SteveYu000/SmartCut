// SPDX-License-Identifier: GPL-3.0-or-later
// Video-only research executable: shared cut planner + native MKV demux/mux.
#include "mkv_input.hpp"
#include <algorithm>
#include <cstring>
#include <fstream>
#include <iostream>
#include <sstream>
#ifdef _WIN32
#include <windows.h>
#endif
using namespace smartcut;

namespace {
struct Output {
    AVFormatContext *ctx = nullptr;
    ~Output() {
        if (ctx) {
            if (ctx->pb) avio_closep(&ctx->pb);
            avformat_free_context(ctx);
        }
    }
};
Bytes sequence(const AVCodecParameters *params) {
    const auto *data = params->extradata;
    size_t size = static_cast<size_t>(params->extradata_size), pos = 0;
    if (size && (data[0] & 128)) pos = 4;
    while (pos < size) {
        const size_t start = pos;
        const auto h = data[pos++];
        require(!(h & 0x81), "Invalid sequence OBU");
        if (h & 4) { require(pos < size, "Missing OBU extension"); ++pos; }
        uint64_t n = size - pos;
        if (h & 2) {
            n = 0;
            bool done = false;
            for (unsigned i = 0; i < 8; ++i) {
                require(pos < size, "Missing OBU length");
                auto b = data[pos++]; n |= uint64_t(b & 127) << (7 * i);
                if (!(b & 128)) { done = true; break; }
            }
            require(done, "OBU length overflow");
        }
        require(n <= size - pos, "Invalid OBU payload length");
        pos += static_cast<size_t>(n);
        if (((h >> 3) & 15) == 1) return {data + start, data + pos};
    }
    fail("AV1 configuration has no sequence header");
}
const AVCodecParameters *unit_params(const Input &input, const Index &idx, const Unit &unit) {
    return unit.copy ? (idx.codec == AV_CODEC_ID_AV1 ? idx.av1_params.get() : input.stream->codecpar)
                     : unit.encoded->params.get();
}
size_t sequence_count(const Input &input, const Index &idx, const Plan &plan) {
    if (idx.codec != AV_CODEC_ID_AV1) return 0;
    std::set<Bytes> values;
    for (const auto &unit : plan.units) values.insert(sequence(unit_params(input, idx, unit)));
    return values.size();
}
Bsf clean_annex_b(const AVCodecParameters *params, AVRational tb) {
    const char *chain = params->codec_id == AV_CODEC_ID_H264
        ? "h264_mp4toannexb,filter_units=remove_types=7|8"
        : "hevc_mp4toannexb,filter_units=remove_types=32|33|34";
    AVBSFContext *raw = nullptr;
    check(av_bsf_list_parse_str(chain, &raw), "Create parameter-set filter chain");
    Bsf bsf(raw);
    check(avcodec_parameters_copy(bsf->par_in, params), "Configure filter chain");
    bsf->time_base_in = tb;
    check(av_bsf_init(bsf.get()), "Initialize filter chain");
    return bsf;
}
std::string report(const Input &input, const Index &idx, const Plan &plan, const PacketSpool &spool,
                   const std::string &mode, const std::string &policy, const std::string &decision,
                   size_t before, size_t after) {
    std::ostringstream out;
    out << "{\n  \"ffmpeg\": " << json_string(av_version_info())
        << ",\n  \"input_format\": " << json_string(input.format.value->iformat->name)
        << ",\n  \"codec\": " << json_string(avcodec_get_name(idx.codec))
        << ",\n  \"header_mode\": " << json_string(mode)
        << ",\n  \"av1_policy\": " << json_string(policy)
        << ",\n  \"decision\": " << json_string(decision)
        << ",\n  \"sequence_headers_before_policy\": " << before
        << ",\n  \"sequence_headers_after_policy\": " << after
        << ",\n  \"copied_frames\": " << plan.copied << ",\n  \"encoded_frames\": " << plan.encoded
        << ",\n  \"decoded_frames\": " << plan.decoded
        << ",\n  \"spool_bytes\": " << spool.bytes << ",\n  \"spool_packets\": " << spool.packets
        << ",\n  \"input_time_base\": " << json_string(std::to_string(input.stream->time_base.num)+"/"+std::to_string(input.stream->time_base.den))
        << ",\n  \"duration_ticks\": " << plan.duration << ",\n  \"units\": [";
    bool first = true;
    for (const auto &unit : plan.units) {
        if (!first) out << ',';
        first = false;
        out << "\n    {\"begin\":" << unit.begin << ",\"end\":" << unit.end
            << ",\"copy\":" << (unit.copy ? "true" : "false")
            << ",\"patch_cra\":" << (unit.patch_cra ? "true" : "false")
            << ",\"output_start\":" << unit.output_start << '}';
    }
    out << "\n  ]\n}\n";
    return out.str();
}
void mux(Input &input, const Index &idx, const Plan &plan, const fs::path &path,
         const std::string &mode) {
    Output output;
    check(avformat_alloc_output_context2(&output.ctx, nullptr, "matroska", utf8(path).c_str()),
          "Create Matroska muxer");
    auto *ctx = output.ctx;
    ctx->avoid_negative_ts = AVFMT_AVOID_NEG_TS_DISABLED;
    auto *stream = avformat_new_stream(ctx, nullptr);
    require(stream, "Create Matroska track");
    const auto *params = unit_params(input, idx, plan.units.front());
    if (idx.codec == AV_CODEC_ID_AV1)
        check(avcodec_parameters_copy(stream->codecpar, params), "Configure AV1 track");
    else {
        auto bsf = annex_b(params, input.stream->time_base);
        check(avcodec_parameters_copy(stream->codecpar, bsf->par_out), "Configure Annex B track");
    }
    stream->codecpar->codec_tag = 0;
    stream->time_base = input.stream->time_base;
    // A positive nominal rate lets FFmpeg emit explicit BlockDuration whenever
    // the actual duration differs. avg_frame_rate is not a VFR resampling rule.
    stream->avg_frame_rate = av_guess_frame_rate(input.format.value, input.stream, nullptr);
    stream->sample_aspect_ratio = av_guess_sample_aspect_ratio(input.format.value, input.stream, nullptr);
    check(avio_open(&ctx->pb, utf8(path).c_str(), AVIO_FLAG_WRITE), "Open staged Matroska");
    check(avformat_write_header(ctx, nullptr), "Write Matroska header");
    std::map<int64_t, int64_t> durations;
    for (const auto &unit : plan.units)
        for (size_t i = unit.begin; i < unit.end; ++i)
            durations[unit.output_start + idx.times[i] - idx.times[unit.begin]] =
                i + 1 == static_cast<size_t>(plan.ranges.back().end)
                    ? plan.last_duration : idx.times[i + 1] - idx.times[i];
    VideoCursor cursor(input, idx, plan);
    Bsf bsf;
    Bytes sets;
    int64_t last_dts = AV_NOPTS_VALUE;
    while (auto item = cursor.next()) {
        auto &p = item->data;
        if (idx.codec != AV_CODEC_ID_AV1) {
            if (item->first) {
                auto headers = annex_b(item->params, input.stream->time_base);
                sets.assign(headers->par_out->extradata,
                            headers->par_out->extradata + headers->par_out->extradata_size);
                bsf = clean_annex_b(item->params, input.stream->time_base);
            }
            check(av_bsf_send_packet(bsf.get(), p.get()), "Convert video framing");
            check(av_bsf_receive_packet(bsf.get(), p.get()), "Receive video framing");
            bool add = mode == "all" ? item->first || (p->flags & AV_PKT_FLAG_KEY)
                                    : mode == "seams" && item->first;
            if (add) {
                int old = p->size;
                check(av_grow_packet(p.get(), static_cast<int>(sets.size())), "Restore active parameter sets");
                std::memmove(p->data + sets.size(), p->data, old);
                std::memcpy(p->data, sets.data(), sets.size());
            }
            if (mode == "extradata" && item->first) {
                auto *extra = av_packet_new_side_data(p.get(), AV_PKT_DATA_NEW_EXTRADATA, sets.size());
                require(extra, "Allocate new extradata");
                std::memcpy(extra, sets.data(), sets.size());
            }
        }
        p->pts = item->pts; p->dts = item->dts;
        p->duration = durations.at(item->pts);
        p->stream_index = stream->index; p->pos = -1;
        av_packet_rescale_ts(p.get(), input.stream->time_base, stream->time_base);
        require(p->duration > 0 && p->dts <= p->pts &&
                    (last_dts == AV_NOPTS_VALUE || p->dts > last_dts),
                "Millisecond Matroska time base cannot represent this decode schedule");
        last_dts = p->dts;
        check(av_interleaved_write_frame(ctx, p.get()), "Mux Matroska video");
    }
    check(av_write_trailer(ctx), "Write Matroska Cues and duration");
    check(avio_closep(&ctx->pb), "Close Matroska output");
}
int entry(std::vector<std::string> args) {
    try {
        std::string mode = "all", policy = "reject";
        for (size_t i = 1; i < args.size();) {
            if (args[i] == "--header-mode" || args[i] == "--av1-policy") {
                require(i + 1 < args.size(), "Research option requires a value");
                (args[i] == "--header-mode" ? mode : policy) = args[i + 1];
                args.erase(args.begin() + i, args.begin() + i + 2);
            } else ++i;
        }
        require(mode == "all" || mode == "seams" || mode == "extradata", "Unknown header mode");
        require(policy == "reject" || policy == "full" || policy == "experimental", "Unknown AV1 policy");
        auto options = parse_options(args);
        av_log_set_level(options.log_level);
        require(!options.output.empty(), "An output path is required");
        require(path8(options.input) != path8(options.output), "Input and output must differ");
        require(options.overwrite || !fs::exists(path8(options.output)), "Output exists; use -y");
        Format original; original.open(options.input);
        int video = av_find_best_stream(original.value, AVMEDIA_TYPE_VIDEO, -1, -1, nullptr, 0);
        require(video >= 0, "No video stream");
        require(original.value->nb_streams == 1 && video == 0,
                "Research executable accepts one video track; auxiliary streams are tested separately");
        Input input(options.input, video);
        Workspace work(options.work.empty() ? path8(options.output).parent_path() : path8(options.work), options.keep_work);
        PacketSpool spool(input, work.directory / "demuxed-packets.bin");
        auto idx = index_input(input);
        spool.finish();
        auto plan = make_plan(idx, options, input.stream->time_base, input.format.value->start_time);
        Layout layout(input.format.value, options);
        auto name = codec_name(options, layout, layout.video, av_guess_format("matroska", nullptr, nullptr));
        if (name != "copy") {
            auto *encoder = find_encoder(name);
            require(encoder && encoder->id == idx.codec, "Encoder codec must match source video");
            auto values = video_options(options, layout, encoder);
            encode_boundaries(input, idx, plan, encoder, values, work);
        } else {
            require(plan.encoded == 0, "Frame-exact boundaries require a matching video encoder");
            calculate_delay(idx, plan);
        }
        const size_t before = sequence_count(input, idx, plan);
        std::string decision = "smart-render";
        if (before > 1 && policy == "full") {
            require(name != "copy", "Full AV1 fallback requires an encoder");
            auto *encoder = find_encoder(name);
            plan.units.clear(); plan.encoded += plan.copied; plan.copied = 0;
            int64_t start = plan.leading;
            for (const auto &range : plan.ranges) {
                Unit unit; unit.begin = static_cast<size_t>(range.begin); unit.end = static_cast<size_t>(range.end);
                while (unit.epoch + 1 < idx.epochs.size() && idx.epochs[unit.epoch].end <= unit.begin) ++unit.epoch;
                unit.output_start = start; start += idx.times[unit.end] - idx.times[unit.begin];
                plan.units.push_back(std::move(unit));
            }
            encode_boundaries(input, idx, plan, encoder, video_options(options, layout, encoder), work);
            decision = "explicit-full-reencode";
        }
        const size_t after = sequence_count(input, idx, plan);
        if (after > 1) decision = policy == "experimental" ? "nonconforming-experiment" : "rejected-sequence-change";
        if (!options.report.empty()) {
            std::ofstream out(path8(options.report));
            out << report(input, idx, plan, spool, mode, policy, decision, before, after);
            require(bool(out), "Cannot write research report");
        }
        require(after <= 1 || policy == "experimental",
                "V_AV1 requires one Sequence Header per Matroska Segment; mixed configuration rejected");
        auto staged = work.directory / "result.mkv";
        mux(input, idx, plan, staged, mode);
        publish_file(staged, path8(options.output), options.overwrite);
        std::cout << decision << ": copied=" << plan.copied << " encoded=" << plan.encoded << '\n';
        return 0;
    } catch (const std::exception &e) {
        std::cerr << "mkv-research: " << e.what() << '\n'; return 1;
    }
}
} // namespace
#ifdef _WIN32
int wmain(int argc, wchar_t **argv) {
    std::vector<std::string> args;
    for (int i = 0; i < argc; ++i) args.push_back(utf8(fs::path(argv[i])));
    return entry(std::move(args));
}
#else
int main(int argc, char **argv) { return entry({argv, argv + argc}); }
#endif
