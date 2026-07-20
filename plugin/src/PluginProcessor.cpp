#include "PluginProcessor.h"
#include "PluginEditor.h"
#include "PreviewMath.h"
#include "CapsulePreviewSource.h"

#include <cmath>

namespace
{
juce::File soundCapsuleApplication()
{
    auto current = juce::File::getSpecialLocation(juce::File::currentExecutableFile);
   #if JUCE_MAC
    for (auto candidate = current; candidate != juce::File();)
    {
        if (candidate.hasFileExtension("app"))
            return candidate;
        const auto parent = candidate.getParentDirectory();
        if (parent == candidate)
            break;
        candidate = parent;
    }
   #endif
    return current;
}

juce::File installedSetupRoot()
{
    const auto application = soundCapsuleApplication();
    const auto adjacent = application.getParentDirectory().getChildFile("Setup");
    if (adjacent.isDirectory())
        return adjacent;
   #if JUCE_MAC
    return juce::File("/Library/Application Support/SoundCapsule/Setup");
   #elif JUCE_WINDOWS
    return juce::File(
        juce::SystemStats::getEnvironmentVariable("ProgramFiles", "C:\\Program Files"))
        .getChildFile("Sound Capsule").getChildFile("Setup");
   #else
    return {};
   #endif
}

juce::File frozenHelperExecutable()
{
    return installedSetupRoot().getChildFile("Helper").getChildFile(
       #if JUCE_WINDOWS
        "Sound Capsule Helper.exe"
       #else
        "Sound Capsule Helper"
       #endif
    );
}
}

juce::AudioProcessor::BusesProperties SoundCapsuleAudioProcessor::soundCapsuleBuses()
{
    auto buses = juce::AudioProcessor::BusesProperties()
                     .withOutput("Output", juce::AudioChannelSet::stereo(), true);

    // The standalone library only plays previews and must not open a microphone
    // or interface input. The optional VST3 remains a transparent Master effect.
    if (juce::PluginHostType::getPluginLoadedAs()
        != juce::AudioProcessor::wrapperType_Standalone)
        buses = buses.withInput("Input", juce::AudioChannelSet::stereo(), true);

    return buses;
}

SoundCapsuleAudioProcessor::SoundCapsuleAudioProcessor()
    : AudioProcessor(soundCapsuleBuses())
{
    formatManager.registerBasicFormats();
    // The standalone app owns native virtual ports where the OS supports them.
    // Creating one in every VST instance causes CoreMIDI conflicts.
#if ! JUCE_WINDOWS
    if (isRunningStandalone())
        initialiseFlControlMidi();
#endif
}

SoundCapsuleAudioProcessor::~SoundCapsuleAudioProcessor()
{
    previewTransport.setSource(nullptr);
    if (helperProcess != nullptr && helperProcess->isRunning())
        helperProcess->kill();
}

void SoundCapsuleAudioProcessor::prepareToPlay(double sampleRate, int samplesPerBlock)
{
    previewBuffer.setSize(juce::jmax(1, getTotalNumOutputChannels()), samplesPerBlock, false, true, false);
    previewTransport.prepareToPlay(samplesPerBlock, sampleRate);
}

void SoundCapsuleAudioProcessor::releaseResources()
{
    previewTransport.releaseResources();
}

bool SoundCapsuleAudioProcessor::isBusesLayoutSupported(const BusesLayout& layouts) const
{
    const auto output = layouts.getMainOutputChannelSet();
    const auto supportedOutput = output == juce::AudioChannelSet::mono()
                              || output == juce::AudioChannelSet::stereo();
    if (isRunningStandalone())
        return supportedOutput && layouts.inputBuses.isEmpty();
    return supportedOutput && layouts.getMainInputChannelSet() == output;
}

void SoundCapsuleAudioProcessor::processBlock(juce::AudioBuffer<float>& buffer, juce::MidiBuffer&)
{
    juce::ScopedNoDenormals noDenormals;

    // Output-only standalone buffers contain no audio that should pass through.
    if (getTotalNumInputChannels() == 0)
        buffer.clear();

    const juce::ScopedTryLock lock(previewLock);
    if (!lock.isLocked() || previewBuffer.getNumChannels() < buffer.getNumChannels()
        || previewBuffer.getNumSamples() < buffer.getNumSamples())
        return;
    previewBuffer.clear();
    juce::AudioSourceChannelInfo info(&previewBuffer, 0, buffer.getNumSamples());
    previewTransport.getNextAudioBlock(info);
    const auto gain = previewGain.load();
    for (int channel = 0; channel < buffer.getNumChannels(); ++channel)
        buffer.addFrom(channel, 0, previewBuffer, channel, 0, buffer.getNumSamples(), gain);
}

