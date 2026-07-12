#include "PluginEditor.h"
#include "CapsulePreviewSource.h"

#include <juce_cryptography/juce_cryptography.h>

#include <cmath>

namespace
{
const auto background = juce::Colour(0xff101318);
const auto panel = juce::Colour(0xff1b2028);
const auto accent = juce::Colour(0xff69d2a8);

#ifndef SOUNDCAPSULE_RELEASE_REPOSITORY
 #define SOUNDCAPSULE_RELEASE_REPOSITORY ""
#endif

#ifndef SOUNDCAPSULE_APPLE_TEAM_ID
 #define SOUNDCAPSULE_APPLE_TEAM_ID ""
#endif

std::array<int, 3> versionParts(juce::String version)
{
    version = version.trim().trimCharactersAtStart("vV");
    const auto tokens = juce::StringArray::fromTokens(version, ".-+", "");
    std::array<int, 3> result{};
    for (int index = 0; index < static_cast<int>(result.size()) && index < tokens.size(); ++index)
        result[static_cast<size_t>(index)] = tokens[index].getIntValue();
    return result;
}

bool isNewerVersion(const juce::String& candidate, const juce::String& current)
{
    return versionParts(candidate) > versionParts(current);
}

juce::String releaseAssetUrl(const juce::var& assets, const juce::String& wantedName)
{
    if (const auto* array = assets.getArray())
        for (const auto& asset : *array)
            if (asset.getProperty("name", "").toString() == wantedName)
                return asset.getProperty("browser_download_url", "").toString();
    return {};
}

int textWidth(const juce::Font& font, const juce::String& text)
{
    juce::GlyphArrangement glyphs;
    glyphs.addLineOfText(font, text, 0.0f, 0.0f);
    return juce::roundToInt(std::ceil(
        glyphs.getBoundingBox(0, glyphs.getNumGlyphs(), true).getWidth()));
}

juce::Path starPath(juce::Point<float> centre, float outerRadius, float innerRadius)
{
    juce::Path path;
    for (int point = 0; point < 10; ++point)
    {
        const auto angle = -juce::MathConstants<float>::halfPi
                         + static_cast<float>(point) * juce::MathConstants<float>::pi / 5.0f;
        const auto radius = point % 2 == 0 ? outerRadius : innerRadius;
        const juce::Point<float> position(centre.x + std::cos(angle) * radius,
                                          centre.y + std::sin(angle) * radius);
        if (point == 0) path.startNewSubPath(position); else path.lineTo(position);
    }
    path.closeSubPath();
    return path;
}

class UpdateSettingsComponent final : public juce::Component
{
public:
    explicit UpdateSettingsComponent(bool checkOnStartup)
        : checkNow("Check for Updates")
    {
        setSize(360, 64);
        startup.setButtonText("Check for Updates on startup");
        startup.setToggleState(checkOnStartup, juce::dontSendNotification);
        addAndMakeVisible(startup);
        addAndMakeVisible(checkNow);
    }

    void resized() override
    {
        auto bounds = getLocalBounds();
        startup.setBounds(bounds.removeFromTop(28));
        bounds.removeFromTop(4);
        checkNow.setBounds(bounds.removeFromTop(28).removeFromLeft(150));
    }

    bool shouldCheckOnStartup() const { return startup.getToggleState(); }
    std::function<void()> onCheckNow;

    void attachCallbacks()
    {
        checkNow.onClick = [this] { if (onCheckNow) onCheckNow(); };
    }

private:
    juce::ToggleButton startup;
    juce::TextButton checkNow;
};

class LibraryLocationComponent final : public juce::Component
{
public:
    explicit LibraryLocationComponent(const juce::String& currentLocation)
        : choose("Choose...")
    {
        setSize(360, 62);
        heading.setText("Capsule save location:", juce::dontSendNotification);
        heading.setColour(juce::Label::textColourId, juce::Colours::lightgrey);
        location.setText(currentLocation, false);
        location.setReadOnly(true);
        location.setCaretVisible(false);
        location.setTooltip(currentLocation);
        choose.setTooltip("Choose where capsule files are saved");
        choose.onClick = [this] { chooseFolder(); };
        addAndMakeVisible(heading);
        addAndMakeVisible(location);
        addAndMakeVisible(choose);
    }

    juce::String getLocation() const { return location.getText(); }

    void resized() override
    {
        auto bounds = getLocalBounds();
        heading.setBounds(bounds.removeFromTop(22));
        bounds.removeFromTop(3);
        choose.setBounds(bounds.removeFromRight(92));
        bounds.removeFromRight(6);
        location.setBounds(bounds);
    }

private:
    void chooseFolder()
    {
        auto initial = juce::File(location.getText());
        if (!initial.isDirectory())
            initial = initial.getParentDirectory();
        chooser = std::make_unique<juce::FileChooser>(
            "Choose capsule save location", initial, juce::String(), true);
        juce::Component::SafePointer<LibraryLocationComponent> safe(this);
        chooser->launchAsync(
            juce::FileBrowserComponent::openMode
                | juce::FileBrowserComponent::canSelectDirectories,
            [safe](const juce::FileChooser& completed) {
                if (safe == nullptr) return;
                const auto selected = completed.getResult();
                if (selected != juce::File())
                {
                    safe->location.setText(selected.getFullPathName(), false);
                    safe->location.setTooltip(selected.getFullPathName());
                }
                safe->chooser.reset();
            });
    }

    juce::Label heading;
    juce::TextEditor location;
    juce::TextButton choose;
    std::unique_ptr<juce::FileChooser> chooser;
};

class SettingsAlertWindow final : public juce::AlertWindow
{
public:
    SettingsAlertWindow(const juce::String& title, bool checkOnStartup,
                        const juce::String& libraryDirectory)
        : juce::AlertWindow(title, {}, juce::MessageBoxIconType::QuestionIcon),
          updateSettings(checkOnStartup),
          libraryLocation(libraryDirectory),
          flSetup("FL Setup")
    {
        addCustomComponent(&libraryLocation);
        addCustomComponent(&updateSettings);
        updateSettings.onCheckNow = [this] { exitModalState(3); };
        updateSettings.attachCallbacks();
        addAndMakeVisible(flSetup);
        flSetup.setTooltip("Show FL Studio auto-open and MIDI setup steps");
        flSetup.onClick = [this] { exitModalState(2); };
    }

    ~SettingsAlertWindow() override
    {
        removeCustomComponent(1);
        removeCustomComponent(0);
    }

    bool shouldCheckOnStartup() const { return updateSettings.shouldCheckOnStartup(); }
    juce::String getLibraryLocation() const { return libraryLocation.getLocation(); }

    void resized() override
    {
        juce::AlertWindow::resized();
        flSetup.setBounds(getWidth() - 100, 8, 88, 26);
    }

private:
    UpdateSettingsComponent updateSettings;
    LibraryLocationComponent libraryLocation;
    juce::TextButton flSetup;
};

class DarkMenuSectionHeader final : public juce::PopupMenu::CustomComponent
{
public:
    explicit DarkMenuSectionHeader(juce::String sectionTitle)
        : juce::PopupMenu::CustomComponent(false), title(std::move(sectionTitle))
    {
    }

    void paint(juce::Graphics& graphics) override
    {
        graphics.fillAll(juce::Colour(0xff11161c));
        graphics.setColour(juce::Colours::white.withAlpha(0.48f));
        graphics.setFont(juce::FontOptions(11.0f, juce::Font::bold));
        graphics.drawFittedText(title, getLocalBounds().reduced(8, 1),
                                juce::Justification::centredLeft, 1);
    }

    void getIdealSize(int& idealWidth, int& idealHeight) override
    {
        idealWidth = 190;
        idealHeight = 22;
    }

private:
    juce::String title;
};

void addDarkMenuSection(juce::PopupMenu& menu, const juce::String& title)
{
    menu.addCustomItem(
        0, std::make_unique<DarkMenuSectionHeader>(title), nullptr, title);
}
}

IconToggleButton::IconToggleButton(Icon iconToUse)
    : juce::Button(iconToUse == Icon::waveform ? "Waveform"
                   : (iconToUse == Icon::midi ? "MIDI"
                      : (iconToUse == Icon::loop ? "Loop" : "Favorites"))),
      icon(iconToUse)
{
    setClickingTogglesState(true);
    setMouseCursor(juce::MouseCursor::PointingHandCursor);
    setTooltip(icon == Icon::waveform ? "Show waveform"
               : (icon == Icon::midi ? "Show MIDI"
                  : (icon == Icon::loop ? "Loop previews" : "Show favorites only")));
}

void IconToggleButton::paintButton(juce::Graphics& graphics, bool highlighted, bool down)
{
    auto area = getLocalBounds().toFloat().reduced(3.0f);
    if (getToggleState() || highlighted)
    {
        graphics.setColour(accent.withAlpha(getToggleState() ? 0.18f : 0.08f));
        graphics.fillRoundedRectangle(area, 5.0f);
    }
    graphics.setColour(getToggleState() || highlighted ? accent : juce::Colours::lightgrey);
    const auto iconArea = area.reduced(6.0f + (down ? 0.5f : 0.0f));
    if (icon == Icon::waveform)
    {
        const auto drawLane = [&](float centreY, float amplitude) {
            juce::Path waveform;
            const float values[] = {0.0f, -0.45f, 0.72f, -1.0f, 0.58f, -0.25f, 0.0f};
            for (int index = 0; index < 7; ++index)
            {
                const auto x = iconArea.getX()
                             + iconArea.getWidth() * static_cast<float>(index) / 6.0f;
                const auto y = centreY + values[index] * amplitude;
                if (index == 0) waveform.startNewSubPath(x, y); else waveform.lineTo(x, y);
            }
            graphics.strokePath(waveform, juce::PathStrokeType(1.35f));
        };
        if (stereoWaveform)
        {
            drawLane(iconArea.getY() + iconArea.getHeight() * 0.30f,
                     iconArea.getHeight() * 0.17f);
            drawLane(iconArea.getY() + iconArea.getHeight() * 0.70f,
                     iconArea.getHeight() * 0.17f);
        }
        else
            drawLane(iconArea.getCentreY(), iconArea.getHeight() * 0.34f);
    }
    else if (icon == Icon::midi)
    {
        const auto width = juce::jmax(4.0f, iconArea.getWidth() * 0.25f);
        const auto height = 3.0f;
        graphics.fillRoundedRectangle(iconArea.getX(), iconArea.getBottom() - height,
                                      width, height, 1.0f);
        graphics.fillRoundedRectangle(iconArea.getCentreX() - width * 0.5f,
                                      iconArea.getCentreY() - height * 0.5f,
                                      width, height, 1.0f);
        graphics.fillRoundedRectangle(iconArea.getRight() - width, iconArea.getY(),
                                      width, height, 1.0f);
    }
    else if (icon == Icon::loop)
    {
        const auto left = iconArea.getX() + 1.0f;
        const auto right = iconArea.getRight() - 1.0f;
        const auto top = iconArea.getY() + iconArea.getHeight() * 0.28f;
        const auto bottom = iconArea.getBottom() - iconArea.getHeight() * 0.28f;
        juce::Path loop;
        loop.startNewSubPath(left + 3.0f, top);
        loop.lineTo(right - 2.0f, top);
        loop.lineTo(right, top + 2.5f);
        loop.startNewSubPath(right - 3.5f, top - 2.5f);
        loop.lineTo(right, top + 2.5f);
        loop.lineTo(right - 3.5f, top + 5.0f);
        loop.startNewSubPath(right - 3.0f, bottom);
        loop.lineTo(left + 2.0f, bottom);
        loop.lineTo(left, bottom - 2.5f);
        loop.startNewSubPath(left + 3.5f, bottom + 2.5f);
        loop.lineTo(left, bottom - 2.5f);
        loop.lineTo(left + 3.5f, bottom - 5.0f);
        graphics.strokePath(loop, juce::PathStrokeType(1.45f,
                            juce::PathStrokeType::curved,
                            juce::PathStrokeType::rounded));
    }
    else
    {
        const auto radius = juce::jmin(iconArea.getWidth(), iconArea.getHeight()) * 0.48f;
        const auto star = starPath(iconArea.getCentre(), radius, radius * 0.46f);
        if (getToggleState())
            graphics.fillPath(star);
        else
            graphics.strokePath(star, juce::PathStrokeType(1.35f));
    }
}

void IconToggleButton::mouseDown(const juce::MouseEvent& event)
{
    if (event.mods.isRightButtonDown())
    {
        rightClickInProgress = true;
        if (onRightClick) onRightClick();
        return;
    }
    rightClickInProgress = false;
    juce::Button::mouseDown(event);
}

void IconToggleButton::mouseUp(const juce::MouseEvent& event)
{
    if (rightClickInProgress)
    {
        rightClickInProgress = false;
        return;
    }
    juce::Button::mouseUp(event);
}

SettingsIconButton::SettingsIconButton()
    : juce::Button("Settings")
{
    setMouseCursor(juce::MouseCursor::PointingHandCursor);
    setTooltip("Settings");
}

void SettingsIconButton::paintButton(juce::Graphics& graphics, bool highlighted, bool down)
{
    auto area = getLocalBounds().toFloat().reduced(3.0f);
    if (highlighted || down)
    {
        graphics.setColour(accent.withAlpha(down ? 0.18f : 0.09f));
        graphics.fillRoundedRectangle(area, 5.0f);
    }
    graphics.setColour(highlighted ? accent : juce::Colours::lightgrey);
    const auto centre = area.getCentre();
    const auto radius = juce::jmin(area.getWidth(), area.getHeight()) * 0.23f;
    graphics.drawEllipse(centre.x - radius, centre.y - radius,
                         radius * 2.0f, radius * 2.0f, 1.6f);
    graphics.drawEllipse(centre.x - radius * 0.36f, centre.y - radius * 0.36f,
                         radius * 0.72f, radius * 0.72f, 1.4f);
    for (int tooth = 0; tooth < 8; ++tooth)
    {
        const auto angle = static_cast<float>(tooth) * juce::MathConstants<float>::pi / 4.0f;
        const auto inner = centre + juce::Point<float>(std::cos(angle), std::sin(angle)) * radius;
        const auto outer = centre + juce::Point<float>(std::cos(angle), std::sin(angle)) * (radius + 4.0f);
        graphics.drawLine(inner.x, inner.y, outer.x, outer.y, 2.0f);
    }
}

