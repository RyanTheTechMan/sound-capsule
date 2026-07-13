#include "FlVersion.h"
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

    using soundcapsule::flversion::displayRelease;
    using soundcapsule::flversion::sourceIsNewer;
    passed &= expect(sourceIsNewer("26.4.0.100", "26.2.9.9999"),
                     "a newer FL minor release must warn");
    passed &= expect(!sourceIsNewer("26.2.9.9999", "26.4.0.100"),
                     "an older FL release must not warn in a newer destination");
    passed &= expect(!sourceIsNewer("26.1.0.5530", "26.1.0.5294"),
                     "platform build numbers in the same release must be equivalent");
    passed &= expect(!sourceIsNewer("26.1.0.5294", "26.1.0.5530"),
                     "platform build equivalence must work in both directions");
    passed &= expect(sourceIsNewer("27.0", "26.9"),
                     "a newer FL major release must warn");
    passed &= expect(!sourceIsNewer("26.2.1", "26.2.0"),
                     "patch releases must share one compatibility level");
    passed &= expect(!sourceIsNewer("unknown", "26.2")
                     && !sourceIsNewer("26..4", "26.2")
                     && !sourceIsNewer("26.4", ""),
                     "missing or malformed versions must not claim a direction");
    passed &= expect(displayRelease("26.4.3.1234") == "26.4",
                     "displayed compatibility versions must use major.minor");
    passed &= expect(sourceIsNewer("26.1.0.5530", "", "FL Studio 2025"),
                     "a newer source major must warn when only the connected host is known");
    passed &= expect(!sourceIsNewer("26.4.0.100", "", "FL Studio 2026"),
                     "a matching host major must not invent an unknown minor mismatch");
    passed &= expect(!sourceIsNewer("26.1", "26.4", "FL Studio 2025"),
                     "an exact destination release must take precedence over the host fallback");
    passed &= expect(
        soundcapsule::flversion::displayDestinationRelease("", "FL Studio 2025") == "25",
        "the fallback warning must display the connected FL major");

    if (passed)
        std::cout << "Preview and FL version tests passed\n";
    return passed ? 0 : 1;
}
