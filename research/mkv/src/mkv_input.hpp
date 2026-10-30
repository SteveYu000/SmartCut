// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include "media.hpp"
namespace smartcut {
bool is_matroska(const Input &input);
// Single-job research adapter. FFmpeg owns Blocks, lacing and decompression.
struct PacketSpool {
    Input &input;
    fs::path path;
    AVIOContext *writer = nullptr;
    int64_t bytes = 0;
    size_t packets = 0;
    PacketSpool(Input &input, const fs::path &path);
    ~PacketSpool();
    void finish();
};
int research_read_packet(Input &input, AVPacket *packet);
} // namespace smartcut
