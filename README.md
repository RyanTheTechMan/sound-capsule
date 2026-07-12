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

## Install

Download the macOS or Windows ZIP from the
[latest GitHub release](https://github.com/RyanTheTechMan/sound-capsule/releases/latest),
extract it, open a terminal in the extracted folder, and run:

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
2. Open **Settings** with the gear icon, then select **FL Setup** for the
   guided connection steps.
3. In FL Studio, add Sound Capsule as an external tool if you want it to open
   alongside FL. Then open **Options → MIDI Settings**, enable **Sound Capsule
   Control**, and choose **Sound Capsule (user)** as its Controller type.
4. Keep the standalone app open while you use the library.

On Windows, create a loopback MIDI input named **Sound Capsule Control** if
one is not already available. The FL Setup guide explains the connection.

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

## License

Sound Capsule is licensed under [AGPL-3.0-only](LICENSE). See
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for bundled dependency notices.
