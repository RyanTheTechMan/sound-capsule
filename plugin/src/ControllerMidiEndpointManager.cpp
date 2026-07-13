#include "ControllerMidiEndpointManager.h"

namespace soundcapsule::midi
{
ControllerMidiEndpointManager::ControllerMidiEndpointManager()
    : juce::Thread("Sound Capsule MIDI endpoint")
{
    startThread();
}

ControllerMidiEndpointManager::~ControllerMidiEndpointManager()
{
    if (isThreadRunning())
    {
        auto stopped = std::make_shared<juce::WaitableEvent>();
        post([this, stopped] {
            if (backend != nullptr)
                backend->stop();
            backend.reset();
            stopped->signal();
        });
        stopped->wait(5000);
    }
    signalThreadShouldExit();
    wake.signal();
    stopThread(5000);
}

void ControllerMidiEndpointManager::post(std::function<void()> command)
{
    {
        const juce::ScopedLock lock(queueLock);
        commands.push_back(std::move(command));
    }
    wake.signal();
}

void ControllerMidiEndpointManager::run()
{
    while (!threadShouldExit())
    {
        wake.wait(1000);
        for (;;)
        {
            std::function<void()> command;
            {
                const juce::ScopedLock lock(queueLock);
                if (commands.empty())
                    break;
                command = std::move(commands.front());
                commands.pop_front();
            }
            if (command)
                command();
        }

        // JUCE does not expose removal events for classic external outputs.
        // A low-frequency enumeration check is limited to that backend.
        if (getMode() == OutputMode::externalMidiPort && backend != nullptr)
            setStatus(backend->refresh());

    }
}

void ControllerMidiEndpointManager::activateExternalAsync(
    juce::String identifier, juce::String name, StatusCallback callback)
{
    post([this, identifier = std::move(identifier), name = std::move(name),
          callback = std::move(callback)]() mutable {
        if (backend != nullptr)
            backend->stop();
        auto external = std::make_unique<ExternalControllerMidiEndpointBackend>(
            std::move(identifier), std::move(name));
        external->setStatusCallback([this](EndpointStatus updated) { setStatus(std::move(updated)); });
        {
            const juce::ScopedLock lock(stateLock);
            mode = OutputMode::externalMidiPort;
        }
        backend = std::move(external);
        const auto started = backend->start();
        setStatus(started);
        if (callback) callback(started);
    });
}

void ControllerMidiEndpointManager::refreshAsync(StatusCallback callback)
{
    post([this, callback = std::move(callback)] {
        EndpointStatus refreshed;
        if (backend != nullptr)
            refreshed = backend->refresh();
        else
        {
            refreshed.state = EndpointState::stopped;
            refreshed.userMessage = "MIDI integration is not configured";
        }
        setStatus(refreshed);
        if (callback) callback(refreshed);
    });
}

void ControllerMidiEndpointManager::enumerateExternalPortsAsync(PortsCallback callback)
{
    post([callback = std::move(callback)] {
        auto ports = ExternalControllerMidiEndpointBackend::enumeratePorts();
        if (callback) callback(std::move(ports));
    });
}

void ControllerMidiEndpointManager::stopAsync()
{
    post([this] {
        if (backend != nullptr)
            backend->stop();
        backend.reset();
        EndpointStatus stopped;
        stopped.state = EndpointState::stopped;
        stopped.userMessage = "MIDI integration is not configured";
        {
            const juce::ScopedLock lock(stateLock);
            mode = OutputMode::notConfigured;
        }
        setStatus(stopped);
    });
}

void ControllerMidiEndpointManager::setStatusListener(StatusCallback callback)
{
    const juce::ScopedLock lock(stateLock);
    statusListener = std::move(callback);
}

OutputMode ControllerMidiEndpointManager::getMode() const
{
    const juce::ScopedLock lock(stateLock);
    return mode;
}

EndpointStatus ControllerMidiEndpointManager::getStatus() const
{
    const juce::ScopedLock lock(stateLock);
    return status;
}

void ControllerMidiEndpointManager::setStatus(EndpointStatus newStatus)
{
    StatusCallback listener;
    EndpointStatus published;
    {
        const juce::ScopedLock lock(stateLock);
        status = std::move(newStatus);
        published = status;
        listener = statusListener;
    }
    if (listener)
        listener(std::move(published));
}

} // namespace soundcapsule::midi
