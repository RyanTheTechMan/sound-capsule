#include "ControllerMidiEndpoint.h"

#if JUCE_WINDOWS
 #include <windows.h>
#endif

namespace
{
#if JUCE_WINDOWS
bool uninstallRegistryContainsLoopMidi(HKEY root, REGSAM registryView)
{
    HKEY uninstallKey = nullptr;
    constexpr auto path = L"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall";
    if (::RegOpenKeyExW(root, path, 0, KEY_READ | registryView, &uninstallKey)
        != ERROR_SUCCESS)
        return false;

    bool found = false;
    wchar_t subkeyName[256]{};
    for (DWORD index = 0; !found; ++index)
    {
        DWORD subkeyLength = static_cast<DWORD>(std::size(subkeyName));
        const auto enumerated = ::RegEnumKeyExW(uninstallKey, index, subkeyName,
                                                &subkeyLength, nullptr, nullptr, nullptr, nullptr);
        if (enumerated == ERROR_NO_MORE_ITEMS)
            break;
        if (enumerated != ERROR_SUCCESS)
            continue;

        HKEY productKey = nullptr;
        if (::RegOpenKeyExW(uninstallKey, subkeyName, 0, KEY_READ | registryView, &productKey)
            != ERROR_SUCCESS)
            continue;
        wchar_t displayName[256]{};
        DWORD displayNameBytes = sizeof(displayName);
        if (::RegGetValueW(productKey, nullptr, L"DisplayName", RRF_RT_REG_SZ,
                           nullptr, displayName, &displayNameBytes) == ERROR_SUCCESS)
            found = juce::String(displayName).containsIgnoreCase("loopMIDI");
        ::RegCloseKey(productKey);
    }
    ::RegCloseKey(uninstallKey);
    return found;
}
#endif
}

namespace soundcapsule::midi
{
ExternalControllerMidiEndpointBackend::ExternalControllerMidiEndpointBackend(
    juce::String identifier, juce::String name)
    : selectedIdentifier(std::move(identifier)), selectedName(std::move(name))
{
    status.displayName = selectedName;
}

ExternalControllerMidiEndpointBackend::~ExternalControllerMidiEndpointBackend()
{
    stop();
}

std::vector<ExternalPort> ExternalControllerMidiEndpointBackend::enumeratePorts()
{
    std::vector<ExternalPort> result;
    for (const auto& device : juce::MidiOutput::getAvailableDevices())
        result.push_back({device.identifier, device.name});
    return result;
}

EndpointStatus ExternalControllerMidiEndpointBackend::start()
{
    return update(true);
}

void ExternalControllerMidiEndpointBackend::stop()
{
    output.reset();
    status.state = EndpointState::stopped;
    status.userMessage = "External MIDI output is stopped";
    publish();
}

EndpointStatus ExternalControllerMidiEndpointBackend::getStatus() const
{
    return status;
}

EndpointStatus ExternalControllerMidiEndpointBackend::refresh()
{
    return update(true);
}

void ExternalControllerMidiEndpointBackend::setStatusCallback(StatusCallback callback)
{
    statusCallback = std::move(callback);
}

EndpointStatus ExternalControllerMidiEndpointBackend::update(bool openIfFound)
{
    const auto devices = juce::MidiOutput::getAvailableDevices();
    const juce::MidiDeviceInfo* match = nullptr;
    for (const auto& device : devices)
        if (selectedIdentifier.isNotEmpty() && device.identifier == selectedIdentifier)
        {
            match = &device;
            break;
        }
    if (match == nullptr)
        for (const auto& device : devices)
            if (selectedName.isNotEmpty() && device.name.equalsIgnoreCase(selectedName))
            {
                match = &device;
                break;
            }

    if (match == nullptr)
    {
        output.reset();
        status.state = EndpointState::selectedPortUnavailable;
        status.displayName = selectedName;
        status.userMessage = selectedName.isNotEmpty()
                               ? "Selected MIDI port is unavailable"
                               : "Select a MIDI output port";
        status.diagnostic = "No enumerated JUCE MIDI output matched the saved identifier or name";
        publish();
        return status;
    }

    selectedIdentifier = match->identifier;
    selectedName = match->name;
    status.displayName = selectedName;
    if (openIfFound && output == nullptr)
        output = juce::MidiOutput::openDevice(match->identifier);
    if (output == nullptr)
    {
        status.state = EndpointState::connectionFailed;
        status.userMessage = "The selected MIDI output could not be opened";
        status.diagnostic = "JUCE MidiOutput::openDevice returned null for " + match->identifier;
    }
    else
    {
        status.state = EndpointState::connected;
        status.userMessage = "External MIDI port: " + selectedName;
        status.diagnostic = "External MIDI output opened";
    }
    publish();
    return status;
}

void ExternalControllerMidiEndpointBackend::publish()
{
    if (statusCallback)
        statusCallback(status);
}

OutputMode outputModeFromString(const juce::String& value)
{
    if (value == "external_midi_port")
        return OutputMode::externalMidiPort;
    return OutputMode::notConfigured;
}

juce::String outputModeToString(OutputMode value)
{
    switch (value)
    {
        case OutputMode::externalMidiPort: return "external_midi_port";
        case OutputMode::notConfigured: break;
    }
    return "not_configured";
}

juce::String endpointStatusText(OutputMode mode, const EndpointStatus& endpoint)
{
    if (mode == OutputMode::notConfigured)
        return "MIDI integration is not configured";
    if (endpoint.userMessage.isNotEmpty())
        return endpoint.userMessage;
    return "External MIDI port: Not connected";
}

LoopMidiInstallation detectLoopMidiInstallation()
{
    LoopMidiInstallation result;
#if JUCE_WINDOWS
    juce::Array<juce::File> candidates;
    const auto addCandidates = [&candidates](const juce::String& programFiles) {
        if (programFiles.isEmpty())
            return;
        const auto root = juce::File(programFiles);
        candidates.add(root.getChildFile("Tobias Erichsen")
                           .getChildFile("loopMIDI").getChildFile("loopMIDI.exe"));
        candidates.add(root.getChildFile("loopMIDI").getChildFile("loopMIDI.exe"));
    };
    addCandidates(juce::SystemStats::getEnvironmentVariable("ProgramFiles(x86)", ""));
    addCandidates(juce::SystemStats::getEnvironmentVariable("ProgramFiles", ""));
    addCandidates(juce::SystemStats::getEnvironmentVariable("LOCALAPPDATA", ""));

    for (const auto& candidate : candidates)
        if (candidate.existsAsFile())
        {
            result.installed = true;
            result.executable = candidate;
            result.diagnostic = "Found loopMIDI executable at " + candidate.getFullPathName();
            return result;
        }

    result.installed = uninstallRegistryContainsLoopMidi(HKEY_LOCAL_MACHINE, KEY_WOW64_32KEY)
                    || uninstallRegistryContainsLoopMidi(HKEY_LOCAL_MACHINE, KEY_WOW64_64KEY)
                    || uninstallRegistryContainsLoopMidi(HKEY_CURRENT_USER, KEY_WOW64_32KEY)
                    || uninstallRegistryContainsLoopMidi(HKEY_CURRENT_USER, KEY_WOW64_64KEY);
    result.diagnostic = result.installed
        ? "loopMIDI is registered as installed, but its executable path was not found"
        : "No loopMIDI executable or uninstall registration was found";
#else
    result.diagnostic = "loopMIDI installation detection is Windows-only";
#endif
    return result;
}

} // namespace soundcapsule::midi
