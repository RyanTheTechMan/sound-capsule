# Sound Capsule

Sound Capsule lets you save an FL Studio sound as a portable `.flcapsule` file.
Each capsule contains the selected generator channel or channels, their notes in
the active pattern, and an audio preview—ready to browse and reuse in another
project.

The standalone app is the main way to use Sound Capsule. The optional VST3 can
also display the library inside FL Studio, but it is not required.

## What you can do

- Capture one channel, a group of selected channels, or individual capsules for
  each selected channel.
- Build a searchable library with tags, favorites, MIDI previews, and audio
  playback.
- Import a capsule into the current pattern, a new pattern, or matching selected
  channels.
- Share capsules by dragging them out, exporting through a native Save dialog,
  or dropping shared `.flcapsule` files into the app.

## Install

Download the native installer from the
[latest GitHub release](https://github.com/RyanTheTechMan/sound-capsule/releases/latest):

- **macOS 13 or later:** open the signed and notarized `.pkg`. The app is installed
  in `/Applications`.
- **Windows 10/11 x64:** open the `.msi`. The app is installed in Program Files,
  added to the Start Menu, and offers an optional desktop shortcut. The MSI is
  currently unsigned, so Windows may show an unknown-publisher warning.

The installers configure the FL Studio bridge automatically. The optional VST3
can be selected from the installer component list. If uv is missing, setup explains
that it is required and offers to open the official installation page. Sound Capsule
never downloads or installs uv automatically. After installing uv, launch Sound Capsule
and choose **Retry Setup**.

For a manual installation, download the macOS or Windows ZIP, extract it, open a
terminal in the extracted folder, and run:

```sh
uv run --python 3.12 scripts/install.py --build .
```

[uv](https://docs.astral.sh/uv/) is required; it can provide Python 3.12 when
needed. To install the optional VST3 too, add `--with-vst`:

```sh
uv run --python 3.12 scripts/install.py --build . --with-vst
```

## First-time setup

1. Launch **Sound Capsule**.
2. On Windows, follow the loopMIDI setup screen. Sound Capsule detects an
   installed copy separately from its available ports; it offers to open an
   installed copy or links to the official website otherwise. Other enumerated
   virtual MIDI cables can also be selected.
3. Open **Settings** with the gear icon, then select **FL Setup** to configure
   the MIDI port or review the guided connection steps.
4. In FL Studio, add Sound Capsule as an external tool if you want it to open
   alongside FL. Then open **Options → MIDI Settings**, enable **Sound Capsule
   MIDI** (or a preserved legacy **Sound Capsule Control** port), and choose
   **Sound Capsule (user)** as its Controller type.
5. Keep the standalone app open while you use the library.

See [Windows MIDI setup](docs/WINDOWS_MIDI_SETUP.md) for the full loopMIDI steps.

In **Settings**, **Capsule save location** chooses where `.flcapsule` files are
stored without moving Sound Capsule's program settings or cache. When changing
locations, you can merge the current library into the new folder or leave the
existing files where they are. Files with the same relative path are not
overwritten and can be revealed from the result message.

## Save a capsule

1. In FL Studio, select the generator channel or channels you want to save and
   choose the pattern to capture.
2. In Sound Capsule, enter a name and optional comma-separated tags.
3. Click **Import** for one channel, **Import selected** for a grouped capsule,
   or **Import individually** to create one capsule per selected channel.
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

Sound Capsule creates a backup before changing a project. **Undo Import** is
available for the recovery period configured in Settings.

## Share a capsule

- Drag a library row from its name/details area to Finder, Explorer, another
  folder, or an app such as Discord. Sound Capsule shares a copy and keeps the
  library file in place.
- Open a row's three-dot menu and choose **Export...** to copy it with the native
  Save dialog.
- Drop one or more `.flcapsule` files anywhere on the Sound Capsule window to
  validate and add them. Valid files in a mixed batch are added while corrupt,
  unsupported, or duplicate capsules are reported and skipped.

## What’s included

Sound Capsule currently captures generator channels and their active-pattern
notes. Third-party plug-ins, sample libraries, and other dependencies must be
installed on the computer where you import the capsule.

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
