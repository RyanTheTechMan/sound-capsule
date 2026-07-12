# FL Studio Sound Capsule

Sound Capsule saves the selected FL Studio generator channels, their notes in
the active pattern, and an automatically rendered audio preview as one portable
`.flcapsule` file. All selected channels are grouped by default; **Import
individually** creates one capsule per selected channel.

The standalone app is the primary UI. **A VST is not required.**

## How it works

There are three coordinated pieces:

- The standalone Sound Capsule app provides the library, preview, capture,
  project-import, and Undo controls.
- A documented FL MIDI controller script reports the selected global Channel
  Rack indexes, current pattern, PPQ, title, dirty state, and project-load
  acknowledgments. During its idle callback it reads short-lived, atomic Save
  requests from the local bridge.
- A local Python helper stages and parses the saved FLP, preserves opaque
  channel/plugin state, asks FL Studio's command-line renderer for the isolated
  preview, and performs checked imports.

There is no mouse, keyboard, menu, or window automation. FL's documented Save
command is triggered from the controller script via the
[MIDI scripting API](https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/html/midi_scripting.htm).
FL's normal Save dialog can therefore appear for a project that has never been
saved.

## User workflow

### Import from FL into the library

1. Select one or more generator channels and choose the pattern to capture.
2. For one channel, click **Import**. With multiple channels selected, click
   **Import selected** (the default grouped behavior) or **Import individually**.
   The name defaults to the selected channel name; grouped selections join
   their channel names, and you can still type a custom name and comma-separated
   tags. Selecting existing library rows never changes these import fields.
3. If FL reports unsaved changes, Sound Capsule asks for confirmation, invokes
   FL's Save command, and waits until FL reports a clean project. With no MIDI
   control port available, the app waits for you to save manually instead. If
   the project is already clean, capture begins immediately without saving it
   again.
4. The helper identifies the exact FLP from FL's recent-project data and the
   file modified by that Save, remembers its path, and copies it to private
   staging. It creates an isolated copy with only the selected channels enabled
   and only their active-pattern notes, routes those channels directly to
   Master, and renders a WAV through FL Studio.
5. Channel state, notes, embedded Sampler audio, preview, manifest, and
   checksums are written to the capsule. The open project is not changed by
   capture.

### Load a capsule into FL Studio

1. Click the inline **+** to use the configured default, initially **Current
   pattern**. Right-click it or open the row's three-dot menu to choose Current
   pattern, New pattern, or Override selection.
2. If the project is dirty, the same confirmed Save handshake runs first.
3. Before mutation, Sound Capsule writes and validates a dedicated backup.
4. Current pattern adds channels and MIDI to the open pattern while preserving
   its existing notes. New pattern creates a named pattern. Override selection
   requires the same number of selected destination channels and preserves
   their mixer destinations.
5. The main FLP is replaced atomically, validated, and opened through the OS.
   A modal progress card remains above the app through each real helper phase
   and reports whether FL's MIDI script confirmed the reload.

**Undo import** appears only after an import and remains available for a
configurable recovery window (10 minutes by default). It can restore the exact
pre-import backup even after you have edited and saved the project during that
window. Sound Capsule asks for confirmation and creates a second safety backup
of the current FLP before restoring, so those newer edits remain recoverable.
Undo Import appears in the header immediately left of Volume, leaving the
bottom import fields unobstructed. After a restore, the button disappears and
does not fall back to an older
import. When the window expires, the button also disappears and the helper
rejects the restore. Backups go to the project's configured data folder when usable,
otherwise `Projects/Backup/Sound Capsule` or a sibling `Backups/Sound Capsule`
folder.

The CLI retains a safer versioned-import default. Pass `--in-place` to request
the app's backup-and-replace behavior.

## Install and first-time setup