std::shared_ptr<SoundCapsuleAudioProcessor::CachedPreview>
SoundCapsuleAudioProcessor::decodePreviewFile(const juce::File& file)
{
    std::unique_ptr<juce::AudioFormatReader> reader;
    if (file.hasFileExtension("flcapsule"))
        reader.reset(formatManager.createReaderFor(createCapsulePreviewStream(file)));
    else
        reader.reset(formatManager.createReaderFor(file));
    if (reader == nullptr || reader->lengthInSamples <= 0 || reader->numChannels == 0
        || reader->lengthInSamples > static_cast<juce::int64>(reader->sampleRate * 600.0))
        return {};

    auto decoded = std::make_shared<CachedPreview>();
    decoded->audio.setSize(static_cast<int>(reader->numChannels),
                           static_cast<int>(reader->lengthInSamples));
    if (!reader->read(&decoded->audio, 0, decoded->audio.getNumSamples(), 0, true, true))
        return {};
    decoded->sampleRate = reader->sampleRate;
    decoded->firstAudibleProportion = soundcapsule::preview::firstAudibleProportion(
        decoded->audio, decoded->sampleRate);
    return decoded;
}

bool SoundCapsuleAudioProcessor::preloadPreviewFile(const juce::File& file)
{
    const auto key = file.getFullPathName();
    {
        const juce::ScopedLock lock(previewCacheLock);
        if (previewCache.contains(key))
            return true;
    }
    auto decoded = decodePreviewFile(file);
    if (decoded == nullptr)
        return false;
    const juce::ScopedLock lock(previewCacheLock);
    previewCache[key] = std::move(decoded);
    return true;
}

void SoundCapsuleAudioProcessor::retainPreloadedPreviewFiles(const juce::StringArray& paths)
{
    const juce::ScopedLock lock(previewCacheLock);
    for (auto iterator = previewCache.begin(); iterator != previewCache.end();)
        if (!paths.contains(iterator->first))
            iterator = previewCache.erase(iterator);
        else
            ++iterator;
}

bool SoundCapsuleAudioProcessor::loadPreviewFile(const juce::File& file, bool decodeIfMissing)
{
    const auto key = file.getFullPathName();
    std::shared_ptr<CachedPreview> decoded;
    {
        const juce::ScopedLock lock(previewCacheLock);
        if (const auto found = previewCache.find(key); found != previewCache.end())
            decoded = found->second;
    }
    if (decoded == nullptr && decodeIfMissing)
    {
        if (!preloadPreviewFile(file))
            return false;
        const juce::ScopedLock lock(previewCacheLock);
        if (const auto found = previewCache.find(key); found != previewCache.end())
            decoded = found->second;
    }
    if (decoded == nullptr)
        return false;

    const juce::ScopedLock lock(previewLock);
    // AudioTransportSource::stop() waits for an audio callback to acknowledge
    // the stop. Calling it while previewLock is held prevents that callback
    // from running and stalls the UI for its one-second timeout. Detaching the
    // source is synchronous and protected by the transport's callback lock.
    previewTransport.setSource(nullptr);
    previewMemory.reset();
    activePreview = std::move(decoded);
    // The cache owns the buffer for as long as previewMemory refers to it, so
    // avoid copying the complete WAV again on every click.
    previewMemory = std::make_unique<juce::MemoryAudioSource>(
        activePreview->audio, false, previewLooping.load());
    previewTransport.setSource(previewMemory.get(), 0, nullptr, activePreview->sampleRate);
    previewTransport.setPosition(0.0);
    return true;
}

void SoundCapsuleAudioProcessor::playPreview(double normalizedStart, bool startAtFirstAudio)
{
    const juce::ScopedLock lock(previewLock);
    normalizedStart = soundcapsule::preview::startProportion(
        normalizedStart, startAtFirstAudio,
        activePreview != nullptr ? activePreview->firstAudibleProportion : 0.0);
    const auto length = previewTransport.getLengthInSeconds();
    previewTransport.setPosition(length * normalizedStart);
    previewTransport.start();
}

void SoundCapsuleAudioProcessor::stopPreview()
{
    const juce::ScopedLock lock(previewLock);
    // See loadPreviewFile(): stop() can block for one second when a host is not
    // currently delivering audio callbacks. Detaching is immediate and the
    // cached preview can be attached again on the next click.
    previewTransport.setSource(nullptr);
    previewMemory.reset();
    activePreview.reset();
}

void SoundCapsuleAudioProcessor::setPreviewLooping(bool shouldLoop)
{
    previewLooping.store(shouldLoop);
    const juce::ScopedLock lock(previewLock);
    if (previewMemory != nullptr)
    {
        if (!shouldLoop)
        {
            const auto length = previewMemory->getTotalLength();
            if (length > 0)
                previewMemory->setNextReadPosition(
                    previewMemory->getNextReadPosition() % length);
        }
        previewMemory->setLooping(shouldLoop);
    }
}

bool SoundCapsuleAudioProcessor::isPreviewPlaying() const
{
    return previewTransport.isPlaying();
}

double SoundCapsuleAudioProcessor::getPreviewPositionProportion() const
{
    const auto length = previewTransport.getLengthInSeconds();
    if (length <= 0.0)
        return 0.0;
    auto position = previewTransport.getCurrentPosition();
    if (previewLooping.load() && previewTransport.isPlaying())
        position = std::fmod(position, length);
    return juce::jlimit(0.0, 1.0, position / length);
}

