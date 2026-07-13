#pragma once

#include <juce_audio_utils/juce_audio_utils.h>

#include <map>

class SoundCapsuleAudioProcessor final : public juce::AudioProcessor
{
public:
    SoundCapsuleAudioProcessor();
    ~SoundCapsuleAudioProcessor() override;

    void prepareToPlay(double sampleRate, int samplesPerBlock) override;
    void releaseResources() override;
    bool isBusesLayoutSupported(const BusesLayout& layouts) const override;
    void processBlock(juce::AudioBuffer<float>&, juce::MidiBuffer&) override;

    juce::AudioProcessorEditor* createEditor() override;
    bool hasEditor() const override { return true; }
    const juce::String getName() const override { return JucePlugin_Name; }
    bool acceptsMidi() const override { return false; }
    bool producesMidi() const override { return false; }
    bool isMidiEffect() const override { return false; }
    double getTailLengthSeconds() const override { return 0.0; }
    int getNumPrograms() override { return 1; }
    int getCurrentProgram() override { return 0; }
    void setCurrentProgram(int) override {}
    const juce::String getProgramName(int) override { return {}; }
    void changeProgramName(int, const juce::String&) override {}
    void getStateInformation(juce::MemoryBlock&) override;
    void setStateInformation(const void*, int) override;

    bool loadPreviewFile(const juce::File& file, bool decodeIfMissing = true);
    bool preloadPreviewFile(const juce::File& file);
    void retainPreloadedPreviewFiles(const juce::StringArray& paths);
    void playPreview(double normalizedStart = 0.0, bool startAtFirstAudio = false);
    void stopPreview();
    bool isPreviewPlaying() const;
    double getPreviewPositionProportion() const;
    void setPreviewVolume(float position)
    {
        const auto limited = juce::jlimit(0.0f, 1.0f, position);
        previewVolume.store(limited);
        previewGain.store(
            limited <= 0.0f
                ? 0.0f
                : juce::Decibels::decibelsToGain(-60.0f + limited * 60.0f));
    }
    float getPreviewVolume() const { return previewVolume.load(); }
    void setPreviewLooping(bool shouldLoop);
    bool getPreviewLooping() const { return previewLooping.load(); }
    bool isRunningStandalone() const { return wrapperType == wrapperType_Standalone; }
    bool ensureHelperRunning();

private:
    static BusesProperties soundCapsuleBuses();

    struct CachedPreview
    {
        juce::AudioBuffer<float> audio;
        double sampleRate = 0.0;
        double firstAudibleProportion = 0.0;
    };

    juce::AudioFormatManager formatManager;
    std::shared_ptr<CachedPreview> activePreview;
    std::unique_ptr<juce::MemoryAudioSource> previewMemory;
    juce::AudioTransportSource previewTransport;
    juce::CriticalSection previewLock;
    juce::AudioBuffer<float> previewBuffer;
    std::atomic<float> previewGain{1.0f};
    std::atomic<float> previewVolume{1.0f};
    std::atomic<bool> previewLooping{true};

    juce::CriticalSection previewCacheLock;
    std::map<juce::String, std::shared_ptr<CachedPreview>> previewCache;

#if ! JUCE_WINDOWS
    std::unique_ptr<juce::MidiOutput> flControlMidi;
#endif
    std::unique_ptr<juce::ChildProcess> helperProcess;

#if ! JUCE_WINDOWS
    void initialiseFlControlMidi();
#endif
    std::shared_ptr<CachedPreview> decodePreviewFile(const juce::File& file);

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR(SoundCapsuleAudioProcessor)
};
