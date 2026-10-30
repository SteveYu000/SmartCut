// SPDX-License-Identifier: GPL-3.0-or-later
#include "mkv_input.hpp"
namespace smartcut {
static PacketSpool *active = nullptr;
bool is_matroska(const Input &input) {
    return std::string(input.format.value->iformat->name).find("matroska") != std::string::npos;
}
PacketSpool::PacketSpool(Input &source, const fs::path &target) : input(source), path(target) {
    require(!active, "Only one research job may use the spool");
    check(avio_open(&writer, utf8(path).c_str(), AVIO_FLAG_WRITE), "Open demuxed packet spool");
    active = this;
}
PacketSpool::~PacketSpool() {
    if (writer)
        avio_closep(&writer);
    if (active == this)
        active = nullptr;
}
void PacketSpool::finish() {
    check(avio_closep(&writer), "Finish demuxed packet spool");
    if (input.raw)
        avio_closep(&input.raw);
    check(avio_open(&input.raw, utf8(path).c_str(), AVIO_FLAG_READ), "Read demuxed packet spool");
    active = nullptr;
}
int research_read_packet(Input &input, AVPacket *packet) {
    require(active && &active->input == &input, "Missing packet spool");
    int result = av_read_frame(input.format.value, packet);
    if (result >= 0 && packet->stream_index == input.video) {
        // pkt.pos identifies an EBML Block, not necessarily decoded packet data;
        // never seek the original MKV as though its samples were MP4 samples.
        packet->pos = avio_tell(active->writer);
        avio_write(active->writer, packet->data, packet->size);
        check(active->writer->error, "Spool demuxed video packet");
        active->bytes += packet->size;
        ++active->packets;
        if (is_matroska(input))
            packet->dts = input.stream->codecpar->codec_id == AV_CODEC_ID_AV1
                              ? packet->pts : AV_NOPTS_VALUE;
    }
    return result;
}
} // namespace smartcut