ImportProgressOverlay::ImportProgressOverlay()
{
    setVisible(false);
    setInterceptsMouseClicks(true, true);
    heading.setText("Importing", juce::dontSendNotification);
    heading.setFont(juce::FontOptions(22.0f, juce::Font::bold));
    heading.setColour(juce::Label::textColourId, juce::Colours::white);
    heading.setJustificationType(juce::Justification::centred);
    stepLabel.setColour(juce::Label::textColourId, juce::Colours::lightgrey);
    stepLabel.setJustificationType(juce::Justification::centred);
    progressBar.setColour(juce::ProgressBar::foregroundColourId, accent);
    progressBar.setColour(juce::ProgressBar::backgroundColourId, juce::Colour(0xff303641));
    addAndMakeVisible(heading);
    addAndMakeVisible(stepLabel);
    addAndMakeVisible(progressBar);
}

void ImportProgressOverlay::begin(const juce::String& initialStep)
{
    progressValue = 0.01;
    heading.setText("Importing", juce::dontSendNotification);
    heading.setColour(juce::Label::textColourId, juce::Colours::white);
    stepLabel.setText(initialStep, juce::dontSendNotification);
    setVisible(true);
    toFront(false);
    repaint();
}

void ImportProgressOverlay::update(double progress, const juce::String& step)
{
    progressValue = juce::jlimit(0.0, 1.0, progress);
    stepLabel.setText(step, juce::dontSendNotification);
    progressBar.repaint();
}

void ImportProgressOverlay::finish(bool succeeded, const juce::String& detail)
{
    progressValue = succeeded ? 1.0 : progressValue;
    heading.setText(succeeded ? "Import complete" : "Import failed",
                    juce::dontSendNotification);
    heading.setColour(juce::Label::textColourId,
                      succeeded ? accent : juce::Colour(0xffff6b6b));
    stepLabel.setText(detail, juce::dontSendNotification);
    progressBar.repaint();
}

void ImportProgressOverlay::paint(juce::Graphics& graphics)
{
    graphics.fillAll(juce::Colours::black.withAlpha(0.70f));
    auto card = getLocalBounds().withSizeKeepingCentre(
        juce::jmin(480, getWidth() - 40), 178).toFloat();
    graphics.setColour(panel);
    graphics.fillRoundedRectangle(card, 12.0f);
    graphics.setColour(juce::Colours::white.withAlpha(0.10f));
    graphics.drawRoundedRectangle(card, 12.0f, 1.0f);
}

void ImportProgressOverlay::resized()
{
    auto card = getLocalBounds().withSizeKeepingCentre(
        juce::jmin(480, getWidth() - 40), 178).reduced(28, 22);
    heading.setBounds(card.removeFromTop(34));
    card.removeFromTop(12);
    stepLabel.setBounds(card.removeFromTop(34));
    card.removeFromTop(12);
    progressBar.setBounds(card.removeFromTop(18));
}

SoundCapsuleAudioProcessorEditor::SoundCapsuleAudioProcessorEditor(SoundCapsuleAudioProcessor& p)
    : AudioProcessorEditor(&p), audioProcessor(p)
{
    const auto helperReady = audioProcessor.ensureHelperRunning();
    thumbnailFormats.registerBasicFormats();
    setSize(860, 540);
    setResizable(true, true);
    setResizeLimits(820, 440, 1300, 900);

    title.setText("SOUND CAPSULE", juce::dontSendNotification);
    title.setFont(juce::FontOptions(22.0f, juce::Font::bold));
    title.setColour(juce::Label::textColourId, accent);
    status.setText("Connecting...", juce::dontSendNotification);
    status.setColour(juce::Label::textColourId, juce::Colours::lightgrey);
    status.setJustificationType(juce::Justification::centredLeft);
    connectionStatus.setText("FL Studio is not connected", juce::dontSendNotification);
    projectStatus.setText("Project: Unknown", juce::dontSendNotification);
    patternStatus.setText("Pattern: Unknown", juce::dontSendNotification);
    for (auto* label : {&connectionStatus, &projectStatus, &patternStatus, &status})
    {
        label->setColour(juce::Label::backgroundColourId, panel);
        label->setColour(juce::Label::textColourId, juce::Colours::lightgrey);
        label->setBorderSize(juce::BorderSize<int>(0, 8, 0, 8));
    }
    connectionStatus.setColour(juce::Label::backgroundColourId, juce::Colour(0xff3a2b1c));
    connectionStatus.setColour(juce::Label::textColourId, juce::Colours::orange);
    connectionStatus.setVisible(false);
    connectionSetup.setVisible(false);
    connectionSetup.setColour(juce::TextButton::buttonColourId, juce::Colour(0xff5b3b1c));
    updateAvailable.setVisible(false);
    updateAvailable.setColour(juce::TextButton::buttonColourId, accent.darker(0.55f));
    updateAvailable.setColour(juce::TextButton::textColourOffId, juce::Colours::white);
    search.setTextToShowWhenEmpty("Search names, plugins, or tags", juce::Colours::grey);
    capsuleName.setTextToShowWhenEmpty("Capsule name", juce::Colours::grey);
    tagsInput.setTextToShowWhenEmpty("Tags (comma-separated)", juce::Colours::grey);
    waveformToggle.setToggleState(true, juce::dontSendNotification);
    midiToggle.setToggleState(true, juce::dontSendNotification);
    loopToggle.setToggleState(audioProcessor.getPreviewLooping(), juce::dontSendNotification);
    sortBy.addItem("Recently added", 1);
    sortBy.addItem("Name", 2);
    sortBy.addItem("Uses", 3);
    sortBy.setSelectedId(1, juce::dontSendNotification);
    volumeLabel.setColour(juce::Label::textColourId, juce::Colours::lightgrey);
    volumeLabel.setJustificationType(juce::Justification::centredLeft);
    previewVolume.setSliderStyle(juce::Slider::LinearHorizontal);
    previewVolume.setTextBoxStyle(juce::Slider::TextBoxRight, false, 68, 22);
    previewVolume.setRange(0.0, 1.0, 0.001);
    previewVolume.textFromValueFunction = [this](double value) {
        if (!volumeDisplayDb)
            return juce::String(juce::roundToInt(value * 100.0)) + "%";
        if (value <= 0.0)
            return juce::String::charToString(0x2212)
                 + juce::String::charToString(0x221e) + " dB";
        return juce::String(-60.0 + value * 60.0, 1) + " dB";
    };
    previewVolume.valueFromTextFunction = [this](const juce::String& text) {
        if (!volumeDisplayDb)
            return juce::jlimit(
                0.0, 1.0,
                text.retainCharacters("0123456789.").getDoubleValue() / 100.0);
        if (text.containsIgnoreCase("inf") || text.containsChar(0x221e))
            return 0.0;
        const auto decibels = text.retainCharacters("-0123456789.").getDoubleValue();
        if (decibels >= 0.0)
            return 1.0;
        return juce::jlimit(0.001, 1.0, (decibels + 60.0) / 60.0);
    };
    previewVolume.setValue(audioProcessor.getPreviewVolume(), juce::dontSendNotification);
    previewVolume.setDoubleClickReturnValue(true, 1.0);
    updateVolumeDisplay();
    updateSortDirectionButton();

    for (auto* component : std::initializer_list<juce::Component*>{
             &title, &status, &search, &capsuleName, &tagsInput, &favoritesOnly,
             &sortBy, &sortDirection, &waveformToggle, &midiToggle, &loopToggle,
             &list, &saveGroup, &saveIndividual,
             &connectionStatus, &projectStatus, &patternStatus,
             &connectionSetup, &updateAvailable, &setup, &volumeLabel, &previewVolume})
        addAndMakeVisible(component);
    addAndMakeVisible(importProgress);
    connectionStatus.setVisible(false);
    connectionSetup.setVisible(false);
    updateAvailable.setVisible(false);
    importProgress.setVisible(false);
    addAndMakeVisible(undoImport);
    undoImport.setVisible(false);

    saveGroup.setColour(juce::TextButton::buttonColourId, accent.darker(0.35f));
    capsuleName.setVisible(false);
    tagsInput.setVisible(false);
    saveGroup.setVisible(false);
    saveIndividual.setVisible(false);
    list.setRowHeight(64);
    list.setColour(juce::ListBox::backgroundColourId, panel);
    list.addMouseListener(this, true);

    search.onTextChange = [this] { searchDueAt = juce::Time::getMillisecondCounter() + 250; };
    waveformToggle.onClick = [this] {
        if (!waveformToggle.getToggleState() && !midiToggle.getToggleState())
            waveformToggle.setToggleState(true, juce::dontSendNotification);
        list.repaint();
    };
    waveformToggle.onRightClick = [this] { toggleWaveformChannels(); };
    midiToggle.onClick = [this] {
        if (!waveformToggle.getToggleState() && !midiToggle.getToggleState())
            midiToggle.setToggleState(true, juce::dontSendNotification);
        list.repaint();
    };
    loopToggle.onClick = [this] {
        audioProcessor.setPreviewLooping(loopToggle.getToggleState());
        completedPreviewId.clear();
        list.repaint();
    };
    favoritesOnly.onClick = [this] { refreshLibrary(); };
    sortBy.onChange = [this] {
        updateSortDirectionButton();
        refreshLibrary();
    };
    sortDirection.onClick = [this] {
        const auto index = juce::jlimit(0, 2, sortBy.getSelectedId() - 1);
        sortDescendingByMode[static_cast<size_t>(index)]
            = !sortDescendingByMode[static_cast<size_t>(index)];
        updateSortDirectionButton();
        refreshLibrary();
    };
    previewVolume.onValueChange = [this] {
        audioProcessor.setPreviewVolume(static_cast<float>(previewVolume.getValue()));
    };
    saveGroup.onClick = [this] { captureSelected(false); };
    saveIndividual.onClick = [this] { captureSelected(true); };
    undoImport.onClick = [this] {
        juce::Component::SafePointer<SoundCapsuleAudioProcessorEditor> safe(this);
        juce::AlertWindow::showAsync(
            juce::MessageBoxOptions::makeOptionsOkCancel(
                juce::MessageBoxIconType::WarningIcon,
                "Undo last import?",
                "This restores the project backup from before the import and can replace changes "
                "made since then. Sound Capsule will first save the current project as a safety backup.",
                "Restore backup", "Cancel", safe.getComponent()),
            [safe](int result) {
                if (safe == nullptr || result != 1) return;
                safe->sendCommand(
                    "undo_import", object({{"open", true}}),
                    [safe](juce::var response) {
                        if (safe == nullptr) return;
                        const auto confirmed = static_cast<bool>(
                            response.getProperty("reload_confirmed", false));
                        safe->undoImport.setVisible(false);
                        safe->resized();
                        safe->status.setText(
                            confirmed ? "Last import restored"
                                      : "Backup restored; verify FL reloaded the project",
                            juce::dontSendNotification);
                        safe->refreshSessionStatus();
                    },
                    120000);
            });
    };
    setup.onClick = [this] { showSetup(false); };
    connectionSetup.onClick = [this] {
        audioProcessor.ensureHelperRunning();
        showSetup(false);
    };
    updateAvailable.onClick = [this] {
        if (audioProcessor.isRunningStandalone() && availableInstallerUrl.isNotEmpty())
            downloadAndInstallUpdate();
        else if (availableReleaseUrl.isNotEmpty())
            juce::URL(availableReleaseUrl).launchInDefaultBrowser();
    };

    // Playback progress is an animation, so update it at display-like cadence.
    // Slower housekeeping work is gated inside timerCallback.
    startTimerHz(60);
    refreshLibrary();
    refreshSessionStatus();
    checkInitialSetup();
    if (!helperReady && audioProcessor.isRunningStandalone())
    {
        juce::Component::SafePointer<SoundCapsuleAudioProcessorEditor> safe(this);
        juce::MessageManager::callAsync([safe] {
            if (safe != nullptr)
                safe->offerSetupRepair();
        });
    }
}

SoundCapsuleAudioProcessorEditor::~SoundCapsuleAudioProcessorEditor()
{
    stopTimer();
    list.removeMouseListener(this);
    shuttingDown.store(true);
    previewPreloadPool.removeAllJobs(true, 5000);
    requestPool.removeAllJobs(true, 5000);
}

void SoundCapsuleAudioProcessorEditor::paint(juce::Graphics& graphics)
{
    graphics.fillAll(background);
}

void SoundCapsuleAudioProcessorEditor::paintOverChildren(juce::Graphics& graphics)
{
    if (!inboundFileDragActive)
        return;

    graphics.fillAll(juce::Colours::black.withAlpha(0.56f));
    const auto card = getLocalBounds().withSizeKeepingCentre(
        juce::jmin(520, getWidth() - 48), 128).toFloat();
    graphics.setColour(panel);
    graphics.fillRoundedRectangle(card, 12.0f);
    graphics.setColour(accent);
    graphics.drawRoundedRectangle(card, 12.0f, 2.0f);
    graphics.setFont(juce::FontOptions(21.0f, juce::Font::bold));
    graphics.drawFittedText(
        incomingFileCount == 1 ? "Add capsule to library"
                               : "Add " + juce::String(incomingFileCount) + " capsules to library",
        card.toNearestInt().reduced(24), juce::Justification::centred, 2);
}

bool SoundCapsuleAudioProcessorEditor::isInterestedInFileDrag(
    const juce::StringArray& files)
{
    if (files.isEmpty())
        return false;
    auto hasExternalCapsule = false;
    for (const auto& path : files)
    {
        if (!juce::File(path).hasFileExtension("flcapsule"))
            return false;
        if (!isLibraryCapsuleFile(path))
            hasExternalCapsule = true;
    }
    return hasExternalCapsule;
}

void SoundCapsuleAudioProcessorEditor::fileDragEnter(
    const juce::StringArray& files, int, int)
{
    incomingFileCount = files.size();
    inboundFileDragActive = true;
    repaint();
}

void SoundCapsuleAudioProcessorEditor::fileDragExit(const juce::StringArray&)
{
    incomingFileCount = 0;
    inboundFileDragActive = false;
    repaint();
}

void SoundCapsuleAudioProcessorEditor::filesDropped(
    const juce::StringArray& files, int, int)
{
    incomingFileCount = 0;
    inboundFileDragActive = false;
    repaint();
    juce::StringArray externalFiles;
    for (const auto& path : files)
        if (!isLibraryCapsuleFile(path))
            externalFiles.add(path);
    addExternalCapsules(externalFiles);
}

