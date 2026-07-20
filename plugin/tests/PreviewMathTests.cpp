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

    const auto exactNote = soundcapsule::preview::midiNoteTiming(0.25, 0.25, 1.0, 0.5);
    passed &= expect(closeTo(exactNote.displayStart, 0.25)
                         && closeTo(exactNote.displayEnd, 0.5)
                         && closeTo(exactNote.audioStart, 0.125)
                         && closeTo(exactNote.audioEnd, 0.25),
                     "exact MIDI timing must map the trimmed timeline into audio progress");
    const auto legacyNote = soundcapsule::preview::midiNoteTiming(0.25, 0.125, 0.5, 0.5);
    passed &= expect(closeTo(legacyNote.displayStart, 0.5)
                         && closeTo(legacyNote.displayEnd, 0.75)
                         && closeTo(legacyNote.audioStart, 0.25)
                         && closeTo(legacyNote.audioEnd, 0.375),
                     "legacy MIDI timing must account for its shorter stored timeline");
    const auto openingRest = soundcapsule::preview::midiNoteTiming(0.2, 0.1, 1.0, 0.8);
    passed &= expect(!soundcapsule::preview::isMidiNoteActive(0.159, openingRest)
                         && soundcapsule::preview::isMidiNoteActive(0.161, openingRest)
                         && !soundcapsule::preview::isMidiNoteActive(0.241, openingRest),
                     "active-note boundaries must preserve opening rests and exclude note end");
    const auto attackAge = soundcapsule::preview::midiAttackAgeSeconds(
        0.18, openingRest, 4.0);
    passed &= expect(closeTo(attackAge, 0.08),
                     "attack age must be measured in preview seconds");
    passed &= expect(closeTo(soundcapsule::preview::midiAttackEnvelope(0.0, 0.28), 1.0)
                         && soundcapsule::preview::midiAttackEnvelope(0.14, 0.28) > 0.0f
                         && closeTo(soundcapsule::preview::midiAttackEnvelope(0.28, 0.28), 0.0),
                     "attack envelope must start full, decay, and finish cleanly");
    const auto overlapping = soundcapsule::preview::midiNoteTiming(0.22, 0.2, 1.0, 0.8);
    passed &= expect(soundcapsule::preview::isMidiNoteActive(0.2, openingRest)
                         && soundcapsule::preview::isMidiNoteActive(0.2, overlapping),
                     "overlapping notes must be independently active");

    passed &= expect(closeTo(soundcapsule::preview::midiPlayedDisplayEnd(
                                 0.1, exactNote, 0.5), exactNote.displayStart)
                         && closeTo(soundcapsule::preview::midiPlayedDisplayEnd(
                                        0.1875, exactNote, 0.5), 0.375)
                         && closeTo(soundcapsule::preview::midiPlayedDisplayEnd(
                                        0.4, exactNote, 0.5), exactNote.displayEnd),
                     "MIDI glow must stop at the playhead while a note is playing");
    passed &= expect(closeTo(soundcapsule::preview::midiPlayedDisplayEnd(
                                 0.3, legacyNote, 0.5), 0.6)
                         && closeTo(soundcapsule::preview::midiPlayedDisplayEnd(
                                        0.3, legacyNote, 0.0), legacyNote.displayStart),
                     "played MIDI clipping must support legacy timing and zero playback spans");

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
