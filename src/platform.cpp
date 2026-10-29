// SPDX-License-Identifier: GPL-3.0-or-later
#include "media.hpp"
#include <chrono>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <random>
#include <sstream>
#include <stdexcept>
#include <thread>
#ifdef _WIN32
#include <windows.h>
#else
#include <fcntl.h>
#include <signal.h>
#include <sys/wait.h>
#include <unistd.h>
#endif

namespace smartcut {
std::atomic_bool cancelled{false};
[[noreturn]] void fail(const std::string &text) {
    throw std::runtime_error(text);
}
void require(bool ok, const std::string &text) {
    if (!ok)
        fail(text);
}
void check(int result, const char *operation) {
    if (result < 0) {
        char text[AV_ERROR_MAX_STRING_SIZE];
        av_strerror(result, text, sizeof(text));
        fail(std::string(operation) + ": " + text);
    }
}
void cancellation_point() {
    require(!cancelled.load(), "Operation cancelled");
}
fs::path path8(const std::string &value) {
    return fs::path(std::u8string(reinterpret_cast<const char8_t *>(value.data()), value.size()));
}
std::string utf8(const fs::path &path) {
    auto text = path.u8string();
    return {reinterpret_cast<const char *>(text.data()), text.size()};
}
Packet packet() {
    Packet p(av_packet_alloc());
    require(bool(p), "Cannot allocate packet");
    return p;
}
Frame frame() {
    Frame f(av_frame_alloc());
    require(bool(f), "Cannot allocate frame");
    return f;
}
Params parameters(const AVCodecParameters *source) {
    Params p(avcodec_parameters_alloc());
    require(bool(p), "Cannot allocate codec parameters");
    if (source)
        check(avcodec_parameters_copy(p.get(), source), "Copy codec parameters");
    return p;
}
void Dictionary::set(const std::string &key, const std::string &text) {
    check(av_dict_set(&value, key.c_str(), text.c_str(), 0), "Set FFmpeg option");
}
void Format::open(const std::string &filename) {
    check(avformat_open_input(&value, filename.c_str(), nullptr, nullptr), "Open input");
    check(avformat_find_stream_info(value, nullptr), "Read stream information");
}
std::string json_string(const std::string &value) {
    std::ostringstream out;
    out << '"';
    for (unsigned char c : value) {
        if (c == '"' || c == '\\')
            out << '\\' << static_cast<char>(c);
        else if (c < 32)
            out << "\\u" << std::hex << std::setw(4) << std::setfill('0') << unsigned(c)
                << std::dec;
        else
            out << static_cast<char>(c);
    }
    out << '"';
    return out.str();
}
std::string seconds(int64_t us) {
    std::ostringstream text;
    text.imbue(std::locale::classic());
    text << us / 1'000'000 << '.' << std::setw(6) << std::setfill('0') << us % 1'000'000;
    return text.str();
}
int64_t parse_time(const std::string &value) {
    int64_t result{};
    check(av_parse_time(&result, value.c_str(), 1), "Invalid FFmpeg time");
    require(result >= 0, "Cut times must be non-negative");
    return result;
}
Workspace::Workspace(const fs::path &parent, bool retain) : keep(retain) {
    require(fs::is_directory(parent), "Work directory does not exist");
    std::random_device random;
    for (int attempt = 0; attempt < 32; ++attempt) {
        auto name = "smartcut-" +
                    std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()) +
                    "-" + std::to_string(random());
        directory = fs::absolute(parent / name).lexically_normal();
        if (fs::create_directory(directory))
            return;
    }
    fail("Cannot create a private work directory");
}
Workspace::~Workspace() {
    if (keep || directory.empty())
        return;
    // Only the unique child created by this job is owned by the application.
    if (directory.filename().string().rfind("smartcut-", 0) == 0) {
        std::error_code error;
        fs::remove_all(directory, error);
    }
}
fs::path executable_path() {
#ifdef _WIN32
    std::wstring buffer(32768, L'\0');
    DWORD n = GetModuleFileNameW(nullptr, buffer.data(), static_cast<DWORD>(buffer.size()));
    require(n > 0 && n < buffer.size(), "Cannot locate application");
    buffer.resize(n);
    return fs::path(buffer);
#else
    std::string buffer(16384, '\0');
    auto n = readlink("/proc/self/exe", buffer.data(), buffer.size());
    require(n > 0 && static_cast<size_t>(n) < buffer.size(), "Cannot locate application");
    buffer.resize(static_cast<size_t>(n));
    return fs::path(buffer);
#endif
}
static std::string log_tail(const fs::path &path) {
    std::ifstream in(path, std::ios::binary);
    std::string data((std::istreambuf_iterator<char>(in)), {});
    if (data.size() > 32768)
        data.erase(0, data.size() - 32768);
    return data;
}
#ifdef _WIN32
struct Handle {
    HANDLE value = INVALID_HANDLE_VALUE;
    explicit Handle(HANDLE h = INVALID_HANDLE_VALUE) : value(h) {}
    ~Handle() {
        if (value && value != INVALID_HANDLE_VALUE)
            CloseHandle(value);
    }
    Handle(const Handle &) = delete;
    Handle &operator=(const Handle &) = delete;
};
static std::wstring wide(const std::string &text) {
    return path8(text).native();
}
static std::wstring quote_argument(const std::wstring &text) {
    std::wstring result = L"\"";
    size_t slashes = 0;
    for (wchar_t c : text) {
        if (c == L'\\') {
            ++slashes;
            continue;
        }
        result.append(slashes * (c == L'"' ? 2 : 1), L'\\');
        slashes = 0;
        if (c == L'"')
            result += L'\\';
        result += c;
    }
    result.append(slashes * 2, L'\\');
    result += L'"';
    return result;
}
#endif
void run_process(const std::vector<std::string> &args, const fs::path &log) {
    require(!args.empty(), "Empty child command");
    cancellation_point();
    int exit_code = 1;
#ifdef _WIN32
    SECURITY_ATTRIBUTES security{sizeof(SECURITY_ATTRIBUTES), nullptr, TRUE};
    Handle output(CreateFileW(log.c_str(), GENERIC_WRITE, FILE_SHARE_READ, &security, CREATE_NEW,
                              FILE_ATTRIBUTE_NORMAL, nullptr));
    require(output.value != INVALID_HANDLE_VALUE, "Cannot create FFmpeg diagnostic log");
    Handle input(CreateFileW(L"NUL", GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE, &security,
                             OPEN_EXISTING, 0, nullptr));
    Handle job(CreateJobObjectW(nullptr, nullptr));
    require(job.value != nullptr, "Cannot create child process job");
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits{};
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    require(SetInformationJobObject(job.value, JobObjectExtendedLimitInformation, &limits,
                                    sizeof(limits)) != 0,
            "Cannot configure child process job");
    std::wstring command;
    for (const auto &arg : args) {
        if (!command.empty())
            command += L' ';
        command += quote_argument(wide(arg));
    }
    require(command.size() < 32767, "FFmpeg command exceeds the Windows command-line limit");
    STARTUPINFOW startup{};
    startup.cb = sizeof(startup);
    startup.dwFlags = STARTF_USESTDHANDLES;
    startup.hStdOutput = output.value;
    startup.hStdError = output.value;
    startup.hStdInput = input.value;
    PROCESS_INFORMATION process{};
    require(CreateProcessW(nullptr, command.data(), nullptr, nullptr, TRUE,
                           CREATE_NO_WINDOW | CREATE_SUSPENDED | CREATE_NEW_PROCESS_GROUP, nullptr,
                           nullptr, &startup, &process) != 0,
            "Cannot start FFmpeg; use --ffmpeg to select its executable");
    Handle process_handle(process.hProcess), thread(process.hThread);
    if (!AssignProcessToJobObject(job.value, process_handle.value)) {
        TerminateProcess(process_handle.value, 1);
        fail("Cannot contain FFmpeg child process");
    }
    require(ResumeThread(thread.value) != static_cast<DWORD>(-1), "Cannot start FFmpeg worker");
    while (WaitForSingleObject(process_handle.value, 100) == WAIT_TIMEOUT) {
        if (cancelled.load()) {
            TerminateJobObject(job.value, 130);
            WaitForSingleObject(process_handle.value, INFINITE);
            fail("Operation cancelled");
        }
    }
    DWORD status{};
    require(GetExitCodeProcess(process_handle.value, &status) != 0,
            "Cannot read FFmpeg exit status");
    exit_code = static_cast<int>(status);
#else
    auto child = fork();
    require(child >= 0, "Cannot fork FFmpeg");
    if (child == 0) {
        setpgid(0, 0);
        int fd = open(log.c_str(), O_WRONLY | O_CREAT | O_EXCL, 0600);
        if (fd < 0)
            _exit(127);
        dup2(fd, STDOUT_FILENO);
        dup2(fd, STDERR_FILENO);
        close(fd);
        int null_fd = open("/dev/null", O_RDONLY);
        if (null_fd >= 0) {
            dup2(null_fd, STDIN_FILENO);
            close(null_fd);
        }
        std::vector<char *> argv;
        for (const auto &arg : args)
            argv.push_back(const_cast<char *>(arg.c_str()));
        argv.push_back(nullptr);
        execvp(argv[0], argv.data());
        _exit(127);
    }
    int status{};
    while (waitpid(child, &status, WNOHANG) == 0) {
        if (cancelled.load()) {
            kill(-child, SIGTERM);
            waitpid(child, &status, 0);
            fail("Operation cancelled");
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }
    exit_code = WIFEXITED(status) ? WEXITSTATUS(status) : 1;
#endif
    if (exit_code != 0)
        fail("FFmpeg failed (exit " + std::to_string(exit_code) + "):\n" + log_tail(log));
    if (av_log_get_level() >= AV_LOG_INFO)
        std::cerr << log_tail(log);
}
void publish_file(const fs::path &source, const fs::path &destination, bool overwrite) {
#ifdef _WIN32
    DWORD flags = MOVEFILE_WRITE_THROUGH | (overwrite ? MOVEFILE_REPLACE_EXISTING : 0);
    require(MoveFileExW(source.c_str(), destination.c_str(), flags) != 0,
            "Cannot publish output; the existing destination was left intact");
#else
    if (overwrite) {
        std::error_code error;
        fs::rename(source, destination, error);
        require(!error, "Cannot replace output atomically");
    } else {
        require(link(source.c_str(), destination.c_str()) == 0,
                "Cannot publish output; destination already exists or is inaccessible");
        fs::remove(source);
    }
#endif
}
} // namespace smartcut
