// SPDX-License-Identifier: GPL-3.0-or-later
// Research scope: one fixed-format, single-layer AV1 video in a seekable MP4.
// FFmpeg owns decoding, encoding, extradata serialization and MP4 table writing.
#include <algorithm>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>
#ifdef _WIN32
#include <windows.h>
#endif
extern "C" {
#include <libavcodec/avcodec.h>
#include <libavformat/avformat.h>
#include <libavutil/avutil.h>
#include <libavutil/intreadwrite.h>
}
using Bytes = std::vector<uint8_t>;
namespace fs = std::filesystem;
fs::path utf8_path(const std::string &s) {
    return fs::path(std::u8string(s.begin(), s.end()));
}
AVPixelFormat planar_format(int f) {
    return f == AV_PIX_FMT_YUVJ420P ? AV_PIX_FMT_YUV420P : AVPixelFormat(f);
}
void need(bool ok, const std::string &why) {
    if (!ok)
        throw std::runtime_error(why);
}
void check(int code, const char *why) {
    if (code >= 0)
        return;
    char message[AV_ERROR_MAX_STRING_SIZE];
    av_strerror(code, message, sizeof(message));
    throw std::runtime_error(std::string(why) + ": " + message);
}
struct Packet {
    AVPacket *p = av_packet_alloc();
    Packet() { need(p != nullptr, "Packet allocation failed"); }
    ~Packet() { av_packet_free(&p); }
    Packet(const Packet &) = delete;
};
struct Frame {
    AVFrame *p = av_frame_alloc();
    Frame() { need(p != nullptr, "Frame allocation failed"); }
    ~Frame() { av_frame_free(&p); }
};
struct Codec {
    AVCodecContext *p = nullptr;
    explicit Codec(const AVCodec *c) {
        need(c != nullptr, "Requested codec is unavailable");
        p = avcodec_alloc_context3(c);
        need(p != nullptr, "Codec allocation failed");
    }
    ~Codec() { avcodec_free_context(&p); }
};
struct Obu {
    int type;
    size_t begin, payload, end;
};
// Only OBU framing and the first four frame-header bits are interpreted here.
// Tile payloads, reference slots, order hints and frame IDs remain untouched.
std::vector<Obu> obus(const uint8_t *data, size_t size) {
    std::vector<Obu> result;
    size_t p = 0;
    while (p < size) {
        const size_t begin = p;
        const uint8_t h = data[p++];
        need(!(h & 0x81), "Invalid OBU header");
        const int type = (h >> 3) & 15;
        if (h & 4) {
            need(p < size, "Truncated OBU extension");
            need(data[p++] == 0, "Multiple temporal/spatial layers are outside this prototype");
        }
        uint64_t length = size - p;
        if (h & 2) {
            length = 0;
            bool done = false;
            for (int i = 0; i < 8; ++i) {
                need(p < size, "Truncated OBU size");
                const uint8_t v = data[p++];
                length |= uint64_t(v & 127) << (i * 7);
                if (!(v & 128)) {
                    done = true;
                    break;
                }
            }
            need(done, "OBU size overflow");
        }
        need(length <= size - p, "Truncated OBU payload");
        need(type != 8, "Tile-list OBUs are not supported by the MP4 binding");
        result.push_back({type, begin, p, p + size_t(length)});
        p += size_t(length);
    }
    return result;
}
struct Inspection {
    Bytes sequence;
    bool sync = false;
    int shown = 0, hidden = 0, existing = 0;
};
Inspection inspect(const uint8_t *data, size_t size) {
    Inspection result;
    bool first = true;
    for (const auto &o : obus(data, size)) {
        if (o.type == 1) {
            need(o.end > o.payload, "Empty sequence header");
            need((data[o.payload] & 0x18) == 0,
                 "Still-picture/reduced headers are outside this prototype");
            need(result.sequence.empty(), "Multiple sequence headers in one temporal unit");
            result.sequence.assign(data + o.begin, data + o.end);
        } else if (o.type == 3 || o.type == 6) {
            need(o.end > o.payload, "Empty frame header");
            const uint8_t h = data[o.payload];
            const bool existing = (h & 128) != 0;
            const int type = (h >> 5) & 3;
            const bool shown = existing || (h & 16);
            if (first) {
                result.sync = !existing && type == 0 && shown && !result.sequence.empty();
                first = false;
            }
            result.shown += shown;
            result.hidden += !shown;
            result.existing += existing;
        }
    }
    return result;
}
Bytes sequence_from_extra(const AVCodecParameters *p) {
    if (!p->extradata_size)
        return {};
    const auto *data = p->extradata;
    size_t size = size_t(p->extradata_size);
    if (data[0] & 128) {
        need(size >= 4 && data[0] == 0x81, "Invalid av1C");
        data += 4;
        size -= 4;
    }
    return inspect(data, size).sequence;
}
struct Sample {
    int64_t pos, pts, duration;
    int size, flags;
    bool sync;
};
struct Input {
    AVFormatContext *ctx = nullptr;
    AVIOContext *raw = nullptr;
    AVStream *stream = nullptr;
    int video = -1;
    std::vector<Sample> samples;
    std::vector<size_t> keys;
    std::vector<int64_t> times;
    Bytes sequence;
    AVRational sar{1, 1};
    int hidden = 0, existing = 0;
    ~Input() {
        avformat_close_input(&ctx);
        if (raw)
            avio_closep(&raw);
    }
    void open(const std::string &path) {
        check(avformat_open_input(&ctx, path.c_str(), nullptr, nullptr), "Open input");
        check(avformat_find_stream_info(ctx, nullptr), "Find stream info");
        need(std::string(ctx->iformat->name).find("mov") != std::string::npos, "Input must be MP4");
        for (unsigned i = 0; i < ctx->nb_streams; ++i)
            if (ctx->streams[i]->codecpar->codec_type == AVMEDIA_TYPE_VIDEO) {
                need(video < 0, "Exactly one video track is required");
                video = int(i);
            }
        need(video >= 0, "No video track");
        stream = ctx->streams[video];
        const auto *p = stream->codecpar;
        need(p->codec_id == AV_CODEC_ID_AV1 && p->codec_tag == MKTAG('a', 'v', '0', '1'),
             "Unencrypted av01 video is required");
        need(planar_format(p->format) == AV_PIX_FMT_YUV420P || p->format == AV_PIX_FMT_YUV420P10LE,
             "Only 4:2:0 8/10-bit is supported");
        need(p->width > 0 && p->height > 0 && p->profile == AV_PROFILE_AV1_MAIN,
             "AV1 Main profile is required");
        need(stream->time_base.num > 0 && stream->time_base.den > 0, "Invalid stream clock");
        sar = av_guess_sample_aspect_ratio(ctx, stream, nullptr);
        if (sar.num <= 0 || sar.den <= 0)
            sar = {1, 1};
        sequence = sequence_from_extra(p);
        Packet packet;
        for (;;) {
            int r = av_read_frame(ctx, packet.p);
            if (r == AVERROR_EOF)
                break;
            check(r, "Read input packet");
            if (packet.p->stream_index == video) {
                auto *q = packet.p;
                need(q->pos >= 0 && q->size > 0 && q->pts != AV_NOPTS_VALUE && q->duration > 0,
                     "Indexed samples need positions, PTS and duration");
                need(!(q->flags & AV_PKT_FLAG_CORRUPT), "Corrupt input packet");
                auto info = inspect(q->data, size_t(q->size));
                need(info.shown == 1, "Each input sample must present exactly one frame");
                if (!info.sequence.empty()) {
                    // Some encoders finalize coding-tool flags only in their
                    // first emitted SH. The sync sample defines the first CVS.
                    if (samples.empty() || sequence.empty())
                        sequence = info.sequence;
                    need(sequence == info.sequence,
                         "Changing sequence headers in the input are not supported");
                }
                need(!(q->flags & AV_PKT_FLAG_KEY) || info.sync,
                     "Container key flag is not a shown KEY_FRAME with a sequence header");
                if (info.sync)
                    keys.push_back(samples.size());
                hidden += info.hidden;
                existing += info.existing;
                if (!times.empty()) {
                    need(q->pts > times.back(), "Temporal-unit PTS must increase strictly");
                    need(q->pts - times.back() == samples.back().duration,
                         "Gapped/overlapping sample durations are not supported");
                }
                samples.push_back({q->pos, q->pts, q->duration, q->size, q->flags, info.sync});
                times.push_back(q->pts);
            }
            av_packet_unref(packet.p);
        }
        need(!samples.empty() && !keys.empty() && keys.front() == 0 && !sequence.empty(),
             "Input must start at a verified random access point");
        need(times.back() <= INT64_MAX - samples.back().duration, "Timeline overflow");
        times.push_back(times.back() + samples.back().duration);
        keys.push_back(samples.size());
        check(avio_open(&raw, path.c_str(), AVIO_FLAG_READ), "Open sample reader");
    }
    void read(size_t i, AVPacket *q) {
        av_packet_unref(q);
        const auto &s = samples.at(i);
        check(av_new_packet(q, s.size), "Allocate sample");
        need(avio_seek(raw, s.pos, SEEK_SET) == s.pos, "Sample seek failed");
        need(avio_read(raw, q->data, s.size) == s.size, "Short sample read");
        q->pts = q->dts = s.pts;
        q->duration = s.duration;
        q->flags = s.sync ? AV_PKT_FLAG_KEY : 0;
        q->time_base = stream->time_base;
    }
};
struct Unit {
    size_t begin, end, decode_begin, decode_end;
    int64_t offset;
    bool copy;
};
struct Options {
    std::string input, output, report, encoder = "libaom-av1";
    std::vector<std::pair<size_t, size_t>> ranges;
    int crf = 18;
    bool analyze = false, single_config = false, drop_copy_sequence = false;
};
std::vector<Unit> plan(const Input &in, Options &options) {
    auto &ranges = options.ranges;
    need(!ranges.empty(), "At least one --keep A:B range is required");
    std::vector<std::pair<size_t, size_t>> normalized;
    for (auto [a, b] : ranges) {
        need(a < b && b <= in.samples.size(), "Invalid frame range");
        need(normalized.empty() || a >= normalized.back().second,
             "Ranges must be ordered and disjoint");
        if (!normalized.empty() && a == normalized.back().second)
            normalized.back().second = b;
        else
            normalized.emplace_back(a, b);
    }
    ranges = normalized;
    std::vector<Unit> units;
    int64_t position = 0;
    for (auto [a, b] : ranges) {
        need(in.times[b] - in.times[a] <= INT64_MAX - position, "Output timeline overflow");
        for (size_t k = 0; k + 1 < in.keys.size(); ++k) {
            auto begin = std::max(a, in.keys[k]), end = std::min(b, in.keys[k + 1]);
            if (begin >= end)
                continue;
            units.push_back({begin, end, in.keys[k], in.keys[k + 1],
                             position + in.times[begin] - in.times[a],
                             begin == in.keys[k] && end == in.keys[k + 1]});
        }
        position += in.times[b] - in.times[a];
    }
    return units;
}
void set_extra(AVCodecParameters *p, const Bytes &bytes) {
    av_freep(&p->extradata);
    p->extradata = static_cast<uint8_t *>(av_mallocz(bytes.size() + AV_INPUT_BUFFER_PADDING_SIZE));
    need(p->extradata != nullptr, "Extradata allocation failed");
    std::memcpy(p->extradata, bytes.data(), bytes.size());
    p->extradata_size = int(bytes.size());
}
struct Output {
    AVFormatContext *ctx = nullptr;
    AVStream *stream = nullptr;
    AVRational clock{};
    bool single;
    int64_t last = -1;
    size_t count = 0;
    Bytes active;
    size_t switches = 0;
    explicit Output(bool one) : single(one) {}
    ~Output() {
        if (ctx) {
            if (ctx->pb)
                avio_closep(&ctx->pb);
            avformat_free_context(ctx);
        }
    }
    void open(const std::string &path, const Input &in) {
        check(avformat_alloc_output_context2(&ctx, nullptr, "mp4", path.c_str()), "Allocate muxer");
        stream = avformat_new_stream(ctx, nullptr);
        need(stream != nullptr, "Create output track");
        check(avcodec_parameters_copy(stream->codecpar, in.stream->codecpar),
              "Copy stream parameters");
        set_extra(stream->codecpar, in.sequence);
        clock = in.stream->time_base;
        stream->time_base = clock;
        stream->sample_aspect_ratio = in.sar;
        stream->codecpar->sample_aspect_ratio = in.sar;
        active = in.sequence;
        AVDictionary *opts = nullptr;
        av_dict_set(&opts, "movie_timescale", std::to_string(clock.den).c_str(), 0);
        av_dict_set(&opts, "video_track_timescale", std::to_string(clock.den).c_str(), 0);
        check(avio_open(&ctx->pb, path.c_str(), AVIO_FLAG_WRITE), "Open output");
        const int r = avformat_write_header(ctx, &opts);
        av_dict_free(&opts);
        check(r, "Write MP4 header");
    }
    void write(AVPacket *p, int64_t pts, int64_t duration, const Bytes &sequence) {
        need(pts > last && duration > 0, "Invalid output temporal-unit timing");
        if (!single && sequence != active) {
            auto *extra = av_packet_new_side_data(p, AV_PKT_DATA_NEW_EXTRADATA, sequence.size());
            need(extra != nullptr, "Packet extradata allocation failed");
            std::memcpy(extra, sequence.data(), sequence.size());
            active = sequence;
            ++switches;
        }
        p->pts = p->dts = pts;
        p->duration = duration;
        p->pos = -1;
        p->stream_index = stream->index;
        av_packet_rescale_ts(p, clock, stream->time_base);
        check(av_interleaved_write_frame(ctx, p), "Mux temporal unit");
        last = pts;
        ++count;
    }
    void finish() {
        check(av_write_trailer(ctx), "Write MP4 trailer");
        check(avio_closep(&ctx->pb), "Close output");
    }
};
void remove_sequence(AVPacket *p) {
    Bytes data;
    for (auto &o : obus(p->data, size_t(p->size)))
        if (o.type != 1)
            data.insert(data.end(), p->data + o.begin, p->data + o.end);
    check(av_packet_make_writable(p), "Make packet writable");
    std::memcpy(p->data, data.data(), data.size());
    av_shrink_packet(p, int(data.size()));
}
void encode(Input &in, const Unit &u, const Options &opt, Output &out, size_t &decoded) {
    Codec decoder(avcodec_find_decoder_by_name("libdav1d"));
    check(avcodec_parameters_to_context(decoder.p, in.stream->codecpar), "Configure decoder");
    decoder.p->pkt_timebase = in.stream->time_base;
    decoder.p->thread_count = 4;
    decoder.p->err_recognition = AV_EF_EXPLODE;
    check(avcodec_open2(decoder.p, decoder.p->codec, nullptr), "Open decoder");
    const AVCodec *codec = avcodec_find_encoder_by_name(opt.encoder.c_str());
    need(codec && codec->id == AV_CODEC_ID_AV1, "An AV1 encoder is required");
    Codec encoder(codec);
    auto *enc = encoder.p;
    auto *par = in.stream->codecpar;
    enc->width = par->width;
    enc->height = par->height;
    enc->pix_fmt = planar_format(par->format);
    enc->sample_aspect_ratio = in.sar;
    enc->color_range = par->color_range;
    enc->color_primaries = par->color_primaries;
    enc->color_trc = par->color_trc;
    enc->colorspace = par->color_space;
    enc->chroma_sample_location = par->chroma_location;
    enc->framerate = av_guess_frame_rate(in.ctx, in.stream, nullptr);
    if (enc->framerate.num <= 0)
        enc->framerate = {25, 1};
    enc->time_base = av_inv_q(enc->framerate);
    enc->gop_size = 100000;
    enc->thread_count = 4;
    AVDictionary *opts = nullptr;
    av_dict_set(&opts, "crf", std::to_string(opt.crf).c_str(), 0);
    if (opt.encoder == "libaom-av1") {
        av_dict_set(&opts, "cpu-used", "8", 0);
        av_dict_set(&opts, "lag-in-frames", "0", 0);
        av_dict_set(&opts, "row-mt", "1", 0);
    } else if (opt.encoder == "libsvtav1") {
        av_dict_set(&opts, "preset", "8", 0);
        av_dict_set(&opts, "svtav1-params", "lp=4", 0);
    }
    int r = avcodec_open2(enc, codec, &opts);
    const bool all_used = av_dict_count(opts) == 0;
    av_dict_free(&opts);
    check(r, "Open boundary encoder");
    need(all_used, "An encoder option was not consumed");
    Frame frame;
    Packet packet, encoded;
    size_t sent = 0, received = 0;
    Bytes sequence;
    auto receive_packets = [&] {
        for (;;) {
            int result = avcodec_receive_packet(enc, encoded.p);
            if (result == AVERROR(EAGAIN) || result == AVERROR_EOF)
                break;
            check(result, "Receive encoded packet");
            auto *p = encoded.p;
            need(p->pts >= 0 && size_t(p->pts) == received && received < u.end - u.begin,
                 "Encoder did not produce ordered one-display temporal units");
            const auto info = inspect(p->data, size_t(p->size));
            need(info.shown == 1, "Encoder sample must show exactly one frame");
            if (!info.sequence.empty())
                sequence = info.sequence;
            need(!sequence.empty() && (received != 0 || info.sync),
                 "Boundary must begin with a sequence header and shown key frame");
            p->flags = info.sync ? AV_PKT_FLAG_KEY : 0;
            const size_t i = u.begin + received;
            out.write(p, u.offset + in.times[i] - in.times[u.begin], in.times[i + 1] - in.times[i],
                      sequence);
            ++received;
            av_packet_unref(p);
        }
    };
    auto receive_frames = [&] {
        for (;;) {
            int result = avcodec_receive_frame(decoder.p, frame.p);
            if (result == AVERROR(EAGAIN) || result == AVERROR_EOF)
                break;
            check(result, "Decode boundary");
            ++decoded;
            auto *f = frame.p;
            need(f->decode_error_flags == 0, "Decoder reported a damaged frame");
            need(f->width == par->width && f->height == par->height &&
                     planar_format(f->format) == planar_format(par->format),
                 "Decoded format changed");
            auto it = std::lower_bound(in.times.begin(), in.times.end() - 1, f->pts);
            need(it != in.times.end() - 1 && *it == f->pts,
                 "Decoded PTS is absent from the sample index");
            size_t i = size_t(it - in.times.begin());
            if (i >= u.begin && i < u.end) {
                need(i == u.begin + sent, "Unexpected displayed frame order");
                f->format = enc->pix_fmt;
                f->sample_aspect_ratio = in.sar;
                f->pts = int64_t(sent);
                f->duration = 1;
                f->pict_type = AV_PICTURE_TYPE_NONE;
                f->flags &= ~AV_FRAME_FLAG_KEY;
                check(avcodec_send_frame(enc, f), "Send boundary frame");
                ++sent;
                receive_packets();
            }
            av_frame_unref(f);
        }
    };
    for (size_t i = u.decode_begin; i < u.decode_end; ++i) {
        in.read(i, packet.p);
        check(avcodec_send_packet(decoder.p, packet.p), "Send decode packet");
        receive_frames();
    }
    check(avcodec_send_packet(decoder.p, nullptr), "Flush decoder");
    receive_frames();
    need(sent == u.end - u.begin, "Not all selected frames were decoded");
    check(avcodec_send_frame(enc, nullptr), "Flush encoder");
    receive_packets();
    need(received == sent, "Encoder frame count mismatch");
}
Options parse(const std::vector<std::string> &args) {
    Options o;
    for (size_t i = 1; i < args.size(); ++i) {
        const auto &key = args[i];
        if (key == "--analyze")
            o.analyze = true;
        else if (key == "--experimental-single-config")
            o.single_config = true;
        else if (key == "--experimental-drop-copy-sequence")
            o.drop_copy_sequence = true;
        else {
            need(i + 1 < args.size(), "Missing option value");
            const auto &value = args[++i];
            if (key == "-i")
                o.input = value;
            else if (key == "-o")
                o.output = value;
            else if (key == "--report")
                o.report = value;
            else if (key == "--encoder")
                o.encoder = value;
            else if (key == "--crf") {
                o.crf = std::stoi(value);
                need(o.crf >= 0 && o.crf <= 63, "CRF outside 0..63");
            } else if (key == "--keep") {
                auto pos = value.find(':');
                need(pos != std::string::npos, "Use --keep A:B");
                need(!value.empty() && value[0] != '-' && pos + 1 < value.size() &&
                         value[pos + 1] != '-',
                     "Frame numbers must be nonnegative");
                size_t used = 0;
                auto a = std::stoull(value.substr(0, pos), &used);
                need(used == pos, "Invalid start frame");
                auto b = std::stoull(value.substr(pos + 1), &used);
                need(used == value.size() - pos - 1, "Invalid end frame");
                o.ranges.emplace_back(size_t(a), size_t(b));
            } else
                throw std::runtime_error("Unknown option: " + key);
        }
    }
    need(!o.input.empty(), "An input is required");
    need(o.analyze || !o.output.empty(), "An output is required");
    return o;
}
int execute(const std::vector<std::string> &args) {
    if (args.size() == 2 && args[1] == "--help") {
        std::cout
            << "av1_smart_render -i input.mp4 -o output.mp4 --keep A:B [--keep C:D] [--encoder "
               "libaom-av1|libsvtav1] [--crf 18] [--report plan.json] [--analyze]\n";
        return 0;
    }
    fs::path temporary;
    try {
        av_log_set_level(AV_LOG_WARNING);
        auto opt = parse(args);
        Input input;
        input.open(opt.input);
        auto units = plan(input, opt);
        size_t copied = 0, encoded = 0, decoded = 0, switches = 0;
        for (auto &u : units)
            (u.copy ? copied : encoded) += u.end - u.begin;
        if (!opt.report.empty())
            need(!fs::exists(utf8_path(opt.report)), "Report already exists");
        if (!opt.report.empty() && !opt.output.empty())
            need(fs::absolute(utf8_path(opt.report)).lexically_normal() !=
                     fs::absolute(utf8_path(opt.output)).lexically_normal(),
                 "Report and video paths must differ");
        if (!opt.analyze) {
            auto target = utf8_path(opt.output);
            need(!fs::exists(target), "Output already exists");
            auto candidate = target;
            candidate += ".partial.mp4";
            need(!fs::exists(candidate), "Temporary output already exists");
            temporary = candidate;
            {
                Output output(opt.single_config);
                auto utf = temporary.u8string();
                output.open(std::string(utf.begin(), utf.end()), input);
                Packet packet;
                for (auto &u : units) {
                    if (u.copy)
                        for (size_t i = u.begin; i < u.end; ++i) {
                            input.read(i, packet.p);
                            if (opt.drop_copy_sequence)
                                remove_sequence(packet.p);
                            output.write(packet.p, u.offset + input.times[i] - input.times[u.begin],
                                         input.times[i + 1] - input.times[i], input.sequence);
                        }
                    else
                        encode(input, u, opt, output, decoded);
                }
                need(output.count == copied + encoded, "Output frame count mismatch");
                switches = output.switches;
                output.finish();
            }
            fs::rename(temporary, target);
            temporary.clear();
        }
        std::ostringstream json;
        json << "{\"input_frames\":" << input.samples.size() << ",\"copied_frames\":" << copied
             << ",\"encoded_frames\":" << encoded << ",\"decoded_frames\":" << decoded
             << ",\"configuration_switches\":" << switches
             << ",\"hidden_frames_in_source\":" << input.hidden
             << ",\"show_existing_in_source\":" << input.existing << ",\"units\":[";
        for (size_t i = 0; i < units.size(); ++i) {
            const auto &u = units[i];
            if (i)
                json << ',';
            json << "{\"begin\":" << u.begin << ",\"end\":" << u.end
                 << ",\"copy\":" << (u.copy ? "true" : "false") << '}';
        }
        json << "]}\n";
        std::cout << json.str();
        if (!opt.report.empty()) {
            need(!fs::exists(utf8_path(opt.report)), "Report path appeared during export");
            std::ofstream report(utf8_path(opt.report));
            report << json.str();
            need(bool(report), "Write report failed");
        }
        return 0;
    } catch (const std::exception &e) {
        if (!temporary.empty()) {
            std::error_code ec;
            fs::remove(temporary, ec);
        }
        std::cerr << "av1_smart_render: " << e.what() << '\n';
        return 1;
    }
}
#ifdef _WIN32
int wmain(int argc, wchar_t **argv) {
    std::vector<std::string> args;
    for (int i = 0; i < argc; ++i) {
        int n = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, argv[i], -1, nullptr, 0, nullptr,
                                    nullptr);
        if (n <= 0)
            return 1;
        std::string s(size_t(n), '\0');
        WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, argv[i], -1, s.data(), n, nullptr,
                            nullptr);
        s.pop_back();
        args.push_back(std::move(s));
    }
    return execute(args);
}
#else
int main(int argc, char **argv) {
    return execute(std::vector<std::string>(argv, argv + argc));
}
#endif
