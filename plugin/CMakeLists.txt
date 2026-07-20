juce_add_plugin(SoundCapsule
    COMPANY_NAME "Sound Capsule"
    BUNDLE_ID "com.soundcapsule.fl"
    ICON_BIG "${CMAKE_CURRENT_SOURCE_DIR}/resources/app-icon.png"
    PLUGIN_MANUFACTURER_CODE SdCp
    PLUGIN_CODE FlCp
    FORMATS VST3 Standalone
    PRODUCT_NAME "Sound Capsule"
    DESCRIPTION "FL Studio channel, MIDI, and preview capsule library"
    IS_SYNTH FALSE
    NEEDS_MIDI_INPUT FALSE
    NEEDS_MIDI_OUTPUT FALSE
    IS_MIDI_EFFECT FALSE
    EDITOR_WANTS_KEYBOARD_FOCUS TRUE
    COPY_PLUGIN_AFTER_BUILD FALSE
    VST3_CATEGORIES Fx Tools
)

juce_add_binary_data(SoundCapsuleAssets
    SOURCES resources/logo-transparent.png
)

target_sources(SoundCapsule PRIVATE
    src/CapsulePreviewSource.cpp
    src/CapsulePreviewSource.h
    src/HelperClient.cpp
    src/HelperClient.h
    src/FlVersion.h
    src/PluginEditor.cpp
    src/PluginEditor.h
    src/PluginProcessor.cpp
    src/PluginProcessor.h
)

target_compile_definitions(SoundCapsule PUBLIC
    JUCE_WEB_BROWSER=0
    JUCE_USE_CURL=0
    JUCE_VST3_CAN_REPLACE_VST2=0
)

target_compile_definitions(SoundCapsule PRIVATE
    SOUNDCAPSULE_RELEASE_REPOSITORY="${SOUNDCAPSULE_RELEASE_REPOSITORY}"
    SOUNDCAPSULE_APPLE_TEAM_ID="${SOUNDCAPSULE_APPLE_TEAM_ID}"
)

target_link_libraries(SoundCapsule PRIVATE
    SoundCapsuleAssets
    juce::juce_audio_utils
    juce::juce_cryptography
    juce::juce_gui_extra
    PUBLIC
    juce::juce_recommended_config_flags
    juce::juce_recommended_lto_flags
    juce::juce_recommended_warning_flags
)

juce_add_console_app(SoundCapsulePreviewTests
    PRODUCT_NAME "Sound Capsule Preview Tests"
)
target_sources(SoundCapsulePreviewTests PRIVATE
    tests/PreviewMathTests.cpp
    src/FlVersion.h
    src/PreviewMath.h
)
target_include_directories(SoundCapsulePreviewTests PRIVATE src)
target_link_libraries(SoundCapsulePreviewTests PRIVATE
    juce::juce_audio_basics
    juce::juce_recommended_config_flags
    juce::juce_recommended_warning_flags
)
if(BUILD_TESTING)
    add_test(NAME SoundCapsulePreviewTests COMMAND SoundCapsulePreviewTests)
endif()

if(APPLE)
    add_custom_command(TARGET SoundCapsule_VST3 POST_BUILD
        COMMAND codesign --force --deep --sign - "$<TARGET_BUNDLE_DIR:SoundCapsule_VST3>"
        COMMENT "Applying local ad-hoc signature after VST3 manifest generation"
        VERBATIM
    )
    add_custom_command(TARGET SoundCapsule_Standalone POST_BUILD
        COMMAND codesign --force --deep --sign - "$<TARGET_BUNDLE_DIR:SoundCapsule_Standalone>"
        COMMENT "Applying local ad-hoc signature to standalone application"
        VERBATIM
    )
endif()
