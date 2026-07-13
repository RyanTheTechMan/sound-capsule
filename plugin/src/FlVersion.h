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

inline std::optional<int> hostMajorRelease(const juce::String& value)
{
    juce::String digits;
    auto position = value.getCharPointer();
    while (!position.isEmpty())
    {
        const auto character = position.getAndAdvance();
        if (juce::CharacterFunctions::isDigit(character))
            digits += character;
        else if (digits.isNotEmpty())
            break;
    }
    auto major = digits.getIntValue();
    if (major >= 2000 && major < 2100)
        major -= 2000;
    return major > 0 ? std::optional<int>(major) : std::nullopt;
}

inline bool sourceIsNewer(const juce::String& source,
                          const juce::String& destination,
                          const juce::String& hostName)
{
    if (compatibilityRelease(destination))
        return sourceIsNewer(source, destination);
    const auto sourceRelease = compatibilityRelease(source);
    const auto hostMajor = hostMajorRelease(hostName);
    return sourceRelease && hostMajor && sourceRelease->major > *hostMajor;
}

inline juce::String displayRelease(const juce::String& value)
{
    if (const auto release = compatibilityRelease(value))
        return juce::String(release->major) + "." + juce::String(release->minor);
    return {};
}

inline juce::String displayDestinationRelease(const juce::String& destination,
                                              const juce::String& hostName)
{
    if (const auto exact = displayRelease(destination); exact.isNotEmpty())
        return exact;
    if (const auto major = hostMajorRelease(hostName))
        return juce::String(*major);
    return {};
}
}
