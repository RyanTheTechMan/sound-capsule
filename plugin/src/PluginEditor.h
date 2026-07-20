#pragma once

#include "HelperClient.h"
#include "PluginProcessor.h"

#include <array>

class IconToggleButton final : public juce::Button
{
public:
    enum class Icon { waveform, midi, favorite, loop };
    explicit IconToggleButton(Icon);
    void paintButton(juce::Graphics&, bool highlighted, bool down) override;
    void mouseDown(const juce::MouseEvent&) override;
    void mouseUp(const juce::MouseEvent&) override;
    void setWaveformStereo(bool stereo) { stereoWaveform = stereo; repaint(); }
    std::function<void()> onRightClick;

private:
    Icon icon;
    bool stereoWaveform = false;
    bool rightClickInProgress = false;
};

class SettingsIconButton final : public juce::Button
{
public:
    SettingsIconButton();
    void paintButton(juce::Graphics&, bool highlighted, bool down) override;
};

class OperationProgressOverlay final : public juce::Component
{
public:
    OperationProgressOverlay();
    void begin(const juce::String& title, const juce::String& initialStep);
    void update(double progress, const juce::String& step);
    void finish(bool succeeded, const juce::String& title, const juce::String& detail);
    void paint(juce::Graphics&) override;
    void resized() override;

private:
    double progressValue = 0.0;
    juce::Label heading;
    juce::Label stepLabel;
    juce::ProgressBar progressBar{progressValue};
};

