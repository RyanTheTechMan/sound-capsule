#pragma once

#include "ControllerMidiEndpoint.h"

#include <deque>

namespace soundcapsule::midi
{
class ControllerMidiEndpointManager final : private juce::Thread
{
public:
    using StatusCallback = std::function<void(EndpointStatus)>;
    using PortsCallback = std::function<void(std::vector<ExternalPort>)>;

    ControllerMidiEndpointManager();
    ~ControllerMidiEndpointManager() override;

    void activateExternalAsync(juce::String identifier, juce::String name,
                               StatusCallback callback);
    void refreshAsync(StatusCallback callback);
    void enumerateExternalPortsAsync(PortsCallback callback);
    void stopAsync();
    void setStatusListener(StatusCallback callback);

    OutputMode getMode() const;
    EndpointStatus getStatus() const;

private:
    void run() override;
    void post(std::function<void()> command);
    void setStatus(EndpointStatus newStatus);

    mutable juce::CriticalSection stateLock;
    juce::CriticalSection queueLock;
    juce::WaitableEvent wake;
    std::deque<std::function<void()>> commands;
    std::unique_ptr<IControllerMidiEndpointBackend> backend;
    OutputMode mode = OutputMode::notConfigured;
    EndpointStatus status;
    StatusCallback statusListener;
};
} // namespace soundcapsule::midi