void SoundCapsuleAudioProcessorEditor::resized()
{
    auto bounds = getLocalBounds().reduced(16);
    auto header = bounds.removeFromTop(40);
    title.setBounds(header.removeFromLeft(220));
    setup.setBounds(header.removeFromRight(34).reduced(2, 2));
    header.removeFromRight(6);
    previewVolume.setBounds(header.removeFromRight(170).reduced(2, 2));
    volumeLabel.setBounds(header.removeFromRight(52).reduced(2, 2));
    if (undoImport.isVisible())
    {
        header.removeFromRight(8);
        undoImport.setBounds(header.removeFromRight(160).reduced(2, 2));
    }
    bounds.removeFromTop(2);

    if (connectionStatus.isVisible())
    {
        auto warningRow = bounds.removeFromTop(32);
        connectionSetup.setBounds(warningRow.removeFromRight(104).reduced(2, 1));
        warningRow.removeFromRight(6);
        connectionStatus.setBounds(warningRow);
        bounds.removeFromTop(6);
    }
    if (updateAvailable.isVisible())
    {
        updateAvailable.setBounds(bounds.removeFromTop(32));
        bounds.removeFromTop(6);
    }
    auto sessionRow = bounds.removeFromTop(28);
    constexpr int sectionGap = 6;
    const auto sectionWidth = (sessionRow.getWidth() - sectionGap * 2) / 3;
    projectStatus.setBounds(sessionRow.removeFromLeft(sectionWidth));
    sessionRow.removeFromLeft(sectionGap);
    patternStatus.setBounds(sessionRow.removeFromLeft(sectionWidth));
    sessionRow.removeFromLeft(sectionGap);
    status.setBounds(sessionRow);
    bounds.removeFromTop(10);
    auto searchRow = bounds.removeFromTop(34);
    loopToggle.setBounds(searchRow.removeFromRight(34));
    searchRow.removeFromRight(4);
    midiToggle.setBounds(searchRow.removeFromRight(34));
    searchRow.removeFromRight(4);
    waveformToggle.setBounds(searchRow.removeFromRight(34));
    searchRow.removeFromRight(8);
    sortDirection.setBounds(searchRow.removeFromRight(40));
    searchRow.removeFromRight(6);
    sortBy.setBounds(searchRow.removeFromRight(145));
    searchRow.removeFromRight(8);
    favoritesOnly.setBounds(searchRow.removeFromRight(34));
    searchRow.removeFromRight(8);
    search.setBounds(searchRow);
    bounds.removeFromTop(8);
    if (capsuleName.isVisible())
    {
        auto importRow = bounds.removeFromBottom(36);
        if (saveIndividual.isVisible())
        {
            saveIndividual.setBounds(importRow.removeFromRight(130).reduced(2, 0));
            importRow.removeFromRight(6);
        }
        if (saveGroup.isVisible())
        {
            saveGroup.setBounds(importRow.removeFromRight(130).reduced(2, 0));
            importRow.removeFromRight(8);
        }
        const auto nameWidth = (importRow.getWidth() - 8) / 2;
        capsuleName.setBounds(importRow.removeFromLeft(nameWidth).reduced(2, 0));
        importRow.removeFromLeft(8);
        tagsInput.setBounds(importRow.reduced(2, 0));
        bounds.removeFromBottom(8);
    }
    list.setBounds(bounds);
    importProgress.setBounds(getLocalBounds());
    importProgress.toFront(false);
}

int SoundCapsuleAudioProcessorEditor::getNumRows() { return static_cast<int>(rows.size()); }

void SoundCapsuleAudioProcessorEditor::paintListBoxItem(int rowNumber, juce::Graphics& graphics,
                                                         int width, int height, bool selectedRow)
{
    if (!juce::isPositiveAndBelow(rowNumber, static_cast<int>(rows.size())))
        return;
    const auto& row = rows[static_cast<size_t>(rowNumber)];
    const auto rowHovered = rowNumber == hoveredRow;
    const auto rowColour = selectedRow ? accent.withAlpha(0.18f)
                                      : (rowHovered ? panel.interpolatedWith(accent, 0.055f) : panel);
    graphics.fillAll(rowColour);
    const auto isPlaying = row.id == playingCapsuleId && audioProcessor.isPreviewPlaying();
    const auto isCompleted = row.id == completedPreviewId;
    const auto previewProgress = isCompleted
                               ? 1.0 : audioProcessor.getPreviewPositionProportion();

    auto previewButton = juce::Rectangle<int>(10, (height - 24) / 2, 24, 24);
    const auto playHovered = rowHovered && hoveredTarget == RowHoverTarget::play;
    if (playHovered)
    {
        graphics.setColour(accent.withAlpha(0.11f));
        graphics.fillEllipse(previewButton.expanded(3).toFloat());
    }
    graphics.setColour(isPlaying || playHovered ? accent : juce::Colours::lightgrey);
    if (isPlaying)
        graphics.fillRoundedRectangle(previewButton.reduced(5).toFloat(), 1.5f);
    else
    {
        juce::Path triangle;
        triangle.addTriangle(static_cast<float>(previewButton.getX() + 6),
                             static_cast<float>(previewButton.getY() + 4),
                             static_cast<float>(previewButton.getRight() - 4),
                             static_cast<float>(previewButton.getCentreY()),
                             static_cast<float>(previewButton.getX() + 6),
                             static_cast<float>(previewButton.getBottom() - 4));
        graphics.fillPath(triangle);
    }

    constexpr int contentX = 46;
    constexpr int actionsWidth = 108;
    const auto actionsX = width - actionsWidth;
    graphics.setColour(juce::Colours::white);
    graphics.setFont(15.0f);
    graphics.drawText(row.name, contentX, 2, actionsX - contentX - 215, 20,
                      juce::Justification::centredLeft);
    graphics.setColour(juce::Colours::lightgrey);
    graphics.setFont(12.0f);
    const auto pluginWidth = row.tagItems.isEmpty()
                           ? actionsX - contentX - 8
                           : juce::jmin(170, textWidth(graphics.getCurrentFont(), row.plugins) + 2);
    graphics.drawText(row.plugins, contentX, 20, pluginWidth, 16,
                      juce::Justification::centredLeft, true);
    auto searchTerms = juce::StringArray::fromTokens(search.getText(), ",", "");
    searchTerms.trim();
    searchTerms.removeEmptyStrings();
    const juce::Font tagFont(juce::FontOptions(11.0f, juce::Font::bold));
    graphics.setFont(tagFont);
    for (const auto& [chip, tag] : tagHitAreas(row, width))
    {
        const auto active = searchTerms.contains(tag, true);
        graphics.setColour(active ? accent : background.brighter(0.12f));
        graphics.fillRoundedRectangle(chip.toFloat(), 3.0f);
        if (!active)
        {
            graphics.setColour(juce::Colours::grey.withAlpha(0.55f));
            graphics.drawRoundedRectangle(chip.toFloat().reduced(0.5f), 3.0f, 1.0f);
        }
        graphics.setColour(active ? background : juce::Colours::lightgrey);
        graphics.drawText(tag, chip.reduced(6, 0), juce::Justification::centred, true);
    }
    const auto countText = juce::String(row.channelCount)
                         + (row.channelCount == 1 ? " channel" : " channels")
                         + "  |  " + juce::String(row.useCount)
                         + (row.useCount == 1 ? " use" : " uses");
    graphics.drawText(countText, actionsX - 205, 2, 195, 20,
                      juce::Justification::centredRight);

    const auto favoriteCentre = juce::Point<float>(static_cast<float>(actionsX + 18),
                                                    static_cast<float>(height / 2));
    const auto favoriteHovered = rowHovered && hoveredTarget == RowHoverTarget::favorite;
    if (favoriteHovered)
    {
        graphics.setColour(accent.withAlpha(0.11f));
        graphics.fillEllipse(favoriteCentre.x - 13.0f, favoriteCentre.y - 13.0f, 26.0f, 26.0f);
    }
    graphics.setColour(row.favorite || favoriteHovered ? accent : juce::Colours::lightgrey);
    const auto favoriteStar = starPath(favoriteCentre, 9.0f, 4.2f);
    if (row.favorite) graphics.fillPath(favoriteStar); else graphics.strokePath(favoriteStar, juce::PathStrokeType(1.4f));

    const auto appendCentre = juce::Point<float>(static_cast<float>(actionsX + 54),
                                                  static_cast<float>(height / 2));
    const auto appendHovered = rowHovered && hoveredTarget == RowHoverTarget::append;
    if (appendHovered)
    {
        graphics.setColour(accent.withAlpha(0.11f));
        graphics.fillEllipse(appendCentre.x - 13.0f, appendCentre.y - 13.0f, 26.0f, 26.0f);
    }
    graphics.setColour(appendHovered ? accent : juce::Colours::lightgrey);
    graphics.drawEllipse(appendCentre.x - 9.0f, appendCentre.y - 9.0f, 18.0f, 18.0f, 1.4f);
    graphics.drawLine(appendCentre.x - 4.0f, appendCentre.y,
                      appendCentre.x + 4.0f, appendCentre.y, 1.4f);
    graphics.drawLine(appendCentre.x, appendCentre.y - 4.0f,
                      appendCentre.x, appendCentre.y + 4.0f, 1.4f);

    const auto menuX = static_cast<float>(actionsX + 90);
    const auto menuHovered = rowHovered && hoveredTarget == RowHoverTarget::menu;
    if (menuHovered)
    {
        graphics.setColour(accent.withAlpha(0.11f));
        graphics.fillRoundedRectangle(menuX - 12.0f, static_cast<float>(height / 2) - 16.0f,
                                      24.0f, 32.0f, 7.0f);
    }
    graphics.setColour(menuHovered ? accent : juce::Colours::lightgrey);
    for (int dot = -1; dot <= 1; ++dot)
        graphics.fillEllipse(menuX - 1.7f, static_cast<float>(height / 2 + dot * 7) - 1.7f, 3.4f, 3.4f);

    const auto previewArea = juce::Rectangle<int>(contentX, 39, actionsX - contentX - 8, height - 45);
    auto drawWaveform = [&](juce::Rectangle<int> area)
    {
        graphics.setColour(juce::Colours::grey.withAlpha(0.35f));
        graphics.drawHorizontalLine(area.getCentreY(), static_cast<float>(area.getX()),
                                    static_cast<float>(area.getRight()));
        if (row.thumbnail != nullptr && row.thumbnail->getTotalLength() > 0.0)
        {
            auto render = [&](juce::Colour colour) {
                graphics.setColour(colour);
                if (waveformChannels == WaveformChannels::stereo)
                    row.thumbnail->drawChannels(graphics, area, 0.0, row.thumbnail->getTotalLength(), 1.0f);
                else
                    for (int channel = 0; channel < row.thumbnail->getNumChannels(); ++channel)
                        row.thumbnail->drawChannel(graphics, area, 0.0, row.thumbnail->getTotalLength(),
                                                   channel, 1.0f);
            };
            render(juce::Colours::lightgrey.withAlpha(0.85f));
            if (isPlaying || isCompleted)
            {
                const auto progressWidth = juce::roundToInt(
                    previewProgress * area.getWidth());
                juce::Graphics::ScopedSaveState state(graphics);
                graphics.reduceClipRegion(area.withWidth(progressWidth));
                render(accent.withAlpha(0.95f));
            }
        }
    };
    auto drawMidi = [&](juce::Rectangle<int> area)
    {
        const auto midiScale = 1.0f / row.midiTimelineEnd;
        graphics.setColour(juce::Colours::grey.withAlpha(0.25f));
        graphics.drawRect(area, 1);
        const auto idleColour = [&](int channel) {
            if (row.channelCount <= 1)
                return juce::Colours::lightgrey.withAlpha(0.9f);
            constexpr float levels[] = {0.88f, 0.60f, 0.74f, 0.50f, 0.82f, 0.66f};
            const auto level = levels[static_cast<size_t>(channel) % std::size(levels)];
            return juce::Colour::fromFloatRGBA(level, level, level, 0.95f);
        };
        const auto playingColour = [&](int channel) {
            if (row.channelCount <= 1 || channel == 0)
                return accent.withAlpha(0.95f);
            constexpr juce::uint32 colours[] = {
                0xff78b7ff, 0xffffb45f, 0xffc99aff,
                0xffff83ad, 0xffffdc6e, 0xff62d8d0,
            };
            return juce::Colour(
                colours[(static_cast<size_t>(channel) - 1) % std::size(colours)]);
        };
        auto render = [&](bool played) {
            for (const auto& note : row.notes)
            {
                graphics.setColour(played ? playingColour(note.channel)
                                          : idleColour(note.channel));
                const auto x = static_cast<float>(area.getX())
                             + note.start * midiScale * static_cast<float>(area.getWidth());
                const auto noteWidth = juce::jmax(
                    2.0f, note.length * midiScale * static_cast<float>(area.getWidth()));
                const auto y = static_cast<float>(area.getBottom() - 2)
                             - note.pitch * static_cast<float>(juce::jmax(1, area.getHeight() - 4));
                graphics.fillRoundedRectangle(x, y - 1.5f, noteWidth, 3.0f, 1.0f);
            }
        };
        render(false);
        if (isPlaying || isCompleted)
        {
            const auto midiProgress = juce::jlimit(
                0.0, 1.0, previewProgress / static_cast<double>(row.midiTimelineEnd));
            const auto progressWidth = juce::roundToInt(
                midiProgress * area.getWidth());
            juce::Graphics::ScopedSaveState state(graphics);
            graphics.reduceClipRegion(area.withWidth(progressWidth));
            render(true);
        }
    };

    const auto showWaveform = waveformToggle.getToggleState();
    const auto showMidi = midiToggle.getToggleState();
    if (showWaveform && !showMidi)
        drawWaveform(previewArea);
    else if (showMidi && !showWaveform)
        drawMidi(previewArea);
    else
    {
        auto left = previewArea;
        const auto waveformWidth = (left.getWidth() - 6) / 2;
        const auto waveformArea = left.removeFromLeft(waveformWidth);
        left.removeFromLeft(6);
        drawWaveform(waveformArea);
        drawMidi(left);
    }
}

