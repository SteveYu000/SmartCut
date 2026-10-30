cmake_minimum_required(VERSION 3.24)
project(SmartCutMatroskaResearch LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(FFMPEG_ROOT "" CACHE PATH "FFmpeg shared development SDK")
if(NOT FFMPEG_ROOT AND DEFINED ENV{FFMPEG_ROOT})
  set(FFMPEG_ROOT "$ENV{FFMPEG_ROOT}" CACHE PATH "FFmpeg SDK" FORCE)
endif()
if(NOT EXISTS "${FFMPEG_ROOT}/include/libavformat/avformat.h")
  message(FATAL_ERROR "Set -DFFMPEG_ROOT=<SDK with include, lib, bin>")
endif()
find_package(Python3 REQUIRED COMPONENTS Interpreter)
get_filename_component(REPO "${CMAKE_CURRENT_SOURCE_DIR}/../.." ABSOLUTE)
# An explicit, small research adapter is applied only to generated build files.
# The product's MP4 implementation and CLI are not modified by this experiment.
execute_process(COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/prepare_core.py"
  "${REPO}" "${CMAKE_CURRENT_BINARY_DIR}/core" COMMAND_ERROR_IS_FATAL ANY)
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
  "${REPO}/src/video.cpp" "${REPO}/src/av1.cpp" prepare_core.py)
add_executable(mkv-smartcut src/main.cpp src/mkv_input.cpp
  "${CMAKE_CURRENT_BINARY_DIR}/core/video.cpp" "${CMAKE_CURRENT_BINARY_DIR}/core/av1.cpp"
  "${REPO}/src/platform.cpp" "${REPO}/src/cli.cpp")
target_include_directories(mkv-smartcut PRIVATE src "${REPO}/src")
target_include_directories(mkv-smartcut SYSTEM PRIVATE "${FFMPEG_ROOT}/include")
foreach(component avformat avcodec avutil)
  unset(${component}_LIBRARY CACHE)
  find_library(${component}_LIBRARY NAMES ${component} PATHS "${FFMPEG_ROOT}/lib" NO_DEFAULT_PATH REQUIRED)
  target_link_libraries(mkv-smartcut PRIVATE ${${component}_LIBRARY})
endforeach()
if(MSVC)
  target_compile_options(mkv-smartcut PRIVATE /W4 /utf-8 /permissive- /EHsc)
  target_compile_definitions(mkv-smartcut PRIVATE NOMINMAX _CRT_SECURE_NO_WARNINGS)
endif()
if(WIN32)
  file(GLOB DLLS "${FFMPEG_ROOT}/bin/*.dll")
  add_custom_command(TARGET mkv-smartcut POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy_if_different ${DLLS} "$<TARGET_FILE_DIR:mkv-smartcut>"
    COMMAND_EXPAND_LISTS VERBATIM)
endif()
