#pragma once

#include <juce_audio_utils/juce_audio_utils.h>

std::unique_ptr<juce::InputStream> createCapsulePreviewStream(const juce::File& capsule);

class CapsulePreviewInputSource final : public juce::InputSource
{
public:
    explicit CapsulePreviewInputSource(juce::File capsuleFile) : capsule(std::move(capsuleFile)) {}

    juce::InputStream* createInputStream() override;
    juce::InputStream* createInputStreamFor(const juce::String&) override;
    juce::int64 hashCode() const override;

private:
    juce::File capsule;
};
