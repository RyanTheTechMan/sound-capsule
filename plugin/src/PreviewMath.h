#pragma once

#include <juce_audio_basics/juce_audio_basics.h>

#include <cmath>

namespace soundcapsule::preview
{
struct MidiNoteTiming
{
    double displayStart = 0.0;
    double displayEnd = 0.0;
    double audioStart = 0.0;
    double audioEnd = 0.0;
};

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

inline MidiNoteTiming midiNoteTiming(double noteStart, double noteLength,
                                     double midiTimelineEnd, double midiPlaybackEnd)
{
    const auto timelineEnd = juce::jmax(0.000001, midiTimelineEnd);
    const auto playbackEnd = juce::jlimit(0.000001, 1.0, midiPlaybackEnd);
    const auto displayStart = juce::jlimit(0.0, 1.0, noteStart / timelineEnd);
    const auto displayEnd = juce::jlimit(
        displayStart, 1.0, (noteStart + juce::jmax(0.0, noteLength)) / timelineEnd);
    return {
        displayStart,
        displayEnd,
        displayStart * playbackEnd,
        displayEnd * playbackEnd,
    };
}

inline bool isMidiNoteActive(double previewProgress, const MidiNoteTiming& timing)
{
    return previewProgress >= timing.audioStart && previewProgress < timing.audioEnd;
}

inline double midiAttackAgeSeconds(double previewProgress, const MidiNoteTiming& timing,
                                   double previewDurationSeconds)
{
    if (previewDurationSeconds <= 0.0 || previewProgress < timing.audioStart)
        return -1.0;
    return (previewProgress - timing.audioStart) * previewDurationSeconds;
}

inline float midiAttackEnvelope(double ageSeconds, double durationSeconds)
{
    if (ageSeconds < 0.0 || durationSeconds <= 0.0 || ageSeconds >= durationSeconds)
        return 0.0f;
    const auto remaining = 1.0 - ageSeconds / durationSeconds;
    // Smoothly settles without a hard visual corner at the end of the pulse.
    return static_cast<float>(remaining * remaining * (3.0 - 2.0 * remaining));
}

inline double midiPlayedDisplayEnd(double previewProgress, const MidiNoteTiming& timing,
                                   double midiPlaybackEnd)
{
    if (midiPlaybackEnd <= 0.0)
        return timing.displayStart;
    const auto playhead = juce::jlimit(0.0, 1.0, previewProgress / midiPlaybackEnd);
    return juce::jlimit(timing.displayStart, timing.displayEnd, playhead);
}

}