void SoundCapsuleAudioProcessorEditor::listBoxItemClicked(int rowNumber, const juce::MouseEvent& event)
{
    if (outboundDragStarted)
        return;
    if (!juce::isPositiveAndBelow(rowNumber, static_cast<int>(rows.size())))
        return;
    if (event.x < 42)
    {
        startPreview(rowNumber, 0.0, true);
        return;
    }
    const auto width = event.eventComponent != nullptr ? event.eventComponent->getWidth() : list.getWidth();
    const auto actionsX = width - 108;
    const auto row = rows[static_cast<size_t>(rowNumber)].id;
    if (!event.mods.isRightButtonDown())
        for (const auto& [chip, tag] : tagHitAreas(rows[static_cast<size_t>(rowNumber)], width))
            if (chip.contains(event.getPosition()))
            {
                toggleTagSearch(tag);
                return;
            }
    if (event.mods.isRightButtonDown() && event.y >= 39 && event.x >= 46 && event.x < actionsX - 8)
    {
        const auto previewX = 46;
        const auto previewWidth = actionsX - previewX - 8;
        auto clickedWaveform = waveformToggle.getToggleState() && !midiToggle.getToggleState();
        if (waveformToggle.getToggleState() && midiToggle.getToggleState())
            clickedWaveform = event.x < previewX + (previewWidth - 6) / 2;
        if (clickedWaveform)
        {
            toggleWaveformChannels();
            return;
        }
    }
    if (event.y >= 39 && event.x >= 46 && event.x < actionsX - 8)
    {
        const auto previewX = 46;
        const auto previewWidth = actionsX - previewX - 8;
        auto normalized = 0.0;
        auto clickedMidi = false;
        if (!(waveformToggle.getToggleState() && midiToggle.getToggleState()))
        {
            normalized = static_cast<double>(event.x - previewX) / juce::jmax(1, previewWidth);
            clickedMidi = midiToggle.getToggleState();
        }
        else
        {
            const auto halfWidth = (previewWidth - 6) / 2;
            const auto rightStart = previewX + halfWidth + 6;
            if (event.x < previewX + halfWidth)
                normalized = static_cast<double>(event.x - previewX) / juce::jmax(1, halfWidth);
            else if (event.x >= rightStart)
            {
                normalized = static_cast<double>(event.x - rightStart) / juce::jmax(1, halfWidth);
                clickedMidi = true;
            }
            else
                return;
        }
        if (clickedMidi)
            normalized *= rows[static_cast<size_t>(rowNumber)].midiTimelineEnd;
        startPreview(rowNumber, juce::jlimit(0.0, 1.0, normalized), false);
    }
    else if (event.x >= actionsX && event.x < actionsX + 36)
    {
        const auto value = !rows[static_cast<size_t>(rowNumber)].favorite;
        sendCommand("favorite", object({{"id", row}, {"value", value}}),
                    [this](juce::var) { refreshLibrary(); });
    }
    else if (event.x >= actionsX + 36 && event.x < actionsX + 72)
    {
        if (event.mods.isRightButtonDown())
            showImportMenu(row, event.getScreenPosition());
        else
            importCapsule(row, defaultImportMode);
    }
    else if (event.x >= actionsX + 72)
        showRowMenu(rowNumber, event.getScreenPosition());
}

void SoundCapsuleAudioProcessorEditor::selectedRowsChanged(int)
{
}

void SoundCapsuleAudioProcessorEditor::listBoxItemDoubleClicked(int, const juce::MouseEvent&)
{
}

void SoundCapsuleAudioProcessorEditor::timerCallback()
{
    const auto now = juce::Time::getMillisecondCounter();
    if (importOperationId.isNotEmpty()
        && static_cast<int32_t>(now - lastImportProgressPollAt) >= 250)
    {
        lastImportProgressPollAt = now;
        pollImportProgress();
    }
    if (importOverlayHideAt != 0
        && static_cast<int32_t>(now - importOverlayHideAt) >= 0)
    {
        importOverlayHideAt = 0;
        importProgress.setVisible(false);
    }
    if (static_cast<int32_t>(now - lastVisiblePreloadAt) >= 125)
    {
        lastVisiblePreloadAt = now;
        updateRowHover(list.getMouseXYRelative());
        preloadVisibleRows();
    }
    if (searchDueAt != 0
        && static_cast<int32_t>(now - searchDueAt) >= 0)
    {
        searchDueAt = 0;
        refreshLibrary();
    }
    if (requestsInFlight.load() == 0 && status.getText() == "Working...")
        status.setText("Ready", juce::dontSendNotification);
    if (playingCapsuleId.isNotEmpty() && !audioProcessor.isPreviewPlaying())
    {
        const auto finishedId = playingCapsuleId;
        playingCapsuleId.clear();
        completedPreviewId = finishedId;
        for (int index = 0; index < static_cast<int>(rows.size()); ++index)
            if (rows[static_cast<size_t>(index)].id == finishedId)
            {
                list.repaintRow(index);
                break;
            }
    }
    else if (playingCapsuleId.isNotEmpty())
        for (int index = 0; index < static_cast<int>(rows.size()); ++index)
            if (rows[static_cast<size_t>(index)].id == playingCapsuleId)
            {
                list.repaintRow(index);
                break;
            }
    if (static_cast<int32_t>(now - lastSessionPollAt) >= 2000)
    {
        lastSessionPollAt = now;
        refreshSessionStatus();
    }
}

SoundCapsuleAudioProcessorEditor::RowHoverTarget
SoundCapsuleAudioProcessorEditor::hitTestRow(juce::Point<int> position, int rowWidth)
{
    if (position.x < 42)
        return RowHoverTarget::play;
    const auto actionsX = rowWidth - 108;
    if (position.y >= 39 && position.x >= 46 && position.x < actionsX - 8)
        return RowHoverTarget::seek;
    if (position.x >= actionsX && position.x < actionsX + 36)
        return RowHoverTarget::favorite;
    if (position.x >= actionsX + 36 && position.x < actionsX + 72)
        return RowHoverTarget::append;
    if (position.x >= actionsX + 72)
        return RowHoverTarget::menu;
    return RowHoverTarget::none;
}

void SoundCapsuleAudioProcessorEditor::updateRowHover(juce::Point<int> position)
{
    auto nextRow = -1;
    auto nextTarget = RowHoverTarget::none;
    if (list.getLocalBounds().contains(position))
    {
        nextRow = list.getRowContainingPosition(position.x, position.y);
        if (juce::isPositiveAndBelow(nextRow, static_cast<int>(rows.size())))
        {
            const auto rowBounds = list.getRowPosition(nextRow, true);
            nextTarget = hitTestRow({position.x, position.y - rowBounds.getY()}, rowBounds.getWidth());
        }
        else
            nextRow = -1;
    }

    if (nextRow == hoveredRow && nextTarget == hoveredTarget)
        return;
    const auto previousRow = hoveredRow;
    hoveredRow = nextRow;
    hoveredTarget = nextTarget;
    if (previousRow >= 0) list.repaintRow(previousRow);
    if (hoveredRow >= 0) list.repaintRow(hoveredRow);
    list.setMouseCursor(hoveredTarget == RowHoverTarget::none
                            ? juce::MouseCursor::NormalCursor
                            : juce::MouseCursor::PointingHandCursor);
}

void SoundCapsuleAudioProcessorEditor::mouseDown(const juce::MouseEvent& event)
{
    dragCandidateRow = -1;
    outboundDragStarted = false;
    if (!event.mods.isLeftButtonDown())
        return;

    const auto position = event.getEventRelativeTo(&list).getPosition();
    const auto rowNumber = list.getRowContainingPosition(position.x, position.y);
    if (!juce::isPositiveAndBelow(rowNumber, static_cast<int>(rows.size())))
        return;
    const auto rowBounds = list.getRowPosition(rowNumber, true);
    const auto rowPosition = juce::Point<int>(position.x, position.y - rowBounds.getY());
    if (hitTestRow(rowPosition, rowBounds.getWidth()) != RowHoverTarget::none)
        return;
    for (const auto& [chip, tag] : tagHitAreas(
             rows[static_cast<size_t>(rowNumber)], rowBounds.getWidth()))
    {
        juce::ignoreUnused(tag);
        if (chip.contains(rowPosition))
            return;
    }
    dragCandidateRow = rowNumber;
}

void SoundCapsuleAudioProcessorEditor::mouseDrag(const juce::MouseEvent& event)
{
    if (outboundDragStarted || !event.mods.isLeftButtonDown()
        || !event.mouseWasDraggedSinceMouseDown()
        || !juce::isPositiveAndBelow(dragCandidateRow, static_cast<int>(rows.size())))
        return;

    const auto capsule = juce::File(
        rows[static_cast<size_t>(dragCandidateRow)].capsulePath);
    dragCandidateRow = -1;
    if (!capsule.existsAsFile())
    {
        status.setText("Capsule file was not found", juce::dontSendNotification);
        return;
    }

    outboundDragStarted = true;
    juce::StringArray files;
    files.add(capsule.getFullPathName());
    juce::Component::SafePointer<SoundCapsuleAudioProcessorEditor> safe(this);
    if (!juce::DragAndDropContainer::performExternalDragDropOfFiles(
            files, false, &list, [safe] {
                if (safe != nullptr)
                    safe->status.setText("Capsule shared", juce::dontSendNotification);
            }))
    {
        outboundDragStarted = false;
        status.setText("Could not start file drag", juce::dontSendNotification);
    }
    else
        status.setText("Sharing capsule...", juce::dontSendNotification);
}

void SoundCapsuleAudioProcessorEditor::mouseUp(const juce::MouseEvent&)
{
    dragCandidateRow = -1;
    if (!outboundDragStarted)
        return;
    juce::Component::SafePointer<SoundCapsuleAudioProcessorEditor> safe(this);
    juce::Timer::callAfterDelay(100, [safe] {
        if (safe != nullptr)
            safe->outboundDragStarted = false;
    });
}

void SoundCapsuleAudioProcessorEditor::mouseMove(const juce::MouseEvent& event)
{
    updateRowHover(event.getEventRelativeTo(&list).getPosition());
}

void SoundCapsuleAudioProcessorEditor::mouseExit(const juce::MouseEvent&)
{
    updateRowHover(list.getMouseXYRelative());
}

void SoundCapsuleAudioProcessorEditor::updateSortDirectionButton()
{
    const auto index = juce::jlimit(0, 2, sortBy.getSelectedId() - 1);
    const auto descending = sortDescendingByMode[static_cast<size_t>(index)];
    sortDirection.setButtonText(juce::String::charToString(descending ? 0x2193 : 0x2191));
    sortDirection.setTooltip(descending ? "Descending" : "Ascending");
}

void SoundCapsuleAudioProcessorEditor::updateVolumeDisplay()
{
    previewVolume.updateText();
    previewVolume.setTooltip(
        volumeDisplayDb ? "Preview volume in decibels" : "Preview volume in percent");
}

void SoundCapsuleAudioProcessorEditor::toggleWaveformChannels()
{
    waveformChannels = waveformChannels == WaveformChannels::mono
                     ? WaveformChannels::stereo : WaveformChannels::mono;
    waveformToggle.setWaveformStereo(waveformChannels == WaveformChannels::stereo);
    const auto mode = waveformChannels == WaveformChannels::mono ? "mono" : "stereo";
    list.repaint();
    juce::Component::SafePointer<SoundCapsuleAudioProcessorEditor> safe(this);
    sendCommand(
        "configure_setup", object({{"waveform_channels", mode}}),
        [safe, mode](juce::var) {
            if (safe != nullptr)
                safe->status.setText("Waveform: " + juce::String(mode),
                                     juce::dontSendNotification);
        });
}

std::vector<std::pair<juce::Rectangle<int>, juce::String>>
SoundCapsuleAudioProcessorEditor::tagHitAreas(const CapsuleRow& row, int rowWidth) const
{
    constexpr int contentX = 46;
    constexpr int actionsWidth = 108;
    const auto actionsX = rowWidth - actionsWidth;
    const juce::Font pluginFont(juce::FontOptions(12.0f));
    const juce::Font tagFont(juce::FontOptions(11.0f, juce::Font::bold));
    const auto pluginWidth = row.plugins.isEmpty()
                           ? 0 : juce::jmin(170, textWidth(pluginFont, row.plugins) + 2);
    auto x = contentX + pluginWidth + (pluginWidth > 0 ? 8 : 0);
    std::vector<std::pair<juce::Rectangle<int>, juce::String>> result;
    for (const auto& tag : row.tagItems)
    {
        const auto chipWidth = textWidth(tagFont, tag) + 14;
        if (x + chipWidth > actionsX - 6)
            break;
        result.emplace_back(juce::Rectangle<int>(x, 19, chipWidth, 18), tag);
        x += chipWidth + 4;
    }
    return result;
}

void SoundCapsuleAudioProcessorEditor::toggleTagSearch(const juce::String& tag)
{
    auto terms = juce::StringArray::fromTokens(search.getText(), ",", "");
    terms.trim();
    terms.removeEmptyStrings();
    const auto existing = terms.indexOf(tag, true);
    if (existing >= 0)
        terms.remove(existing);
    else
        terms.add(tag);
    search.setText(terms.joinIntoString(", "), true);
}

SoundCapsuleAudioProcessorEditor::CapsuleRow* SoundCapsuleAudioProcessorEditor::selected()
{
    const auto index = list.getSelectedRow();
    return juce::isPositiveAndBelow(index, static_cast<int>(rows.size())) ? &rows[static_cast<size_t>(index)] : nullptr;
}

void SoundCapsuleAudioProcessorEditor::refreshLibrary()
{
    const auto generation = ++listGeneration;
    const auto sortId = sortBy.getSelectedId();
    const auto sortName = sortId == 2 ? "name" : (sortId == 3 ? "uses" : "recent");
    const auto directionIndex = juce::jlimit(0, 2, sortId - 1);
    sendCommand("list", object({{"search", search.getText()},
                                {"favorites_only", favoritesOnly.getToggleState()},
                                {"sort_by", sortName},
                                {"descending", sortDescendingByMode[static_cast<size_t>(directionIndex)]}}),
                [this, generation](juce::var response) {
        if (generation != listGeneration)
            return;
        std::vector<CapsuleRow> updated;
        if (auto* values = response.getProperty("capsules", juce::var()).getArray())
        {
            for (const auto& value : *values)
            {
                CapsuleRow row;
                row.id = value.getProperty("id", "").toString();
                row.name = value.getProperty("name", "").toString();
                row.favorite = static_cast<bool>(value.getProperty("favorite", false));
                row.channelCount = static_cast<int>(value.getProperty("channel_count", 0));
                row.useCount = static_cast<int>(value.getProperty("use_count", 0));
                row.capsulePath = value.getProperty("path", "").toString();
                auto plugins = juce::JSON::parse(value.getProperty("plugin_names", "[]").toString());
                auto tagValues = juce::JSON::parse(value.getProperty("tags", "[]").toString());
                auto noteValues = juce::JSON::parse(value.getProperty("note_preview", "[]").toString());
                juce::StringArray pluginNames, tagNames;
                if (auto* array = plugins.getArray()) for (const auto& item : *array) pluginNames.add(item.toString());
                if (auto* array = tagValues.getArray()) for (const auto& item : *array) tagNames.add(item.toString());
                if (auto* notes = noteValues.getArray())
                    for (const auto& item : *notes)
                        if (auto* note = item.getArray(); note != nullptr && note->size() >= 3)
                            row.notes.push_back({static_cast<float>((*note)[0]),
                                                 static_cast<float>((*note)[1]),
                                                 static_cast<float>((*note)[2]),
                                                 note->size() >= 4
                                                     ? static_cast<int>((*note)[3]) : 0});
                if (!row.notes.empty())
                {
                    row.midiTimelineEnd = 0.0f;
                    for (const auto& note : row.notes)
                        row.midiTimelineEnd = juce::jmax(
                            row.midiTimelineEnd, note.start + note.length);
                    row.midiTimelineEnd = juce::jlimit(0.000001f, 1.0f, row.midiTimelineEnd);
                }
                row.plugins = pluginNames.joinIntoString(", ");
                row.tags = tagNames.joinIntoString(", ");
                row.tagItems = tagNames;
                updated.push_back(std::move(row));
            }
        }
        rows = std::move(updated);
        list.updateContent();
        list.repaint();
        preloadVisibleRows();
        status.setText(juce::String(rows.size()) + " capsules", juce::dontSendNotification);
    });
}

