// SPDX-License-Identifier: GPL-3.0-or-later
// IDR / CRA planning and parameter-set switching are derived from this
// repository's validated research/h264 and research/h265 implementations.
#include "media.hpp"
#include <algorithm>
#include <cstring>
#include <iostream>
#include <limits>
#include <numeric>

namespace smartcut {
bool is_irap(AVCodecID codec, int type) {
    if (codec == AV_CODEC_ID_AV1)
        return type == 0;
    return codec == AV_CODEC_ID_H264 ? type == 5 : type >= 16 && type <= 21;
}
bool is_idr(AVCodecID codec, int type) {
    if (codec == AV_CODEC_ID_AV1)
        return type == 0;
    return codec == AV_CODEC_ID_H264 ? type == 5 : type == 19 || type == 20;
}
static bool rasl(int type) {
    return type == 8 || type == 9;
}
static bool radl(int type) {
    return type == 6 || type == 7;
}
static Bytes bytes(const uint8_t *data, size_t size) {
    return {data, data + size};
}
Input::Input(const std::string &filename, int video_stream) : video(video_stream) {
    format.open(filename);
    require(video >= 0 && video < static_cast<int>(format.value->nb_streams),
            "Selected video stream is missing");
    stream = format.value->streams[video];
    auto *p = stream->codecpar;
    require(std::string(format.value->iformat->name).find("mov") != std::string::npos,
            "Smart rendering currently requires MP4 input");
    require(p->codec_id == AV_CODEC_ID_H264 || p->codec_id == AV_CODEC_ID_HEVC ||
                p->codec_id == AV_CODEC_ID_AV1,
            "Selected video must be H.264, HEVC or AV1");
    bool avc = p->codec_id == AV_CODEC_ID_H264;
    bool av1 = p->codec_id == AV_CODEC_ID_AV1;
    require(avc   ? (p->codec_tag == MKTAG('a', 'v', 'c', '1') ||
                     p->codec_tag == MKTAG('a', 'v', 'c', '3'))
            : av1 ? p->codec_tag == MKTAG('a', 'v', '0', '1')
                  : (p->codec_tag == MKTAG('h', 'v', 'c', '1') ||
                     p->codec_tag == MKTAG('h', 'e', 'v', '1')),
            "Unsupported or encrypted MP4 video sample entry");
    require(av1 || p->field_order == AV_FIELD_PROGRESSIVE,
            "Only positively identified progressive video is supported");
    require(p->width > 0 && p->height > 0, "Invalid video dimensions");
    require(p->format == AV_PIX_FMT_YUV420P || p->format == AV_PIX_FMT_YUVJ420P ||
                p->format == AV_PIX_FMT_YUV420P10LE,
            "Video must be 4:2:0 8-bit or 10-bit");
    if (avc) {
        int profile = p->profile & ~AV_PROFILE_H264_CONSTRAINED;
        require(profile == AV_PROFILE_H264_BASELINE || profile == AV_PROFILE_H264_MAIN ||
                    profile == AV_PROFILE_H264_HIGH || profile == AV_PROFILE_H264_HIGH_10,
                "Unsupported H.264 input profile");
    } else if (av1)
        require(p->profile == AV_PROFILE_AV1_MAIN, "AV1 input must use Main profile");
    else
        require(p->profile == AV_PROFILE_HEVC_MAIN || p->profile == AV_PROFILE_HEVC_MAIN_10,
                "HEVC input must use Main or Main 10");
    require(stream->time_base.num > 0 && stream->time_base.den > 0, "Invalid video time base");
    check(avio_open(&raw, filename.c_str(), AVIO_FLAG_READ), "Open compressed video sample reader");
}
static void read_configuration(Index &idx, const AVCodecParameters *par) {
    auto *data = par->extradata;
    size_t size = static_cast<size_t>(par->extradata_size);
    if (idx.codec == AV_CODEC_ID_H264) {
        require(size >= 7 && data[0] == 1, "An avcC record is required");
        idx.nal_length = (data[4] & 3) + 1;
        size_t pos = 6;
        auto collect = [&](unsigned count, int type) {
            for (unsigned i = 0; i < count; ++i) {
                require(pos + 2 <= size, "Truncated avcC");
                size_t n = (data[pos] << 8) | data[pos + 1];
                pos += 2;
                require(n > 0 && pos + n <= size && (data[pos] & 31) == type,
                        "Invalid avcC parameter set");
                idx.parameter_sets.insert(bytes(data + pos, n));
                pos += n;
            }
        };
        unsigned count = data[5] & 31;
        require(count > 0, "Missing H.264 SPS");
        collect(count, 7);
        require(pos < size && data[pos] > 0, "Missing H.264 PPS");
        count = data[pos++];
        collect(count, 8);
    } else {
        require(size >= 23 && data[0] == 1, "An hvcC record is required");
        idx.nal_length = (data[21] & 3) + 1;
        size_t pos = 23;
        std::set<int> found;
        for (unsigned a = 0; a < data[22]; ++a) {
            require(pos + 3 <= size, "Truncated hvcC");
            int type = data[pos++] & 63;
            unsigned count = (data[pos] << 8) | data[pos + 1];
            pos += 2;
            if (type >= 32 && type <= 34)
                require(count == 1 && !found.contains(type),
                        "One fixed HEVC VPS/SPS/PPS configuration is required");
            for (unsigned i = 0; i < count; ++i) {
                require(pos + 2 <= size, "Truncated hvcC");
                size_t n = (data[pos] << 8) | data[pos + 1];
                pos += 2;
                require(n >= 2 && pos + n <= size && ((data[pos] >> 1) & 63) == type,
                        "Invalid hvcC NAL");
                if (type >= 32 && type <= 34) {
                    idx.parameter_sets.insert(bytes(data + pos, n));
                    found.insert(type);
                }
                pos += n;
            }
        }
        require(found.size() == 3 && pos == size, "Missing or invalid HEVC parameter sets");
    }
}
static unsigned first_mb(std::span<const uint8_t> nal) {
    Bytes rbsp;
    int zeros = 0;
    for (size_t i = 1; i < nal.size() && rbsp.size() < 12; ++i) {
        auto b = nal[i];
        if (zeros == 2 && b == 3) {
            zeros = 0;
            continue;
        }
        rbsp.push_back(b);
        zeros = b == 0 ? zeros + 1 : 0;
    }
    size_t bit = 0;
    auto read = [&]() {
        require(bit < rbsp.size() * 8, "Truncated H.264 slice header");
        auto b = (rbsp[bit / 8] >> (7 - bit % 8)) & 1;
        ++bit;
        return b;
    };
    unsigned zeros_count = 0;
    while (!read())
        require(++zeros_count < 31, "Invalid H.264 slice header");
    unsigned value = 1;
    for (unsigned i = 0; i < zeros_count; ++i)
        value = (value << 1) | read();
    return value - 1;
}
static int inspect_sample(const Index &idx, const AVPacket *packet) {
    size_t pos = 0;
    int picture = -1;
    unsigned firsts = 0;
    while (pos < static_cast<size_t>(packet->size)) {
        require(pos + idx.nal_length <= static_cast<size_t>(packet->size), "Truncated NAL length");
        uint32_t n = 0;
        for (int j = 0; j < idx.nal_length; ++j)
            n = (n << 8) | packet->data[pos++];
        require(n > 0 && n <= static_cast<size_t>(packet->size) - pos, "Invalid NAL length");
        auto *nal = packet->data + pos;
        if (idx.codec == AV_CODEC_ID_H264) {
            require(!(nal[0] & 0x80), "Invalid H.264 NAL header");
            int type = nal[0] & 31;
            if (type == 1 || type == 5) {
                require(picture < 0 || picture == type, "Mixed access units in one sample");
                picture = type;
                if (first_mb({nal, n}) == 0)
                    ++firsts;
            } else if (type == 7 || type == 8)
                require(idx.parameter_sets.contains(bytes(nal, n)),
                        "Dynamic H.264 parameter sets are unsupported as input");
            else
                require(type == 6 || type == 9 || type == 10 || type == 11 || type == 12,
                        "Unsupported H.264 extension/data-partition NAL");
        } else {
            require(n >= 2 && !(nal[0] & 0x80) && (nal[1] & 7) != 0 &&
                        ((nal[0] & 1) * 32 + (nal[1] >> 3)) == 0,
                    "Invalid or multi-layer HEVC NAL");
            int type = (nal[0] >> 1) & 63;
            if (type <= 31) {
                require(n >= 3 && (type <= 9 || is_irap(idx.codec, type)),
                        "Unsupported HEVC VCL NAL");
                require(picture < 0 || picture == type, "Mixed HEVC pictures in one sample");
                picture = type;
                if (nal[2] & 0x80)
                    ++firsts;
            } else if (type >= 32 && type <= 34)
                require(idx.parameter_sets.contains(bytes(nal, n)),
                        "Dynamic HEVC parameter sets are unsupported as input");
            else
                require(type >= 35 && type <= 40, "Unsupported HEVC non-VCL NAL");
        }
        pos += n;
    }
    require(firsts == 1 && picture >= 0, "Each sample must contain one progressive access unit");
    return picture;
}
Index index_input(Input &input) {
    if (input.stream->codecpar->codec_id == AV_CODEC_ID_AV1)
        return index_av1(input);
    Index idx;
    idx.codec = input.stream->codecpar->codec_id;
    read_configuration(idx, input.stream->codecpar);
    auto p = packet();
    int result;
    int64_t length = avio_size(input.raw);
    require(length >= 0, "Cannot determine the input file size");
    while ((result = av_read_frame(input.format.value, p.get())) >= 0) {
        cancellation_point();
        if (p->stream_index == input.video) {
            require(p->pts != AV_NOPTS_VALUE && p->dts != AV_NOPTS_VALUE,
                    "Video samples require PTS and DTS");
            require(!(p->flags & (AV_PKT_FLAG_CORRUPT | AV_PKT_FLAG_DISCARD)),
                    "Corrupt or edit-list discard video samples are unsupported");
            require(p->pos >= 0 && p->size > 0 && p->pos <= length - p->size,
                    "Video samples must be directly addressable in the MP4");
            require(idx.samples.empty() || p->dts > idx.samples.back().dts,
                    "Video DTS must increase strictly");
            for (int i = 0; i < p->side_data_elems; ++i) {
                auto &side = p->side_data[i];
                require(side.type != AV_PKT_DATA_ENCRYPTION_INFO, "Encrypted input is unsupported");
                if (side.type == AV_PKT_DATA_NEW_EXTRADATA)
                    require(
                        side.size == static_cast<size_t>(input.stream->codecpar->extradata_size) &&
                            !std::memcmp(side.data, input.stream->codecpar->extradata, side.size),
                        "Changing sample descriptions are unsupported as input");
            }
            idx.samples.push_back({p->pos, p->pts, p->dts, p->duration, p->size, p->flags,
                                   inspect_sample(idx, p.get()), 0});
        }
        av_packet_unref(p.get());
    }
    require(result == AVERROR_EOF, "Input demux failed before EOF");
    require(!idx.samples.empty() && is_idr(idx.codec, idx.samples[0].type),
            "Video must begin with a real IDR access unit");
    idx.display.resize(idx.samples.size());
    std::iota(idx.display.begin(), idx.display.end(), size_t{0});
    std::sort(idx.display.begin(), idx.display.end(),
              [&](size_t a, size_t b) { return idx.samples[a].pts < idx.samples[b].pts; });
    for (size_t i = 0; i < idx.display.size(); ++i) {
        auto &sample = idx.samples[idx.display[i]];
        sample.display = i;
        require(i == 0 || sample.pts > idx.times.back(),
                "Duplicate display timestamps are unsupported");
        idx.times.push_back(sample.pts);
    }
    auto duration = idx.samples[idx.display.back()].duration;
    require(duration > 0, "Last display frame requires a positive duration");
    require(idx.times.back() <= std::numeric_limits<int64_t>::max() - duration,
            "Last frame timestamp overflows the time base");
    idx.times.push_back(idx.times.back() + duration);
    size_t start = 0;
    for (size_t end = 1; end <= idx.samples.size(); ++end) {
        if (end != idx.samples.size() && !is_irap(idx.codec, idx.samples[end].type))
            continue;
        auto lo = idx.samples[start].display, hi = lo;
        bool leading_radl = false;
        for (size_t i = start; i < end; ++i) {
            lo = std::min(lo, idx.samples[i].display);
            hi = std::max(hi, idx.samples[i].display);
            if (idx.codec == AV_CODEC_ID_HEVC) {
                bool leading = idx.samples[i].pts < idx.samples[start].pts;
                require(leading == (rasl(idx.samples[i].type) || radl(idx.samples[i].type)),
                        "Unexpected HEVC leading-picture layout");
                leading_radl |= radl(idx.samples[i].type);
            }
        }
        require(hi - lo + 1 == end - start &&
                    lo == (idx.epochs.empty() ? 0 : idx.epochs.back().end),
                "Interleaved access-point presentation ranges are unsupported");
        idx.epochs.push_back({start, end, lo, hi + 1, idx.samples[start].display, leading_radl});
        start = end;
    }
    return idx;
}
Plan make_plan(const Index &idx, const Options &options, AVRational tb, int64_t format_start_us) {
    Plan plan;
    if (!options.frame_ranges.empty()) {
        int64_t previous = -1;
        for (auto range : options.frame_ranges) {
            require(range.begin >= 0 && range.begin < range.end && range.begin >= previous &&
                        range.end <= static_cast<int64_t>(idx.display.size()),
                    "Frame ranges must be ordered, non-overlapping and inside the video");
            previous = range.end;
            if (!plan.ranges.empty() && plan.ranges.back().end == range.begin)
                plan.ranges.back().end = range.end;
            else
                plan.ranges.push_back(range);
        }
        plan.origin_ticks = idx.times[static_cast<size_t>(plan.ranges[0].begin)];
        auto b = static_cast<size_t>(plan.ranges.back().end);
        plan.last_duration = idx.times[b] - idx.times[b - 1];
    } else {
        constexpr int64_t infinity = std::numeric_limits<int64_t>::max() / 4;
        auto add = [&](int64_t a, int64_t b) {
            require(a < infinity && b < infinity && a <= infinity - b, "Cut time is too large");
            return a + b;
        };
        auto start = add(options.input_times.start, options.output_times.start);
        int64_t end = infinity;
        if (options.input_times.duration)
            end = add(options.input_times.start, *options.input_times.duration);
        else if (options.input_times.end)
            end = *options.input_times.end;
        if (options.output_times.duration)
            end = std::min(end, add(start, *options.output_times.duration));
        else if (options.output_times.end)
            end = std::min(end, add(options.input_times.start, *options.output_times.end));
        require(end > start, "Cut end must be after the start");
        auto absolute_start = add(format_start_us, start);
        auto absolute_end = end == infinity ? infinity : add(format_start_us, end);
        auto bound = [&](int64_t time) {
            return static_cast<size_t>(
                std::lower_bound(idx.times.begin(), idx.times.end() - 1, time,
                                 [&](int64_t pts, int64_t us) {
                                     return av_compare_ts(pts, tb, us, AV_TIME_BASE_Q) < 0;
                                 }) -
                idx.times.begin());
        };
        size_t a = bound(absolute_start),
               b = absolute_end == infinity ? idx.display.size() : bound(absolute_end);
        require(a < b, "The selected interval contains no video frames");
        plan.ranges.push_back({static_cast<int64_t>(a), static_cast<int64_t>(b)});
        plan.origin_ticks = av_rescale_q(absolute_start, AV_TIME_BASE_Q, tb);
        plan.leading = idx.times[a] - plan.origin_ticks;
        plan.last_duration = idx.times[b] - idx.times[b - 1];
        if (absolute_end != infinity)
            plan.last_duration =
                std::min(plan.last_duration,
                         av_rescale_q(absolute_end, AV_TIME_BASE_Q, tb) - idx.times[b - 1]);
        require(plan.last_duration > 0, "Cut is shorter than one video time-base tick");
    }
    plan.duration = plan.leading;
    auto append = [&](size_t e, size_t a, size_t b, bool copy, bool patch) {
        if (a == b)
            return;
        Unit unit;
        unit.epoch = e;
        unit.begin = a;
        unit.end = b;
        unit.copy = copy;
        unit.patch_cra = patch;
        unit.output_start = plan.duration;
        plan.units.push_back(std::move(unit));
        (copy ? plan.copied : plan.encoded) += b - a;
        plan.duration += idx.times[b] - idx.times[a];
    };
    for (auto range : plan.ranges)
        for (size_t e = 0; e < idx.epochs.size(); ++e) {
            const auto &epoch = idx.epochs[e];
            size_t a = std::max(epoch.begin, static_cast<size_t>(range.begin)),
                   b = std::min(epoch.end, static_cast<size_t>(range.end));
            if (a >= b)
                continue;
            bool full = a == epoch.begin && b == epoch.end;
            bool continuous = !plan.units.empty() && plan.units.back().copy &&
                              plan.units.back().epoch + 1 == e && plan.units.back().end == a &&
                              a == epoch.begin;
            int type = idx.samples[epoch.packet_begin].type;
            if (idx.codec == AV_CODEC_ID_AV1)
                // Complete AV1 temporal units can end midway through an epoch.
                append(e, a, b, a == epoch.begin, false);
            else if (idx.codec == AV_CODEC_ID_HEVC && type == 21 && !continuous &&
                     !epoch.has_radl && a <= epoch.rap && b == epoch.end) {
                append(e, a, epoch.rap, false, false);
                append(e, epoch.rap, b, true, true);
            } else if (full && (idx.codec == AV_CODEC_ID_H264 || type != 21 || continuous))
                append(e, a, b, true, false);
            else
                append(e, a, b, false, false);
        }
    require(!plan.units.empty(), "Empty video plan");
    auto b = static_cast<size_t>(plan.ranges.back().end);
    plan.duration -= idx.times[b] - idx.times[b - 1] - plan.last_duration;
    return plan;
}
static Packet read_source(Input &input, const Sample &sample) {
    auto p = packet();
    check(av_new_packet(p.get(), sample.size), "Allocate source packet");
    require(avio_seek(input.raw, sample.pos, SEEK_SET) == sample.pos, "Source packet seek failed");
    require(avio_read(input.raw, p->data, sample.size) == sample.size,
            "Short source read; input changed during processing");
    p->pts = sample.pts;
    p->dts = sample.dts;
    p->duration = sample.duration;
    p->flags = sample.flags;
    p->stream_index = input.video;
    return p;
}
Bsf annex_b(const AVCodecParameters *params, AVRational tb) {
    const char *name =
        params->codec_id == AV_CODEC_ID_H264 ? "h264_mp4toannexb" : "hevc_mp4toannexb";
    auto *filter = av_bsf_get_by_name(name);
    require(filter != nullptr, std::string("Missing ") + name);
    AVBSFContext *raw = nullptr;
    check(av_bsf_alloc(filter, &raw), "Allocate bitstream filter");
    Bsf result(raw);
    check(avcodec_parameters_copy(result->par_in, params), "Configure bitstream filter");
    result->time_base_in = tb;
    check(av_bsf_init(result.get()), "Initialize bitstream filter");
    return result;
}
AVPixelFormat encoder_pixel_format(AVCodecID codec, int format) {
    return codec == AV_CODEC_ID_AV1 && format == AV_PIX_FMT_YUVJ420P
               ? AV_PIX_FMT_YUV420P
               : static_cast<AVPixelFormat>(format);
}
Codec make_encoder(const Input &input, const AVCodec *codec,
                   const std::map<std::string, std::string> &values) {
    Codec context(avcodec_alloc_context3(codec));
    require(bool(context), "Cannot allocate video encoder");
    auto *p = input.stream->codecpar;
    context->width = p->width;
    context->height = p->height;
    context->pix_fmt = encoder_pixel_format(codec->id, p->format);
    context->time_base = input.stream->time_base;
    context->framerate = av_guess_frame_rate(input.format.value, input.stream, nullptr);
    if (codec->id == AV_CODEC_ID_AV1) {
        require(context->framerate.num > 0 && context->framerate.den > 0,
                "AV1 encoder requires a valid nominal frame rate");
        // Display ordinals are mapped back to source timestamps after encoding.
        context->time_base = av_inv_q(context->framerate);
    }
    context->sample_aspect_ratio =
        av_guess_sample_aspect_ratio(input.format.value, input.stream, nullptr);
    context->color_range = p->color_range;
    context->color_primaries = p->color_primaries;
    context->color_trc = p->color_trc;
    context->colorspace = p->color_space;
    context->chroma_sample_location = p->chroma_location;
    context->field_order = AV_FIELD_PROGRESSIVE;
    context->flags |= AV_CODEC_FLAG_GLOBAL_HEADER;
    Dictionary options;
    for (const auto &[name, value] : values)
        options.set(name, value);
    check(avcodec_open2(context.get(), codec, &options.value), "Open requested video encoder");
    require(av_dict_count(options.value) == 0, "Unrecognized video encoder option");
    require(context->width == p->width && context->height == p->height &&
                context->pix_fmt == encoder_pixel_format(codec->id, p->format),
            "Encoded geometry/pixel format must match copied video");
    require(context->color_range == p->color_range &&
                context->color_primaries == p->color_primaries &&
                context->color_trc == p->color_trc && context->colorspace == p->color_space,
            "Encoded color metadata must match copied video");
    return context;
}
static int64_t frame_duration(const Index &idx, const Plan &plan, size_t rank) {
    return rank + 1 == static_cast<size_t>(plan.ranges.back().end)
               ? plan.last_duration
               : idx.times[rank + 1] - idx.times[rank];
}
struct Spool {
    AVIOContext *io = nullptr;
    ~Spool() {
        if (io)
            avio_closep(&io);
    }
};
static int encoded_picture_type(AVCodecID codec, const AVPacket *packet) {
    // KEY can also describe an H.264 recovery-point I picture. Inspect the
    // access unit before marking it as an independently seekable MP4 sample.
    auto *data = packet->data;
    size_t size = static_cast<size_t>(packet->size), pos = 0;
    auto start_code = [&](size_t at) -> size_t {
        if (at + 3 <= size && !data[at] && !data[at + 1]) {
            if (data[at + 2] == 1)
                return 3;
            if (at + 4 <= size && !data[at + 2] && data[at + 3] == 1)
                return 4;
        }
        return 0;
    };
    bool annex = start_code(0) != 0;
    int picture = -1;
    while (pos < size) {
        size_t begin{}, end{};
        if (annex) {
            auto prefix = start_code(pos);
            require(prefix != 0, "Invalid encoder Annex B packet");
            begin = pos + prefix;
            end = begin;
            while (end < size && !start_code(end))
                ++end;
        } else {
            require(pos + 4 <= size, "Truncated encoder NAL length");
            uint32_t length = 0;
            for (int j = 0; j < 4; ++j)
                length = (length << 8) | data[pos++];
            require(length > 0 && length <= size - pos, "Invalid encoder NAL length");
            begin = pos;
            end = pos + length;
        }
        require(end > begin, "Empty encoder NAL");
        int type = codec == AV_CODEC_ID_H264 ? data[begin] & 31 : (data[begin] >> 1) & 63;
        if ((codec == AV_CODEC_ID_H264 && (type == 1 || type == 5)) ||
            (codec == AV_CODEC_ID_HEVC && type <= 31)) {
            require(picture < 0 || picture == type, "Mixed pictures in encoder packet");
            picture = type;
        }
        pos = end;
    }
    require(picture >= 0, "Encoder packet contains no picture");
    return picture;
}
static size_t encode_unit(Input &input, const Index &idx, Unit &unit, const Plan &plan,
                          const AVCodec *codec, const std::map<std::string, std::string> &options,
                          const fs::path &path) {
    auto encoded = std::make_unique<Encoded>();
    encoded->path = path;
    Spool file;
    check(avio_open(&file.io, utf8(path).c_str(), AVIO_FLAG_WRITE), "Create encoded packet spool");
    auto encoder = make_encoder(input, codec, options);
    auto *decoder_codec = avcodec_find_decoder(idx.codec);
    require(decoder_codec != nullptr, "Missing video decoder");
    Codec decoder(avcodec_alloc_context3(decoder_codec));
    require(bool(decoder), "Cannot allocate decoder");
    check(avcodec_parameters_to_context(decoder.get(), input.stream->codecpar),
          "Configure source decoder");
    decoder->pkt_timebase = input.stream->time_base;
    decoder->err_recognition = AV_EF_EXPLODE | AV_EF_CAREFUL;
    decoder->thread_count = 4;
    check(avcodec_open2(decoder.get(), decoder_codec, nullptr), "Open source decoder");
    size_t start_epoch = unit.epoch;
    if (idx.codec == AV_CODEC_ID_HEVC && unit.begin < idx.epochs[start_epoch].rap &&
        idx.samples[idx.epochs[start_epoch].packet_begin].type == 21 && start_epoch)
        --start_epoch;
    size_t last_epoch = unit.epoch;
    while (last_epoch + 1 < idx.epochs.size() && idx.epochs[last_epoch].end < unit.end)
        ++last_epoch;
    auto f = frame();
    size_t kept = 0, decoded = 0;
    std::set<int64_t> seen;
    auto receive_encoded = [&]() {
        auto p = packet();
        int result;
        while ((result = avcodec_receive_packet(encoder.get(), p.get())) >= 0) {
            require(p->size > 0 && p->pts != AV_NOPTS_VALUE,
                    "Encoder produced an invalid video packet");
            auto type = encoded_picture_type(idx.codec, p.get());
            require(!encoded->samples.empty() || is_idr(idx.codec, type),
                    "Each encoded boundary must start with a real IDR");
            auto position = std::lower_bound(idx.times.begin() + unit.begin,
                                             idx.times.begin() + unit.end, p->pts);
            require(position != idx.times.begin() + unit.end && *position == p->pts &&
                        seen.insert(p->pts).second,
                    "Encoder changed or duplicated a presentation timestamp");
            auto at = avio_tell(file.io);
            avio_write(file.io, p->data, p->size);
            check(file.io->error, "Write encoded packet spool");
            encoded->samples.push_back(
                {at, p->pts, p->size, is_irap(idx.codec, type) ? AV_PKT_FLAG_KEY : 0});
            av_packet_unref(p.get());
        }
        if (result != AVERROR(EAGAIN) && result != AVERROR_EOF)
            check(result, "Receive encoded boundary packet");
    };
    auto receive_decoded = [&]() {
        int result;
        while ((result = avcodec_receive_frame(decoder.get(), f.get())) >= 0) {
            cancellation_point();
            auto pts = f->best_effort_timestamp;
            require(f->decode_error_flags == 0 &&
                        !(f->flags & (AV_FRAME_FLAG_CORRUPT | AV_FRAME_FLAG_INTERLACED)),
                    "Corrupt or interlaced boundary frame");
            require(f->width == input.stream->codecpar->width &&
                        f->height == input.stream->codecpar->height &&
                        f->format == input.stream->codecpar->format,
                    "Dynamic video format is unsupported");
            if (pts >= idx.times[unit.begin] && pts < idx.times[unit.end]) {
                require(kept < unit.end - unit.begin && pts == idx.times[unit.begin + kept],
                        "Decoded display order differs from the source index");
                f->pts = pts;
                f->duration = frame_duration(idx, plan, unit.begin + kept);
                f->pict_type = AV_PICTURE_TYPE_NONE;
                f->flags &= ~AV_FRAME_FLAG_KEY;
                while (f->nb_side_data)
                    av_frame_remove_side_data(f.get(), f->side_data[0]->type);
                check(avcodec_send_frame(encoder.get(), f.get()), "Encode boundary frame");
                ++kept;
                receive_encoded();
            }
            ++decoded;
            av_frame_unref(f.get());
        }
        if (result != AVERROR(EAGAIN) && result != AVERROR_EOF)
            check(result, "Decode source boundary");
    };
    for (size_t i = idx.epochs[start_epoch].packet_begin;
         i < idx.epochs[last_epoch].packet_end && kept < unit.end - unit.begin; ++i) {
        cancellation_point();
        const auto &sample = idx.samples[i];
        if (idx.codec == AV_CODEC_ID_HEVC && start_epoch > 0 &&
            i < idx.epochs[start_epoch].packet_end && rasl(sample.type))
            continue;
        auto p = read_source(input, sample);
        check(avcodec_send_packet(decoder.get(), p.get()), "Send compressed source frame");
        receive_decoded();
    }
    if (kept < unit.end - unit.begin) {
        check(avcodec_send_packet(decoder.get(), nullptr), "Flush source decoder");
        receive_decoded();
    }
    require(kept == unit.end - unit.begin, "Missing decoded boundary frames");
    check(avcodec_send_frame(encoder.get(), nullptr), "Flush video encoder");
    receive_encoded();
    require(encoded->samples.size() == unit.end - unit.begin, "Video encoder frame count mismatch");
    encoded->params = parameters(nullptr);
    check(avcodec_parameters_from_context(encoded->params.get(), encoder.get()),
          "Read boundary codec parameters");
    require(encoded->params->extradata_size > 0, "Encoder did not provide parameter sets");
    check(avio_closep(&file.io), "Close encoded packet spool");
    unit.encoded = std::move(encoded);
    return decoded;
}
static bool high444(const AVCodecParameters *params) {
    auto *data = params->extradata;
    size_t n = static_cast<size_t>(params->extradata_size);
    if (n >= 4 && data[0] == 1)
        return data[1] == 244;
    for (size_t i = 0; i + 5 < n; ++i)
        if (data[i] == 0 && data[i + 1] == 0 && data[i + 2] == 1 && (data[i + 3] & 31) == 7)
            return data[i + 4] == 244;
    return params->profile == AV_PROFILE_H264_HIGH_444_PREDICTIVE;
}
void encode_boundaries(Input &input, const Index &idx, Plan &plan, const AVCodec *encoder,
                       const std::map<std::string, std::string> &options, const Workspace &work) {
    if (idx.codec == AV_CODEC_ID_AV1) {
        encode_av1_boundaries(input, idx, plan, encoder, options, work);
        calculate_delay(idx, plan);
        return;
    }
    // Validate AVOptions even for all-copy selections; no research quality,
    // preset, B-frame, GOP or encoder-thread defaults are injected here.
    {
        auto validate = make_encoder(input, encoder, options);
    }
    for (size_t i = 0; i < plan.units.size(); ++i)
        if (!plan.units[i].copy)
            plan.decoded += encode_unit(input, idx, plan.units[i], plan, encoder, options,
                                        work.directory / ("encoded-" + std::to_string(i) + ".bin"));
    bool fallback = false;
    if (idx.codec == AV_CODEC_ID_H264 && plan.copied)
        for (const auto &unit : plan.units)
            if (unit.encoded && high444(unit.encoded->params.get()))
                fallback = true;
    if (fallback) {
        std::cerr << "H.264 lossless profile transition: re-encoding the complete selection with "
                     "the requested options.\n";
        plan.units.clear();
        plan.encoded += plan.copied;
        plan.copied = 0;
        plan.lossless_fallback = true;
        int64_t offset = plan.leading;
        for (size_t r = 0; r < plan.ranges.size(); ++r) {
            auto range = plan.ranges[r];
            Unit unit;
            unit.begin = static_cast<size_t>(range.begin);
            unit.end = static_cast<size_t>(range.end);
            unit.output_start = offset;
            while (unit.epoch + 1 < idx.epochs.size() && idx.epochs[unit.epoch].end <= unit.begin)
                ++unit.epoch;
            plan.decoded +=
                encode_unit(input, idx, unit, plan, encoder, options,
                            work.directory / ("lossless-" + std::to_string(r) + ".bin"));
            offset += idx.times[unit.end] - idx.times[unit.begin];
            plan.units.push_back(std::move(unit));
        }
    }
    calculate_delay(idx, plan);
}
void calculate_delay(const Index &idx, Plan &plan) {
    plan.delay = 0;
    for (const auto &unit : plan.units) {
        size_t ordinal = 0;
        auto consume = [&](int64_t pts) {
            plan.delay = std::max(plan.delay, idx.times[unit.begin + ordinal] - pts);
            ++ordinal;
        };
        if (unit.copy) {
            const auto &epoch = idx.epochs[unit.epoch];
            for (size_t i = epoch.packet_begin; i < epoch.packet_end; ++i)
                if (idx.samples[i].display >= unit.begin && idx.samples[i].display < unit.end)
                    consume(idx.samples[i].pts);
        } else if (unit.encoded)
            for (const auto &sample : unit.encoded->samples)
                consume(sample.pts);
        else
            continue;
        require(ordinal == unit.end - unit.begin, "Decode schedule frame count mismatch");
    }
    // Only use signed CTS if movenc's DTS <= 0 heuristic would move the
    // inferred start_cts before the first presentation time.
    plan.signed_cts = false;
    int64_t initial_cts = AV_NOPTS_VALUE, initial_pts = AV_NOPTS_VALUE;
    for (const auto &unit : plan.units) {
        size_t ordinal = 0;
        auto inspect = [&](int64_t pts) {
            auto dts = unit.output_start + idx.times[unit.begin + ordinal++] -
                       idx.times[unit.begin] - plan.delay;
            auto cts = unit.output_start + pts - idx.times[unit.begin] - dts;
            if (initial_cts == AV_NOPTS_VALUE) {
                initial_cts = cts;
                initial_pts = dts + cts;
            }
            if (dts <= 0 && cts < initial_cts)
                plan.signed_cts = true;
        };
        if (unit.copy) {
            const auto &epoch = idx.epochs[unit.epoch];
            for (size_t i = epoch.packet_begin; i < epoch.packet_end; ++i)
                if (idx.samples[i].display >= unit.begin && idx.samples[i].display < unit.end)
                    inspect(idx.samples[i].pts);
        } else if (unit.encoded)
            for (const auto &sample : unit.encoded->samples)
                inspect(sample.pts);
    }
    // A copied HEVC IDR with RADL may be decoded before earlier display frames.
    // Shifting that first DTS to its PTS would make movenc's empty edit discard
    // the wanted leading pictures. Keep the unsigned-CTS schedule in that case.
    plan.signed_cts = plan.signed_cts && initial_pts == plan.leading;
}
std::optional<VideoPacket> VideoCursor::next() {
    cancellation_point();
    if (unit_index >= plan.units.size())
        return std::nullopt;
    const auto &unit = plan.units[unit_index];
    Packet p;
    int64_t pts{};
    const AVCodecParameters *params = nullptr;
    if (unit.copy) {
        const auto &epoch = index.epochs[unit.epoch];
        if (ordinal == 0)
            source_packet = epoch.packet_begin;
        while (source_packet < epoch.packet_end &&
               (index.samples[source_packet].display < unit.begin ||
                index.samples[source_packet].display >= unit.end))
            ++source_packet;
        require(source_packet < epoch.packet_end, "Missing planned copy packet");
        const auto &sample = index.samples[source_packet++];
        p = read_source(input, sample);
        pts = sample.pts;
        p->flags = is_irap(index.codec, sample.type) ? AV_PKT_FLAG_KEY : 0;
        params = index.codec == AV_CODEC_ID_AV1 ? index.av1_params.get() : input.stream->codecpar;
        if (unit.patch_cra && ordinal == 0) {
            size_t pos = 0;
            while (pos < static_cast<size_t>(p->size)) {
                uint32_t length = 0;
                for (int j = 0; j < index.nal_length; ++j)
                    length = (length << 8) | p->data[pos++];
                if (((p->data[pos] >> 1) & 63) == 21)
                    p->data[pos] = (p->data[pos] & 0x81) | (18 << 1);
                pos += length;
            }
        }
    } else {
        require(bool(unit.encoded), "Boundary packets were not encoded");
        if (ordinal == 0)
            check(avio_open(&encoded_file, utf8(unit.encoded->path).c_str(), AVIO_FLAG_READ),
                  "Open encoded packet spool");
        const auto &sample = unit.encoded->samples[ordinal];
        p = packet();
        check(av_new_packet(p.get(), sample.size), "Allocate boundary packet");
        require(avio_seek(encoded_file, sample.pos, SEEK_SET) == sample.pos &&
                    avio_read(encoded_file, p->data, sample.size) == sample.size,
                "Cannot read encoded packet spool");
        p->flags = sample.flags;
        pts = sample.pts;
        params = unit.encoded->params.get();
    }
    auto position =
        std::lower_bound(index.times.begin() + unit.begin, index.times.begin() + unit.end, pts);
    require(position != index.times.begin() + unit.end && *position == pts,
            "Unexpected packet display timestamp");
    size_t rank = static_cast<size_t>(position - index.times.begin());
    int64_t output_pts = unit.output_start + pts - index.times[unit.begin];
    int64_t output_dts = unit.output_start + index.times[unit.begin + ordinal] -
                         index.times[unit.begin] - plan.delay;
    int64_t duration = frame_duration(index, plan, rank);
    if (ordinal + 1 == unit.end - unit.begin)
        duration = frame_duration(index, plan, unit.end - 1);
    VideoPacket result{std::move(p), output_pts, output_dts, duration, params, ordinal == 0};
    if (++ordinal == unit.end - unit.begin) {
        if (encoded_file)
            avio_closep(&encoded_file);
        ordinal = 0;
        ++unit_index;
    }
    return result;
}
} // namespace smartcut
