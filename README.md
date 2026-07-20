# Sound Capsule

Sound Capsule lets you save an FL Studio sound as a portable `.flcapsule.wav` file.
Each capsule contains the selected generator channel or channels, their notes in
the active pattern, and an audio preview—ready to play in Finder, Explorer, chat
apps, and ordinary audio players, then reuse in another project.

The standalone app is the main way to use Sound Capsule. The optional VST3 can
also display the library inside FL Studio, but it is not required.

## What you can do

- Capture one channel, a group of selected channels, or individual capsules for
  each selected channel.
- Build a searchable library with tags, favorites, MIDI previews, and audio
  playback.
- Import a capsule into the current pattern, a new pattern, or matching selected
  channels.
- Share playable capsules by dragging them out, exporting through a native Save
  dialog, or dropping shared `.flcapsule.wav` files into the app.

## Install

Download the native installer from the
[latest GitHub release](https://github.com/RyanTheTechMan/sound-capsule/releases/latest):

- **macOS 13 or later:** open the signed and notarized `.pkg`. The app is installed
  in `/Applications`.
- **Windows 10/11 x64:** open the `.msi`. The app is installed in Program Files,
  added to the Start Menu, and offers an optional desktop shortcut. The MSI is
  currently unsigned, so Windows may show an unknown-publisher warning.

The installers configure the FL Studio bridge automatically. The optional VST3
can be selected from the installer component list. Native releases include a
self-contained helper, so end users do not need to install Python or uv. First launch
also verifies the helper and FL Studio bridge, repairing setup automatically if an
installer post-action was skipped.

Sound Capsule automatically follows FL Studio's **User data folder**, including when
it has been moved from Documents. On Windows it reads Image-Line's `Shared data`
registry value; on macOS it reads the same value from
`~/Library/Preferences/Image-Line/reg.xml`. It installs the MIDI bridge there and uses
that folder's Browser history and Projects directory. Sound Capsule does not persist a
second setting or create a guessed FL Studio data folder when registry data is missing.

For a manual installation, download and extract the macOS or Windows ZIP, then
open the Sound Capsule application. Its included helper and FL Studio bridge are
configured on first launch. Copy the optional VST3 bundle into the platform's VST3
directory if you also want the in-FL library browser.

## First-time setup

1. Launch **Sound Capsule**.
2. On Windows, install loopMIDI and create or start a virtual port.
3. Open **Settings**, select **FL Setup**, and choose any listed loopMIDI port.
4. In FL Studio, add Sound Capsule as an external tool if you want it to open
   alongside FL. Then open **Options → MIDI Settings**. Under Input, enable the
   chosen port and select **Sound Capsule (user)** as its Controller type. Under
   Output, enable the same port.
5. Keep the standalone app open while you use the library.

See [Windows MIDI setup](docs/WINDOWS_MIDI_SETUP.md) for the full loopMIDI steps.

In **Settings**, **Capsule save location** chooses where `.flcapsule.wav` files are
stored without moving Sound Capsule's program settings or cache. When changing
locations, you can merge the current library into the new folder or leave the
existing files where they are. Files with the same relative path are not
overwritten and can be revealed from the result message.

## Save a capsule

1. In FL Studio, select the generator channel or channels you want to save and
   choose the pattern to capture. You may also select Automation Clip channels
   in the Channel Rack; select each clip's target generator channel as well.
2. In Sound Capsule, enter a name and optional comma-separated tags.
3. Click **Save capsule** for one channel, **Save selected** for a grouped capsule,
   or **Save individually** to create one capsule per selected channel.
4. Sound Capsule saves the capsule to its library and creates an audio preview.

## Use a capsule

1. Find a capsule in the library. Use search, tags, favorites, and the preview
   controls to choose the sound you want.
   MIDI previews preserve any opening rest but trim unused pattern space after
   the final note. Their playback indicator and click-to-seek timeline can
   therefore finish before the full audio waveform.
2. Click its **Import** button to add it to the configured destination.
3. Right-click **Import**, or use the three-dot menu, to choose a destination
   for this import:

   - **Current pattern** adds the capsule’s channels and notes to the open
     pattern.
   - **New pattern** creates a pattern for the capsule.
   - **Override selection** replaces matching selected channels while keeping
     their mixer destinations.

Selected Automation Clips that target captured Channel Rack channels are saved
with their current-arrangement Playlist instances. On import, those instances
are recreated from the playhead. Mixer, effect, and global-control automation is
not yet supported.

Sound Capsule creates a backup before changing a project. **Undo Import** is
available for the recovery period configured in Settings.

## Share a capsule

- Drag a library row from its name/details area to Finder, Explorer, another
  folder, or an app such as Discord. The same file can play as WAV audio and
  still imports with its MIDI and channel information intact.
- Open a row's three-dot menu and choose **Export...** to copy it with the native
  Save dialog.
- Drop one or more `.flcapsule.wav` files anywhere on the Sound Capsule window to
  validate and add them. Valid files in a mixed batch are added while corrupt,
  unsupported, or duplicate capsules are reported and skipped.

Older ZIP-based `.flcapsule` files remain supported. The app upgrades verified
legacy files in the library to playable `.flcapsule.wav` files without changing
their IDs or musical contents; a legacy source is removed only after its
replacement verifies successfully.

The capsule data lives in a private `SCAP` chunk after the WAV audio. Copying or
downloading the original file preserves it, but trimming or re-exporting the file
through an audio editor may remove the capsule data and leave ordinary audio.
The file is intentionally not a generic ZIP archive; use Sound Capsule to inspect
or import its embedded contents.

## What’s included

Sound Capsule captures generator channels, their active-pattern notes, and
explicitly selected Automation Clips targeting those channels. Third-party
plug-ins, sample libraries, and other dependencies must be installed on the
computer where you import the capsule.

## Contributing

Building from source requires CMake 3.22+, Git, a C++20 compiler, and uv.

```sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release \
  -DSOUNDCAPSULE_BUILD_PLUGIN=ON
cmake --build build --config Release -j 4
```

Run the helper test suite with:

```sh
uv run --python 3.12 --project helper python -m unittest discover -s helper/tests -v
```

## Releases

The manual GitHub workflow builds ZIP downloads, a Windows x64 MSI, and a universal
macOS PKG. It signs and notarizes the macOS app, VST3, and installer before creating
a draft release. Maintainer setup and the required encrypted Apple secrets are documented in
[docs/RELEASING.md](docs/RELEASING.md).

## License

Sound Capsule is licensed under [AGPL-3.0-only](LICENSE). See
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for bundled dependency notices.
