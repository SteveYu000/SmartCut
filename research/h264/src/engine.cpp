#include "engine.hpp"
extern "C" {
#include <libavformat/avformat.h>
#include <libavcodec/avcodec.h>
#include <libavcodec/bsf.h>
#include <libavutil/avutil.h>
#include <libavutil/opt.h>
#include <libavutil/pixdesc.h>
}
#include <algorithm>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iostream>
#include <limits>
#include <map>
#include <memory>
#include <numeric>
#include <set>
#include <span>
#include <sstream>
#include <stdexcept>
#include <vector>

namespace sr {
namespace {
namespace fs = std::filesystem;
using Bytes = std::vector<uint8_t>;
fs::path path8(const std::string& text) {
    return fs::path(std::u8string(reinterpret_cast<const char8_t*>(text.data()), text.size()));
}
[[noreturn]] void fail(const std::string& s) { throw std::runtime_error(s); }
void require(bool condition, const std::string& s) { if (!condition) fail(s); }
void check(int result, const char* context) {
    if (result < 0) { char text[AV_ERROR_MAX_STRING_SIZE]; av_strerror(result, text, sizeof(text)); fail(std::string(context) + ": " + text); }
}
struct PacketFree { void operator()(AVPacket* p) const { av_packet_free(&p); } };
struct FrameFree { void operator()(AVFrame* p) const { av_frame_free(&p); } };
struct CodecFree { void operator()(AVCodecContext* p) const { avcodec_free_context(&p); } };
struct BsfFree { void operator()(AVBSFContext* p) const { av_bsf_free(&p); } };
using Packet = std::unique_ptr<AVPacket, PacketFree>;
using Frame = std::unique_ptr<AVFrame, FrameFree>;
using Codec = std::unique_ptr<AVCodecContext, CodecFree>;
using Bsf = std::unique_ptr<AVBSFContext, BsfFree>;
Packet packet() { Packet p(av_packet_alloc()); require(bool(p), "Packet allocation failed"); return p; }
Frame frame() { Frame f(av_frame_alloc()); require(bool(f), "Frame allocation failed"); return f; }
std::string quoted(const std::string& s) {
    std::string result = "\"";
    for (unsigned char c : s) {
        if (c == '\\' || c == '"') { result += '\\'; result += static_cast<char>(c); }
        else if (c < 32) result += '?'; else result += static_cast<char>(c);
    }
    return result + '"';
}
Bytes bytes(const uint8_t* data, size_t size) { return Bytes(data, data + size); }
struct Input {
    AVFormatContext* ctx = nullptr;
    AVIOContext* raw = nullptr;
    int video = -1;
    AVStream* stream = nullptr;
    ~Input() { avformat_close_input(&ctx); if (raw) avio_closep(&raw); }
    explicit Input(const std::string& path) {
      try {
        check(avformat_open_input(&ctx, path.c_str(), nullptr, nullptr), "Open input");
        check(avformat_find_stream_info(ctx, nullptr), "Find streams");
        require(std::string(ctx->iformat->name).find("mov") != std::string::npos, "Input must be an MP4 file");
        for (unsigned i = 0; i < ctx->nb_streams; ++i) {
            if (ctx->streams[i]->codecpar->codec_type == AVMEDIA_TYPE_VIDEO) {
                require(video == -1, "Exactly one video stream is supported (including cover art)");
                video = static_cast<int>(i);
            }
        }
        require(video >= 0, "No video stream"); stream = ctx->streams[video];
        auto* p = stream->codecpar;
        require(p->codec_id == AV_CODEC_ID_H264, "Video codec must be H.264");
        require(p->codec_tag == MKTAG('a','v','c','1') || p->codec_tag == MKTAG('a','v','c','3'), "Only unencrypted avc1/avc3 input is supported");
        require(p->field_order == AV_FIELD_PROGRESSIVE, "Only positively identified progressive video is supported");
        require(p->width > 0 && p->height > 0, "Invalid dimensions");
        require(p->format == AV_PIX_FMT_YUV420P || p->format == AV_PIX_FMT_YUVJ420P || p->format == AV_PIX_FMT_YUV420P10LE,
                "Supported pixel formats: yuv420p, yuvj420p and yuv420p10le");
        int profile = p->profile & ~AV_PROFILE_H264_CONSTRAINED;
        require(profile == AV_PROFILE_H264_BASELINE || profile == AV_PROFILE_H264_MAIN ||
                profile == AV_PROFILE_H264_HIGH || profile == AV_PROFILE_H264_HIGH_10,
                "Supported input profiles: Baseline, Main, High, High 10 (lossless/High 4:4:4 input is unsupported)");
        require(stream->time_base.num > 0 && stream->time_base.den > 0, "Invalid time base");
        check(avio_open(&raw, path.c_str(), AVIO_FLAG_READ), "Open sample reader");
      } catch (...) { avformat_close_input(&ctx); if (raw) avio_closep(&raw); throw; }
    }
};
struct Sample {
    int64_t pos{}, pts{}, dts{}, duration{};
    int size{}, flags{};
    bool idr{};
    size_t display{};
};
struct Epoch { size_t packet_begin{}, packet_end{}, begin{}, end{}; };
struct Index {
    std::vector<Sample> samples;
    std::vector<size_t> display;
    std::vector<int64_t> times; // N+1 presentation boundaries in input ticks.
    std::vector<Epoch> epochs;
    std::set<Bytes> parameter_sets;
    int nal_length{};
    size_t non_idr_keys{};
};
// Only framing and the first Exp-Golomb field are inspected. No slice rewriting.
unsigned first_mb(std::span<const uint8_t> nal) {
    Bytes rbsp; int zeros = 0;
    for (size_t i = 1; i < nal.size() && rbsp.size() < 12; ++i) {
        uint8_t b = nal[i];
        if (zeros == 2 && b == 3) { zeros = 0; continue; }
        rbsp.push_back(b); zeros = b == 0 ? zeros + 1 : 0;
    }
    size_t bit = 0;
    auto read = [&]() { require(bit < rbsp.size() * 8, "Truncated slice header"); auto v = (rbsp[bit / 8] >> (7 - bit % 8)) & 1; ++bit; return v; };
    unsigned z = 0; while (!read()) { require(++z < 31, "Invalid slice header"); }
    unsigned value = 1; for (unsigned i = 0; i < z; ++i) value = (value << 1) | read();
    return value - 1;
}
void read_config(Index& idx, const AVCodecParameters* par) {
    auto* data = par->extradata; size_t size = static_cast<size_t>(par->extradata_size);
    require(size >= 7 && data[0] == 1, "An AVCDecoderConfigurationRecord is required");
    idx.nal_length = (data[4] & 3) + 1;
    size_t pos = 6; unsigned count = data[5] & 31;
    auto collect = [&](unsigned n, int type) {
        for (unsigned i = 0; i < n; ++i) {
            require(pos + 2 <= size, "Truncated avcC");
            size_t length = (data[pos] << 8) | data[pos + 1]; pos += 2;
            require(length > 0 && pos + length <= size && (data[pos] & 31) == type, "Invalid avcC parameter set");
            idx.parameter_sets.insert(bytes(data + pos, length)); pos += length;
        }
    };
    require(count > 0, "Missing SPS"); collect(count, 7);
    require(pos < size && data[pos] > 0, "Missing PPS"); count = data[pos++]; collect(count, 8);
}
bool inspect_sample(const Index& idx, const AVPacket* p) {
    size_t pos = 0; bool idr = false, non_idr = false; unsigned firsts = 0;
    while (pos < static_cast<size_t>(p->size)) {
        require(pos + idx.nal_length <= static_cast<size_t>(p->size), "Truncated NAL length");
        uint32_t n = 0;
        for (int j = 0; j < idx.nal_length; ++j) n = (n << 8) | p->data[pos++];
        require(n > 0 && n <= static_cast<size_t>(p->size) - pos, "Invalid NAL length");
        const auto nal = std::span<const uint8_t>(p->data + pos, n);
        require(!(nal[0] & 128), "forbidden_zero_bit is set"); int type = nal[0] & 31;
        if (type == 1 || type == 5) {
            firsts += first_mb(nal) == 0;
            idr |= type == 5; non_idr |= type == 1;
        } else if (type == 7 || type == 8) {
            require(idx.parameter_sets.contains(bytes(nal.data(), nal.size())), "Changing in-band SPS/PPS is outside the supported input contract");
        } else require(type == 6 || type == 9 || type == 10 || type == 11 || type == 12,
                       "Unsupported H.264 extension/data partition NAL type");
        pos += n;
    }
    require(firsts == 1 && !(idr && non_idr), "Each MP4 sample must contain exactly one progressive access unit");
    return idr;
}
Index index_input(Input& input) {
    Index idx; read_config(idx, input.stream->codecpar);
    auto p = packet(); int result; int64_t size = avio_size(input.raw);
    while ((result = av_read_frame(input.ctx, p.get())) >= 0) {
        if (p->stream_index == input.video) {
            require(p->pts != AV_NOPTS_VALUE && p->dts != AV_NOPTS_VALUE, "Missing PTS/DTS");
            require(!(p->flags & (AV_PKT_FLAG_CORRUPT | AV_PKT_FLAG_DISCARD)), "Corrupt or edit-list preroll/discard samples are unsupported");
            require(p->pos >= 0 && p->size > 0 && p->pos <= size - p->size, "Source sample is not directly addressable in the local MP4");
            require(idx.samples.empty() || p->dts > idx.samples.back().dts, "Input DTS must be strictly increasing");
            for (int s = 0; s < p->side_data_elems; ++s) {
                auto& side = p->side_data[s];
                require(side.type != AV_PKT_DATA_ENCRYPTION_INFO, "Encrypted input is unsupported");
                if (side.type == AV_PKT_DATA_NEW_EXTRADATA) {
                    auto* par = input.stream->codecpar;
                    require(side.size == static_cast<size_t>(par->extradata_size) && !memcmp(side.data, par->extradata, side.size), "Changing sample descriptions are unsupported as input");
                }
            }
            bool idr = inspect_sample(idx, p.get());
            if (!idr && (p->flags & AV_PKT_FLAG_KEY)) ++idx.non_idr_keys;
            idx.samples.push_back({p->pos, p->pts, p->dts, p->duration, p->size, p->flags, idr, 0});
        }
        av_packet_unref(p.get());
    }
    require(result == AVERROR_EOF, "Input demux failed before EOF");
    require(!idx.samples.empty() && idx.samples.front().idr, "Input must start with a real IDR access unit");
    idx.display.resize(idx.samples.size()); std::iota(idx.display.begin(), idx.display.end(), size_t{0});
    std::sort(idx.display.begin(), idx.display.end(), [&](size_t a, size_t b) { return idx.samples[a].pts < idx.samples[b].pts; });
    for (size_t i = 0; i < idx.display.size(); ++i) {
        auto& sample = idx.samples[idx.display[i]]; sample.display = i;
        require(i == 0 || sample.pts > idx.times.back(), "Duplicate presentation timestamps are unsupported");
        idx.times.push_back(sample.pts);
    }
    int64_t last_duration = idx.samples[idx.display.back()].duration;
    require(last_duration > 0, "Last sample has no positive duration");
    idx.times.push_back(idx.times.back() + last_duration);
    size_t start = 0;
    for (size_t end = 1; end <= idx.samples.size(); ++end) {
        if (end != idx.samples.size() && !idx.samples[end].idr) continue;
        auto lo = idx.samples[start].display; auto hi = lo;
        for (size_t j = start; j < end; ++j) { lo = std::min(lo, idx.samples[j].display); hi = std::max(hi, idx.samples[j].display); }
        require(lo == idx.samples[start].display && hi - lo + 1 == end - start, "Presentation reordering crosses an IDR boundary");
        idx.epochs.push_back({start, end, lo, hi + 1}); start = end;
    }
    return idx;
}
struct Unit { size_t epoch{}, begin{}, end{}; bool copy{}; int64_t output_start{}; };
struct Plan { std::vector<Unit> units; size_t copied{}, encoded{}; int64_t duration{}, delay{}; };
Plan plan(const Index& idx, const std::vector<Range>& ranges) {
    Plan p; int64_t previous = -1;
    std::vector<Range> normalized;
    for (auto range : ranges) {
        require(range.begin < range.end && range.begin >= previous && range.end <= static_cast<int64_t>(idx.display.size()), "Invalid, overlapping, unsorted, or out-of-bounds --keep range");
        previous = range.end;
        if (!normalized.empty() && normalized.back().end == range.begin) normalized.back().end = range.end;
        else normalized.push_back(range);
    }
    for (auto range : normalized) {
        for (size_t e = 0; e < idx.epochs.size(); ++e) {
            const auto& epoch = idx.epochs[e];
            size_t a = std::max(epoch.begin, static_cast<size_t>(range.begin));
            size_t b = std::min(epoch.end, static_cast<size_t>(range.end));
            if (a >= b) continue;
            bool copy = a == epoch.begin && b == epoch.end;
            p.units.push_back({e, a, b, copy, p.duration});
            (copy ? p.copied : p.encoded) += b - a;
            if (copy) {
                for (size_t j = 0; j < b - a; ++j) {
                    // Container DTS follows a monotonically increasing slot grid; VCL decode order is unchanged.
                    int64_t delta = idx.times[a + j] - idx.samples[epoch.packet_begin + j].pts;
                    p.delay = std::max(p.delay, delta);
                }
            }
            p.duration += idx.times[b] - idx.times[a];
        }
    }
    require(!p.units.empty(), "Empty plan");
    return p;
}
Packet read_sample(Input& input, const Sample& sample) {
    auto p = packet(); check(av_new_packet(p.get(), sample.size), "Allocate sample");
    require(avio_seek(input.raw, sample.pos, SEEK_SET) == sample.pos, "Sample seek failed");
    require(avio_read(input.raw, p->data, sample.size) == sample.size, "Short sample read (input changed?)");
    p->pts = sample.pts; p->dts = sample.dts; p->duration = sample.duration; p->pos = sample.pos;
    p->stream_index = input.video; p->flags = sample.flags;
    return p;
}
Bsf annex_b(const AVCodecParameters* params, AVRational time_base) {
    auto* filter = av_bsf_get_by_name("h264_mp4toannexb"); require(filter != nullptr, "Missing h264_mp4toannexb");
    AVBSFContext* raw = nullptr; check(av_bsf_alloc(filter, &raw), "Allocate bitstream filter"); Bsf bsf(raw);
    check(avcodec_parameters_copy(bsf->par_in, params), "Copy BSF parameters");
    bsf->time_base_in = time_base; check(av_bsf_init(bsf.get()), "Initialize bitstream filter"); return bsf;
}
struct Output {
    AVFormatContext* ctx = nullptr;
    AVStream* stream = nullptr;
    AVRational input_tb{};
    bool stsd{};
    int64_t last_dts = AV_NOPTS_VALUE;
    size_t packets{};
    ~Output() { if (ctx) { if (ctx->pb) avio_closep(&ctx->pb); avformat_free_context(ctx); } }
    Output(Input& input, const std::string& path, bool descriptions) : input_tb(input.stream->time_base), stsd(descriptions) {
      try {
        check(avformat_alloc_output_context2(&ctx, nullptr, "mp4", path.c_str()), "Allocate MP4 output");
        stream = avformat_new_stream(ctx, nullptr); require(stream != nullptr, "Allocate video stream");
        check(avcodec_parameters_copy(stream->codecpar, input.stream->codecpar), "Copy output parameters");
        stream->time_base = input_tb; stream->avg_frame_rate = input.stream->avg_frame_rate;
        stream->sample_aspect_ratio = input.stream->sample_aspect_ratio;
        av_dict_copy(&stream->metadata, input.stream->metadata, 0);
        if (!stsd) {
            auto bsf = annex_b(input.stream->codecpar, input_tb);
            check(avcodec_parameters_copy(stream->codecpar, bsf->par_out), "Copy Annex-B parameters");
        }
        stream->codecpar->codec_tag = stsd ? MKTAG('a','v','c','1') : MKTAG('a','v','c','3');
        // Keep muxing changes explicit; PTS always start at zero, initial DTS may be negative.
        ctx->avoid_negative_ts = AVFMT_AVOID_NEG_TS_DISABLED;
        check(avio_open(&ctx->pb, path.c_str(), AVIO_FLAG_WRITE), "Open output");
        AVDictionary* opts = nullptr;
        av_dict_set(&opts, "movflags", "+faststart", 0); av_dict_set(&opts, "use_editlist", "1", 0);
        int result = avformat_write_header(ctx, &opts); av_dict_free(&opts); check(result, "Write MP4 header");
      } catch (...) { if (ctx) { if (ctx->pb) avio_closep(&ctx->pb); avformat_free_context(ctx); ctx = nullptr; } throw; }
    }
    void write(AVPacket* p, int64_t pts, int64_t dts, int64_t duration, const uint8_t* extra = nullptr, size_t extra_size = 0) {
        require(dts <= pts && (last_dts == AV_NOPTS_VALUE || dts > last_dts), "Non-monotonic or invalid planned DTS");
        last_dts = dts;
        if (stsd && extra_size) {
            auto* data = av_packet_new_side_data(p, AV_PKT_DATA_NEW_EXTRADATA, extra_size);
            require(data != nullptr, "Allocate configuration change"); memcpy(data, extra, extra_size);
        }
        p->pts = pts; p->dts = dts; p->duration = duration; p->pos = -1; p->stream_index = 0;
        av_packet_rescale_ts(p, input_tb, stream->time_base);
        check(av_interleaved_write_frame(ctx, p), "Mux video sample"); ++packets;
    }
    void finish() { check(av_write_trailer(ctx), "Finish MP4"); check(avio_closep(&ctx->pb), "Close MP4"); }
};
int64_t relative_pts(const Index& idx, const Unit& u, int64_t pts) { return u.output_start + pts - idx.times[u.begin]; }
int64_t decode_slot(const Index& idx, const Unit& u, const Plan& plan, size_t n) {
    return relative_pts(idx, u, idx.times[u.begin + n]) - plan.delay;
}
void copy_unit(Input& input, const Index& idx, const Unit& unit, const Plan& plan, Output& out) {
    const auto& epoch = idx.epochs[unit.epoch];
    auto bsf = out.stsd ? Bsf{} : annex_b(input.stream->codecpar, input.stream->time_base);
    for (size_t i = epoch.packet_begin; i < epoch.packet_end; ++i) {
        const auto& sample = idx.samples[i]; auto p = read_sample(input, sample);
        // Advertise only true IDRs as random access points in the output MP4.
        p->flags = sample.idr ? AV_PKT_FLAG_KEY : 0;
        if (bsf) {
            check(av_bsf_send_packet(bsf.get(), p.get()), "Filter copied sample");
            check(av_bsf_receive_packet(bsf.get(), p.get()), "Receive copied sample");
        }
        size_t n = i - epoch.packet_begin;
        int64_t duration = idx.times[sample.display + 1] - idx.times[sample.display];
        if (i + 1 == epoch.packet_end) duration = idx.times[unit.end] - idx.times[unit.end - 1];
        auto* par = input.stream->codecpar;
        out.write(p.get(), relative_pts(idx, unit, sample.pts), decode_slot(idx, unit, plan, n), duration,
                  n == 0 ? par->extradata : nullptr, n == 0 ? par->extradata_size : 0);
    }
}
struct Encoder {
    Codec ctx;
    Input& input; const Index& idx; const Unit& unit; const Plan& plan; Output& out;
    size_t sent{}, received{};
    Encoder(Input& in, const Index& index, const Unit& u, const Plan& pl, Output& output, double crf, const AVFrame* first)
        : input(in), idx(index), unit(u), plan(pl), out(output) {
        auto* codec = avcodec_find_encoder_by_name("libx264"); require(codec != nullptr, "FFmpeg was built without libx264");
        ctx.reset(avcodec_alloc_context3(codec)); require(bool(ctx), "Allocate encoder");
        ctx->width = first->width; ctx->height = first->height; ctx->pix_fmt = static_cast<AVPixelFormat>(first->format);
        ctx->time_base = input.stream->time_base; ctx->framerate = input.stream->avg_frame_rate;
        ctx->sample_aspect_ratio = first->sample_aspect_ratio;
        ctx->color_range = first->color_range; ctx->color_primaries = first->color_primaries;
        ctx->color_trc = first->color_trc; ctx->colorspace = first->colorspace; ctx->chroma_sample_location = first->chroma_location;
        ctx->max_b_frames = 0; ctx->gop_size = 250; ctx->thread_count = 4;
        ctx->flags |= AV_CODEC_FLAG_GLOBAL_HEADER;
        check(av_opt_set(ctx->priv_data, "preset", "medium", 0), "Set x264 preset");
        if (crf == 0) check(av_opt_set_int(ctx->priv_data, "qp", 0, 0), "Set lossless QP");
        else check(av_opt_set_double(ctx->priv_data, "crf", crf, 0), "Set CRF");
        // This is intentionally NOT an SPS-ID remapping implementation.
        std::string params = "annexb=1:open-gop=0:bframes=0:scenecut=0:repeat-headers=" + std::string(out.stsd ? "0" : "1");
        check(av_opt_set(ctx->priv_data, "x264-params", params.c_str(), 0), "Set x264 parameters");
        if (crf != 0) {
            int profile = input.stream->codecpar->profile & ~AV_PROFILE_H264_CONSTRAINED;
            const char* name = profile == AV_PROFILE_H264_BASELINE ? "baseline" : profile == AV_PROFILE_H264_MAIN ? "main" :
                               first->format == AV_PIX_FMT_YUV420P10LE ? "high10" : "high";
            check(av_opt_set(ctx->priv_data, "profile", name, 0), "Set x264 profile limit");
        }
        check(avcodec_open2(ctx.get(), codec, nullptr), "Open x264 encoder");
    }
    void drain() {
        auto p = packet(); int result;
        while ((result = avcodec_receive_packet(ctx.get(), p.get())) >= 0) {
            require(received < unit.end - unit.begin, "Encoder produced too many packets");
            int64_t expected = relative_pts(idx, unit, idx.times[unit.begin + received]);
            require(p->pts == expected, "Unexpected encoder presentation order");
            int64_t duration = idx.times[unit.begin + received + 1] - idx.times[unit.begin + received];
            out.write(p.get(), expected, decode_slot(idx, unit, plan, received), duration,
                      received == 0 ? ctx->extradata : nullptr, received == 0 ? ctx->extradata_size : 0);
            ++received; av_packet_unref(p.get());
        }
        require(result == AVERROR(EAGAIN) || result == AVERROR_EOF, "Encoder receive failed");
    }
    void send(AVFrame* f, int64_t original_pts) {
        f->pts = relative_pts(idx, unit, original_pts); f->pict_type = AV_PICTURE_TYPE_NONE;
        f->flags &= ~AV_FRAME_FLAG_KEY;
        f->duration = idx.times[unit.begin + sent + 1] - idx.times[unit.begin + sent];
        // Original HRD/recovery/user SEI does not describe the newly encoded pictures.
        while (f->nb_side_data) av_frame_remove_side_data(f, f->side_data[0]->type);
        check(avcodec_send_frame(ctx.get(), f), "Send frame to x264"); ++sent; drain();
    }
    void finish() {
        check(avcodec_send_frame(ctx.get(), nullptr), "Flush x264"); drain();
        require(received == unit.end - unit.begin && sent == received, "Encoded frame count mismatch");
    }
};
size_t encode_unit(Input& input, const Index& idx, const Unit& unit, const Plan& plan, Output& out, double crf) {
    auto* codec = avcodec_find_decoder(AV_CODEC_ID_H264); require(codec != nullptr, "Missing H.264 decoder");
    Codec decoder(avcodec_alloc_context3(codec)); require(bool(decoder), "Allocate decoder");
    check(avcodec_parameters_to_context(decoder.get(), input.stream->codecpar), "Configure decoder");
    decoder->pkt_timebase = input.stream->time_base; decoder->err_recognition = AV_EF_EXPLODE | AV_EF_CAREFUL;
    decoder->thread_count = 4; // Bound startup cost for short boundary windows.
    check(avcodec_open2(decoder.get(), codec, nullptr), "Open decoder");
    const auto& epoch = idx.epochs[unit.epoch]; auto f = frame(); size_t decoded = 0, kept = 0;
    std::unique_ptr<Encoder> encoder;
    auto drain = [&]() {
        int result;
        while ((result = avcodec_receive_frame(decoder.get(), f.get())) >= 0) {
            require(decoded < epoch.end - epoch.begin, "Decoder produced unexpected extra frames");
            int64_t pts = f->best_effort_timestamp;
            require(pts == idx.times[epoch.begin + decoded], "Decoded frame order differs from sample index");
            require(f->decode_error_flags == 0 && !(f->flags & (AV_FRAME_FLAG_CORRUPT | AV_FRAME_FLAG_INTERLACED)), "Corrupt/interlaced frame");
            require(f->width == input.stream->codecpar->width && f->height == input.stream->codecpar->height && f->format == input.stream->codecpar->format, "Dynamic video format is unsupported");
            if (epoch.begin + decoded >= unit.begin && epoch.begin + decoded < unit.end) {
                if (!encoder) encoder = std::make_unique<Encoder>(input, idx, unit, plan, out, crf, f.get());
                encoder->send(f.get(), pts); ++kept;
            }
            ++decoded; av_frame_unref(f.get());
        }
        if (result != AVERROR(EAGAIN) && result != AVERROR_EOF) check(result, "Decode boundary frames");
    };
    for (size_t i = epoch.packet_begin; i < epoch.packet_end; ++i) {
        auto p = read_sample(input, idx.samples[i]);
        check(avcodec_send_packet(decoder.get(), p.get()), "Send source sample to decoder"); drain();
        if (epoch.begin + decoded >= unit.end) break;
    }
    if (epoch.begin + decoded < unit.end) {
        check(avcodec_send_packet(decoder.get(), nullptr), "Flush decoder"); drain();
    }
    require(epoch.begin + decoded >= unit.end && kept == unit.end - unit.begin && encoder, "Boundary decode frame count mismatch");
    encoder->finish(); return decoded;
}
std::string report_json(const Options& o, const Input& input, const Index& idx, const Plan& p, size_t decoded, double elapsed) {
    std::ostringstream s;
    s << "{\n  \"status\": " << quoted(o.analyze ? "analyzed" : "completed")
      << ",\n  \"input\": " << quoted(o.input) << ",\n  \"output\": " << quoted(o.output)
      << ",\n  \"ffmpeg\": " << quoted(av_version_info()) << ",\n  \"ps_mode\": " << quoted(o.ps_mode)
      << ",\n  \"lossless_full_encode_fallback\": " << ((o.crf == 0 && p.encoded > 0) ? "true" : "false")
      << ",\n  \"time_base\": [" << input.stream->time_base.num << ',' << input.stream->time_base.den << ']'
      << ",\n  \"input_frames\": " << idx.samples.size() << ",\n  \"idr_epochs\": " << idx.epochs.size()
      << ",\n  \"non_idr_key_packets\": " << idx.non_idr_keys
      << ",\n  \"copied_frames\": " << p.copied << ",\n  \"encoded_frames\": " << p.encoded
      << ",\n  \"decoded_frames\": " << decoded << ",\n  \"duration_ticks\": " << p.duration
      << ",\n  \"decode_delay_ticks\": " << p.delay << ",\n  \"elapsed_seconds\": " << elapsed
      << ",\n  \"units\": [";
    for (size_t i = 0; i < p.units.size(); ++i) {
        const auto& u = p.units[i]; const auto& e = idx.epochs[u.epoch];
        s << (i ? "," : "") << "\n    {\"mode\":" << quoted(u.copy ? "copy" : "encode")
          << ",\"begin\":" << u.begin << ",\"end\":" << u.end
          << ",\"decode_begin\":" << e.begin << ",\"decode_end\":" << e.end
          << ",\"output_start_ticks\":" << u.output_start << '}';
    }
    s << "\n  ]\n}\n"; return s.str();
}
} // namespace

void run(const Options& o) {
    av_log_set_level(AV_LOG_WARNING);
    auto started = std::chrono::steady_clock::now();
    require(fs::is_regular_file(path8(o.input)), "Input must be a local regular file");
    require(path8(o.input).extension() == ".mp4" || path8(o.input).extension() == ".MP4", "Input extension must be .mp4");
    if (!o.analyze) {
        require(path8(o.output).extension() == ".mp4" || path8(o.output).extension() == ".MP4", "Output extension must be .mp4");
        require(!fs::exists(path8(o.output)), "Output already exists; no files were overwritten");
    }
    if (!o.report.empty()) {
        require(path8(o.report).extension() == ".json", "Report extension must be .json");
        require(!fs::exists(path8(o.report)), "Report already exists; choose another path");
        require(fs::is_directory(fs::absolute(path8(o.report)).parent_path()), "Report parent directory does not exist");
        require(fs::absolute(path8(o.report)).lexically_normal() != fs::absolute(path8(o.output)).lexically_normal(), "Report path must differ from output");
    }
    Input input(o.input); auto idx = index_input(input); auto p = plan(idx, o.ranges);
    // Lossless H.264 uses transform bypass / High 4:4:4 Predictive even for 4:2:0 pixels.
    // Tests found decoder-state problems when switching that profile to/from copied lossy GOPs.
    // Never advertise mixed-mode losslessness: explicitly re-encode the complete selection instead.
    if (o.crf == 0 && p.encoded > 0) {
        p.encoded += p.copied; p.copied = 0; p.delay = 0;
        for (auto& unit : p.units) unit.copy = false;
        std::cerr << "Lossless fallback: encode the entire selection with QP=0 to avoid mixed lossless/lossy profile transitions.\n";
    }
    std::cerr << "Plan: " << p.copied << " copied, " << p.encoded << " encoded; " << idx.epochs.size()
              << " IDR epochs, " << idx.non_idr_keys << " non-IDR key packets\n";
    if (p.encoded > p.copied) std::cerr << "Note: more than half of the selection needs encoding under the IDR-only safety policy.\n";
    size_t decoded = 0;
    if (!o.analyze) {
        auto suffix = std::to_string(std::chrono::steady_clock::now().time_since_epoch().count());
        auto temporary = o.output + ".partial-" + suffix + ".mp4";
        require(!fs::exists(path8(temporary)), "Temporary filename collision");
        try {
            {
                Output out(input, temporary, o.ps_mode == "stsd");
                for (const auto& unit : p.units) {
                    if (unit.copy) copy_unit(input, idx, unit, p, out);
                    else decoded += encode_unit(input, idx, unit, p, out, o.crf);
                }
                require(out.packets == p.copied + p.encoded, "Output sample count mismatch"); out.finish();
            }
            fs::rename(path8(temporary), path8(o.output));
        } catch (...) { std::error_code error; fs::remove(path8(temporary), error); throw; }
    }
    double seconds = std::chrono::duration<double>(std::chrono::steady_clock::now() - started).count();
    auto json = report_json(o, input, idx, p, decoded, seconds);
    if (!o.report.empty()) {
        std::ofstream report(path8(o.report), std::ios::binary); report << json; report.close();
        require(bool(report), "Unable to write JSON report");
    }
    std::cout << json;
}
} // namespace sr
