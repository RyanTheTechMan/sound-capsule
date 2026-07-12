#include "CapsulePreviewSource.h"

namespace
{
constexpr juce::int64 maximumPreviewBytes = 512LL * 1024LL * 1024LL;

juce::String previewMemberName(juce::ZipFile& archive)
{
    const auto manifestIndex = archive.getIndexOfFileName("manifest.json");
    if (manifestIndex < 0)
        return {};
    std::unique_ptr<juce::InputStream> manifestStream(
        archive.createStreamForEntry(manifestIndex));
    if (manifestStream == nullptr)
        return {};
    const auto manifest = juce::JSON::parse(manifestStream->readEntireStreamAsString());
    return manifest.getProperty("preview_path", "preview.wav").toString();
}
}

std::unique_ptr<juce::InputStream> createCapsulePreviewStream(const juce::File& capsule)
{
    if (!capsule.existsAsFile())
        return {};
    juce::ZipFile archive(capsule);
    const auto memberName = previewMemberName(archive);
    const auto memberIndex = archive.getIndexOfFileName(memberName);
    const auto* entry = archive.getEntry(memberIndex);
    if (entry == nullptr || entry->uncompressedSize <= 0
        || entry->uncompressedSize > maximumPreviewBytes)
        return {};
    std::unique_ptr<juce::InputStream> source(archive.createStreamForEntry(memberIndex));
    if (source == nullptr)
        return {};
    juce::MemoryBlock data;
    source->readIntoMemoryBlock(
        data, static_cast<juce::pointer_sized_int>(maximumPreviewBytes));
    if (static_cast<juce::int64>(data.getSize()) != entry->uncompressedSize)
        return {};
    return std::make_unique<juce::MemoryInputStream>(std::move(data));
}

juce::InputStream* CapsulePreviewInputSource::createInputStream()
{
    return createCapsulePreviewStream(capsule).release();
}

juce::InputStream* CapsulePreviewInputSource::createInputStreamFor(const juce::String&)
{
    return createInputStream();
}

juce::int64 CapsulePreviewInputSource::hashCode() const
{
    return capsule.getFullPathName().hashCode64();
}