void SoundCapsuleAudioProcessorEditor::preloadVisibleRows()
{
    if (rows.empty() || list.getHeight() <= 0)
    {
        audioProcessor.retainPreloadedPreviewFiles({});
        return;
    }
    auto first = list.getRowContainingPosition(1, 1);
    auto last = list.getRowContainingPosition(1, juce::jmax(1, list.getHeight() - 2));
    if (first < 0) first = 0;
    if (last < 0) last = juce::jmin(static_cast<int>(rows.size()) - 1,
                                    first + list.getNumRowsOnScreen());
    first = juce::jmax(0, first - 1);
    last = juce::jmin(static_cast<int>(rows.size()) - 1, last + 1);

    juce::StringArray retainedPaths;
    auto repaintNeeded = false;
    juce::Component::SafePointer<SoundCapsuleAudioProcessorEditor> safe(this);
    for (int index = 0; index < static_cast<int>(rows.size()); ++index)
    {
        auto& row = rows[static_cast<size_t>(index)];
        if (index < first || index > last)
        {
            row.thumbnail.reset();
            row.preloadQueued = false;
            continue;
        }
        const juce::File capsule(row.capsulePath);
        retainedPaths.add(capsule.getFullPathName());
        if (row.thumbnail == nullptr && capsule.existsAsFile())
        {
            row.thumbnail = std::make_unique<juce::AudioThumbnail>(
                512, thumbnailFormats, thumbnailCache);
            row.thumbnail->setSource(new CapsulePreviewInputSource(capsule));
            repaintNeeded = true;
        }
        else if (row.thumbnail != nullptr && !row.thumbnail->isFullyLoaded())
            repaintNeeded = true;

        if (!row.preloadQueued && capsule.existsAsFile())
        {
            row.preloadQueued = true;
            previewPreloadPool.addJob([safe, capsule] {
                if (safe != nullptr && !safe->shuttingDown.load())
                    safe->audioProcessor.preloadPreviewFile(capsule);
            });
        }
    }
    audioProcessor.retainPreloadedPreviewFiles(retainedPaths);
    if (repaintNeeded)
        list.repaint();
}

void SoundCapsuleAudioProcessorEditor::refreshSessionStatus()
{
    lastSessionPollAt = juce::Time::getMillisecondCounter();
    juce::Component::SafePointer<SoundCapsuleAudioProcessorEditor> safe(this);
    sendCommand("session", object({}), [safe](juce::var response) {
        if (safe == nullptr) return;
        auto projectTitle = response.getProperty("project_title", "").toString();
        if (projectTitle.isEmpty())
            projectTitle = "Unnamed project";
        const auto patternName = response.getProperty("pattern_name", "Pattern").toString();
        auto selectedCount = 0;
        juce::StringArray selectedNames;
        if (auto* selectedChannels = response.getProperty("selected_channels", juce::var()).getArray())
            selectedCount = selectedChannels->size();
        if (auto* names = response.getProperty("selected_channel_names", juce::var()).getArray())
            for (const auto& name : *names)
                if (name.toString().trim().isNotEmpty())
                    selectedNames.add(name.toString().trim());

        auto nextSuggestion = selectedNames.joinIntoString(" + ");
        if (nextSuggestion.length() > 80 && selectedNames.size() > 1)
            nextSuggestion = selectedNames[0] + " + " + juce::String(selectedNames.size() - 1) + " more";
        const auto currentName = safe->capsuleName.getText().trim();
        if (nextSuggestion.isNotEmpty()
            && (currentName.isEmpty() || currentName == safe->suggestedCapsuleName || currentName == "Sound Capsule"))
            safe->capsuleName.setText(nextSuggestion, false);
        safe->suggestedCapsuleName = nextSuggestion;

        const auto dirty = static_cast<int>(response.getProperty("changed", 0)) != 0;
        const auto connectionWarningWasVisible = safe->connectionStatus.isVisible();
        const auto importFieldsWereVisible = safe->capsuleName.isVisible();
        safe->connectionStatus.setVisible(false);
        safe->connectionSetup.setVisible(false);
        safe->capsuleName.setVisible(true);
        safe->tagsInput.setVisible(true);
        safe->projectStatus.setText("Project: " + projectTitle + (dirty ? " (unsaved)" : ""),
                                    juce::dontSendNotification);
        safe->projectStatus.setColour(juce::Label::textColourId,
                                      dirty ? juce::Colours::orange : juce::Colours::white);
        safe->patternStatus.setText("Pattern: " + patternName, juce::dontSendNotification);
        safe->patternStatus.setColour(juce::Label::textColourId, juce::Colours::white);
        const auto saveVisibilityChanged = safe->saveGroup.isVisible() != (selectedCount > 0)
                                        || safe->saveIndividual.isVisible() != (selectedCount > 1);
        safe->saveGroup.setButtonText(selectedCount > 1 ? "Import selected" : "Import");
        safe->saveGroup.setVisible(selectedCount > 0);
        safe->saveIndividual.setVisible(selectedCount > 1);
        const auto undoAvailable = static_cast<bool>(
            response.getProperty("undo_available", false));
        const auto undoRemaining = static_cast<int>(
            response.getProperty("undo_remaining_seconds", 0));
        const auto undoVisibilityChanged = safe->undoImport.isVisible() != undoAvailable;
        safe->undoImport.setVisible(undoAvailable);
        if (undoAvailable)
        {
            const auto remainingMinutes = juce::jmax(1, static_cast<int>(
                std::ceil(static_cast<double>(undoRemaining) / 60.0)));
            safe->undoImport.setButtonText(
                "Undo import (" + juce::String(remainingMinutes) + "m)");
            safe->undoImport.setTooltip(
                "Restore the pre-import backup. " + juce::String(remainingMinutes)
                + (remainingMinutes == 1 ? " minute remaining." : " minutes remaining."));
        }
        const auto projectPath = response.getProperty("project_path", "").toString();
        safe->projectStatus.setTooltip(projectPath.isNotEmpty() ? projectPath : projectTitle);
        safe->connectionStatus.setTooltip(
            "FL MIDI scripting API " + response.getProperty("midi_api_version", 0).toString());
        safe->patternStatus.setTooltip(patternName);
        safe->saveGroup.setTooltip(selectedNames.joinIntoString(", "));
        if (connectionWarningWasVisible || !importFieldsWereVisible
            || undoVisibilityChanged || saveVisibilityChanged)
            safe->resized();
    }, 1500, true);
}

void SoundCapsuleAudioProcessorEditor::captureSelected(bool individually)
{
    const auto name = capsuleName.getText().trim().isNotEmpty()
                        ? capsuleName.getText().trim()
                        : (suggestedCapsuleName.isNotEmpty() ? suggestedCapsuleName : "Sound Capsule");
    juce::Array<juce::var> tags;
    for (auto tag : juce::StringArray::fromTokens(tagsInput.getText(), ",", ""))
        if (tag.trim().isNotEmpty()) tags.add(tag.trim());
    juce::Component::SafePointer<SoundCapsuleAudioProcessorEditor> safe(this);
    runAfterProjectSaved([safe, name, individually, tags] {
        if (safe == nullptr) return;
        safe->sendCommand(
            "capture", object({{"name", name}, {"individually", individually}, {"tags", tags}}),
            [safe](juce::var) {
                if (safe == nullptr) return;
                safe->status.setText("Imported to library", juce::dontSendNotification);
                safe->refreshLibrary();
            },
            300000);
    });
}

void SoundCapsuleAudioProcessorEditor::checkInitialSetup()
{
    juce::Component::SafePointer<SoundCapsuleAudioProcessorEditor> safe(this);
    sendCommand("setup_status", object({}), [safe](juce::var response) {
        if (safe == nullptr) return;
        safe->waveformChannels = response.getProperty("waveform_channels", "mono").toString() == "stereo"
                               ? WaveformChannels::stereo : WaveformChannels::mono;
        const auto destination = response.getProperty(
            "import_destination", "current_pattern").toString();
        safe->defaultImportMode = destination == "new_pattern"
                                ? ImportMode::newPattern
                                : (destination == "override_selection"
                                       ? ImportMode::overrideSelection
                                       : ImportMode::currentPattern);
        safe->volumeDisplayDb = response.getProperty(
            "volume_display", "percent").toString() == "db";
        safe->updateVolumeDisplay();
        safe->waveformToggle.setWaveformStereo(safe->waveformChannels == WaveformChannels::stereo);
        safe->list.repaint();
        if (safe->audioProcessor.isRunningStandalone()
            && static_cast<bool>(response.getProperty("check_updates_on_startup", true)))
            safe->checkForUpdates();
        if (safe->audioProcessor.isRunningStandalone()
            && !static_cast<bool>(response.getProperty("setup_complete", false)))
            safe->showSetup(true);
    });
}

void SoundCapsuleAudioProcessorEditor::checkForUpdates(bool userInitiated)
{
    const auto repository = juce::String(SOUNDCAPSULE_RELEASE_REPOSITORY).trim();
    if (repository.isEmpty())
    {
        if (userInitiated)
            juce::AlertWindow::showMessageBoxAsync(
                juce::MessageBoxIconType::InfoIcon, "Updates unavailable",
                "This development build is not connected to a GitHub release repository.",
                "OK", this);
        return;
    }
    if (updateCheckInFlight.exchange(true))
    {
        if (userInitiated)
            status.setText("Already checking for updates", juce::dontSendNotification);
        return;
    }

    juce::Component::SafePointer<SoundCapsuleAudioProcessorEditor> safe(this);
    requestPool.addJob([safe, repository, userInitiated] {
        if (safe == nullptr || safe->shuttingDown.load())
            return;

        int statusCode = 0;
        juce::String error;
        juce::String tag;
        juce::String releaseUrl;
        juce::String installerName;
        juce::String installerUrl;
        juce::String checksumUrl;
        const auto endpoint = juce::URL("https://api.github.com/repos/" + repository
                                        + "/releases/latest");
        auto stream = endpoint.createInputStream(
            juce::URL::InputStreamOptions(juce::URL::ParameterHandling::inAddress)
                .withConnectionTimeoutMs(5000)
                .withStatusCode(&statusCode)
                .withExtraHeaders("Accept: application/vnd.github+json\r\n"
                                  "User-Agent: Sound-Capsule/" JucePlugin_VersionString "\r\n"));
        if (stream == nullptr || statusCode != 200)
            error = "GitHub did not return a published release.";
        else
        {
            const auto response = juce::JSON::parse(stream->readEntireStreamAsString());
            if (!response.isObject())
                error = "GitHub returned an invalid update response.";
            else
            {
                tag = response.getProperty("tag_name", "").toString();
                releaseUrl = response.getProperty("html_url", "").toString();
                if (tag.isEmpty() || releaseUrl.isEmpty())
                    error = "GitHub returned incomplete release information.";
                else
                {
                    const auto version = tag.trimCharactersAtStart("vV");
                   #if JUCE_MAC
                    installerName = "Sound-Capsule-v" + version + "-macOS.pkg";
                   #elif JUCE_WINDOWS
                    installerName = "Sound-Capsule-v" + version + "-Windows-x64.msi";
                   #endif
                    const auto assets = response.getProperty("assets", juce::var());
                    installerUrl = releaseAssetUrl(assets, installerName);
                    checksumUrl = releaseAssetUrl(assets, "SHA256SUMS.txt");
                }
            }
        }

        if (safe->shuttingDown.load())
            return;
        juce::MessageManager::callAsync([safe, tag, releaseUrl, installerName,
                                         installerUrl, checksumUrl, error, userInitiated] {
            if (safe == nullptr)
                return;
            safe->updateCheckInFlight.store(false);
            if (error.isNotEmpty())
            {
                if (userInitiated)
                    juce::AlertWindow::showMessageBoxAsync(
                        juce::MessageBoxIconType::WarningIcon, "Could not check for updates",
                        error, "OK", safe.getComponent());
                return;
            }

            if (isNewerVersion(tag, JucePlugin_VersionString))
            {
                safe->availableUpdateTag = tag;
                safe->availableInstallerName = installerName;
                safe->availableInstallerUrl = installerUrl;
                safe->availableChecksumUrl = checksumUrl;
                safe->availableReleaseUrl = releaseUrl;
                const auto canInstall = safe->audioProcessor.isRunningStandalone()
                                     && installerUrl.isNotEmpty() && checksumUrl.isNotEmpty();
                safe->updateAvailable.setButtonText(
                    "Sound Capsule " + tag + " is available - "
                    + (canInstall ? "Download and Install" : "View release notes"));
                safe->updateAvailable.setTooltip(
                    canInstall ? "Download, verify, and launch the native Sound Capsule installer."
                               : "Open the release notes and downloads for Sound Capsule " + tag + ".");
                safe->updateAvailable.setVisible(true);
                safe->resized();
                if (userInitiated)
                {
                    juce::Component::SafePointer<SoundCapsuleAudioProcessorEditor> promptSafe(safe);
                    juce::AlertWindow::showAsync(
                        juce::MessageBoxOptions::makeOptionsOkCancel(
                            juce::MessageBoxIconType::InfoIcon,
                            "Update available",
                            "Sound Capsule " + tag + " is available."
                                + (canInstall ? " The native installer is ready to download."
                                              : " Open the release to download it manually."),
                            canInstall ? "Download Update" : "View Release",
                            "Not now", safe.getComponent()),
                        [promptSafe, releaseUrl, canInstall](int result) {
                            if (result != 1 || promptSafe == nullptr)
                                return;
                            if (canInstall)
                                promptSafe->downloadAndInstallUpdate();
                            else
                                juce::URL(releaseUrl).launchInDefaultBrowser();
                        });
                }
            }
            else if (userInitiated)
                juce::AlertWindow::showMessageBoxAsync(
                    juce::MessageBoxIconType::InfoIcon, "Sound Capsule is up to date",
                    "You are running the latest published version ("
                        + juce::String(JucePlugin_VersionString) + ").",
                    "OK", safe.getComponent());
        });
    });
}

