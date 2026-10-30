// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
extern "C" {
#include <libavcodec/avcodec.h>
#include <libavcodec/bsf.h>
#include <libavformat/avformat.h>
#include <libavutil/avutil.h>
#include <libavutil/opt.h>
#include <libavutil/parseutils.h>
#include <libavutil/pixdesc.h>
}
#include <atomic>
#include <cstdint>
#include <filesystem>
#include <map>
#include <memory>
#include <optional>
#include <set>
#include <span>
#include <string>
#include <vector>

namespace smartcut {
namespace fs = std::filesystem;
using Bytes = std::vector<uint8_t>;
extern std::atomic_bool cancelled;
[[noreturn]] void fail(const std::string &message);
void require(bool ok, const std::string &message);
void check(int result, const char *operation);
void cancellation_point();
fs::path path8(const std::string &value);
std::string utf8(const fs::path &path);
std::string json_string(const std::string &value);
std::string seconds(int64_t microseconds);
int64_t parse_time(const std::string &value);
fs::path executable_path();
void run_process(const std::vector<std::string> &arguments, const fs::path &log);
void publish_file(const fs::path &source, const fs::path &destination, bool overwrite);

struct PacketFree {
    void operator()(AVPacket *p) const { av_packet_free(&p); }
};
struct FrameFree {
    void operator()(AVFrame *p) const { av_frame_free(&p); }
};
struct CodecFree {
    void operator()(AVCodecContext *p) const { avcodec_free_context(&p); }
};
struct BsfFree {
    void operator()(AVBSFContext *p) const { av_bsf_free(&p); }
};
struct ParamsFree {
    void operator()(AVCodecParameters *p) const { avcodec_parameters_free(&p); }
};
using Packet = std::unique_ptr<AVPacket, PacketFree>;
using Frame = std::unique_ptr<AVFrame, FrameFree>;
using Codec = std::unique_ptr<AVCodecContext, CodecFree>;
using Bsf = std::unique_ptr<AVBSFContext, BsfFree>;
using Params = std::unique_ptr<AVCodecParameters, ParamsFree>;
Packet packet();
Frame frame();
Params parameters(const AVCodecParameters *source);
struct Dictionary {
    AVDictionary *value = nullptr;
    ~Dictionary() { av_dict_free(&value); }
    Dictionary() = default;
    Dictionary(const Dictionary &) = delete;
    Dictionary &operator=(const Dictionary &) = delete;
    void set(const std::string &key, const std::string &value);
};
struct Format {
    AVFormatContext *value = nullptr;
    ~Format() { avformat_close_input(&value); }
    void open(const std::string &filename);
};
struct Workspace {
    fs::path directory;
    bool keep = false;
    explicit Workspace(const fs::path &parent, bool retain);
    ~Workspace();
    Workspace(const Workspace &) = delete;
    Workspace &operator=(const Workspace &) = delete;
};
struct FrameRange {
    int64_t begin{}, end{};
};
struct Option {
    std::string key, value;
    bool takes_value = true;
};
struct Times {
    int64_t start = 0;
    std::optional<int64_t> duration, end;
};
struct Options {
    std::string input, output, report, ffmpeg, work;
    std::vector<std::string> maps;
    std::vector<Option> output_options;
    std::vector<FrameRange> frame_ranges;
    Times input_times, output_times;
    bool overwrite = false, no_overwrite = false, no_video = false, no_audio = false,
         no_subtitles = false;
    bool analyze = false, keep_work = false, hide_banner = false;
    int log_level = AV_LOG_INFO;
};
Options parse_options(const std::vector<std::string> &arguments);
std::string help_text();
struct MappedStream {
    int source{}, output{}, auxiliary = -1;
    AVMediaType type{};
};
struct Layout {
    std::vector<MappedStream> streams;
    int video = -1;
    AVFormatContext *matching = nullptr;
    explicit Layout(AVFormatContext *input, const Options &options);
    ~Layout() { avformat_free_context(matching); }
    Layout(const Layout &) = delete;
    Layout &operator=(const Layout &) = delete;
    bool matches(const Option &option, int output_index) const;
};
std::string option_stem(const Option &option);
std::string codec_name(const Options &options, const Layout &layout, int output_index,
                       const AVOutputFormat *format);
const AVCodec *find_encoder(const std::string &name);
std::map<std::string, std::string> video_options(const Options &options, const Layout &layout,
                                                 const AVCodec *encoder);
std::vector<std::string>
auxiliary_arguments(const Options &options, const Layout &layout, const fs::path &output,
                    const std::vector<FrameRange> &ranges, const std::vector<int64_t> &times,
                    AVRational time_base, int64_t origin_us, const AVCodec *video_encoder);

struct Sample {
    int64_t pos{}, pts{}, dts{}, duration{};
    int size{}, flags{}, type{};
    size_t display{};
};
struct Epoch {
    size_t packet_begin{}, packet_end{}, begin{}, end{}, rap{};
    bool has_radl{};
};
struct Index {
    AVCodecID codec{};
    std::vector<Sample> samples;
    std::vector<size_t> display;
    std::vector<int64_t> times;
    std::vector<Epoch> epochs;
    std::set<Bytes> parameter_sets;
    int nal_length{};
    Params av1_params;
};
struct Input {
    Format format;
    AVIOContext *raw = nullptr;
    AVStream *stream = nullptr;
    int video = -1;
    Input(const std::string &filename, int video_stream);
    ~Input() {
        if (raw)
            avio_closep(&raw);
    }
};
Index index_input(Input &input);
Index index_av1(Input &input);
AVPixelFormat encoder_pixel_format(AVCodecID codec, int format);
bool is_irap(AVCodecID codec, int type);
bool is_idr(AVCodecID codec, int type);
struct EncodedSample {
    int64_t pos{}, pts{};
    int size{}, flags{};
};
struct Encoded {
    fs::path path;
    Params params;
    std::vector<EncodedSample> samples;
};
struct Unit {
    size_t epoch{}, begin{}, end{};
    bool copy{}, patch_cra{};
    int64_t output_start{};
    std::unique_ptr<Encoded> encoded;
};
struct Plan {
    std::vector<Unit> units;
    std::vector<FrameRange> ranges;
    size_t copied{}, encoded{}, decoded{};
    int64_t duration{}, delay{}, leading{}, origin_ticks{}, last_duration{};
    bool lossless_fallback = false, signed_cts = false;
};
Plan make_plan(const Index &index, const Options &options, AVRational time_base,
               int64_t format_start_us);
Codec make_encoder(const Input &input, const AVCodec *codec,
                   const std::map<std::string, std::string> &values);
void encode_boundaries(Input &input, const Index &index, Plan &plan, const AVCodec *encoder,
                       const std::map<std::string, std::string> &options, const Workspace &work);
void encode_av1_boundaries(Input &input, const Index &index, Plan &plan, const AVCodec *encoder,
                           const std::map<std::string, std::string> &options,
                           const Workspace &work);
void calculate_delay(const Index &index, Plan &plan);
Bsf annex_b(const AVCodecParameters *params, AVRational time_base);
struct VideoPacket {
    Packet data;
    int64_t pts{}, dts{}, duration{};
    const AVCodecParameters *params{};
    bool first{};
};
struct VideoCursor {
    Input &input;
    const Index &index;
    const Plan &plan;
    size_t unit_index{}, ordinal{}, source_packet{};
    AVIOContext *encoded_file = nullptr;
    VideoCursor(Input &i, const Index &x, const Plan &p) : input(i), index(x), plan(p) {}
    ~VideoCursor() {
        if (encoded_file)
            avio_closep(&encoded_file);
    }
    std::optional<VideoPacket> next();
};
int run(const Options &options);
} // namespace smartcut
