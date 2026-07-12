cmake_minimum_required(VERSION 3.22)

option(SOUNDCAPSULE_UNIVERSAL_MACOS "Build arm64+x86_64 on macOS" ON)
option(SOUNDCAPSULE_BUILD_PLUGIN "Build the standalone library app and optional JUCE VST3" OFF)
if(APPLE AND SOUNDCAPSULE_UNIVERSAL_MACOS)
    set(CMAKE_OSX_ARCHITECTURES "arm64;x86_64" CACHE STRING "macOS architectures" FORCE)
endif()

project(FLSoundCapsule VERSION 0.1.0 LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_OSX_DEPLOYMENT_TARGET "11.0" CACHE STRING "Minimum macOS version")

if(SOUNDCAPSULE_BUILD_PLUGIN)
    include(FetchContent)
    FetchContent_Declare(
        JUCE
        GIT_REPOSITORY https://github.com/juce-framework/JUCE.git
        GIT_TAG 8.0.9
        GIT_SHALLOW TRUE
    )
    FetchContent_MakeAvailable(JUCE)
    add_subdirectory(plugin)
endif()