double SoundCapsuleAudioProcessor::getPreviewLengthSeconds() const
{
    return previewTransport.getLengthInSeconds();
}

#if ! JUCE_WINDOWS
void SoundCapsuleAudioProcessor::initialiseFlControlMidi()
{
    const auto preferred = juce::SystemStats::getEnvironmentVariable(
        "SOUNDCAPSULE_MIDI_OUTPUT", "Sound Capsule Control");
    for (const auto& device : juce::MidiOutput::getAvailableDevices())
    {
        if (device.name.equalsIgnoreCase(preferred))
        {
            flControlMidi = juce::MidiOutput::openDevice(device.identifier);
            if (flControlMidi != nullptr)
                return;
        }
    }

   #if JUCE_MAC || JUCE_LINUX || JUCE_BSD
    flControlMidi = juce::MidiOutput::createNewDevice("Sound Capsule Control");
   #endif
}
#endif

bool SoundCapsuleAudioProcessor::ensureHelperRunning(bool refreshSetup)
{
    if (!isRunningStandalone())
        return false;
    if (!refreshSetup && helperProcess != nullptr && helperProcess->isRunning())
        return true;

    const auto frozenHelper = frozenHelperExecutable();
    if (frozenHelper.existsAsFile())
    {
        if (refreshSetup)
        {
            const auto bridge = installedSetupRoot()
                .getChildFile("fl-studio").getChildFile("SoundCapsule")
                .getChildFile("device_SoundCapsule.py");
            juce::ChildProcess setupProcess;
            const juce::StringArray setupArguments{
                frozenHelper.getFullPathName(), "setup",
                "--bridge-script", bridge.getFullPathName(),
                "--app-path", soundCapsuleApplication().getFullPathName()
            };
            if (!bridge.existsAsFile() || !setupProcess.start(setupArguments, 0))
                return false;
            if (!setupProcess.waitForProcessToFinish(60 * 1000))
            {
                setupProcess.kill();
                return false;
            }
            if (setupProcess.getExitCode() != 0)
                return false;
        }

        if (helperProcess != nullptr && helperProcess->isRunning())
            return true;

        helperProcess = std::make_unique<juce::ChildProcess>();
        if (!helperProcess->start(
                {frozenHelper.getFullPathName(), "serve"}, 0))
        {
            helperProcess.reset();
            return false;
        }
        return true;
    }

    // Development/source-tree fallback. Native releases always use the
    // self-contained helper above and require no user-installed Python.
    juce::File python;
    const auto configuredHome = juce::SystemStats::getEnvironmentVariable("SOUNDCAPSULE_HOME", "");
    if (configuredHome.isNotEmpty())
    {
        python = juce::File(configuredHome)
                     .getChildFile("venv")
                     .getChildFile(
                        #if JUCE_WINDOWS
                         "Scripts/python.exe"
                        #else
                         "bin/python"
                        #endif
                     );
    }
    else
    {
   #if JUCE_MAC
        python = juce::File::getSpecialLocation(juce::File::userHomeDirectory)
                     .getChildFile("Library/Application Support/SoundCapsule/venv/bin/python");
   #elif JUCE_WINDOWS
        const auto localAppData = juce::SystemStats::getEnvironmentVariable("LOCALAPPDATA", "");
        python = juce::File(localAppData).getChildFile("SoundCapsule/venv/Scripts/python.exe");
   #else
        return false;
   #endif
    }
    if (!python.existsAsFile())
        return false;

    juce::StringArray arguments{python.getFullPathName(), "-m", "soundcapsule", "serve"};
    helperProcess = std::make_unique<juce::ChildProcess>();
    if (!helperProcess->start(arguments, 0))
    {
        helperProcess.reset();
        return false;
    }
    return true;
}

juce::AudioProcessorEditor* SoundCapsuleAudioProcessor::createEditor()
{
    return new SoundCapsuleAudioProcessorEditor(*this);
}

void SoundCapsuleAudioProcessor::getStateInformation(juce::MemoryBlock& destination)
{
    juce::XmlElement state("SoundCapsule");
    state.setAttribute("version", 1);
    state.setAttribute("previewVolume", static_cast<double>(previewVolume.load()));
    state.setAttribute("previewLooping", previewLooping.load());
    const auto text = state.toString();
    destination.reset();
    destination.append(text.toRawUTF8(), static_cast<size_t>(text.getNumBytesAsUTF8()));
}

void SoundCapsuleAudioProcessor::setStateInformation(const void* data, int size)
{
    if (data == nullptr || size <= 0)
        return;
    if (auto state = juce::parseXML(juce::String::fromUTF8(
            static_cast<const char*>(data), size)); state != nullptr
        && state->hasTagName("SoundCapsule"))
    {
        setPreviewVolume(static_cast<float>(state->getDoubleAttribute("previewVolume", 1.0)));
        setPreviewLooping(state->getBoolAttribute("previewLooping", true));
    }
}

juce::AudioProcessor* JUCE_CALLTYPE createPluginFilter()
{
    return new SoundCapsuleAudioProcessor();
}
