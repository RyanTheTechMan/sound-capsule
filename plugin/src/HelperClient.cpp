#include "HelperClient.h"

#include <array>
#include <cstring>

namespace
{
constexpr int helperProtocolVersion = 2;

juce::File helperTokenFile()
{
    const auto configuredHome = juce::SystemStats::getEnvironmentVariable(
        "SOUNDCAPSULE_HOME", "").trim();
    if (configuredHome.isNotEmpty())
        return juce::File(configuredHome).getChildFile("helper-token");

   #if JUCE_WINDOWS
    const auto localAppData = juce::SystemStats::getEnvironmentVariable(
        "LOCALAPPDATA", "");
    return juce::File(localAppData).getChildFile("SoundCapsule/helper-token");
   #else
    return juce::File::getSpecialLocation(juce::File::userHomeDirectory)
        .getChildFile("Library/Application Support/SoundCapsule/helper-token");
   #endif
}

juce::String loadHelperToken()
{
    const auto file = helperTokenFile();
    if (!file.existsAsFile())
        throw std::runtime_error(
            "Sound Capsule helper authentication is not initialized; run Setup again");
    const auto token = file.loadFileAsString().trim();
    if (token.length() < 32 || token.length() > 512)
        throw std::runtime_error(
            "Sound Capsule helper authentication is invalid; run Setup again");
    return token;
}
}

juce::var HelperClient::request(const juce::String& command, const juce::var& arguments,
                                const std::atomic<bool>* cancelled, int timeoutMs) const
{
    juce::StreamingSocket socket;
    const auto connectDeadline = juce::Time::getMillisecondCounterHiRes()
                               + static_cast<double>(juce::jmin(timeoutMs, 5000));
    while (!socket.connect(host, port, 250))
    {
        if (cancelled != nullptr && cancelled->load())
            throw std::runtime_error("Request cancelled");
        if (juce::Time::getMillisecondCounterHiRes() >= connectDeadline)
            throw std::runtime_error("Sound Capsule helper is not running");
        juce::Thread::sleep(50);
    }

    auto requestObject = std::make_unique<juce::DynamicObject>();
    requestObject->setProperty("protocol_version", helperProtocolVersion);
    requestObject->setProperty("client_version", JucePlugin_VersionString);
    requestObject->setProperty("auth_token", loadHelperToken());
    requestObject->setProperty("command", command);
    requestObject->setProperty("args", arguments);
    const auto requestText = juce::JSON::toString(juce::var(requestObject.release()), true) + "\n";
    const auto bytes = requestText.toRawUTF8();
    const auto length = static_cast<int>(std::strlen(bytes));
    int sent = 0;
    while (sent < length)
    {
        if (cancelled != nullptr && cancelled->load())
            throw std::runtime_error("Request cancelled");
        const auto ready = socket.waitUntilReady(false, 2000);
        if (ready <= 0)
            throw std::runtime_error("Could not send request to Sound Capsule helper");
        const auto written = socket.write(bytes + sent, length - sent);
        if (written <= 0)
            throw std::runtime_error("Could not send request to Sound Capsule helper");
        sent += written;
    }

    juce::MemoryOutputStream response;
    const auto deadline = juce::Time::getMillisecondCounterHiRes() + timeoutMs;
    bool terminated = false;
    while (response.getDataSize() < 2 * 1024 * 1024)
    {
        if (cancelled != nullptr && cancelled->load())
            throw std::runtime_error("Request cancelled");
        const auto remaining = static_cast<int>(deadline - juce::Time::getMillisecondCounterHiRes());
        if (remaining <= 0)
            throw std::runtime_error("Sound Capsule helper request timed out");
        const auto ready = socket.waitUntilReady(true, juce::jmin(100, remaining));
        if (ready < 0)
            throw std::runtime_error("Sound Capsule helper connection failed");
        if (ready == 0)
            continue;
        std::array<char, 16384> buffer{};
        const auto count = socket.read(buffer.data(), static_cast<int>(buffer.size()), false);
        if (count <= 0)
            break;
        const auto* newline = static_cast<const char*>(
            std::memchr(buffer.data(), '\n', static_cast<size_t>(count)));
        if (newline != nullptr)
        {
            response.write(buffer.data(), static_cast<size_t>(newline - buffer.data()));
            terminated = true;
            break;
        }
        response.write(buffer.data(), static_cast<size_t>(count));
    }
    socket.close();
    if (response.getDataSize() == 0)
        throw std::runtime_error("Sound Capsule helper returned no response");
    if (!terminated)
        throw std::runtime_error("Sound Capsule helper response was incomplete or too large");

    const auto parsed = juce::JSON::parse(response.toString());
    if (!parsed.isObject())
        throw std::runtime_error("Sound Capsule helper returned invalid JSON");
    if (static_cast<int>(parsed.getProperty("protocol_version", -1))
        != helperProtocolVersion)
        throw std::runtime_error(
            "The running Sound Capsule helper is incompatible; close older app instances and retry");
    const auto serverVersion = parsed.getProperty(
        "server_version", "").toString();
    if (serverVersion != JucePlugin_VersionString)
        throw std::runtime_error(
            ("Sound Capsule helper " + serverVersion
             + " does not match app " + JucePlugin_VersionString
             + "; close older app instances and run Retry Setup")
                .toStdString());
    if (!static_cast<bool>(parsed.getProperty("ok", false)))
        throw std::runtime_error(parsed.getProperty("error", "Helper request failed").toString().toStdString());
    return parsed;
}