void SoundCapsuleAudioProcessorEditor::downloadAndInstallUpdate()
{
    if (!audioProcessor.isRunningStandalone() || availableInstallerUrl.isEmpty()
        || availableChecksumUrl.isEmpty() || updateDownloadInFlight.load())
        return;

    juce::Component::SafePointer<SoundCapsuleAudioProcessorEditor> safe(this);
    juce::AlertWindow::showAsync(
        juce::MessageBoxOptions::makeOptionsOkCancel(
            juce::MessageBoxIconType::InfoIcon,
            "Install Sound Capsule " + availableUpdateTag + "?",
            "Sound Capsule will download and verify the native installer, open it, and then quit. "
            "Your capsule library and settings will be preserved.",
            "Download and Install", "Cancel", this),
        [safe](int result) {
            if (safe == nullptr || result != 1 || safe->updateDownloadInFlight.exchange(true))
                return;

            safe->status.setText("Downloading " + safe->availableUpdateTag + "...",
                                 juce::dontSendNotification);
            const auto installerUrl = safe->availableInstallerUrl;
            const auto checksumUrl = safe->availableChecksumUrl;
            const auto installerName = safe->availableInstallerName;
            const auto tag = safe->availableUpdateTag;
            const auto releaseUrl = safe->availableReleaseUrl;
            safe->requestPool.addJob([safe, installerUrl, checksumUrl, installerName, tag, releaseUrl] {
                juce::String error;
                const auto updateDirectory = juce::File::getSpecialLocation(juce::File::tempDirectory)
                    .getChildFile("SoundCapsule Updates").getChildFile(tag);
                updateDirectory.deleteRecursively();
                if (!updateDirectory.createDirectory())
                    error = "Could not create the temporary update directory.";

                const auto installer = updateDirectory.getChildFile(installerName);
                if (error.isEmpty())
                {
                    int statusCode = 0;
                    auto input = juce::URL(installerUrl).createInputStream(
                        juce::URL::InputStreamOptions(juce::URL::ParameterHandling::inAddress)
                            .withConnectionTimeoutMs(15000)
                            .withStatusCode(&statusCode)
                            .withExtraHeaders("Accept: application/octet-stream\r\n"
                                              "User-Agent: Sound-Capsule/" JucePlugin_VersionString "\r\n"));
                    juce::FileOutputStream output(installer);
                    if (input == nullptr || statusCode != 200 || !output.openedOk())
                        error = "The installer download could not be started.";
                    else
                    {
                        juce::HeapBlock<char> buffer(64 * 1024);
                        const auto total = input->getTotalLength();
                        juce::int64 downloaded = 0;
                        int lastPercent = -1;
                        while (!safe->shuttingDown.load())
                        {
                            const auto count = input->read(buffer.getData(), 64 * 1024);
                            if (count <= 0)
                                break;
                            if (!output.write(buffer.getData(), static_cast<size_t>(count)))
                            {
                                error = "The downloaded installer could not be saved.";
                                break;
                            }
                            downloaded += count;
                            if (total > 0)
                            {
                                const auto percent = juce::roundToInt(100.0 * downloaded / total);
                                if (percent >= lastPercent + 5)
                                {
                                    lastPercent = percent;
                                    juce::MessageManager::callAsync([safe, percent] {
                                        if (safe != nullptr)
                                            safe->status.setText("Downloading update (" + juce::String(percent) + "%)...",
                                                                 juce::dontSendNotification);
                                    });
                                }
                            }
                        }
                        output.flush();
                        if (error.isEmpty() && (safe->shuttingDown.load() || downloaded <= 0))
                            error = "The installer download was interrupted.";
                    }
                }

                juce::String checksumText;
                if (error.isEmpty())
                {
                    int statusCode = 0;
                    auto input = juce::URL(checksumUrl).createInputStream(
                        juce::URL::InputStreamOptions(juce::URL::ParameterHandling::inAddress)
                            .withConnectionTimeoutMs(10000)
                            .withStatusCode(&statusCode)
                            .withExtraHeaders("User-Agent: Sound-Capsule/" JucePlugin_VersionString "\r\n"));
                    if (input == nullptr || statusCode != 200)
                        error = "The release checksum file could not be downloaded.";
                    else
                        checksumText = input->readEntireStreamAsString();
                }

                if (error.isEmpty())
                {
                    juce::String expected;
                    juce::StringArray lines;
                    lines.addLines(checksumText);
                    for (const auto& line : lines)
                    {
                        juce::StringArray tokens;
                        tokens.addTokens(line.trim(), " \t", "");
                        if (tokens.size() >= 2
                            && tokens[tokens.size() - 1].trimCharactersAtStart("*") == installerName)
                        {
                            expected = tokens[0].toLowerCase();
                            break;
                        }
                    }
                    const auto actual = juce::SHA256(installer).toHexString().toLowerCase();
                    if (expected.length() != 64 || actual != expected)
                        error = "The installer checksum did not match the published release.";
                }

               #if JUCE_MAC
                if (error.isEmpty())
                {
                    juce::ChildProcess signatureCheck;
                    const juce::StringArray arguments{
                        "/usr/sbin/pkgutil", "--check-signature", installer.getFullPathName()
                    };
                    if (!signatureCheck.start(arguments) || !signatureCheck.waitForProcessToFinish(30000)
                        || signatureCheck.getExitCode() != 0)
                        error = "The macOS installer signature could not be verified.";
                    else
                    {
                        const auto signature = signatureCheck.readAllProcessOutput();
                        const auto expectedTeam = juce::String(SOUNDCAPSULE_APPLE_TEAM_ID).trim();
                        if (!signature.contains("Developer ID Installer")
                            || (expectedTeam.isNotEmpty() && !signature.contains(expectedTeam)))
                            error = "The macOS installer was not signed by the expected developer.";
                    }
                }
               #endif

                bool launched = false;
                if (error.isEmpty())
                {
                    juce::ChildProcess launcher;
                   #if JUCE_MAC
                    launched = launcher.start(juce::StringArray{
                        "/usr/bin/open", installer.getFullPathName()
                    });
                   #elif JUCE_WINDOWS
                    const auto msiexec = juce::File(
                        juce::SystemStats::getEnvironmentVariable("SystemRoot", "C:\\Windows"))
                        .getChildFile("System32").getChildFile("msiexec.exe");
                    launched = launcher.start(juce::StringArray{
                        msiexec.getFullPathName(), "/i", installer.getFullPathName()
                    });
                   #endif
                    if (!launched)
                        error = "The native installer could not be opened.";
                }

                juce::MessageManager::callAsync([safe, error, releaseUrl, launched] {
                    if (safe == nullptr)
                        return;
                    safe->updateDownloadInFlight.store(false);
                    if (launched && error.isEmpty())
                    {
                        juce::JUCEApplicationBase::quit();
                        return;
                    }
                    safe->status.setText("Update was not installed", juce::dontSendNotification);
                    juce::AlertWindow::showAsync(
                        juce::MessageBoxOptions::makeOptionsOkCancel(
                            juce::MessageBoxIconType::WarningIcon,
                            "Could not install update", error,
                            "Open Release", "Close", safe.getComponent()),
                        [releaseUrl](int choice) {
                            if (choice == 1)
                                juce::URL(releaseUrl).launchInDefaultBrowser();
                        });
                });
            });
        });
}

void SoundCapsuleAudioProcessorEditor::offerSetupRepair()
{
    if (!audioProcessor.isRunningStandalone() || setupRepairInFlight.load())
        return;
    juce::Component::SafePointer<SoundCapsuleAudioProcessorEditor> safe(this);
    juce::AlertWindow::showAsync(
        juce::MessageBoxOptions::makeOptionsOkCancel(
            juce::MessageBoxIconType::WarningIcon,
            "Finish Sound Capsule setup",
            "The local helper is not ready. uv must be installed before Sound Capsule can finish "
            "setup. Sound Capsule will never download or install uv automatically.",
            "Retry Setup", "uv Instructions", this),
        [safe](int result) {
            if (safe == nullptr)
                return;
            if (result == 1)
                safe->runSetupRepair();
            else if (result == 0)
                juce::URL("https://docs.astral.sh/uv/getting-started/installation/")
                    .launchInDefaultBrowser();
        });
}

void SoundCapsuleAudioProcessorEditor::runSetupRepair()
{
    if (setupRepairInFlight.exchange(true))
        return;
    status.setText("Finishing Sound Capsule setup...", juce::dontSendNotification);
    juce::Component::SafePointer<SoundCapsuleAudioProcessorEditor> safe(this);
    requestPool.addJob([safe] {
        juce::String error;
        juce::StringArray arguments;
       #if JUCE_MAC
        const auto script = juce::File("/Library/Application Support/SoundCapsule/Setup/bootstrap-install.sh");
        arguments = {script.getFullPathName()};
       #elif JUCE_WINDOWS
        const auto script = juce::File(
            juce::SystemStats::getEnvironmentVariable("ProgramFiles", "C:\\Program Files"))
            .getChildFile("Sound Capsule").getChildFile("Setup").getChildFile("bootstrap-install.ps1");
        arguments = {
            juce::File(juce::SystemStats::getEnvironmentVariable("SystemRoot", "C:\\Windows"))
                .getChildFile("System32").getChildFile("WindowsPowerShell")
                .getChildFile("v1.0").getChildFile("powershell.exe").getFullPathName(),
            "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script.getFullPathName()
        };
       #endif
        if (!script.existsAsFile())
            error = "The native setup payload is missing. Reinstall Sound Capsule or install uv manually.";
        else
        {
            juce::ChildProcess process;
            if (!process.start(arguments))
                error = "Sound Capsule could not start its setup helper.";
            else if (!process.waitForProcessToFinish(10 * 60 * 1000))
            {
                process.kill();
                error = "Sound Capsule setup timed out.";
            }
            else if (process.getExitCode() != 0)
                error = "Setup failed. Install uv from the official instructions, then choose Retry Setup.";
        }
        juce::MessageManager::callAsync([safe, error] {
            if (safe == nullptr)
                return;
            safe->setupRepairInFlight.store(false);
            if (error.isEmpty() && safe->audioProcessor.ensureHelperRunning())
            {
                safe->status.setText("Sound Capsule setup complete", juce::dontSendNotification);
                safe->refreshLibrary();
                safe->refreshSessionStatus();
                return;
            }
            safe->status.setText("Sound Capsule setup needs attention", juce::dontSendNotification);
            juce::AlertWindow::showAsync(
                juce::MessageBoxOptions::makeOptionsOkCancel(
                    juce::MessageBoxIconType::WarningIcon,
                    "Setup could not finish",
                    error.isNotEmpty() ? error : "The local helper still could not be started.",
                    "Open uv Instructions", "Close", safe.getComponent()),
                [](int result) {
                    if (result == 1)
                        juce::URL("https://docs.astral.sh/uv/getting-started/installation/")
                            .launchInDefaultBrowser();
                });
        });
    });
}

