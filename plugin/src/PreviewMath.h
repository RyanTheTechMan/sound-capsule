#pragma once

#include <juce_audio_basics/juce_audio_basics.h>

#include <cmath>

namespace soundcapsule::preview
{
inline double firstAudibleProportion(const juce::AudioBuffer<float>& audio,
                                     double sampleRate)
{
    if (audio.getNumSamples() <= 0 || audio.getNumChannels() <= 0 || sampleRate <= 0.0)
        return 0.0;

    auto peak = 0.0f;
    for (int channel = 0; channel < audio.getNumChannels(); ++channel)
        peak = juce::jmax(peak, audio.getMagnitude(channel, 0, audio.getNumSamples()));
    const auto threshold = juce::jmax(1.0e-5f, peak * 0.001f);
    for (int sample = 0; sample < audio.getNumSamples(); ++sample)
        for (int channel = 0; channel < audio.getNumChannels(); ++channel)
            if (std::abs(audio.getSample(channel, sample)) >= threshold)
            {
                const auto preroll = juce::roundToInt(sampleRate * 0.005);
                return static_cast<double>(juce::jmax(0, sample - preroll))
                     / static_cast<double>(audio.getNumSamples());
            }
    return 0.0;
}

inline double startProportion(double requested, bool startAtFirstAudio,
                              double firstAudible)
{
    const auto start = startAtFirstAudio && requested <= 0.0
                     ? firstAudible : requested;
    return juce::jlimit(0.0, 1.0, start);
}

inline float waveformVerticalZoom(float sharedPeak, bool normalize)
{
    return normalize ? 1.0f / juce::jmax(1.0e-5f, sharedPeak) : 1.0f;
}
}
