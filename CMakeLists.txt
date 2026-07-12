cmake_minimum_required(VERSION 3.22)

option(SOUNDCAPSULE_UNIVERSAL_MACOS "Build arm64+x86_64 on macOS" ON)
option(SOUNDCAPSULE_BUILD_PLUGIN "Build the standalone library app and optional JUCE VST3" OFF)
set(SOUNDCAPSULE_RELEASE_REPOSITORY "" CACHE STRING
    "GitHub owner/repository used by the in-app release check")
if(APPLE AND SOUNDCAPSULE_UNIVERSAL_MACOS)
    set(CMAKE_OSX_ARCHITECTURES "arm64;x86_64" CACHE STRING "macOS architectures" FORCE)
endif()

file(READ "${CMAKE_CURRENT_SOURCE_DIR}/helper/soundcapsule/__init__.py" SOUNDCAPSULE_INIT)
string(REGEX MATCH
    "__version__[ \t]*=[ \t]*\"([0-9]+\\.[0-9]+\\.[0-9]+)\""
    SOUNDCAPSULE_VERSION_MATCH "${SOUNDCAPSULE_INIT}")
if(NOT CMAKE_MATCH_1)
    message(FATAL_ERROR "Could not read Sound Capsule version from helper/soundcapsule/__init__.py")
endif()
set(SOUNDCAPSULE_VERSION "${CMAKE_MATCH_1}")

if(SOUNDCAPSULE_RELEASE_REPOSITORY
   AND NOT SOUNDCAPSULE_RELEASE_REPOSITORY MATCHES "^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    message(FATAL_ERROR "SOUNDCAPSULE_RELEASE_REPOSITORY must look like owner/repository")
endif()

project(FLSoundCapsule VERSION "${SOUNDCAPSULE_VERSION}" LANGUAGES C CXX)

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