class SoundCapsuleAudioProcessorEditor final : public juce::AudioProcessorEditor,
                                                public juce::FileDragAndDropTarget,
                                                private juce::ListBoxModel,
                                                private juce::Timer
{
public:
    explicit SoundCapsuleAudioProcessorEditor(SoundCapsuleAudioProcessor&);
    ~SoundCapsuleAudioProcessorEditor() override;

    void paint(juce::Graphics&) override;
    void paintOverChildren(juce::Graphics&) override;
    void resized() override;
    bool isInterestedInFileDrag(const juce::StringArray& files) override;
    void fileDragEnter(const juce::StringArray& files, int x, int y) override;
    void fileDragExit(const juce::StringArray& files) override;
    void filesDropped(const juce::StringArray& files, int x, int y) override;

private:
    enum class WaveformChannels { mono = 1, stereo = 2 };
    enum class RowHoverTarget { none, play, seek, versionWarning, favorite, append, menu };
    enum class ImportMode { currentPattern, newPattern, overrideSelection };

    struct NotePreview
    {
        float start = 0.0f;
        float length = 0.0f;
        float pitch = 0.0f;
        int channel = 0;
    };

    struct CapsuleRow
    {
        juce::String id;
        juce::String name;
        juce::String plugins;
        juce::String tags;
        juce::String sourceFlVersion;
        juce::StringArray tagItems;
        juce::StringArray channelNames;
        bool favorite = false;
        int channelCount = 0;
        int useCount = 0;
        juce::String capsulePath;
        std::vector<NotePreview> notes;
        float midiTimelineEnd = 1.0f;
        float midiPlaybackEnd = 1.0f;
        std::unique_ptr<juce::AudioThumbnail> thumbnail;
        bool preloadQueued = false;
    };

    int getNumRows() override;
    juce::String getTooltipForRow(int row) override;
    void paintListBoxItem(int row, juce::Graphics&, int width, int height, bool selected) override;
    void listBoxItemClicked(int row, const juce::MouseEvent&) override;
    void selectedRowsChanged(int row) override;
    void listBoxItemDoubleClicked(int row, const juce::MouseEvent&) override;
    void timerCallback() override;
    void mouseDown(const juce::MouseEvent&) override;
    void mouseDrag(const juce::MouseEvent&) override;
    void mouseUp(const juce::MouseEvent&) override;
    void mouseMove(const juce::MouseEvent&) override;
    void mouseExit(const juce::MouseEvent&) override;

    CapsuleRow* selected();
    void refreshLibrary();
    void preloadVisibleRows();
    void refreshSessionStatus();
    void captureSelected(bool individually);
    void checkInitialSetup();
    void checkForUpdates(bool userInitiated = false);
    void downloadAndInstallUpdate();
    void offerSetupRepair();
    void runSetupRepair();
    void showSetup(bool initial);
#if JUCE_WINDOWS
    void showExternalMidiSetup(std::function<void(juce::String)> continuation = {},
                               juce::String notice = {});
#endif
    void runAfterProjectSaved(std::function<void()> action,
                              std::function<void(juce::String)> onFailure = {});
    void waitForFlSave(int previousSaveSequence, std::function<void()> action,
                       std::function<void(juce::String)> onFailure = {});
    void stopPreviewPlayback();
    void startPreview(int row, double normalizedStart, bool toggleIfPlaying);
    void importCapsule(const juce::String& id, ImportMode mode);
    void performImportCapsule(const juce::String& id, ImportMode mode);
    void showImportMenu(const juce::String& id, juce::Point<int> screenPosition);
    void pollOperationProgress();
    void showRowMenu(int row, juce::Point<int> screenPosition);
    void exportCapsule(const juce::String& path, const juce::String& name);
    void copyCapsuleForExport(const juce::File& source, const juce::File& destination);
    void addExternalCapsules(const juce::StringArray& files);
    void showAddCapsulesResult(const juce::var& response);
    bool isLibraryCapsuleFile(const juce::String& path) const;
    void promptRename(const juce::String& id, const juce::String& currentName,
                      const juce::StringArray& channelNames);
    void promptTags(const juce::String& id, const juce::String& currentTags);
    void confirmDelete(const juce::String& id, const juce::String& name);
    void updateRowHover(juce::Point<int> listPosition);
    void updateSortDirectionButton();
    void updateVolumeDisplay();
    void toggleWaveformChannels();
    void toggleTagSearch(const juce::String& tag);
    std::vector<std::pair<juce::Rectangle<int>, juce::String>>
        tagHitAreas(const CapsuleRow&, int rowWidth) const;
    static RowHoverTarget hitTestRow(juce::Point<int> rowPosition, int rowWidth,
                                     bool versionWarningVisible);
    void sendCommand(const juce::String& command,
                     const juce::var& arguments,
                     std::function<void(juce::var)> onSuccess = {},
                     int timeoutMs = 60000,
                     bool quiet = false,
                     std::function<void(const juce::String&)> onError = {});
    void setBusy(const juce::String& message);
    static juce::var object(std::initializer_list<std::pair<juce::Identifier, juce::var>> values);

    SoundCapsuleAudioProcessor& audioProcessor;
    juce::TooltipWindow tooltipWindow;
    juce::AudioFormatManager thumbnailFormats;
    juce::AudioThumbnailCache thumbnailCache{64};
    std::vector<CapsuleRow> rows;
    std::atomic<int> requestsInFlight{0};
    std::atomic<bool> shuttingDown{false};
    juce::ThreadPool requestPool{3};
    juce::ThreadPool previewPreloadPool{1};
    uint32_t searchDueAt = 0;
    uint32_t lastSessionPollAt = 0;
    uint32_t lastVisiblePreloadAt = 0;
    uint32_t lastOperationProgressPollAt = 0;
    uint32_t operationOverlayHideAt = 0;
    uint64_t listGeneration = 0;
    bool migrationNoticeShown = false;
    uint64_t previewGeneration = 0;
    juce::String suggestedCapsuleName;
    juce::String playingCapsuleId;
    juce::String completedPreviewId;
    juce::String pendingPreviewId;
    juce::String operationId;
    juce::String currentProjectFlVersion;
    juce::String currentHostName;
    juce::String availableUpdateTag;
    juce::String availableInstallerName;
    juce::String availableInstallerUrl;
    juce::String availableChecksumUrl;
    juce::String availableReleaseUrl;
    std::atomic<bool> operationProgressPollInFlight{false};
    std::atomic<bool> updateCheckInFlight{false};
    std::atomic<bool> updateDownloadInFlight{false};
    std::atomic<bool> setupRepairInFlight{false};
    double pendingPreviewStart = 0.0;
    int hoveredRow = -1;
    int dragCandidateRow = -1;
    int incomingFileCount = 0;
    RowHoverTarget hoveredTarget = RowHoverTarget::none;
    bool outboundDragStarted = false;
    bool inboundFileDragActive = false;
    bool capsuleNameCustom = false;
    bool operationPollingEnabled = false;
    bool startPreviewAtFirstAudio = true;
    bool pendingPreviewStartsAtAudio = false;
    bool normalizeWaveformDisplay = false;
    bool showSingleChannelNameInRename = false;

    juce::Label title;
    juce::Label status;
    juce::Label connectionStatus;
    juce::TextButton connectionSetup{"Open Setup"};
    juce::TextButton updateAvailable;
    juce::Label projectStatus;
    juce::Label patternStatus;
    juce::TextEditor search;
    juce::TextEditor capsuleName;
    juce::TextButton capsuleNameClear{juce::String::charToString(0x00d7)};
    juce::TextEditor tagsInput;
    juce::TextButton tagsInputClear{juce::String::charToString(0x00d7)};
    IconToggleButton favoritesOnly{IconToggleButton::Icon::favorite};
    juce::ComboBox sortBy;
    juce::TextButton sortDirection;
    IconToggleButton waveformToggle{IconToggleButton::Icon::waveform};
    IconToggleButton midiToggle{IconToggleButton::Icon::midi};
    IconToggleButton loopToggle{IconToggleButton::Icon::loop};
    juce::ListBox list{"Capsule library", this};
    juce::TextButton saveGroup{"Save selected"};
    juce::TextButton saveIndividual{"Save individually"};
    juce::TextButton undoImport{"Undo import"};
    SettingsIconButton setup;
    juce::Label volumeLabel{{}, "Volume"};
    juce::Slider previewVolume;
    OperationProgressOverlay operationProgress;
    std::unique_ptr<juce::FileChooser> exportChooser;
    std::array<bool, 3> sortDescendingByMode{{true, false, true}};
    WaveformChannels waveformChannels = WaveformChannels::mono;
    ImportMode defaultImportMode = ImportMode::currentPattern;
    bool volumeDisplayDb = false;
#if JUCE_WINDOWS
    uint64_t midiSetupGeneration = 0;
#endif

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR(SoundCapsuleAudioProcessorEditor)
};
