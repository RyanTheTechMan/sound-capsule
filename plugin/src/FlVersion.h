#pragma once

#include <juce_core/juce_core.h>

#include <optional>
#include <tuple>

namespace soundcapsule::flversion
{
struct Release
{
    int major = 0;
    int minor = 0;
};

inline std::optional<Release> compatibilityRelease(const juce::String& value)
{
    const auto version = value.trim();
    if (version.isEmpty() || version.startsWithChar('.') || version.endsWithChar('.')
        || version.contains(".."))
        return std::nullopt;

    for (auto character : version)
        if (character != '.' && !juce::CharacterFunctions::isDigit(character))
            return std::nullopt;

    const auto parts = juce::StringArray::fromTokens(version, ".", "");
    if (parts.size() < 2)
        return std::nullopt;
    return Release{parts[0].getIntValue(), parts[1].getIntValue()};
}

inline bool sourceIsNewer(const juce::String& source,
                          const juce::String& destination)
{
    const auto sourceRelease = compatibilityRelease(source);
    const auto destinationRelease = compatibilityRelease(destination);
    if (!sourceRelease || !destinationRelease)
        return false;
    return std::tie(sourceRelease->major, sourceRelease->minor)
         > std::tie(destinationRelease->major, destinationRelease->minor);
}

inline juce::String displayRelease(const juce::String& value)
{
    if (const auto release = compatibilityRelease(value))
        return juce::String(release->major) + "." + juce::String(release->minor);
    return {};
}
}
