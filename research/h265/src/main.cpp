// SPDX-License-Identifier: GPL-3.0-or-later
#include "engine.hpp"
#include <charconv>
#include <cmath>
#include <iostream>
#include <stdexcept>
#include <string_view>
#ifdef _WIN32
#include <windows.h>
#endif

static int64_t integer(std::string_view v) {
    int64_t result{};
    auto [p, e] = std::from_chars(v.data(), v.data() + v.size(), result);
    if (e != std::errc{} || p != v.data() + v.size() || result < 0)
        throw std::runtime_error("Expected a non-negative frame index");
    return result;
}
static int entry(const std::vector<std::string>& argv) {
    try {
        sr::Options o;
        for (size_t i = 1; i < argv.size(); ++i) {
            const auto& arg = argv[i];
            auto value = [&]() -> const std::string& {
                if (++i == argv.size()) throw std::runtime_error("Missing value for " + arg);
                return argv[i];
            };
            if (arg == "--help" || arg == "-h") {
                std::cout << "H.265/MP4 smart rendering (video only)\n"
                    "smart_render -i input.mp4 -o output.mp4 --keep 13:147 [--keep 180:250]\n"
                    "  --keep A:B    zero-based display frames, A inclusive / B exclusive\n"
                    "  --ps-mode hev1|stsd   parameter-set transport (default hev1)\n"
                    "  --crf 0..51   boundary quality (default 18); CRF 0 is not lossless\n"
                    "  --lossless    lossless boundary encoding (x265 lossless=1)\n"
                    "  --access cra|idr   CRA-aware planning (default) or conservative IDR baseline\n"
                    "  --analyze     index and plan only\n"
                    "  --report FILE write JSON plan and counters\n"
                    "Ranges must be ordered and non-overlapping. Existing outputs are never overwritten.\n";
                return 0;
            } else if (arg == "-i" || arg == "--input") o.input = value();
            else if (arg == "-o" || arg == "--output") o.output = value();
            else if (arg == "--report") o.report = value();
            else if (arg == "--ps-mode") o.ps_mode = value();
            else if (arg == "--analyze") o.analyze = true;
            else if (arg == "--lossless") o.lossless = true;
            else if (arg == "--access") o.access = value();
            else if (arg == "--experimental-no-bla") o.bla = false;
            else if (arg == "--crf") {
                size_t pos{}; const auto& v = value(); o.crf = std::stod(v, &pos);
                if (pos != v.size() || !std::isfinite(o.crf) || o.crf < 0 || o.crf > 51)
                    throw std::runtime_error("CRF must be within 0..51");
            } else if (arg == "--keep") {
                const auto& v = value(); auto p = v.find(':');
                if (p == std::string::npos) throw std::runtime_error("Use --keep A:B");
                o.ranges.push_back({integer(std::string_view(v).substr(0, p)), integer(std::string_view(v).substr(p + 1))});
            } else throw std::runtime_error("Unknown argument: " + arg);
        }
        if (o.input.empty() || (!o.analyze && o.output.empty()) || o.ranges.empty())
            throw std::runtime_error("Input, --keep, and (unless --analyze) output are required; see --help");
        if (o.ps_mode != "hev1" && o.ps_mode != "stsd") throw std::runtime_error("--ps-mode must be hev1 or stsd");
        if (o.access != "cra" && o.access != "idr") throw std::runtime_error("--access must be cra or idr");
        sr::run(o);
        return 0;
    } catch (const std::exception& e) { std::cerr << "ERROR: " << e.what() << '\n'; return 1; }
}
#ifdef _WIN32
int wmain(int argc, wchar_t** argv) {
    std::vector<std::string> args;
    for (int i = 0; i < argc; ++i) {
        int n = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, argv[i], -1, nullptr, 0, nullptr, nullptr);
        if (!n) return 1;
        std::string s(static_cast<size_t>(n), '\0');
        WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, argv[i], -1, s.data(), n, nullptr, nullptr);
        s.pop_back(); args.push_back(std::move(s));
    }
    return entry(args);
}
#else
int main(int argc, char** argv) { return entry({argv, argv + argc}); }
#endif
