// SPDX-License-Identifier: GPL-3.0-or-later
#include "media.hpp"
#include <iostream>
using namespace smartcut;
int main() {
    try {
        auto o =
            parse_options({"smartcut", "-hide_banner", "-y",      "-i",          "in.mp4", "-ss",
                           "1:0:1",    "-to",          "2:0:1",   "-map",        "0:v:0?", "-map",
                           "0:a:0?",   "-c:v:0",       "libx264", "-preset:v:0", "slower", "-c:a:0",
                           "aac",      "-b:a:0",       "192k",    "out.mp4"});
        require(o.output_times.start == 3'601'000'000LL && *o.output_times.end == 7'201'000'000LL,
                "HH:MM:SS parsing");
        require(o.output_options.size() == 4 && o.output_options[1].key == "preset:v:0" &&
                    o.output_options[1].value == "slower",
                "Stream-specific option preservation");
        auto frames =
            parse_options({"smartcut", "-i", "in.mp4", "--keep-frames", "23:25", "-an", "out.mp4"});
        require(frames.frame_ranges[0].begin == 23 && frames.frame_ranges[0].end == 25,
                "Display frame bounds");
        // The reported command has spaces, a full-width exclamation mark,
        // stream-specific tuning, and metadata mapping. Test the token vector
        // independently of media availability and of the executable's name.
        const std::string input =
            "C:\\Users\\Steve\\Videos\\NVIDIA\\Minecraft\\Bad Apple\xef\xbc\x81 "
            "2026.05.16 - 20.29.43.01.mp4";
        const std::string output =
            "C:\\Users\\Steve\\Videos\\NVIDIA\\Minecraft\\Bad Apple\xef\xbc\x81 "
            "2026.05.16 - 20.29.43.01_2026.10.29-18.12.59.mp4";
        std::vector<std::string> supplied = {
            "ffmpeg.exe", "-hide_banner", "-y",      "-i",          input,    "-ss",
            "0:23",       "-to",          "1:15",    "-map",        "0:v:0?", "-map",
            "0:a:0?",     "-c:v:0",       "libx265", "-preset:v:0", "slow",   "-tune:v:0",
            "animation",  "-crf:v:0",     "24",      "-c:a:0",      "copy",   "-map_metadata",
            "0",          output};
        auto example = parse_options(supplied);
        require(example.input == input && example.output == output && example.overwrite &&
                    example.hide_banner && example.output_times.start == 23'000'000 &&
                    *example.output_times.end == 75'000'000 && example.maps.size() == 2,
                "Reported Unicode command and MM:SS parsing");
        require(example.output_options.size() == 6 && example.output_options[2].key == "tune:v:0" &&
                    example.output_options[2].value == "animation" &&
                    example.output_options[5].key == "map_metadata" &&
                    example.output_options[5].value == "0",
                "Reported encoder tune and metadata preservation");
        auto diagnostic = [&](std::vector<std::string> args, const std::string &expected) {
            try {
                parse_options(args);
            } catch (const std::exception &error) {
                require(std::string(error.what()).find(expected) != std::string::npos,
                        "Argument diagnostic did not identify the offending token");
                return;
            }
            fail("Malformed argument list was accepted");
        };
        supplied.insert(supplied.begin(), "smartcut.exe");
        diagnostic(supplied, "argument 1 \"ffmpeg.exe\" before -i");
        diagnostic({"smartcut.exe", "-i", "in.mp4", "Bad", "Apple.mp4"},
                   "argument 3 \"Bad\" followed by \"Apple.mp4\"");
        auto bad = [&](std::vector<std::string> args) {
            bool rejected = false;
            try {
                parse_options(args);
            } catch (...) {
                rejected = true;
            }
            require(rejected, "Unsafe command was accepted");
        };
        bad({"smartcut", "-y", "-n", "-i", "in.mp4", "out.mp4"});
        bad({"smartcut", "-i", "in.mp4", "-vf", "scale=640:360", "out.mp4"});
        bad({"smartcut", "-i", "a.mp4", "-i", "b.mp4", "out.mp4"});
        bad({"smartcut", "-i", "in.mp4", "--keep-frames", "1:2", "-ss", "1", "out.mp4"});
        bad({"smartcut", "-i", "in.mp4", "-ss", "-1", "out.mp4"});
        std::cout << "CLI option tests passed\n";
        return 0;
    } catch (const std::exception &error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
