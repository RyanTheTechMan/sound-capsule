#pragma once

#include <juce_audio_utils/juce_audio_utils.h>

#include <functional>
#include <memory>
#include <vector>

namespace soundcapsule::midi
{
inline constexpr auto canonicalEndpointName = "Sound Capsule MIDI";
inline constexpr auto legacyEndpointName = "Sound Capsule Control";
inline constexpr auto loopMidiDownloadUrl = "https://www.tobias-erichsen.de/software/loopmidi.html";

enum class OutputMode
{
    notConfigured,
    externalMidiPort
};

enum class EndpointState
{
    stopped,
    connected,
    selectedPortUnavailable,
    connectionFailed
};

struct EndpointStatus
{
    EndpointState state = EndpointState::stopped;
    juce::String displayName;
    juce::String userMessage;
    juce::String diagnostic;

    bool isUsable() const noexcept { return state == EndpointState::connected; }
};

struct ExternalPort
{
    juce::String identifier;
    juce::String name;
};

struct LoopMidiInstallation
{
    bool installed = false;
    juce::File executable;
    juce::String diagnostic;

    bool canLaunch() const { return executable.existsAsFile(); }
};

class IControllerMidiEndpointBackend
{
public:
    using StatusCallback = std::function<void(EndpointStatus)>;

    virtual ~IControllerMidiEndpointBackend() = default;
    virtual EndpointStatus start() = 0;
    virtual void stop() = 0;
    virtual EndpointStatus getStatus() const = 0;
    virtual EndpointStatus refresh() = 0;
    virtual void setStatusCallback(StatusCallback callback) = 0;
};

class ExternalControllerMidiEndpointBackend final : public IControllerMidiEndpointBackend
{
public:
    ExternalControllerMidiEndpointBackend(juce::String identifier, juce::String name);
    ~ExternalControllerMidiEndpointBackend() override;

    EndpointStatus start() override;
    void stop() override;
    EndpointStatus getStatus() const override;
    EndpointStatus refresh() override;
    void setStatusCallback(StatusCallback callback) override;

    static std::vector<ExternalPort> enumeratePorts();

private:
    EndpointStatus update(bool openIfFound);
    void publish();

    juce::String selectedIdentifier;
    juce::String selectedName;
    std::unique_ptr<juce::MidiOutput> output;
    EndpointStatus status;
    StatusCallback statusCallback;
};

OutputMode outputModeFromString(const juce::String& value);
juce::String outputModeToString(OutputMode value);
juce::String endpointStatusText(OutputMode mode, const EndpointStatus& status);
LoopMidiInstallation detectLoopMidiInstallation();

} // namespace soundcapsule::midi