[uv](https://docs.astral.sh/uv/) is required. It supplies a managed Python 3.12
when the computer does not already have a compatible interpreter. From the
source or development package:

```sh
uv run --python 3.12 scripts/install.py
```

There is no install-location or project-folder prompt. The app uses the
standard per-user application location. Projects may live anywhere: Sound
Capsule reads FL's recent-project list, confirms the file changed by Save, and
caches that exact path.

Then:

1. Launch **Sound Capsule** once. The gear icon opens Settings for the Undo
   duration, default import destination, volume readout, and Mono/Stereo
   waveform preference.
   Use **FL Setup** whenever you want the
   guided FL Studio connection steps; its button sits at the top-right of
   Settings, and opening mode is not stored by the app.
2. To optionally open Sound Capsule with FL, open **Options → File settings → External tools**,
   select an empty row, choose the installed Sound Capsule app, name it Sound
   Capsule, and enable **Launch at startup**. The setup dialog copies the app
   path to the clipboard. Sound Capsule does not edit FL's private settings.
3. On macOS, launching the app creates a virtual MIDI source named **Sound
   Capsule Control**. In FL Studio, open **Options → MIDI Settings**. Enable the **Sound Capsule
   Control** input and choose **Sound Capsule (user)** as its Controller type.
4. Keep the standalone app open while using the library.

The standalone app is output-only: it opens no microphone or audio-interface
input because it uses audio solely for capsule previews. The optional VST3
keeps stereo input so it remains transparent when inserted on FL's Master.

Three status blocks show the detected project, active pattern, and current
operation. Hover **Project** to see the full FLP path. A connection block is not
shown during normal operation; if the bridge is missing or stale, an orange
warning banner appears with an **Open Setup** action.

Each library row has inline play/stop, favorite, Import, and three-dot controls.
Left-click Import uses the configured default, which starts as **Current
pattern**. Right-click Import—or use the three-dot menu—to choose **Current
pattern**, **New pattern**, or **Override selection** for that operation.
The three-dot menu keeps Rename, Edit tags, and Show File first, followed by a
full-width dark **Import to...** section strip and the three import choices.
Current pattern adds the capsule instruments and MIDI without replacing notes
already in the open pattern; New pattern retains the original named-pattern
behavior. Override selection retains destination routing and requires matching
channel counts.
The three-dot menu includes **Show File** to reveal the portable `.flcapsule`
in Finder or Explorer.
Favorites do not change the library order; use the **Favorites** filter to show
only starred capsules. Sorting defaults to newest **Recently added** first and
can switch to **Name** or successful import **Uses**. The adjacent arrow flips
ascending/descending direction independently for each sort mode.
Tags render as compact chips. Clicking a tag toggles it in the comma-separated
search field, and active tag filters fill with the accent color. The top-right
Volume follows a -60 dB to 0 dB perceptual taper and is restored with app/plugin
state. Settings can display it as 0–100% or as dB, with `0.0 dB` at maximum and
`−∞ dB` at mute. The gear icon sits to its right. Name, Tags,
and library Import share the wider bottom row; operational messages use the
third status card.
The row can show its audio waveform, a compact active-pattern MIDI preview, or
both side by side. The waveform and MIDI icon buttons show or hide each view.
The adjacent Loop control is enabled by default, changes the active preview
immediately, and persists with app/plugin state.
Grouped capsules draw each channel's notes with a different grayscale while
idle. During playback, channel one retains green and additional channels use
distinct colors in the played region. A naturally completed preview remains
fully colored until another entry starts or Stop is pressed.
Waveforms default to a full-height mono overlay; Mono/Stereo is stored in
**Settings**, and right-clicking either a visible waveform or the waveform
toggle switches it immediately. The toggle draws one waveform for mono and two
stacked waveforms for stereo.
Click anywhere in either preview to start playback from
that time. White preview content fills green from left to right during playback.
The embedded WAV is read directly from `.flcapsule`; there is no permanent
sidecar WAV. Only visible rows plus a one-row scroll buffer are decoded into
memory, so visible Play actions are warm without loading the entire library.
Preview progress redraws only the active row at 60 FPS. Preloading uses a
separate low-impact worker, and new capsules store the already-rendered PCM
preview without ZIP deflation to reduce cold-start work. Playback controls
detach the transport without JUCE's blocking stop timeout, and cache hits reuse
the decoded buffer without making another full audio copy.
MIDI is scaled to the captured full pattern length, including trailing empty
bars, so it stays aligned with the rendered audio. FL 25.2's reported pattern
length is converted from Channel Rack steps (four per quarter-note beat) into
FLP PPQ ticks before drawing or seeking. All three project-import modes, Rename,
Edit tags, and Delete are in the three-dot menu; Delete is highlighted red.
Project imports display a modal, front-and-center progress card driven by real
helper phases: project staging, capsule verification, state restoration, merge,
backup, validation, reopen, and FL reconnection. It remains visible while FL
Studio closes and reloads.
Unselected rows receive a very light hover tint. The play, favorite, Import,
and three-dot targets use localized accent hover feedback and a pointing cursor;
seekable waveform/MIDI regions also use the pointing cursor.

Windows does not provide JUCE applications a native virtual MIDI-device API.
If there is no available MIDI input to host the controller script, create a
loopback input named **Sound Capsule Control** (or set
`SOUNDCAPSULE_MIDI_OUTPUT` to another port name), then assign the script to that
input in FL. Save requests themselves use the local file bridge, not MIDI.

Nothing from Sound Capsule starts at system login. The standalone app starts
its local helper when it opens and stops it when the app exits. The helper
listens only on `127.0.0.1`. The optional VST3 can show the same library inside FL and play
previews through Master, but it is not used to save or render capsules:

```sh
uv run --python 3.12 scripts/install.py --with-vst
```

## Compatibility and safety

FLP mutation is allowlisted to the exact tested project-format version
`25.2.5.5055`. Unknown builds remain library/inspection-only. The event parser
round-trips unmodified files byte-for-byte, preserves unknown events, and
rejects unprofiled channel events instead of guessing.

Capsules reject unsafe ZIP paths, duplicate members, missing or incorrect
checksums, unsupported schemas, and projects from a newer FL format. Writes use
private staging, checksums, validation, fsync, atomic rename/link operations,
and an append-only transaction journal.

Version one captures generator channels and the active pattern. It intentionally
excludes automation channels, Layers, Playlist placement, mixer effects,
sends, sidechains, and complete routing graphs. Third-party libraries remain
external and must be installed on the destination computer.

## Build and test

The helper has no third-party runtime dependencies:

```sh
uv run --python 3.12 --project helper python -m unittest discover -s helper/tests -v
```

The standalone app and optional VST3 require CMake 3.22+, Git, and a C++20
compiler. The open-source build fetches JUCE 8.0.9:

```sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release \
  -DSOUNDCAPSULE_BUILD_PLUGIN=ON
cmake --build build --config Release -j 4
```

macOS builds arm64+x86_64 by default. Development bundles are ad-hoc signed;
distribution still requires an Apple Developer ID signature and notarization.

See [docs/VALIDATION.md](docs/VALIDATION.md) for the automated and host-level
acceptance gates.

## Capsule contents

```text
manifest.json
channels/000.fst
notes/000.bin
preview.wav
assets/...            # embedded FL Sampler sources, when available
checksums.json
```

Each indexed capsule also has a derived WAV sidecar, so the library folder can
be added to FL Studio's Browser for native previewing.

## License

Sound Capsule is AGPL-3.0-only so the JUCE targets can use JUCE's open-source
license. Capsule files, FL projects, presets, samples, and the user's library
remain user data, not project source code. See `LICENSE` and
`THIRD_PARTY_NOTICES.md`.
