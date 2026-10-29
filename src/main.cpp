// SPDX-License-Identifier: GPL-3.0-or-later
#include "media.hpp"
#include <csignal>
#include <exception>
#include <iostream>
#ifdef _WIN32
#include <windows.h>
#endif
static void interrupt(int) {
    smartcut::cancelled.store(true);
}
static int entry(const std::vector<std::string> &args) {
    try {
        std::signal(SIGINT, interrupt);
        std::signal(SIGTERM, interrupt);
        for (const auto &arg : args)
            if (arg == "-h" || arg == "--help" || arg == "-help") {
                std::cout << smartcut::help_text();
                return 0;
            }
        for (const auto &arg : args)
            if (arg == "--version" || arg == "-version") {
                std::cout << "SmartCut 1.0.0; FFmpeg " << av_version_info() << '\n';
                return 0;
            }
        return smartcut::run(smartcut::parse_options(args));
    } catch (const std::exception &error) {
        std::cerr << "smartcut: " << error.what() << '\n';
        return smartcut::cancelled.load() ? 130 : 1;
    }
}
#ifdef _WIN32
int wmain(int argc, wchar_t **argv) {
    std::vector<std::string> args;
    for (int i = 0; i < argc; ++i) {
        int n = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, argv[i], -1, nullptr, 0, nullptr,
                                    nullptr);
        if (!n)
            return 1;
        std::string text(static_cast<size_t>(n), '\0');
        WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, argv[i], -1, text.data(), n, nullptr,
                            nullptr);
        text.pop_back();
        args.push_back(std::move(text));
    }
    return entry(args);
}
#else
int main(int argc, char **argv) {
    return entry({argv, argv + argc});
}
#endif
