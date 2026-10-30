// SPDX-License-Identifier: GPL-3.0-or-later
#include "media.hpp"
#include <algorithm>
#include <cctype>
#include <chrono>
#include <cstring>
#include <fstream>
#include <iostream>
#include <limits>
#include <numeric>
#include <sstream>

namespace smartcut {
static std::string lower(std::string value) {
    for (auto &c : value)
        c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    return value;
}
static bool is_mux_option(AVFormatContext *context, const std::string &stem) {
    auto *option = av_opt_find(context, stem.c_str(), nullptr, 0, AV_OPT_SEARCH_CHILDREN);
    return option && (option->flags & AV_OPT_FLAG_ENCODING_PARAM);
}
static void set_metadata(AVDictionary **destination, const std::string &text) {
    auto equal = text.find('=');
    require(equal != std::string::npos, "Metadata uses key=value");
    auto key = text.substr(0, equal), value = text.substr(equal + 1);
    check(av_dict_set(destination, key.c_str(), value.empty() ? nullptr : value.c_str(), 0),
          "Set metadata");
}
static int disposition(const std::string &text, int initial) {
    if (text.empty() || text == "0")
        return 0;
    int result = (text[0] == '+' || text[0] == '-') ? initial : 0;
    size_t pos = 0;
    while (pos < text.size()) {
        bool remove = text[pos] == '-';
        if (text[pos] == '+' || text[pos] == '-')
            ++pos;
        auto end = text.find_first_of("+-", pos);
        if (end == std::string::npos)
            end = text.size();
        auto name = text.substr(pos, end - pos);
        int flag = av_disposition_from_string(name.c_str());
        require(flag >= 0, "Unknown disposition: " + name);
        if (remove)
            result &= ~flag;
        else
            result |= flag;
        pos = end;
    }
    return result;
}
static void copy_chapters(AVFormatContext *output, AVFormatContext *source, int64_t begin_us,
                          int64_t end_us) {
    for (unsigned i = 0; i < source->nb_chapters; ++i) {
        auto *original = source->chapters[i];
        auto begin = av_rescale_q(original->start, original->time_base, AV_TIME_BASE_Q);
        auto end = av_rescale_q(original->end, original->time_base, AV_TIME_BASE_Q);
        if (end <= begin_us || begin >= end_us)
            continue;
        auto *chapter = static_cast<AVChapter *>(av_mallocz(sizeof(AVChapter)));
        require(chapter != nullptr, "Cannot allocate chapter");
        chapter->id = original->id;
        chapter->time_base = AV_TIME_BASE_Q;
        chapter->start = std::max(begin, begin_us) - begin_us;
        chapter->end = std::min(end, end_us) - begin_us;
        av_dict_copy(&chapter->metadata, original->metadata, 0);
        auto **chapters = static_cast<AVChapter **>(
            av_realloc_array(output->chapters, output->nb_chapters + 1, sizeof(AVChapter *)));
        if (!chapters) {
            av_dict_free(&chapter->metadata);
            av_free(chapter);
            fail("Cannot allocate chapter list");
        }
        output->chapters = chapters;
        output->chapters[output->nb_chapters++] = chapter;
    }
}
struct Muxer {
    AVFormatContext *context = nullptr;
    ~Muxer() {
        if (context) {
            if (context->pb)
                avio_closep(&context->pb);
            avformat_free_context(context);
        }
    }
};
static bool descriptions(const Options &options, const Layout &layout, AVCodecID codec) {
    bool result = codec == AV_CODEC_ID_AV1;
    for (const auto &option : options.output_options)
        if (option_stem(option) == "tag" && layout.matches(option, layout.video)) {
            if (codec == AV_CODEC_ID_AV1) {
                require(option.value == "av01", "AV1 requires the av01 sample entry");
                continue;
            }
            auto inband = codec == AV_CODEC_ID_H264 ? "avc3" : "hev1",
                 outband = codec == AV_CODEC_ID_H264 ? "avc1" : "hvc1";
            require(option.value == inband || option.value == outband,
                    "Video sample entry does not match the selected codec");
            result = option.value == outband;
        }
    return result;
}
static void validate_video_format(const Options &options, const Layout &layout, Input &input) {
    for (const auto &option : options.output_options)
        if (layout.matches(option, layout.video)) {
            auto stem = option_stem(option);
            if (stem == "pix_fmt")
                require(av_get_pix_fmt(option.value.c_str()) ==
                            encoder_pixel_format(input.stream->codecpar->codec_id,
                                                 input.stream->codecpar->format),
                        "-pix_fmt must match the copied source; pixel conversion needs a full "
                        "transcode");
            if (stem == "aspect") {
                AVRational aspect{};
                check(av_parse_ratio(&aspect, option.value.c_str(), 1000000, 0, nullptr),
                      "Invalid -aspect");
                auto sar = av_guess_sample_aspect_ratio(input.format.value, input.stream, nullptr);
                if (!sar.num)
                    sar = {1, 1};
                auto expected =
                    av_mul_q(sar, {input.stream->codecpar->width, input.stream->codecpar->height});
                require(av_cmp_q(aspect, expected) == 0,
                        "-aspect would change copied frames; use a full transcode");
            }
        }
}
static void mux(Input &input, const Index &idx, const Plan &plan, const Options &options,
                const Layout &layout, Format *auxiliary, const fs::path &output, bool stsd) {
    Muxer mux;
    check(avformat_alloc_output_context2(&mux.context, nullptr, "mp4", utf8(output).c_str()),
          "Create MP4 muxer");
    auto *context = mux.context;
    context->avoid_negative_ts = AVFMT_AVOID_NEG_TS_DISABLED;
    std::map<int, int> auxiliary_by_output;
    if (auxiliary) {
        for (unsigned i = 0; i < auxiliary->value->nb_streams; ++i) {
            auto type = auxiliary->value->streams[i]->codecpar->codec_type;
            if (type == AVMEDIA_TYPE_AUDIO || type == AVMEDIA_TYPE_SUBTITLE) {
                auto id = auxiliary->value->streams[i]->id;
                auto mapped = std::find_if(
                    layout.streams.begin(), layout.streams.end(), [&](const auto &stream) {
                        return stream.output + 1 == id && stream.type == type;
                    });
                require(mapped != layout.streams.end() &&
                            !auxiliary_by_output.contains(mapped->output),
                        "FFmpeg auxiliary stream identity changed unexpectedly");
                auxiliary_by_output[mapped->output] = static_cast<int>(i);
            }
        }
    }
    std::map<int, int> auxiliary_to_output;
    std::map<int, int> output_indices;
    for (const auto &mapped : layout.streams) {
        if (mapped.type != AVMEDIA_TYPE_VIDEO && !auxiliary_by_output.contains(mapped.output)) {
            av_log(nullptr, AV_LOG_WARNING,
                   "Selected %s stream %d has no packets in the interval and was omitted.\n",
                   av_get_media_type_string(mapped.type), mapped.source);
            continue;
        }
        auto *stream = avformat_new_stream(context, nullptr);
        require(stream != nullptr, "Cannot allocate output stream");
        output_indices[mapped.output] = stream->index;
        if (mapped.type == AVMEDIA_TYPE_VIDEO) {
            const auto &first = plan.units.front();
            auto *params = first.copy ? (idx.codec == AV_CODEC_ID_AV1 ? idx.av1_params.get()
                                                                      : input.stream->codecpar)
                                      : first.encoded->params.get();
            if (stsd)
                check(avcodec_parameters_copy(stream->codecpar, params), "Copy video parameters");
            else {
                auto bsf = annex_b(params, input.stream->time_base);
                check(avcodec_parameters_copy(stream->codecpar, bsf->par_out),
                      "Copy in-band video configuration");
            }
            stream->codecpar->codec_tag =
                idx.codec == AV_CODEC_ID_AV1 ? MKTAG('a', 'v', '0', '1')
                : idx.codec == AV_CODEC_ID_H264
                    ? (stsd ? MKTAG('a', 'v', 'c', '1') : MKTAG('a', 'v', 'c', '3'))
                    : (stsd ? MKTAG('h', 'v', 'c', '1') : MKTAG('h', 'e', 'v', '1'));
            stream->time_base = input.stream->time_base;
            stream->avg_frame_rate = input.stream->avg_frame_rate;
            stream->sample_aspect_ratio =
                av_guess_sample_aspect_ratio(input.format.value, input.stream, nullptr);
            av_dict_copy(&stream->metadata, input.stream->metadata, 0);
            stream->disposition = input.stream->disposition;
        } else {
            require(auxiliary != nullptr, "Missing FFmpeg auxiliary output");
            auto source = auxiliary_by_output.at(mapped.output);
            auto *original = auxiliary->value->streams[source];
            require(original->codecpar->codec_type == mapped.type,
                    "FFmpeg auxiliary stream order changed");
            check(avcodec_parameters_copy(stream->codecpar, original->codecpar),
                  "Copy FFmpeg audio/subtitle parameters");
            stream->time_base = original->time_base;
            stream->duration = original->duration;
            stream->disposition = original->disposition;
            av_dict_copy(&stream->metadata, original->metadata, 0);
            auxiliary_to_output[source] = stream->index;
        }
    }
    auto *metadata_source = auxiliary ? auxiliary->value : input.format.value;
    bool copy_global = true, chapters = true;
    for (const auto &option : options.output_options) {
        auto stem = option_stem(option);
        if (stem == "map_metadata") {
            require(option.key == stem && (option.value == "0" || option.value == "-1"),
                    "Only global -map_metadata 0 or -1 is supported");
            copy_global = option.value != "-1";
        }
        if (stem == "map_chapters") {
            require(option.key == stem && (option.value == "0" || option.value == "-1"),
                    "Only -map_chapters 0 or -1 is supported");
            chapters = option.value != "-1";
        }
    }
    if (copy_global)
        av_dict_copy(&context->metadata, metadata_source->metadata, 0);
    if (chapters) {
        if (auxiliary)
            copy_chapters(context, auxiliary->value, 0, std::numeric_limits<int64_t>::max() / 4);
        else if (plan.ranges.size() == 1) {
            auto begin = av_rescale_q(plan.origin_ticks, input.stream->time_base, AV_TIME_BASE_Q);
            copy_chapters(context, input.format.value, begin,
                          begin +
                              av_rescale_q(plan.duration, input.stream->time_base, AV_TIME_BASE_Q));
        }
    }
    Dictionary mux_options;
    mux_options.set("use_editlist", "1");
    // Empty edits use the movie timescale, not the video track timescale.
    // Cover the selected tracks' clocks where their LCM fits the MP4 option.
    // Retain exact video ticks if unrelated auxiliary clocks exceed that limit.
    int64_t movie_scale = input.stream->time_base.den;
    for (unsigned i = 0; i < context->nb_streams; ++i) {
        int64_t denominator = context->streams[i]->time_base.den;
        int64_t multiple = movie_scale / std::gcd(movie_scale, denominator) * denominator;
        if (multiple <= std::numeric_limits<int>::max())
            movie_scale = multiple;
    }
    mux_options.set("movie_timescale", std::to_string(movie_scale));
    for (const auto &option : options.output_options) {
        auto stem = option_stem(option);
        if (stem == "metadata") {
            if (option.key == "metadata")
                set_metadata(&context->metadata, option.value);
            else
                for (const auto &mapped : layout.streams)
                    if (mapped.type == AVMEDIA_TYPE_VIDEO && layout.matches(option, mapped.output))
                        set_metadata(&context->streams[output_indices.at(mapped.output)]->metadata,
                                     option.value);
        } else if (stem == "disposition") {
            for (const auto &mapped : layout.streams)
                if (mapped.type == AVMEDIA_TYPE_VIDEO && layout.matches(option, mapped.output)) {
                    auto *target = context->streams[output_indices.at(mapped.output)];
                    target->disposition = disposition(option.value, target->disposition);
                }
        } else if (stem == "f")
            require(option.value == "mp4", "Output format must be mp4");
        else if (is_mux_option(context, stem)) {
            require(option.key == stem, "Muxer options cannot have a stream specifier");
            require(stem.rfind("encryption", 0) != 0,
                    "Encrypted smart-rendered output is not supported");
            auto *descriptor =
                av_opt_find(context, stem.c_str(), nullptr, 0, AV_OPT_SEARCH_CHILDREN);
            auto *previous = av_dict_get(mux_options.value, stem.c_str(), nullptr, 0);
            if (descriptor && descriptor->type == AV_OPT_TYPE_FLAGS && previous &&
                !option.value.empty() && (option.value[0] == '+' || option.value[0] == '-'))
                mux_options.set(stem, std::string(previous->value) + option.value);
            else
                mux_options.set(stem, option.value);
        }
    }
    // Signed composition offsets prevent movenc's negative-DTS start heuristic
    // from rounding an NTSC edit list down when the first GOP has B pictures.
    if (plan.signed_cts) {
        auto *flags = av_dict_get(mux_options.value, "movflags", nullptr, 0);
        std::string movflags = flags ? flags->value : "";
        mux_options.set("movflags", movflags + "+negative_cts_offsets");
    }
    check(avio_open(&context->pb, utf8(output).c_str(), AVIO_FLAG_WRITE), "Create staged output");
    check(avformat_write_header(context, &mux_options.value), "Write MP4 header");
    require(av_dict_count(mux_options.value) == 0, "Unrecognized MP4 muxer option");
    int video_output = output_indices.at(layout.video);
    VideoCursor cursor(input, idx, plan);
    auto video = cursor.next();
    auto auxiliary_packet = packet();
    bool have_aux = false;
    int64_t last_video_dts = AV_NOPTS_VALUE;
    size_t video_count = 0;
    auto read_aux = [&]() {
        if (!auxiliary)
            return false;
        int result;
        while ((result = av_read_frame(auxiliary->value, auxiliary_packet.get())) >= 0) {
            if (auxiliary_to_output.contains(auxiliary_packet->stream_index))
                return true;
            av_packet_unref(auxiliary_packet.get());
        }
        require(result == AVERROR_EOF, "FFmpeg auxiliary demux failed");
        return false;
    };
    have_aux = read_aux();
    Bsf bsf;
    while (video || have_aux) {
        cancellation_point();
        bool choose_video =
            bool(video) &&
            (!have_aux ||
             av_compare_ts(video->dts, input.stream->time_base, auxiliary_packet->dts,
                           auxiliary->value->streams[auxiliary_packet->stream_index]->time_base) <=
                 0);
        if (choose_video) {
            auto &item = *video;
            require(item.dts <= item.pts &&
                        (last_video_dts == AV_NOPTS_VALUE || item.dts > last_video_dts),
                    "Invalid video decode schedule");
            last_video_dts = item.dts;
            if (item.first) {
                if (stsd) {
                    auto *extra =
                        av_packet_new_side_data(item.data.get(), AV_PKT_DATA_NEW_EXTRADATA,
                                                static_cast<size_t>(item.params->extradata_size));
                    require(extra != nullptr, "Cannot attach video configuration");
                    std::memcpy(extra, item.params->extradata,
                                static_cast<size_t>(item.params->extradata_size));
                } else
                    bsf = annex_b(item.params, input.stream->time_base);
            }
            if (!stsd) {
                check(av_bsf_send_packet(bsf.get(), item.data.get()),
                      "Convert video packet to Annex B");
                check(av_bsf_receive_packet(bsf.get(), item.data.get()),
                      "Read Annex B video packet");
                if ((item.first || (item.data->flags & AV_PKT_FLAG_KEY)) &&
                    bsf->par_out->extradata_size) {
                    int prefix = bsf->par_out->extradata_size;
                    int original = item.data->size;
                    check(av_grow_packet(item.data.get(), prefix), "Restore video parameter sets");
                    std::memmove(item.data->data + prefix, item.data->data,
                                 static_cast<size_t>(original));
                    std::memcpy(item.data->data, bsf->par_out->extradata,
                                static_cast<size_t>(prefix));
                }
            }
            item.data->pts = item.pts;
            item.data->dts = item.dts;
            item.data->duration = item.duration;
            item.data->stream_index = video_output;
            item.data->pos = -1;
            av_packet_rescale_ts(item.data.get(), input.stream->time_base,
                                 context->streams[video_output]->time_base);
            check(av_interleaved_write_frame(context, item.data.get()), "Mux smart-rendered video");
            ++video_count;
            video = cursor.next();
        } else {
            int source = auxiliary_packet->stream_index;
            int target = auxiliary_to_output.at(source);
            av_packet_rescale_ts(auxiliary_packet.get(),
                                 auxiliary->value->streams[source]->time_base,
                                 context->streams[target]->time_base);
            auxiliary_packet->stream_index = target;
            auxiliary_packet->pos = -1;
            check(av_interleaved_write_frame(context, auxiliary_packet.get()),
                  "Mux FFmpeg audio/subtitle packet");
            av_packet_unref(auxiliary_packet.get());
            have_aux = read_aux();
        }
    }
    require(video_count == plan.copied + plan.encoded, "Final video packet count mismatch");
    check(av_write_trailer(context), "Finish MP4");
    check(avio_closep(&context->pb), "Close staged output");
}
static std::string report_json(const Options &options, const Layout &layout, const Index *index,
                               const Plan *plan, const std::string &encoder,
                               const std::map<std::string, std::string> &values, double elapsed) {
    std::ostringstream out;
    out.imbue(std::locale::classic());
    out << "{\n  \"status\": " << json_string(options.analyze ? "analyzed" : "completed")
        << ",\n  \"tool\": \"smartcut\",\n  \"version\": \"1.0.0\",\n  \"ffmpeg_libraries\": "
        << json_string(av_version_info())
        << ",\n  \"ffmpeg_executable\": " << json_string(options.ffmpeg)
        << ",\n  \"input\": " << json_string(options.input)
        << ",\n  \"output\": " << json_string(options.output)
        << ",\n  \"video_encoder\": " << json_string(encoder) << ",\n  \"video_options\": {";
    bool first = true;
    for (const auto &[key, value] : values) {
        if (!first)
            out << ',';
        out << json_string(key) << ':' << json_string(value);
        first = false;
    }
    out << "},\n  \"mapped_streams\": [";
    for (size_t i = 0; i < layout.streams.size(); ++i) {
        auto &stream = layout.streams[i];
        if (i)
            out << ',';
        out << "{\"input_index\":" << stream.source
            << ",\"requested_output_index\":" << stream.output
            << ",\"type\":" << json_string(av_get_media_type_string(stream.type)) << '}';
    }
    out << ']';
    if (index && plan) {
        out << ",\n  \"time_base\": [" << layout.matching->streams[layout.video]->time_base.num
            << ',' << layout.matching->streams[layout.video]->time_base.den << ']';
        out << ",\n  \"input_frames\": " << index->samples.size()
            << ",\n  \"copied_frames\": " << plan->copied
            << ",\n  \"encoded_frames\": " << plan->encoded
            << ",\n  \"decoded_frames\": " << plan->decoded
            << ",\n  \"duration_ticks\": " << plan->duration
            << ",\n  \"decode_delay_ticks\": " << plan->delay
            << ",\n  \"signed_composition_offsets\": " << (plan->signed_cts ? "true" : "false")
            << ",\n  \"lossless_full_encode_fallback\": "
            << (plan->lossless_fallback ? "true" : "false") << ",\n  \"units\": [";
        for (size_t i = 0; i < plan->units.size(); ++i) {
            const auto &u = plan->units[i];
            if (i)
                out << ',';
            out << "{\"mode\":" << json_string(u.copy ? "copy" : "encode")
                << ",\"begin\":" << u.begin << ",\"end\":" << u.end
                << ",\"cra_to_bla\":" << (u.patch_cra ? "true" : "false") << '}';
        }
        out << ']';
    }
    out << ",\n  \"elapsed_seconds\": " << elapsed << "\n}\n";
    return out.str();
}
int run(const Options &requested) {
    Options options = requested;
    av_log_set_level(options.log_level);
    auto started = std::chrono::steady_clock::now();
    require(fs::is_regular_file(path8(options.input)), "Input must be a local regular file");
    if (!options.hide_banner)
        std::cerr << "SmartCut 1.0.0 | FFmpeg " << av_version_info() << '\n';
    auto source_size = fs::file_size(path8(options.input));
    auto source_time = fs::last_write_time(path8(options.input));
    fs::path destination = options.output.empty() ? fs::current_path() / "unused.mp4"
                                                  : fs::absolute(path8(options.output));
    if (!options.analyze) {
        require(lower(destination.extension().string()) == ".mp4", "Output must be an MP4 file");
        require(fs::is_directory(destination.parent_path()),
                "Output parent directory does not exist");
        require(!fs::exists(destination) || options.overwrite,
                "Output exists; use -y to replace it atomically");
        require(!fs::exists(destination) || !fs::equivalent(destination, path8(options.input)),
                "Input and output must be different files");
    }
    if (!options.report.empty()) {
        auto report = fs::absolute(path8(options.report));
        require(fs::is_directory(report.parent_path()), "Report directory does not exist");
        require(!fs::exists(report) || options.overwrite,
                "Report exists; choose another path or use -y");
        require(report.lexically_normal() != destination.lexically_normal() &&
                    report.lexically_normal() !=
                        fs::absolute(path8(options.input)).lexically_normal(),
                "Report must differ from input and output");
    }
    if (options.ffmpeg.empty())
        options.ffmpeg = utf8(executable_path().parent_path() /
#ifdef _WIN32
                              "ffmpeg.exe"
#else
                              "ffmpeg"
#endif
        );
    Format probe;
    probe.open(options.input);
    Layout layout(probe.value, options);
    auto *format = av_guess_format("mp4", nullptr, nullptr);
    require(format != nullptr, "Missing MP4 muxer");
    bool has_aux = std::any_of(layout.streams.begin(), layout.streams.end(),
                               [](const auto &stream) { return stream.auxiliary >= 0; });
    for (const auto &stream : layout.streams)
        if (stream.auxiliary >= 0) {
            auto name = codec_name(options, layout, stream.output, format);
            if (name != "copy")
                require(find_encoder(name)->type == stream.type,
                        "Selected encoder does not match the audio/subtitle stream type");
        }
    std::unique_ptr<Input> video_input;
    std::optional<Index> index;
    std::optional<Plan> plan;
    const AVCodec *encoder = nullptr;
    std::string name = "none";
    std::map<std::string, std::string> encoder_values;
    bool stsd = false;
    if (layout.video >= 0) {
        video_input = std::make_unique<Input>(options.input, layout.streams[layout.video].source);
        name = codec_name(options, layout, layout.video, format);
        if (name != "copy") {
            encoder = find_encoder(name);
            require(encoder->id == video_input->stream->codecpar->codec_id,
                    "Video codec mismatch: selected encoder " + name + " outputs " +
                        avcodec_get_name(encoder->id) + ", copied video is " +
                        avcodec_get_name(video_input->stream->codecpar->codec_id));
        }
        index = index_input(*video_input);
        auto start =
            probe.value->start_time == AV_NOPTS_VALUE
                ? av_rescale_q(index->times[0], video_input->stream->time_base, AV_TIME_BASE_Q)
                : probe.value->start_time;
        plan = make_plan(*index, options, video_input->stream->time_base, start);
        layout.matching->streams[layout.video]->time_base = video_input->stream->time_base;
        if (name == "copy")
            require(plan->encoded == 0,
                    "Frame-exact boundaries require a matching video encoder (-c:v)");
        else {
            encoder_values = video_options(options, layout, encoder);
            auto validate = make_encoder(*video_input, encoder, encoder_values);
        }
        validate_video_format(options, layout, *video_input);
        stsd = descriptions(options, layout, index->codec);
        std::cerr << "SmartCut plan: " << plan->copied << " copied, " << plan->encoded
                  << " encoded video frames\n";
    } else
        require(options.frame_ranges.empty(), "--keep-frames requires a selected video stream");
    if (!options.analyze) {
        Workspace work(options.work.empty() ? destination.parent_path() : path8(options.work),
                       options.keep_work);
        Workspace publication(destination.parent_path(), false);
        fs::path staged = publication.directory / "output.mp4";
        Format auxiliary;
        if (has_aux) {
            require(fs::is_regular_file(path8(options.ffmpeg)),
                    "FFmpeg executable is required for audio/subtitles; use --ffmpeg");
            auto output = layout.video >= 0 ? work.directory / "audio-subtitles.mp4" : staged;
            std::vector<FrameRange> ranges;
            std::vector<int64_t> times;
            AVRational tb = AV_TIME_BASE_Q;
            int64_t origin = 0;
            if (plan) {
                ranges = plan->ranges;
                times = index->times;
                tb = video_input->stream->time_base;
                origin = probe.value->start_time == AV_NOPTS_VALUE
                             ? av_rescale_q(index->times[0], tb, AV_TIME_BASE_Q)
                             : probe.value->start_time;
            }
            run_process(
                auxiliary_arguments(options, layout, output, ranges, times, tb, origin, encoder),
                work.directory / "ffmpeg.log");
            if (layout.video >= 0)
                auxiliary.open(utf8(output));
        }
        if (layout.video >= 0) {
            if (encoder)
                encode_boundaries(*video_input, *index, *plan, encoder, encoder_values, work);
            else
                calculate_delay(*index, *plan);
            mux(*video_input, *index, *plan, options, layout, has_aux ? &auxiliary : nullptr,
                staged, stsd);
        }
        require(fs::file_size(path8(options.input)) == source_size &&
                    fs::last_write_time(path8(options.input)) == source_time,
                "Input changed while the job was running");
        publish_file(staged, destination, options.overwrite);
        if (options.keep_work)
            std::cerr << "Work files: " << utf8(work.directory) << '\n';
    } else if (plan)
        calculate_delay(*index, *plan);
    auto elapsed =
        std::chrono::duration<double>(std::chrono::steady_clock::now() - started).count();
    auto report = report_json(options, layout, index ? &*index : nullptr, plan ? &*plan : nullptr,
                              name, encoder_values, elapsed);
    if (!options.report.empty()) {
        auto target = fs::absolute(path8(options.report));
        Workspace stage(target.parent_path(), false);
        auto temporary = stage.directory / "report.json";
        {
            std::ofstream out(temporary, std::ios::binary);
            out << report;
            out.close();
            require(bool(out), "Cannot write JSON report");
        }
        publish_file(temporary, target, options.overwrite);
    }
    std::cout << report;
    return 0;
}
} // namespace smartcut