void SoundCapsuleAudioProcessorEditor::showSetup(bool initial)
{
    juce::Component::SafePointer<SoundCapsuleAudioProcessorEditor> safe(this);
    sendCommand("setup_status", object({}), [safe, initial](juce::var response) {
        if (safe == nullptr) return;
        const auto currentUndoMinutes = static_cast<int>(
            response.getProperty("undo_window_minutes", 10));
        const auto currentWaveformChannels =
            response.getProperty("waveform_channels", "mono").toString();
        const auto currentImportDestination =
            response.getProperty("import_destination", "current_pattern").toString();
        const auto currentVolumeDisplay =
            response.getProperty("volume_display", "percent").toString();
        const auto currentCheckUpdatesOnStartup = static_cast<bool>(
            response.getProperty("check_updates_on_startup", true));
        const auto currentLibraryDirectory =
            response.getProperty("library_dir", "").toString();
        const auto appPath = response.getProperty("app_path", "").toString();
        const auto titleText = initial ? "Welcome to Sound Capsule" : "Sound Capsule settings";
        auto* dialog = new SettingsAlertWindow(
            titleText, currentCheckUpdatesOnStartup, currentLibraryDirectory);
        dialog->addTextEditor("undo_minutes", juce::String(currentUndoMinutes),
                              "Undo minutes (1-1440):");
        juce::StringArray waveformModes;
        waveformModes.add("Mono");
        waveformModes.add("Stereo");
        dialog->addComboBox("waveform_channels", waveformModes, "Waveform:");
        if (auto* waveformMode = dialog->getComboBoxComponent("waveform_channels"))
            waveformMode->setSelectedId(currentWaveformChannels == "stereo" ? 2 : 1,
                                        juce::dontSendNotification);
        juce::StringArray importDestinations;
        importDestinations.add("Current pattern");
        importDestinations.add("New pattern");
        importDestinations.add("Override selection");
        dialog->addComboBox("import_destination", importDestinations, "Default import:");
        if (auto* importMode = dialog->getComboBoxComponent("import_destination"))
            importMode->setSelectedId(
                currentImportDestination == "new_pattern"
                    ? 2
                    : (currentImportDestination == "override_selection" ? 3 : 1),
                juce::dontSendNotification);
        juce::StringArray volumeDisplays;
        volumeDisplays.add("Percentage");
        volumeDisplays.add("dB");
        dialog->addComboBox("volume_display", volumeDisplays, "Volume display:");
        if (auto* volumeDisplay = dialog->getComboBoxComponent("volume_display"))
            volumeDisplay->setSelectedId(
                currentVolumeDisplay == "db" ? 2 : 1,
                juce::dontSendNotification);
        dialog->addButton("Save", 1, juce::KeyPress(juce::KeyPress::returnKey));
        dialog->addButton(initial ? "Not now" : "Cancel", 0,
                          juce::KeyPress(juce::KeyPress::escapeKey));
        dialog->enterModalState(
            true,
            juce::ModalCallbackFunction::create(
                [safe, dialog, appPath, currentLibraryDirectory](int result) {
                    if (safe == nullptr) return;
                    if (result == 3)
                    {
                        safe->checkForUpdates(true);
                        return;
                    }
                    if (result != 1 && result != 2) return;
                    const auto showInstructions = result == 2;
                    const auto* undoEditor = dialog->getTextEditor("undo_minutes");
                    const auto* waveformMode = dialog->getComboBoxComponent("waveform_channels");
                    const auto* importMode = dialog->getComboBoxComponent("import_destination");
                    const auto* volumeDisplay = dialog->getComboBoxComponent("volume_display");
                    const auto undoMinutes = undoEditor != nullptr
                                           ? undoEditor->getText().getIntValue() : 0;
                    const auto waveformSetting = waveformMode != nullptr
                                               && waveformMode->getSelectedId() == 2
                                               ? juce::String("stereo") : juce::String("mono");
                    const auto importSetting = importMode != nullptr
                                                    && importMode->getSelectedId() == 2
                                                ? juce::String("new_pattern")
                                                : (importMode != nullptr
                                                       && importMode->getSelectedId() == 3
                                                       ? juce::String("override_selection")
                                                       : juce::String("current_pattern"));
                    const auto volumeDisplaySetting = volumeDisplay != nullptr
                                                           && volumeDisplay->getSelectedId() == 2
                                                       ? juce::String("db")
                                                       : juce::String("percent");
                    const auto checkUpdatesOnStartup = dialog->shouldCheckOnStartup();
                    const auto selectedLibraryDirectory = dialog->getLibraryLocation();
                    if (undoMinutes < 1 || undoMinutes > 1440)
                    {
                        juce::AlertWindow::showMessageBoxAsync(
                            juce::MessageBoxIconType::WarningIcon,
                            "Invalid Undo duration",
                            "Enter a duration from 1 to 1440 minutes.",
                            "OK", safe.getComponent());
                        return;
                    }
                    auto saveSettings =
                        [safe, appPath, showInstructions, undoMinutes, waveformSetting,
                         importSetting, volumeDisplaySetting, checkUpdatesOnStartup]
                        (juce::var libraryResult) {
                        if (safe == nullptr) return;
                        safe->sendCommand(
                            "configure_setup",
                            object({{"undo_window_minutes", undoMinutes},
                                    {"waveform_channels", waveformSetting},
                                    {"import_destination", importSetting},
                                    {"volume_display", volumeDisplaySetting},
                                    {"check_updates_on_startup", checkUpdatesOnStartup}}),
                            [safe, appPath, showInstructions, waveformSetting,
                             importSetting, volumeDisplaySetting, libraryResult](juce::var) {
                            if (safe == nullptr) return;
                            safe->waveformChannels = waveformSetting == "stereo"
                                                   ? WaveformChannels::stereo
                                                   : WaveformChannels::mono;
                            safe->waveformToggle.setWaveformStereo(
                                safe->waveformChannels == WaveformChannels::stereo);
                            safe->defaultImportMode = importSetting == "new_pattern"
                                                    ? ImportMode::newPattern
                                                    : (importSetting == "override_selection"
                                                           ? ImportMode::overrideSelection
                                                           : ImportMode::currentPattern);
                            safe->volumeDisplayDb = volumeDisplaySetting == "db";
                            safe->updateVolumeDisplay();
                            safe->list.repaint();
                            const auto locationChanged = libraryResult.isObject();
                            const auto movedCount = locationChanged
                                ? static_cast<int>(libraryResult.getProperty("moved_count", 0)) : 0;
                            const auto notMovedCount = locationChanged
                                ? static_cast<int>(libraryResult.getProperty("not_moved_count", 0)) : 0;
                            safe->status.setText(
                                movedCount > 0
                                    ? "Settings saved; " + juce::String(movedCount)
                                        + (movedCount == 1 ? " capsule moved" : " capsules moved")
                                    : "Settings saved",
                                juce::dontSendNotification);
                            if (locationChanged)
                            {
                                safe->audioProcessor.stopPreview();
                                safe->refreshLibrary();
                            }
                            if (notMovedCount > 0)
                            {
                                const auto previousDirectory = libraryResult.getProperty(
                                    "previous_library_dir", "").toString();
                                juce::AlertWindow::showAsync(
                                    juce::MessageBoxOptions::makeOptionsOkCancel(
                                        juce::MessageBoxIconType::WarningIcon,
                                        "Some capsules were not moved",
                                        juce::String(movedCount) + (movedCount == 1
                                            ? " capsule moved. " : " capsules moved. ")
                                            + juce::String(notMovedCount)
                                            + (notMovedCount == 1
                                                ? " was not moved and remains in the previous location."
                                                : " were not moved and remain in the previous location."),
                                        "Show Not Moved", "Dismiss", safe.getComponent()),
                                    [previousDirectory](int choice) {
                                        if (choice == 1 && previousDirectory.isNotEmpty())
                                            juce::File(previousDirectory).revealToUser();
                                    });
                            }
                            if (!showInstructions)
                            {
                                safe->refreshSessionStatus();
                                return;
                            }
                            if (appPath.isNotEmpty())
                                juce::SystemClipboard::copyTextToClipboard(appPath);
                            auto setupText =
                                juce::String("Optional auto-open with FL Studio:\n\n")
                                + "1. Open Options > File settings > External tools.\n"
                                  "2. Choose an empty row and browse to Sound Capsule.app.\n"
                                  "3. Name it Sound Capsule and enable Launch at startup.\n\n"
                                  "This FL option launches Sound Capsule with FL Studio, not at system login.\n\n";
                            if (appPath.isNotEmpty())
                                setupText += "The app path is on your clipboard:\n" + appPath + "\n\n";
                            setupText +=
                                "Required MIDI bridge:\n\n"
                                "Open Options > MIDI Settings, enable the Sound Capsule Control input, "
                                "and choose Sound Capsule (user) as its Controller type.\n\n"
                                "These FL assignments only need to be done once.";
                            juce::AlertWindow::showMessageBoxAsync(
                                juce::MessageBoxIconType::InfoIcon,
                                "Finish FL Studio setup", setupText,
                                "Got it", safe.getComponent());
                            safe->refreshSessionStatus();
                            });
                        };

                    const auto libraryChanged =
                        juce::File(selectedLibraryDirectory)
                            != juce::File(currentLibraryDirectory);
                    if (!libraryChanged)
                    {
                        saveSettings(juce::var());
                        return;
                    }

                    juce::AlertWindow::showAsync(
                        juce::MessageBoxOptions::makeOptionsYesNoCancel(
                            juce::MessageBoxIconType::QuestionIcon,
                            "Change capsule save location",
                            "Do you want to move existing capsules into the new location? "
                            "Capsules already present there will be merged into the library.",
                            "Move Existing", "Don't Move", "Cancel", safe.getComponent()),
                        [safe, selectedLibraryDirectory, saveSettings](int choice) {
                            if (safe == nullptr || choice == 0) return;
                            safe->sendCommand(
                                "set_library_location",
                                object({{"path", selectedLibraryDirectory},
                                        {"move_existing", choice == 1}}),
                                [saveSettings](juce::var locationResult) {
                                    saveSettings(locationResult);
                                },
                                120000);
                        });
                }),
            true);
    });
}

void SoundCapsuleAudioProcessorEditor::runAfterProjectSaved(std::function<void()> action)
{
    juce::Component::SafePointer<SoundCapsuleAudioProcessorEditor> safe(this);
    sendCommand("session", object({}), [safe, continuation = std::move(action)](juce::var response) mutable {
        if (safe == nullptr) return;
        const auto changed = static_cast<int>(response.getProperty("changed", 0));
        const auto previousSequence = static_cast<int>(response.getProperty("save_sequence", 0));
        if (changed == 0)
        {
            // The helper resolves clean projects from FL's recent-project metadata,
            // so there is no save transition to wait for here.
            continuation();
            return;
        }
        juce::AlertWindow::showAsync(
            juce::MessageBoxOptions::makeOptionsOkCancel(
                juce::MessageBoxIconType::QuestionIcon,
                "Save FL Studio project?",
                "FL Studio has unsaved changes. Save the project and continue? "
                "FL may show its normal Save dialog for a new project.",
                "Save and continue", "Cancel", safe.getComponent()),
            [safe, previousSequence, confirmedAction = std::move(continuation)](int result) mutable {
                if (safe != nullptr && result == 1)
                    safe->waitForFlSave(previousSequence, std::move(confirmedAction));
            });
    });
}

void SoundCapsuleAudioProcessorEditor::waitForFlSave(int previousSaveSequence, std::function<void()> action)
{
    juce::Component::SafePointer<SoundCapsuleAudioProcessorEditor> safe(this);
    setBusy("Requesting Save from FL Studio...");
    sendCommand("request_save", object({}),
                [safe, previousSaveSequence, continuation = std::move(action)](juce::var) mutable {
        if (safe == nullptr) return;
        safe->setBusy("Waiting for FL Studio to finish saving...");
        ++safe->requestsInFlight;
        safe->requestPool.addJob([safe, previousSaveSequence,
                                  completedAction = std::move(continuation)]() mutable {
        juce::String error;
        bool saved = false;
        const auto deadline = juce::Time::getMillisecondCounterHiRes() + 30000.0;
        while (safe != nullptr && !safe->shuttingDown.load()
               && juce::Time::getMillisecondCounterHiRes() < deadline)
        {
            try
            {
                const auto response = HelperClient().request(
                    "session", SoundCapsuleAudioProcessorEditor::object({}), &safe->shuttingDown, 5000);
                const auto sequence = static_cast<int>(response.getProperty("save_sequence", 0));
                const auto changed = static_cast<int>(response.getProperty("changed", 1));
                if (changed == 0 && sequence > previousSaveSequence)
                {
                    saved = true;
                    break;
                }
            }
            catch (const std::exception& exception)
            {
                error = exception.what();
            }
            juce::Thread::sleep(200);
        }
        if (!saved && error.isEmpty())
            error = "FL Studio did not finish saving within 30 seconds";
        juce::MessageManager::callAsync(
            [safe, saved, error, continuationAfterSave = std::move(completedAction)]() mutable {
            if (safe == nullptr) return;
            --safe->requestsInFlight;
            if (!saved)
            {
                safe->status.setText(error, juce::dontSendNotification);
                return;
            }
            safe->status.setText("Project saved", juce::dontSendNotification);
            continuationAfterSave();
        });
        });
    }, 5000);
}

void SoundCapsuleAudioProcessorEditor::startPreview(int rowNumber, double normalizedStart,
                                                     bool toggleIfPlaying)
{
    if (!juce::isPositiveAndBelow(rowNumber, static_cast<int>(rows.size())))
        return;
    const auto id = rows[static_cast<size_t>(rowNumber)].id;
    list.selectRow(rowNumber);
    if (id == playingCapsuleId)
    {
        if (toggleIfPlaying)
        {
            ++previewGeneration;
            pendingPreviewId.clear();
            playingCapsuleId.clear();
            completedPreviewId.clear();
            audioProcessor.stopPreview();
        }
        else
            audioProcessor.playPreview(normalizedStart);
        list.repaint();
        return;
    }
    if (id == pendingPreviewId)
    {
        if (toggleIfPlaying)
        {
            ++previewGeneration;
            pendingPreviewId.clear();
        }
        else
            pendingPreviewStart = normalizedStart;
        return;
    }

    const auto generation = ++previewGeneration;
    audioProcessor.stopPreview();
    playingCapsuleId.clear();
    completedPreviewId.clear();
    pendingPreviewId = id;
    pendingPreviewStart = normalizedStart;
    list.repaint();
    const juce::File capsule(rows[static_cast<size_t>(rowNumber)].capsulePath);
    if (audioProcessor.loadPreviewFile(capsule, false))
    {
        pendingPreviewId.clear();
        playingCapsuleId = id;
        audioProcessor.playPreview(pendingPreviewStart);
        list.repaint();
        return;
    }
    juce::Component::SafePointer<SoundCapsuleAudioProcessorEditor> safe(this);
    requestPool.addJob([safe, capsule, id, generation] {
        const auto loaded = safe != nullptr && !safe->shuttingDown.load()
                         && safe->audioProcessor.preloadPreviewFile(capsule);
        juce::MessageManager::callAsync([safe, capsule, id, generation, loaded] {
            if (safe == nullptr || generation != safe->previewGeneration
                || id != safe->pendingPreviewId)
                return;
            safe->pendingPreviewId.clear();
            if (loaded && safe->audioProcessor.loadPreviewFile(capsule, false))
            {
                safe->playingCapsuleId = id;
                safe->audioProcessor.playPreview(safe->pendingPreviewStart);
                safe->list.repaint();
            }
            else
                safe->status.setText("Could not read preview", juce::dontSendNotification);
        });
    });
}

void SoundCapsuleAudioProcessorEditor::importCapsule(const juce::String& id,
                                                      ImportMode mode)
{
    juce::Component::SafePointer<SoundCapsuleAudioProcessorEditor> safe(this);
    runAfterProjectSaved([safe, id, mode] {
        if (safe == nullptr) return;
        const auto destination = mode == ImportMode::newPattern
                               ? juce::String("new_pattern")
                               : (mode == ImportMode::overrideSelection
                                      ? juce::String("override_selection")
                                      : juce::String("current_pattern"));
        const auto helperMode = mode == ImportMode::overrideSelection ? "override" : "append";
        safe->importOperationId = juce::Uuid().toString();
        safe->importOverlayHideAt = 0;
        safe->lastImportProgressPollAt = 0;
        safe->importProgress.begin("Preparing the project");
        safe->resized();
        safe->sendCommand(
            "import", object({{"id", id},
                              {"mode", helperMode},
                              {"import_destination", destination},
                              {"operation_id", safe->importOperationId},
                              {"open", true},
                              {"in_place", true}}),
            [safe, mode](juce::var response) {
                if (safe == nullptr) return;
                const auto confirmed = static_cast<bool>(response.getProperty("reload_confirmed", false));
                safe->importProgress.finish(
                    true,
                    confirmed ? "FL Studio reopened the updated project"
                              : "Project updated; verify that FL Studio reopened it");
                safe->importOverlayHideAt = juce::Time::getMillisecondCounter() + 1100;
                safe->importOperationId.clear();
                safe->importProgressPollInFlight.store(false);
                safe->status.setText(
                    confirmed
                        ? (mode == ImportMode::overrideSelection
                               ? "Overridden and reloaded" : "Imported and reloaded")
                        : "Project updated; verify FL reloaded it",
                    juce::dontSendNotification);
                safe->refreshSessionStatus();
                safe->refreshLibrary();
            },
            120000,
            false,
            [safe](const juce::String& error) {
                if (safe == nullptr) return;
                safe->importProgress.finish(false, error);
                safe->importOverlayHideAt = juce::Time::getMillisecondCounter() + 3000;
                safe->importOperationId.clear();
                safe->importProgressPollInFlight.store(false);
            });
    });
}

void SoundCapsuleAudioProcessorEditor::showImportMenu(
    const juce::String& id, juce::Point<int> screenPosition)
{
    juce::PopupMenu menu;
    addDarkMenuSection(menu, "Import to...");
    menu.addItem(1, "Current pattern");
    menu.addItem(2, "New pattern");
    menu.addItem(3, "Override selection");
    juce::Component::SafePointer<SoundCapsuleAudioProcessorEditor> safe(this);
    const auto target = juce::Rectangle<int>(screenPosition.x, screenPosition.y, 1, 1);
    menu.showMenuAsync(juce::PopupMenu::Options().withTargetComponent(&list)
                                                 .withTargetScreenArea(target),
                       [safe, id](int result) {
        if (safe == nullptr) return;
        if (result == 1) safe->importCapsule(id, ImportMode::currentPattern);
        else if (result == 2) safe->importCapsule(id, ImportMode::newPattern);
        else if (result == 3) safe->importCapsule(id, ImportMode::overrideSelection);
    });
}

