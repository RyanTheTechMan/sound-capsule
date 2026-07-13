#include "PreviewMath.h"

#include <cmath>
#include <iostream>

namespace
{
bool closeTo(double actual, double expected, double tolerance = 1.0e-7)
{
    return std::abs(actual - expected) <= tolerance;
}

bool expect(bool condition, const char* message)
{
    if (!condition)
        std::cerr << message << '\n';
    return condition;
}
}

int main()
{
    auto passed = true;
    juce::AudioBuffer<float> audio(2, 1000);
    audio.clear();
    audio.setSample(0, 300, 0.5f);
    audio.setSample(1, 400, 0.25f);

    const auto firstAudible = soundcapsule::preview::firstAudibleProportion(audio, 1000.0);
    passed &= expect(closeTo(firstAudible, 0.295),
                     "first-audible detection must include a 5 ms preroll");
    passed &= expect(
        closeTo(soundcapsule::preview::startProportion(0.0, true, firstAudible), 0.295),
        "normal playback must begin at the detected first audio");
    passed &= expect(
        closeTo(soundcapsule::preview::startProportion(0.42, true, firstAudible), 0.42),
        "manual timeline seeking must remain exact");
    passed &= expect(
        closeTo(soundcapsule::preview::startProportion(0.0, false, firstAudible), 0.0),
        "loop restart must retain the opening silence");

    const auto zoom = soundcapsule::preview::waveformVerticalZoom(0.25f, true);
    passed &= expect(closeTo(zoom, 4.0),
                     "waveform normalization must use the shared peak");
    passed &= expect(closeTo(0.25 * zoom / (0.125 * zoom), 2.0),
                     "shared waveform normalization must preserve stereo balance");
    passed &= expect(closeTo(soundcapsule::preview::waveformVerticalZoom(0.25f, false), 1.0),
                     "disabled waveform normalization must not alter display scale");

    if (passed)
        std::cout << "Preview math tests passed\n";
    return passed ? 0 : 1;
}
