// SPDX-License-Identifier: GPL-3.0-or-later
// AV1 temporal-unit indexing and boundary encoding, based on research/av1.
// FFmpeg owns coding and av1C/stsd serialization; tile/reference data is opaque.
#include "media.hpp"
#include <algorithm>
#include <cstring>
#include <limits>
#include <numeric>

namespace smartcut {
namespace {
struct Inspection {
    Bytes sequence;
    bool key = false;
    bool sync = false;
    int shown = 0;
};
Inspection inspect(std::span<const uint8_t> data) {
    Inspection info;
    bool first = true;
    size_t pos = 0;
    while (pos < data.size()) {
        const size_t begin = pos;
        const auto header = data[pos++];
        require(!(header & 0x81), "Invalid AV1 OBU header");
        const int type = (header >> 3) & 15;
        if (header & 4) {
            require(pos < data.size(), "Truncated AV1 OBU extension");
            require(data[pos++] == 0, "Layered AV1 is unsupported");
        }
        uint64_t size = data.size() - pos;
        if (header & 2) {
            size = 0;
            bool done = false;
            for (unsigned i = 0; i < 8; ++i) {
                require(pos < data.size(), "Truncated AV1 OBU length");
                const auto byte = data[pos++];
                size |= uint64_t(byte & 127) << (i * 7);
                if (!(byte & 128)) {
                    done = true;
                    break;
                }
            }
            require(done, "AV1 OBU length overflow");
        }
        require(size <= data.size() - pos, "Truncated AV1 OBU payload");
        require(type != 8, "AV1 tile-list OBUs are unsupported");
        const size_t end = pos + static_cast<size_t>(size);
        if (type == 1) {
            require(size && !(data[pos] & 0x18), "AV1 still/reduced headers are unsupported");
            require(info.sequence.empty(), "Multiple AV1 sequence headers in one sample");
            info.sequence.assign(data.begin() + begin, data.begin() + end);
        } else if (type == 3 || type == 6) {
            require(size > 0, "Empty AV1 frame header");
            const auto bits = data[pos];
            const bool existing = (bits & 128) != 0;
            const bool shown = existing || (bits & 16);
            if (first) {
                info.key = !existing && ((bits >> 5) & 3) == 0 && shown;
                info.sync = info.key && !info.sequence.empty();
                first = false;
            }
            info.shown += shown;
        }
        pos = end;
    }
    return info;
}
Bytes sequence_of(const uint8_t *data, size_t size) {
    if (!size)
        return {};
    if (data[0] & 128) {
        require(size >= 4 && data[0] == 0x81, "Invalid AV1 codec configuration record");
        data += 4;
        size -= 4;
    }
    return inspect({data, size}).sequence;
}
void set_sequence(AVCodecParameters *params, const Bytes &sequence) {
    require(!sequence.empty() && sequence.size() <= INT_MAX, "Missing AV1 sequence header");
    av_freep(&params->extradata);
    params->extradata =
        static_cast<uint8_t *>(av_mallocz(sequence.size() + AV_INPUT_BUFFER_PADDING_SIZE));
    require(params->extradata != nullptr, "Cannot allocate AV1 sequence header");
    std::memcpy(params->extradata, sequence.data(), sequence.size());
    params->extradata_size = static_cast<int>(sequence.size());
}
Packet read(Input &input, const Sample &sample) {
    auto p = packet();
    check(av_new_packet(p.get(), sample.size), "Allocate AV1 source sample");
    require(avio_seek(input.raw, sample.pos, SEEK_SET) == sample.pos &&
                avio_read(input.raw, p->data, sample.size) == sample.size,
            "Cannot read AV1 source sample; input changed");
    p->pts = sample.pts;
    p->dts = sample.dts;
    p->duration = sample.duration;
    p->flags = sample.flags;
    return p;
}
struct Spool {
    AVIOContext *io = nullptr;
    ~Spool() {
        if (io)
            avio_closep(&io);
    }
};
size_t encode_unit(Input &input, const Index &idx, Unit &unit, const AVCodec *codec,
                   const std::map<std::string, std::string> &values, const fs::path &path) {
    auto encoder = make_encoder(input, codec, values);
    auto *decoder_codec = avcodec_find_decoder_by_name("libdav1d");
    if (!decoder_codec)
        decoder_codec = avcodec_find_decoder(AV_CODEC_ID_AV1);
    require(decoder_codec != nullptr, "Missing AV1 decoder");
    Codec decoder(avcodec_alloc_context3(decoder_codec));
    require(bool(decoder), "Cannot allocate AV1 decoder");
    check(avcodec_parameters_to_context(decoder.get(), idx.av1_params.get()),
          "Configure AV1 decoder");
    decoder->pkt_timebase = input.stream->time_base;
    decoder->err_recognition = AV_EF_EXPLODE | AV_EF_CAREFUL;
    decoder->thread_count = 4;
    check(avcodec_open2(decoder.get(), decoder_codec, nullptr), "Open AV1 decoder");
    auto encoded = std::make_unique<Encoded>();
    encoded->path = path;
    Spool spool;
    check(avio_open(&spool.io, utf8(path).c_str(), AVIO_FLAG_WRITE), "Create AV1 packet spool");
    auto f = frame();
    size_t sent = 0, received = 0, decoded = 0;
    Bytes sequence;
    auto receive_packets = [&] {
        auto p = packet();
        int result;
        while ((result = avcodec_receive_packet(encoder.get(), p.get())) >= 0) {
            cancellation_point();
            require(p->size > 0 && p->pts >= 0 && static_cast<uint64_t>(p->pts) == received &&
                        received < unit.end - unit.begin,
                    "AV1 encoder must return ordered one-display temporal units");
            const auto info = inspect({p->data, static_cast<size_t>(p->size)});
            require(info.shown == 1, "AV1 encoded sample must display exactly one frame");
            require(!info.key || info.sequence.empty() || info.sync,
                    "AV1 sequence header must precede its key frame");
            if (!info.sequence.empty()) {
                require(sequence.empty() || sequence == info.sequence,
                        "AV1 encoder changed sequence configuration within a boundary");
                sequence = info.sequence;
            }
            if (sequence.empty())
                sequence =
                    sequence_of(encoder->extradata, static_cast<size_t>(encoder->extradata_size));
            require(!sequence.empty() && (received || info.key),
                    "AV1 boundary must start with a shown key frame and sequence header");
            // MP4's GLOBAL_HEADER may move SH to extradata. Restore it before
            // every sync sample so copied and encoded CVSs remain independent.
            if (info.key && info.sequence.empty()) {
                const int prefix = static_cast<int>(sequence.size());
                const int original = p->size;
                check(av_grow_packet(p.get(), prefix), "Restore AV1 sequence header");
                std::memmove(p->data + prefix, p->data, static_cast<size_t>(original));
                std::memcpy(p->data, sequence.data(), sequence.size());
            }
            const auto pos = avio_tell(spool.io);
            avio_write(spool.io, p->data, p->size);
            check(spool.io->error, "Write AV1 packet spool");
            encoded->samples.push_back(
                {pos, idx.times[unit.begin + received], p->size, info.key ? AV_PKT_FLAG_KEY : 0});
            ++received;
            av_packet_unref(p.get());
        }
        if (result != AVERROR(EAGAIN) && result != AVERROR_EOF)
            check(result, "Receive AV1 packet");
    };
    auto receive_frames = [&] {
        int result;
        while ((result = avcodec_receive_frame(decoder.get(), f.get())) >= 0) {
            cancellation_point();
            ++decoded;
            auto *par = input.stream->codecpar;
            require(!f->decode_error_flags &&
                        !(f->flags & (AV_FRAME_FLAG_CORRUPT | AV_FRAME_FLAG_INTERLACED)),
                    "Corrupt AV1 boundary frame");
            require(f->width == par->width && f->height == par->height &&
                        encoder_pixel_format(idx.codec, f->format) == encoder->pix_fmt,
                    "Dynamic AV1 video format is unsupported");
            const auto pts = f->best_effort_timestamp;
            if (pts >= idx.times[unit.begin] && pts < idx.times[unit.end]) {
                require(sent < unit.end - unit.begin && pts == idx.times[unit.begin + sent],
                        "AV1 decoded frame differs from source display order");
                f->format = encoder->pix_fmt;
                f->sample_aspect_ratio = encoder->sample_aspect_ratio;
                f->pts = static_cast<int64_t>(sent);
                f->duration = 1;
                f->pict_type = AV_PICTURE_TYPE_NONE;
                f->flags &= ~AV_FRAME_FLAG_KEY;
                while (f->nb_side_data)
                    av_frame_remove_side_data(f.get(), f->side_data[0]->type);
                check(avcodec_send_frame(encoder.get(), f.get()), "Encode AV1 boundary frame");
                ++sent;
                receive_packets();
            }
            av_frame_unref(f.get());
        }
        if (result != AVERROR(EAGAIN) && result != AVERROR_EOF)
            check(result, "Decode AV1 boundary");
    };
    const auto &epoch = idx.epochs[unit.epoch];
    for (size_t i = epoch.packet_begin; i < epoch.packet_end && sent < unit.end - unit.begin; ++i) {
        auto p = read(input, idx.samples[i]);
        check(avcodec_send_packet(decoder.get(), p.get()), "Send AV1 source sample");
        receive_frames();
    }
    if (sent < unit.end - unit.begin) {
        check(avcodec_send_packet(decoder.get(), nullptr), "Flush AV1 decoder");
        receive_frames();
    }
    require(sent == unit.end - unit.begin, "Missing AV1 boundary frames");
    check(avcodec_send_frame(encoder.get(), nullptr), "Flush AV1 encoder");
    receive_packets();
    require(received == sent, "AV1 boundary packet count mismatch");
    encoded->params = parameters(nullptr);
    check(avcodec_parameters_from_context(encoded->params.get(), encoder.get()),
          "Read AV1 encoder parameters");
    set_sequence(encoded->params.get(), sequence);
    check(avio_closep(&spool.io), "Close AV1 packet spool");
    unit.encoded = std::move(encoded);
    return decoded;
}
} // namespace

Index index_av1(Input &input) {
    Index idx;
    idx.codec = AV_CODEC_ID_AV1;
    auto *par = input.stream->codecpar;
    auto sequence = sequence_of(par->extradata, static_cast<size_t>(par->extradata_size));
    const int64_t length = avio_size(input.raw);
    require(length >= 0, "Cannot determine AV1 input size");
    auto p = packet();
    int result;
    std::vector<size_t> keys;
    while ((result = av_read_frame(input.format.value, p.get())) >= 0) {
        cancellation_point();
        if (p->stream_index == input.video) {
            require(p->pts != AV_NOPTS_VALUE && p->pts == p->dts && p->duration > 0,
                    "AV1 temporal units require PTS=DTS and positive duration");
            require(p->pos >= 0 && p->size > 0 && p->pos <= length - p->size &&
                        !(p->flags & (AV_PKT_FLAG_CORRUPT | AV_PKT_FLAG_DISCARD)),
                    "Corrupt or unaddressable AV1 source sample");
            const auto info = inspect({p->data, static_cast<size_t>(p->size)});
            require(info.shown == 1, "Each AV1 sample must display exactly one frame");
            if (!info.sequence.empty()) {
                if (idx.samples.empty())
                    sequence = info.sequence;
                require(info.sequence == sequence,
                        "Dynamic AV1 input sequence headers are unsupported");
            }
            const bool sync = info.sync;
            require(!(p->flags & AV_PKT_FLAG_KEY) || sync,
                    "AV1 sync sample requires a shown key frame preceded by a sequence header");
            for (int i = 0; i < p->side_data_elems; ++i) {
                const auto &side = p->side_data[i];
                require(side.type != AV_PKT_DATA_ENCRYPTION_INFO,
                        "Encrypted AV1 input is unsupported");
                if (side.type == AV_PKT_DATA_NEW_EXTRADATA && !idx.samples.empty())
                    require(sequence_of(side.data, side.size) == sequence,
                            "Changing AV1 sample descriptions are unsupported as input");
            }
            if (!idx.samples.empty()) {
                const auto &last = idx.samples.back();
                require(p->pts > last.pts && p->pts - last.pts == last.duration,
                        "AV1 temporal-unit durations must be continuous");
            }
            if (sync)
                keys.push_back(idx.samples.size());
            idx.samples.push_back({p->pos, p->pts, p->dts, p->duration, p->size,
                                   sync ? AV_PKT_FLAG_KEY : 0, sync ? 0 : 1, idx.samples.size()});
            idx.times.push_back(p->pts);
        }
        av_packet_unref(p.get());
    }
    require(result == AVERROR_EOF, "AV1 demux failed before EOF");
    require(!idx.samples.empty() && !keys.empty() && keys.front() == 0 && !sequence.empty(),
            "AV1 input must start at a verified random access point");
    require(idx.times.back() <= INT64_MAX - idx.samples.back().duration, "AV1 timeline overflow");
    idx.times.push_back(idx.times.back() + idx.samples.back().duration);
    idx.display.resize(idx.samples.size());
    std::iota(idx.display.begin(), idx.display.end(), size_t{0});
    keys.push_back(idx.samples.size());
    for (size_t i = 0; i + 1 < keys.size(); ++i)
        idx.epochs.push_back({keys[i], keys[i + 1], keys[i], keys[i + 1], keys[i], false});
    idx.av1_params = parameters(par);
    set_sequence(idx.av1_params.get(), sequence);
    idx.av1_params->sample_aspect_ratio =
        av_guess_sample_aspect_ratio(input.format.value, input.stream, nullptr);
    return idx;
}
void encode_av1_boundaries(Input &input, const Index &idx, Plan &plan, const AVCodec *encoder,
                           const std::map<std::string, std::string> &options,
                           const Workspace &work) {
    for (size_t i = 0; i < plan.units.size(); ++i)
        if (!plan.units[i].copy)
            plan.decoded += encode_unit(input, idx, plan.units[i], encoder, options,
                                        work.directory / ("av1-" + std::to_string(i) + ".bin"));
}
} // namespace smartcut
