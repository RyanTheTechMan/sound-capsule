#include "HelperClient.h"

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
        char byte = 0;
        const auto count = socket.read(&byte, 1, false);
        if (count <= 0)
            break;
        if (byte == '\n')
        {
            terminated = true;
            break;
        }
        response.writeByte(byte);
    }
    socket.close();
    if (response.getDataSize() == 0)
        throw std::runtime_error("Sound Capsule helper returned no response");
    if (!terminated)
        throw std::runtime_error("Sound Capsule helper response was incomplete or too large");

    const auto parsed = juce::JSON::parse(response.toString());
    if (!parsed.isObject())
        throw std::runtime_error("Sound Capsule helper returned invalid JSON");
    if (!static_cast<bool>(parsed.getProperty("ok", false)))
        throw std::runtime_error(parsed.getProperty("error", "Helper request failed").toString().toStdString());
    return parsed;
}
