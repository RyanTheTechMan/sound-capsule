#pragma once

#include <juce_core/juce_core.h>

class HelperClient final
{
public:
    explicit HelperClient(juce::String serverHost = "127.0.0.1", int serverPort = 51943)
        : host(std::move(serverHost)), port(serverPort) {}

    juce::var request(const juce::String& command,
                      const juce::var& arguments = juce::var(new juce::DynamicObject()),
                      const std::atomic<bool>* cancelled = nullptr,
                      int timeoutMs = 60000) const;

private:
    juce::String host;
    int port;
};
