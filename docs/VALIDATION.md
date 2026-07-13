# Compatibility validation

Mutation profiles are exact FL project-format versions, not major/minor
prefixes. A profile is enabled only after automated corpus checks and the FL
host matrix pass on that OS/build.

## Automated checks

Run the synthetic helper suite:

```sh
uv run --python 3.12 --project helper python -m unittest discover -s helper/tests -v
```

It covers lossless parsing, opaque events, exact note properties, grouped and
individual capsule packaging, embedded Sampler assets, ZIP/checksum attacks,
new-pattern and current-pattern append, override, PPQ scaling, isolated preview construction, project lookup,
dirty-state rejection, in-place backup/restore, configurable time-limited Undo,
post-import-change safety backups, expired-restore rejection, library indexing,
favorite filtering, explicit recent/name/usage sorting and usage counters,
validated external capsule ingestion, duplicate-ID skipping, collision-safe
library filenames, partial batch results,
the local JSON protocol, pollable import progress, persisted first-run setup, automatic current-project
resolution, Save-time disambiguation, and non-blocking live-session heartbeats
while current-project discovery is still running. It also verifies chronological
FLP note-event normalization, indexed source-FL version metadata, and the
explicit newer-version try-import path.

Audit every real FLP below a fixture tree without modifying a source file:

```sh
uv run --python 3.12 scripts/validate_fl_corpus.py "/Applications/FL Studio 2025.app"
```

For each supported generator fixture the audit requires byte-identical
unmodified serialization, exact channel counts, fully profiled channel event
ownership, an isolated preview project, structurally valid append, and
structurally valid override.

Build the JUCE targets and validate the VST3 at pluginval strictness 10,
including GUI tests, 44.1/48/96 kHz, and block sizes 64–1024. Verify both app
bundles with the platform's signature tooling. Development macOS bundles are
ad-hoc signed, not notarized.

## FL runtime checks

The current macOS development validation includes a real FL command-line render
of an isolated channel and current pattern from Image-Line's bundled
`NewStuff.flp`. FL Studio 26.1.0.5294 has loaded and rendered both a generated
25.2.5.5055-layout project containing opaque per-channel event 251 and an
isolated 26.1.0.5294-layout channel from `temp2.flp`. The latter validates FL
26's overloaded pre-rack event 64, exact 11-channel detection, byte-identical
round-trip, selected-channel isolation, 12 retained notes, and a non-silent
48 kHz stereo render. Results must pass RIFF/WAVE structure, duration,
sample-rate, channel-count, and finite-sample checks. Silent or malformed
renders are rejected rather than packaged.

FL Studio 25.2.5.5055 also rendered a disposable copy of the reported Pattern 3
channel with every other channel muted. The append-order form rendered silence;
the otherwise identical project with note records stably sorted by position
rendered non-silent audio. This is the regression fixture for Piano Roll notes
that disappeared during zooming or remained silent until individually clicked.

FL Studio 26.1.0.5530 on Windows loaded and rendered a disposable isolated
Pattern 4 FLEX channel from a byte-exact project containing its single zero
event-stream padding byte. The generated 48 kHz stereo WAV was non-silent and
passed the helper's RIFF/WAVE validation.

The following still require explicit interactive host acceptance before a
public release profile is claimed:

1. On Windows, validate **Sound Capsule MIDI** through loopMIDI; preserve
   **Sound Capsule Control** for an upgraded legacy setup. Confirm the selected
   port appears under FL Studio Input and can be assigned to the installed
   controller script without Sound Capsule transmitting musical MIDI. Make a
   clean and dirty save, plus a first save that opens FL's normal Save dialog,
   and confirm the unchanged JSON bridge reports an incremented Save sequence
   and clean state.
2. Capture single, grouped, and individual selections containing FL Sampler,
   3xOsc, FLEX, Sytrus, wrapped VST2/VST3, CLAP, Serum, Kontakt, Unicode names,
   unusual wrapper flags, missing samples, and trial placeholders.
3. Reopen every isolated preview FLP in FL, render it, and confirm only selected
   channels and current-pattern notes sound. Test generators with long release,
   tempo sync, sidechain assumptions, and missing dependencies.
4. Append to both the active pattern and a new pattern at matching and different
   PPQ. Verify plugin state, pattern selection/naming, all note properties,
   preserved active-pattern notes, direct-to-Master routing, and unchanged existing
   channels, mixer, Playlist, and arrangement state.
5. Override an equal-size destination selection. Verify target routing and
   unrelated notes remain unchanged. Reject count mismatches.
6. Exercise the in-place transaction on disposable projects. Verify the backup
   before replacement, atomic main-file write, OS reopen, `PL_LoadOk` reload
   acknowledgment, reopen in the same FL Studio major version that initiated
   the import even when another version is the OS default, exact custom Undo
   during the configured recovery window,
   before-Undo safety backup after later project saves, and expired-Undo
   rejection.
7. Exercise unsaved projects, duplicate titles, nonstandard project locations, project
   data folders, locked files, helper/app termination, render failures,
   corrupted capsules, missing plugins/assets, and newer FL formats. Confirm a
   newer-version capsule has an orange warning on its library row, shows a
   Try-import confirmation, and can still proceed after explicit acceptance.
8. Exercise capsule sharing on macOS and Windows: drag a row to Finder/Explorer,
   another folder, and Discord; confirm the library source remains present. Drop
   single and multiple valid capsules back into the window, then repeat with a
   duplicate and a corrupt capsule in the same batch. Verify the native Export
   dialog's default filename, cancellation, overwrite warning, and saved bytes.
9. Repeat the suite on Windows x64, macOS Intel, and macOS Apple Silicon before
   adding each exact build to `helper/soundcapsule/compatibility.py`.

Do not enable a profile based only on parser success. FL must load and render
the generated fixtures with the expected logical and audible state.

## Release gates

- Windows requires a tested input-device path for hosting the controller
  script; Save commands use the platform-neutral local file bridge.
- macOS/Windows installers must be signed according to platform policy.
- Opening the just-replaced main FLP must produce the MIDI script's documented
  `PL_LoadOk` status; lack of acknowledgment is a warning and not reported as a
  successful reload.
- Source archives and platform packages must have published SHA-256 checksums.
