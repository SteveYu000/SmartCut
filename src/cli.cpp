// SPDX-License-Identifier: GPL-3.0-or-later
#include "media.hpp"
#include <algorithm>
#include <charconv>
#include <limits>
#include <sstream>

namespace smartcut {
static int64_t integer(const std::string &text) {
    int64_t value{};
    auto [end, error] = std::from_chars(text.data(), text.data() + text.size(), value);
    require(error == std::errc{} && end == text.data() + text.size() && value >= 0,
            "Invalid frame index: " + text);
    return value;
}
Options parse_options(const std::vector<std::string> &args) {
    Options result;
    bool input_seen = false;
    for (size_t i = 1; i < args.size(); ++i) {
        std::string key = args[i];
        auto value = [&]() {
            require(i + 1 < args.size(), "Missing value for " + key);
            return args[++i];
        };
        if (key == "-i") {
            require(!input_seen, "One input file is supported");
            result.input = value();
            input_seen = true;
        } else if (key == "-y")
            result.overwrite = true;
        else if (key == "-n")
            result.no_overwrite = true;
        else if (key == "-hide_banner")
            result.hide_banner = true;
        else if (key == "-nostdin" || key == "-smartcut") {
        } else if (key == "-an")
            result.no_audio = true;
        else if (key == "-sn")
            result.no_subtitles = true;
        else if (key == "-vn")
            result.no_video = true;
        else if (key == "-ss" || key == "-t" || key == "-to") {
            auto &times = input_seen ? result.output_times : result.input_times;
            auto time = parse_time(value());
            if (key == "-ss")
                times.start = time;
            else if (key == "-t")
                times.duration = time;
            else
                times.end = time;
        } else if (key == "--keep-frames" || key == "--keep") {
            auto text = value();
            auto colon = text.find(':');
            require(colon != std::string::npos, "Use --keep-frames A:B");
            result.frame_ranges.push_back(
                {integer(text.substr(0, colon)), integer(text.substr(colon + 1))});
        } else if (key == "--report" || key == "--json-report")
            result.report = value();
        else if (key == "--ffmpeg")
            result.ffmpeg = value();
        else if (key == "--work-dir")
            result.work = value();
        else if (key == "--keep-work")
            result.keep_work = true;
        else if (key == "--analyze")
            result.analyze = true;
        else if (key == "-loglevel" || key == "-v") {
            auto level = value();
            const std::map<std::string, int> levels = {
                {"quiet", AV_LOG_QUIET},     {"panic", AV_LOG_PANIC},     {"fatal", AV_LOG_FATAL},
                {"error", AV_LOG_ERROR},     {"warning", AV_LOG_WARNING}, {"info", AV_LOG_INFO},
                {"verbose", AV_LOG_VERBOSE}, {"debug", AV_LOG_DEBUG},     {"trace", AV_LOG_TRACE}};
            auto found = levels.find(level);
            require(found != levels.end(), "Use a named -loglevel (quiet through trace)");
            result.log_level = found->second;
        } else if (key == "-map")
            result.maps.push_back(value());
        else if (!key.empty() && key[0] == '-') {
            require(input_seen,
                    "Unsupported input option " + key + "; input -ss/-t/-to are supported");
            require(key.rfind("--", 0) != 0, "Unknown SmartCut option: " + key);
            key.erase(0, 1);
            const std::set<std::string> unsupported = {"copyts",
                                                       "start_at_zero",
                                                       "copytb",
                                                       "vsync",
                                                       "r",
                                                       "vf",
                                                       "filter:v",
                                                       "filter_complex",
                                                       "s",
                                                       "s:v",
                                                       "shortest",
                                                       "fs",
                                                       "itsoffset",
                                                       "itsscale",
                                                       "force_key_frames",
                                                       "frames:v",
                                                       "vframes",
                                                       "fps_mode",
                                                       "enc_time_base",
                                                       "bsf:v",
                                                       "vpre",
                                                       "target",
                                                       "stream_loop",
                                                       "re",
                                                       "readrate",
                                                       "filter_threads",
                                                       "filter_complex_threads"};
            auto stem = key.substr(0, key.find(':'));
            require(!unsupported.contains(key) && !unsupported.contains(stem),
                    "Option -" + key +
                        " cannot be applied to copied video; use a full FFmpeg transcode for "
                        "timeline, filter or geometry changes");
            if (key == "vcodec")
                key = "c:v";
            else if (key == "acodec")
                key = "c:a";
            else if (key == "scodec")
                key = "c:s";
            else if (key == "b" || key == "vb")
                key = "b:v";
            else if (key == "ab")
                key = "b:a";
            else if (key == "vtag")
                key = "tag:v";
            else if (key == "atag")
                key = "tag:a";
            else if (key == "af")
                key = "filter:a";
            result.output_options.push_back({key, value(), true});
        } else {
            auto argument = "argument " + std::to_string(i) + " " + json_string(key);
            require(input_seen,
                    "Unexpected positional " + argument +
                        " before -i. Pass options directly after smartcut.exe; do not include "
                        "another ffmpeg.exe command name. Quote paths containing spaces.");
            require(result.output.empty(),
                    "Multiple output filenames are not supported: " + argument);
            if (i + 1 != args.size())
                fail("Output filename must be the last argument; encountered " + argument +
                     " followed by " + json_string(args[i + 1]) +
                     ". Quote the entire input and output paths if they contain spaces.");
            result.output = key;
        }
    }
    require(!result.input.empty(), "An input is required; see --help");
    require(result.analyze || !result.output.empty(), "An output is required; see --help");
    require(!(result.overwrite && result.no_overwrite), "-y and -n cannot be combined");
    if (!result.frame_ranges.empty())
        require(result.input_times.start == 0 && result.output_times.start == 0 &&
                    !result.input_times.duration && !result.output_times.duration &&
                    !result.input_times.end && !result.output_times.end,
                "--keep-frames cannot be combined with -ss/-t/-to");
    return result;
}
std::string help_text() {
    return "SmartCut 1.0.0 - H.264 / HEVC MP4 smart rendering\n"
           "Usage: smartcut [global options] [-ss TIME] -i INPUT [-ss TIME] [-to TIME | -t TIME]\n"
           "                [-map 0:v:0?] [-map 0:a:0?] [-map 0:s?] [encoding options] OUTPUT.mp4\n"
           "Example: smartcut -hide_banner -y -i input.mp4 -ss 1:0:1 -to 2:0:1\n"
           "         -map 0:v:0? -map 0:a:0? -c:v:0 libx264 -preset:v:0 slower\n"
           "         -c:a:0 aac -b:a:0 192k output.mp4\n"
           "Video encoder options apply only to re-encoded boundaries. The codec must\n"
           "match the source. Audio/subtitles use the original FFmpeg command pipeline\n"
           "over the entire selected interval, including -c:a copy / -c:s copy.\n"
           "Supported input: local MP4, one selected progressive H.264/HEVC video,\n"
           "fixed configuration, 4:2:0 8/10 bit. Multiple audio/subtitle tracks allowed.\n"
           "Video filters, frame-rate/geometry conversion, multiple inputs/outputs are\n"
           "rejected. Encoder options use FFmpeg AVOptions; no CRF/preset/B-frame override.\n"
           "  --keep-frames A:B  exact display-frame range, end exclusive; repeat for video-only "
           "cuts\n"
           "  --report FILE     JSON plan, encoder settings and copy/encode counts\n"
           "  --analyze         validate settings and show plan without writing output\n"
           "  --ffmpeg FILE     original FFmpeg executable (default: beside smartcut)\n"
           "  --work-dir DIR    parent for a private temporary directory\n"
           "  --keep-work       retain temporary files for diagnosis\n"
           "  -y / -n           atomic overwrite / refuse overwrite; existing files are\n"
           "                    preserved on any encoding or validation failure\n";
}
std::string option_stem(const Option &option) {
    return option.key.substr(0, option.key.find(':'));
}
static std::string stream_specifier(const Option &option) {
    if (option.key.rfind("metadata:s:", 0) == 0)
        return option.key.substr(11);
    if (option.key == "metadata:s")
        return "";
    auto colon = option.key.find(':');
    return colon == std::string::npos ? "" : option.key.substr(colon + 1);
}
Layout::Layout(AVFormatContext *input, const Options &options) {
    std::vector<int> selected;
    if (options.maps.empty()) {
        int selected_video = -1, audio = -1, subtitle = -1;
        int64_t pixels = -1;
        int channels = -1;
        auto *format = av_guess_format("mp4", nullptr, nullptr);
        require(format != nullptr, "Missing MP4 muxer");
        bool force_subtitles = false;
        for (const auto &option : options.output_options)
            if ((option_stem(option) == "c" || option_stem(option) == "codec") &&
                stream_specifier(option).rfind("s", 0) == 0)
                force_subtitles = true;
        for (unsigned i = 0; i < input->nb_streams; ++i) {
            auto *stream = input->streams[i];
            auto *p = stream->codecpar;
            if (p->codec_type == AVMEDIA_TYPE_VIDEO &&
                !(stream->disposition & AV_DISPOSITION_ATTACHED_PIC) &&
                int64_t(p->width) * p->height > pixels) {
                selected_video = static_cast<int>(i);
                pixels = int64_t(p->width) * p->height;
            }
            if (p->codec_type == AVMEDIA_TYPE_AUDIO && p->ch_layout.nb_channels > channels) {
                audio = static_cast<int>(i);
                channels = p->ch_layout.nb_channels;
            }
            if (p->codec_type == AVMEDIA_TYPE_SUBTITLE && subtitle < 0) {
                auto *desc = avcodec_descriptor_get(p->codec_id);
                if (force_subtitles || (format->subtitle_codec != AV_CODEC_ID_NONE && desc &&
                                        (desc->props & AV_CODEC_PROP_TEXT_SUB)))
                    subtitle = static_cast<int>(i);
            }
        }
        for (int stream : {selected_video, audio, subtitle})
            if (stream >= 0)
                selected.push_back(stream);
    } else {
        for (auto mapping : options.maps) {
            bool negative = !mapping.empty() && mapping[0] == '-';
            if (negative)
                mapping.erase(0, 1);
            bool optional = !mapping.empty() && mapping.back() == '?';
            if (optional)
                mapping.pop_back();
            require(mapping == "0" || mapping.rfind("0:", 0) == 0,
                    "Only input 0 stream maps are supported: " + mapping);
            auto spec = mapping == "0" ? "" : mapping.substr(2);
            bool matched = false;
            for (unsigned i = 0; i < input->nb_streams; ++i) {
                int match = avformat_match_stream_specifier(input, input->streams[i], spec.c_str());
                check(match, "Invalid input stream specifier");
                if (match) {
                    matched = true;
                    if (negative)
                        selected.erase(
                            std::remove(selected.begin(), selected.end(), static_cast<int>(i)),
                            selected.end());
                    else
                        selected.push_back(static_cast<int>(i));
                }
            }
            require(matched || optional, "Stream map matches no streams: " + mapping);
        }
    }
    matching = avformat_alloc_context();
    require(matching != nullptr, "Cannot allocate stream mapping context");
    int auxiliary = 0;
    try {
        for (int source : selected) {
            auto *stream = input->streams[source];
            auto type = stream->codecpar->codec_type;
            if ((type == AVMEDIA_TYPE_VIDEO && options.no_video) ||
                (type == AVMEDIA_TYPE_AUDIO && options.no_audio) ||
                (type == AVMEDIA_TYPE_SUBTITLE && options.no_subtitles))
                continue;
            require(type == AVMEDIA_TYPE_VIDEO || type == AVMEDIA_TYPE_AUDIO ||
                        type == AVMEDIA_TYPE_SUBTITLE,
                    "Only video, audio and subtitle output streams are supported");
            int out = static_cast<int>(streams.size());
            if (type == AVMEDIA_TYPE_VIDEO) {
                require(video < 0, "Select exactly one video stream");
                video = out;
            }
            auto *match = avformat_new_stream(matching, nullptr);
            require(match != nullptr, "Cannot allocate mapped stream");
            check(avcodec_parameters_copy(match->codecpar, stream->codecpar),
                  "Copy mapped stream parameters");
            av_dict_copy(&match->metadata, stream->metadata, 0);
            match->disposition = stream->disposition;
            streams.push_back({source, out, type == AVMEDIA_TYPE_VIDEO ? -1 : auxiliary++, type});
        }
        require(!streams.empty(), "No streams selected");
    } catch (...) {
        avformat_free_context(matching);
        matching = nullptr;
        throw;
    }
}
bool Layout::matches(const Option &option, int output_index) const {
    auto spec = stream_specifier(option);
    int result =
        avformat_match_stream_specifier(matching, matching->streams[output_index], spec.c_str());
    check(result, "Invalid output stream specifier");
    return result > 0;
}
const AVCodec *find_encoder(const std::string &name) {
    auto *codec = avcodec_find_encoder_by_name(name.c_str());
    if (!codec) {
        auto *descriptor = avcodec_descriptor_get_by_name(name.c_str());
        if (descriptor)
            codec = avcodec_find_encoder(descriptor->id);
    }
    require(codec != nullptr, "Unknown or unavailable encoder: " + name);
    return codec;
}
std::string codec_name(const Options &options, const Layout &layout, int output_index,
                       const AVOutputFormat *format) {
    std::string result;
    for (const auto &option : options.output_options)
        if ((option_stem(option) == "c" || option_stem(option) == "codec") &&
            layout.matches(option, output_index))
            result = option.value;
    if (result.empty()) {
        auto type = layout.streams[output_index].type;
        auto id = av_guess_codec(format, nullptr, nullptr, nullptr, type);
        auto *codec = avcodec_find_encoder(id);
        require(codec != nullptr,
                "No default encoder for selected stream; specify -c:v / -c:a / -c:s");
        result = codec->name;
    }
    return result;
}
static bool special(const std::string &stem) {
    static const std::set<std::string> names = {
        "c",        "codec",        "tag",          "pix_fmt",     "aspect",
        "metadata", "map_metadata", "map_chapters", "disposition", "f"};
    return names.contains(stem);
}
static bool mux_option(const std::string &stem) {
    AVFormatContext *raw = nullptr;
    check(avformat_alloc_output_context2(&raw, nullptr, "mp4", nullptr),
          "Allocate muxer option context");
    auto *option = av_opt_find(raw, stem.c_str(), nullptr, 0, AV_OPT_SEARCH_CHILDREN);
    bool found = option && (option->flags & AV_OPT_FLAG_ENCODING_PARAM);
    avformat_free_context(raw);
    return found;
}
std::map<std::string, std::string> video_options(const Options &options, const Layout &layout,
                                                 const AVCodec *encoder) {
    std::map<std::string, std::string> result;
    Codec context(avcodec_alloc_context3(encoder));
    require(bool(context), "Cannot allocate encoder options context");
    for (const auto &option : options.output_options) {
        auto stem = option_stem(option);
        if (special(stem) || !layout.matches(option, layout.video) || mux_option(stem))
            continue;
        auto *found = av_opt_find(context.get(), stem.c_str(), nullptr, 0, AV_OPT_SEARCH_CHILDREN);
        if (found && (found->flags & AV_OPT_FLAG_ENCODING_PARAM) &&
            (found->flags & AV_OPT_FLAG_VIDEO_PARAM)) {
            if (found->type == AV_OPT_TYPE_FLAGS && result.contains(stem) &&
                !option.value.empty() && (option.value[0] == '+' || option.value[0] == '-'))
                result[stem] += option.value;
            else
                result[stem] = option.value;
        } else {
            bool auxiliary_target = false;
            for (const auto &stream : layout.streams)
                if (stream.auxiliary >= 0 && layout.matches(option, stream.output))
                    auxiliary_target = true;
            require(auxiliary_target, "Unknown or unsupported video option: -" + option.key);
        }
    }
    return result;
}
static void append_times(std::vector<std::string> &args, const Times &times) {
    if (times.start)
        args.insert(args.end(), {"-ss", seconds(times.start)});
    if (times.duration)
        args.insert(args.end(), {"-t", seconds(*times.duration)});
    if (times.end)
        args.insert(args.end(), {"-to", seconds(*times.end)});
}
std::vector<std::string>
auxiliary_arguments(const Options &options, const Layout &layout, const fs::path &output,
                    const std::vector<FrameRange> &ranges, const std::vector<int64_t> &times,
                    AVRational time_base, int64_t origin_us, const AVCodec *video_encoder) {
    std::vector<std::string> result = {options.ffmpeg, "-nostdin",
                                       "-hide_banner", "-y",
                                       "-loglevel",    std::to_string(options.log_level)};
    if (options.frame_ranges.empty())
        append_times(result, options.input_times);
    result.insert(result.end(), {"-i", options.input});
    if (options.frame_ranges.empty())
        append_times(result, options.output_times);
    else {
        require(ranges.size() == 1,
                "Multiple --keep-frames ranges currently require video-only output (-an -sn)");
        auto begin =
            av_rescale_q(times[static_cast<size_t>(ranges[0].begin)], time_base, AV_TIME_BASE_Q) -
            origin_us;
        auto end =
            av_rescale_q(times[static_cast<size_t>(ranges[0].end)], time_base, AV_TIME_BASE_Q) -
            origin_us;
        result.insert(result.end(), {"-ss", seconds(begin), "-t", seconds(end - begin)});
    }
    for (const auto &stream : layout.streams)
        if (stream.auxiliary >= 0)
            result.insert(result.end(), {"-map", "0:" + std::to_string(stream.source)});
    Codec context(video_encoder ? avcodec_alloc_context3(video_encoder) : nullptr);
    for (const auto &option : options.output_options) {
        auto stem = option_stem(option);
        if (stem == "f") {
            require(option.value == "mp4", "Output format must be mp4");
            continue;
        }
        if (stem == "metadata" && option.key == "metadata") {
            result.insert(result.end(), {"-metadata", option.value});
            continue;
        }
        if (stem == "map_metadata" || stem == "map_chapters") {
            require(option.key == stem && (option.value == "0" || option.value == "-1"),
                    "Only global -map_metadata / -map_chapters 0 or -1 are supported");
            result.insert(result.end(), {"-" + option.key, option.value});
            continue;
        }
        if (mux_option(stem)) {
            if (layout.video < 0)
                result.insert(result.end(), {"-" + stem, option.value});
            continue;
        }
        if (stem == "pix_fmt" || stem == "aspect")
            continue;
        auto *found =
            context ? av_opt_find(context.get(), stem.c_str(), nullptr, 0, AV_OPT_SEARCH_CHILDREN)
                    : nullptr;
        if (stream_specifier(option).empty() && found && (found->flags & AV_OPT_FLAG_VIDEO_PARAM) &&
            !(found->flags & AV_OPT_FLAG_AUDIO_PARAM))
            continue;
        for (const auto &stream : layout.streams)
            if (stream.auxiliary >= 0 && layout.matches(option, stream.output)) {
                auto key = stem == "metadata" ? "metadata:s:" + std::to_string(stream.auxiliary)
                                              : stem + ":" + std::to_string(stream.auxiliary);
                result.insert(result.end(), {"-" + key, option.value});
            }
    }
    // Stable IDs let the muxer distinguish tracks that FFmpeg omits because
    // the selected interval contains no packets. No audio/subtitle data changes.
    if (layout.video >= 0) {
        for (const auto &stream : layout.streams)
            if (stream.auxiliary >= 0)
                result.insert(result.end(), {"-streamid", std::to_string(stream.auxiliary) + ":" +
                                                              std::to_string(stream.output + 1)});
        result.insert(result.end(), {"-use_stream_ids_as_track_ids", "1"});
    }
    result.insert(result.end(), {"-vn", "-f", "mp4", utf8(output)});
    return result;
}
} // namespace smartcut