void SoundCapsuleAudioProcessorEditor::pollImportProgress()
{
    if (importOperationId.isEmpty() || importProgressPollInFlight.exchange(true))
        return;
    const auto operationId = importOperationId;
    juce::Component::SafePointer<SoundCapsuleAudioProcessorEditor> safe(this);
    sendCommand(
        "import_status", object({{"operation_id", operationId}}),
        [safe, operationId](juce::var response) {
            if (safe == nullptr) return;
            safe->importProgressPollInFlight.store(false);
            if (operationId != safe->importOperationId)
                return;
            const auto value = static_cast<int>(response.getProperty("progress", 0));
            const auto step = response.getProperty("step", "Importing").toString();
            safe->importProgress.update(static_cast<double>(value) / 100.0, step);
        },
        5000,
        true,
        [safe](const juce::String&) {
            if (safe != nullptr)
                safe->importProgressPollInFlight.store(false);
        });
}

void SoundCapsuleAudioProcessorEditor::showRowMenu(int rowNumber, juce::Point<int> screenPosition)
{
    if (!juce::isPositiveAndBelow(rowNumber, static_cast<int>(rows.size())))
        return;
    list.selectRow(rowNumber);
    const auto& row = rows[static_cast<size_t>(rowNumber)];
    const auto id = row.id;
    const auto name = row.name;
    const auto currentTags = row.tags;
    const auto capsulePath = row.capsulePath;

    juce::PopupMenu menu;
    menu.addItem(10, "Rename");
    menu.addItem(11, "Edit tags");
    menu.addItem(12, "Show File");
    menu.addItem(14, "Export...");
    addDarkMenuSection(menu, "Import to...");
    menu.addItem(1, "Current pattern");
    menu.addItem(2, "New pattern");
    menu.addItem(3, "Override selection");
    menu.addSeparator();
    juce::PopupMenu::Item deleteItem("Delete");
    deleteItem.itemID = 13;
    deleteItem.colour = juce::Colour(0xffff5c5c);
    menu.addItem(std::move(deleteItem));

    juce::Component::SafePointer<SoundCapsuleAudioProcessorEditor> safe(this);
    const auto target = juce::Rectangle<int>(screenPosition.x, screenPosition.y, 1, 1);
    menu.showMenuAsync(juce::PopupMenu::Options().withTargetComponent(&list)
                                                 .withTargetScreenArea(target),
                       [safe, id, name, currentTags, capsulePath](int result) {
        if (safe == nullptr) return;
        if (result == 1) safe->importCapsule(id, ImportMode::currentPattern);
        else if (result == 2) safe->importCapsule(id, ImportMode::newPattern);
        else if (result == 3) safe->importCapsule(id, ImportMode::overrideSelection);
        else if (result == 10) safe->promptRename(id, name);
        else if (result == 11) safe->promptTags(id, currentTags);
        else if (result == 12)
        {
            const juce::File capsuleFile(capsulePath);
            if (capsuleFile.existsAsFile())
            {
                capsuleFile.revealToUser();
                safe->status.setText("Showing capsule file", juce::dontSendNotification);
            }
            else
                safe->status.setText("Capsule file was not found", juce::dontSendNotification);
        }
        else if (result == 14) safe->exportCapsule(capsulePath, name);
        else if (result == 13) safe->confirmDelete(id, name);
    });
}

void SoundCapsuleAudioProcessorEditor::exportCapsule(
    const juce::String& path, const juce::String& name)
{
    const juce::File source(path);
    if (!source.existsAsFile())
    {
        status.setText("Capsule file was not found", juce::dontSendNotification);
        return;
    }

    auto filename = juce::File::createLegalFileName(name.trim());
    if (filename.isEmpty())
        filename = "Sound Capsule";
    filename = juce::File(filename).withFileExtension("flcapsule").getFileName();
    const auto initial = juce::File::getSpecialLocation(
        juce::File::userDocumentsDirectory).getChildFile(filename);
    exportChooser = std::make_unique<juce::FileChooser>(
        "Export Sound Capsule", initial, "*.flcapsule", true, false, this);
    juce::Component::SafePointer<SoundCapsuleAudioProcessorEditor> safe(this);
    exportChooser->launchAsync(
        juce::FileBrowserComponent::saveMode
            | juce::FileBrowserComponent::canSelectFiles
            | juce::FileBrowserComponent::warnAboutOverwriting,
        [safe, source](const juce::FileChooser& chooser) {
            if (safe == nullptr)
                return;
            auto destination = chooser.getResult();
            if (destination == juce::File())
                return;
            destination = destination.withFileExtension("flcapsule");
            safe->copyCapsuleForExport(source, destination);
        });
}

void SoundCapsuleAudioProcessorEditor::copyCapsuleForExport(
    const juce::File& source, const juce::File& destination)
{
    setBusy("Exporting capsule...");
    ++requestsInFlight;
    juce::Component::SafePointer<SoundCapsuleAudioProcessorEditor> safe(this);
    requestPool.addJob([safe, source, destination] {
        auto succeeded = source == destination;
        if (!succeeded && safe != nullptr && !safe->shuttingDown.load())
        {
            juce::TemporaryFile temporary(destination);
            succeeded = source.copyFileTo(temporary.getFile())
                     && temporary.overwriteTargetFileWithTemporary();
        }
        juce::MessageManager::callAsync([safe, succeeded, destination] {
            if (safe == nullptr)
                return;
            --safe->requestsInFlight;
            safe->status.setText(
                succeeded ? "Exported " + destination.getFileName()
                          : "Could not export capsule to " + destination.getFullPathName(),
                juce::dontSendNotification);
        });
    });
}

void SoundCapsuleAudioProcessorEditor::addExternalCapsules(
    const juce::StringArray& files)
{
    if (files.isEmpty())
        return;
    juce::Array<juce::var> paths;
    for (const auto& path : files)
        paths.add(path);
    juce::Component::SafePointer<SoundCapsuleAudioProcessorEditor> safe(this);
    sendCommand(
        "add_capsules", object({{"paths", paths}}),
        [safe](juce::var response) {
            if (safe != nullptr)
                safe->showAddCapsulesResult(response);
        },
        120000);
    setBusy(files.size() == 1 ? "Adding shared capsule..."
                              : "Adding " + juce::String(files.size()) + " shared capsules...");
}

void SoundCapsuleAudioProcessorEditor::showAddCapsulesResult(const juce::var& response)
{
    const auto importedValue = response.getProperty("imported", juce::var());
    const auto skippedValue = response.getProperty("skipped", juce::var());
    const auto failedValue = response.getProperty("failed", juce::var());
    const auto* imported = importedValue.getArray();
    const auto* skipped = skippedValue.getArray();
    const auto* failed = failedValue.getArray();
    const auto importedCount = imported != nullptr ? imported->size() : 0;
    const auto skippedCount = skipped != nullptr ? skipped->size() : 0;
    const auto failedCount = failed != nullptr ? failed->size() : 0;

    status.setText(
        juce::String(importedCount) + (importedCount == 1 ? " capsule added"
                                                          : " capsules added"),
        juce::dontSendNotification);
    if (importedCount > 0)
        refreshLibrary();
    if (skippedCount == 0 && failedCount == 0)
        return;

    juce::String details;
    details << importedCount << " added, " << skippedCount << " skipped, "
            << failedCount << " failed.\n\n";
    auto issueCount = 0;
    const auto appendIssues = [&details, &issueCount](
                                  const juce::Array<juce::var>* issues,
                                  const juce::Identifier& detailProperty) {
        if (issues == nullptr)
            return;
        for (const auto& issue : *issues)
        {
            if (issueCount >= 12)
                break;
            const auto source = issue.getProperty("source", "").toString();
            details << juce::File(source).getFileName() << ": "
                    << issue.getProperty(detailProperty, "Unknown error").toString()
                    << "\n";
            ++issueCount;
        }
    };
    appendIssues(skipped, juce::Identifier("reason"));
    appendIssues(failed, juce::Identifier("error"));
    if (skippedCount + failedCount > issueCount)
        details << "...and " << skippedCount + failedCount - issueCount << " more";

    juce::AlertWindow::showMessageBoxAsync(
        juce::MessageBoxIconType::WarningIcon,
        importedCount > 0 ? "Some capsules were not added"
                          : "Capsules could not be added",
        details.trimEnd(), "OK", this);
}

bool SoundCapsuleAudioProcessorEditor::isLibraryCapsuleFile(
    const juce::String& path) const
{
    const juce::File candidate(path);
    for (const auto& row : rows)
        if (candidate == juce::File(row.capsulePath))
            return true;
    return false;
}

void SoundCapsuleAudioProcessorEditor::promptRename(const juce::String& id,
                                                     const juce::String& currentName)
{
    auto* dialog = new juce::AlertWindow("Rename capsule", "", juce::MessageBoxIconType::NoIcon, this);
    dialog->addTextEditor("value", currentName, "Name:");
    dialog->addButton("Rename", 1, juce::KeyPress(juce::KeyPress::returnKey));
    dialog->addButton("Cancel", 0, juce::KeyPress(juce::KeyPress::escapeKey));
    juce::Component::SafePointer<SoundCapsuleAudioProcessorEditor> safe(this);
    dialog->enterModalState(true, juce::ModalCallbackFunction::create(
        [safe, id, dialog](int result) {
            if (safe == nullptr || result != 1) return;
            const auto newName = dialog->getTextEditorContents("value").trim();
            if (newName.isNotEmpty())
                safe->sendCommand("rename", object({{"id", id}, {"name", newName}}),
                                  [safe](juce::var) { if (safe != nullptr) safe->refreshLibrary(); });
        }), true);
    if (auto* editor = dialog->getTextEditor("value"))
        editor->selectAll();
}

void SoundCapsuleAudioProcessorEditor::promptTags(const juce::String& id,
                                                   const juce::String& currentTags)
{
    auto* dialog = new juce::AlertWindow("Edit tags", "Separate tags with commas.",
                                          juce::MessageBoxIconType::NoIcon, this);
    dialog->addTextEditor("value", currentTags, "Tags:");
    dialog->addButton("Save", 1, juce::KeyPress(juce::KeyPress::returnKey));
    dialog->addButton("Cancel", 0, juce::KeyPress(juce::KeyPress::escapeKey));
    juce::Component::SafePointer<SoundCapsuleAudioProcessorEditor> safe(this);
    dialog->enterModalState(true, juce::ModalCallbackFunction::create(
        [safe, id, dialog](int result) {
            if (safe == nullptr || result != 1) return;
            juce::Array<juce::var> values;
            for (auto item : juce::StringArray::fromTokens(dialog->getTextEditorContents("value"), ",", ""))
                if (item.trim().isNotEmpty()) values.add(item.trim());
            safe->sendCommand("tags", object({{"id", id}, {"tags", values}}),
                              [safe](juce::var) { if (safe != nullptr) safe->refreshLibrary(); });
        }), true);
    if (auto* editor = dialog->getTextEditor("value"))
        editor->selectAll();
}

void SoundCapsuleAudioProcessorEditor::confirmDelete(const juce::String& id,
                                                      const juce::String& name)
{
    juce::Component::SafePointer<SoundCapsuleAudioProcessorEditor> safe(this);
    juce::AlertWindow::showAsync(
        juce::MessageBoxOptions::makeOptionsOkCancel(juce::MessageBoxIconType::WarningIcon,
                                                      "Delete capsule", "Delete \"" + name + "\"?",
                                                      "Delete", "Cancel", this),
        [safe, id](int result) {
            if (result == 1 && safe != nullptr)
                safe->sendCommand("delete", object({{"id", id}}),
                                  [safe](juce::var) { if (safe != nullptr) safe->refreshLibrary(); });
        });
}

void SoundCapsuleAudioProcessorEditor::sendCommand(const juce::String& command,
                                                    const juce::var& arguments,
                                                    std::function<void(juce::var)> completionCallback,
                                                    int timeoutMs,
                                                    bool quiet,
                                                    std::function<void(const juce::String&)> errorCallback)
{
    if (!quiet)
        setBusy("Working...");
    ++requestsInFlight;
    juce::Component::SafePointer<SoundCapsuleAudioProcessorEditor> safe(this);
    requestPool.addJob([safe, command, arguments, timeoutMs, quiet,
                        backgroundCompletion = std::move(completionCallback),
                        backgroundError = std::move(errorCallback)]() mutable {
        juce::var result;
        juce::String error;
        try
        {
            if (safe == nullptr || safe->shuttingDown.load())
                return;
            result = HelperClient().request(command, arguments, &safe->shuttingDown, timeoutMs);
        }
        catch (const std::exception& exception) { error = exception.what(); }
        juce::MessageManager::callAsync(
            [safe, result, error, quiet,
             messageCompletion = std::move(backgroundCompletion),
             messageError = std::move(backgroundError)]() mutable {
            if (safe == nullptr)
                return;
            --safe->requestsInFlight;
            if (error.isNotEmpty())
            {
                if (messageError)
                    messageError(error);
                if (quiet)
                {
                    safe->connectionStatus.setText(
                        "FL Studio is not connected. Open Setup to repair the connection.",
                        juce::dontSendNotification);
                    safe->connectionStatus.setColour(juce::Label::textColourId, juce::Colours::orange);
                    safe->connectionStatus.setTooltip(error);
                    safe->connectionStatus.setVisible(true);
                    safe->connectionSetup.setVisible(true);
                    safe->projectStatus.setText("Project: Unknown", juce::dontSendNotification);
                    safe->patternStatus.setText("Pattern: Unknown", juce::dontSendNotification);
                    safe->status.setText("Waiting for FL Studio", juce::dontSendNotification);
                    safe->capsuleName.setVisible(false);
                    safe->tagsInput.setVisible(false);
                    safe->saveGroup.setVisible(false);
                    safe->saveIndividual.setVisible(false);
                    safe->undoImport.setVisible(false);
                    safe->resized();
                }
                else
                    safe->status.setText(error, juce::dontSendNotification);
                return;
            }
            if (messageCompletion)
                messageCompletion(result);
            else
                safe->status.setText("Done", juce::dontSendNotification);
            });
    });
}

void SoundCapsuleAudioProcessorEditor::setBusy(const juce::String& message)
{
    status.setText(message, juce::dontSendNotification);
}

juce::var SoundCapsuleAudioProcessorEditor::object(
    std::initializer_list<std::pair<juce::Identifier, juce::var>> values)
{
    auto result = std::make_unique<juce::DynamicObject>();
    for (const auto& [key, value] : values)
        result->setProperty(key, value);
    return juce::var(result.release());
}
