# Compatibility validation

Sound Capsule attempts narrowly scoped mutation for every FL project version
that passes lossless parsing and structural validation. This is best-effort
compatibility, not a promise that an untested future FLP layout is unchanged.
Capsules warn when their saved FL `major.minor` release is newer than the
destination project's saved release; patch and platform build numbers are
treated as equivalent.

## Automated checks

Run the synthetic helper suite:

```sh
uv run --python 3.12 --project helper python -m unittest discover -s helper/tests -v
```

It covers lossless parsing, opaque events, exact note properties, grouped and
individual capsule packaging, selected Automation Clip multi-target decoding,
filtering, remapping, and playhead placement, automation-aware sanitized
Song-mode previews, selection/playhead phrase resolution, FL 25/26 Playlist
offset decoding, boundary cropping, repeated Pattern placement restoration,
mixer insert and gapped
effect-slot extraction, shared-chain restoration, bypass/mix parameters, pristine
insert allocation, embedded Sampler assets, ZIP/checksum attacks,
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
structurally valid override. Routed generators additionally exercise portable
insert-state extraction, effect-aware preview sanitization, shared-chain
restoration, ascending pristine allocation, and clean insufficient-space failure.

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

Mixer-insert development also restored a real one-slot Fruity Parametric EQ 2
Insert-State extracted from Image-Line's bundled `NewStuff.flp` into disposable
FL 25.2.5.5055- and FL 26.1.0.5294-layout projects. FL Studio 2025 and 2026 each
loaded the generated project, processed the generator through fresh insert 2,
and produced a non-silent WAV. Wrapped third-party effects, shared chains,
disabled/mixed slots, and Master-effect interaction remain part of the explicit
interactive acceptance list below rather than being claimed from that smoke test.

FL Studio 25.2.5.5319 on Windows byte-exactly parsed and rewrote the live
`sound-capsule-2025-test2.flp`, then captured its selected Pattern 1 channel
through the isolated renderer. The original project remained open and the
generated 431,008-byte WAV passed non-silent RIFF/WAVE validation.

The following still require explicit interactive host acceptance before a
release is claimed as host-tested:

1. On Windows, validate **Sound Capsule MIDI** through loopMIDI; preserve
   **Sound Capsule Control** for an upgraded legacy setup. Confirm the selected
   port appears under FL Studio Input and Output, is enabled in both, and can be
   assigned to the installed controller script as an Input without Sound Capsule
   transmitting musical MIDI. Make a clean and dirty save, plus a first save
   that opens FL's normal Save dialog, and confirm the unchanged JSON bridge
   reports an incremented Save sequence and clean state.
2. Capture single, grouped, and individual selections containing FL Sampler,
   3xOsc, FLEX, Sytrus, wrapped VST2/VST3, CLAP, Serum, Kontakt, Unicode names,
   unusual wrapper flags, missing samples, and trial placeholders.
3. Reopen every isolated preview FLP in FL, render it, and confirm only selected
   channels and current-pattern notes sound. With **Save mixer insert** enabled,
   confirm native and wrapped effects remain audible while unscoped sends and
   external I/O are absent; repeat with the option disabled. Test generators with
   long release, tempo sync, sidechain assumptions, and missing dependencies.
4. Append to both the active pattern and a new pattern at matching and different
   PPQ. Verify schema-6 append creates an isolated pattern even when the saved
   preference is **Current pattern**, while schemas 1–5 retain their legacy
   current-pattern behavior. Verify plugin state, pattern selection/naming, all note properties,
   preserved active-pattern notes, fresh restored inserts for saved mixer state,
   direct-to-Master routing for capsules without mixer state, and unchanged
   existing channels, Master, unrelated mixer inserts, Playlist, and arrangement
   state. Include shared inserts, gapped native and wrapped effects, disabled and
   partially mixed slots, and projects with no remaining pristine inserts.
5. Select generator- and effect-targeted Automation Clips in the Channel Rack
   alongside their target generators. Capture an explicit Playlist selection
   with repetitions, gaps, partial Pattern clips, leading space, and automation
   crossing both boundaries. Repeat without a selection at, between, and beyond
   current-Pattern occurrences. Verify the preview ends at the phrase boundary,
   then import into all three destinations at several playheads and PPQs. Cover
   insert volume/pan/stereo/EQ, effect parameters, slot bypass and mix, and shared
   inserts. Confirm unselected and out-of-window automation is excluded; Master,
   global, routing, send, and unrelated targets must produce the omission warning
   and must not appear in the imported project.
   Then select only the generators, enable **Find related automation**, and
   confirm every related clip inside the range is added automatically. Verify
   effect-targeted clips follow **Save mixer insert**, out-of-range clips stay
   excluded and fall back to the playhead, unrelated generators need no range,
   and saving without a range only shows the alert when related placed clips exist.
6. Override an equal-size destination selection. Verify saved chains use fresh
   inserts and reroute only the overridden channels; legacy and toggle-off
   capsules retain target routing. Confirm former inserts and unrelated channels
   using them remain unchanged. Reject count mismatches.
7. Exercise the in-place transaction on disposable projects. Verify the backup
   before replacement, atomic main-file write, OS reopen, `PL_LoadOk` reload
   acknowledgment, reopen in the same FL Studio major version that initiated
   the import even when another version is the OS default, exact custom Undo
   during the configured recovery window,
   before-Undo safety backup after later project saves, and expired-Undo
   rejection.
8. Exercise unsaved projects, duplicate titles, nonstandard project locations, project
   data folders, locked files, helper/app termination, render failures,
   corrupted capsules, missing plugins/assets, and newer FL formats. Confirm a
   newer-version capsule has an orange warning on its library row, shows a
   Try-import confirmation, and can still proceed after explicit acceptance.
9. Exercise capsule sharing on macOS and Windows: drag a row to Finder/Explorer,
   another folder, and Discord; confirm the library source remains present. Drop
   single and multiple valid capsules back into the window, then repeat with a
   duplicate and a corrupt capsule in the same batch. Verify the native Export
   dialog's default filename, cancellation, overwrite warning, and saved bytes.
10. Repeat the suite on Windows x64, macOS Intel, and macOS Apple Silicon before
   claiming a release as host-tested. Untested releases remain available on a
   best-effort basis when their structure passes the same safety checks.

Do not claim host validation based only on parser success. FL must load and
render the generated fixtures with the expected logical and audible state.

## Release gates

- Windows requires a tested input-device path for hosting the controller
  script; Save commands use the platform-neutral local file bridge.
- macOS/Windows installers must be signed according to platform policy.
- Opening the just-replaced main FLP must produce the MIDI script's documented
  `PL_LoadOk` status; lack of acknowledgment is a warning and not reported as a
  successful reload.
- Source archives and platform packages must have published SHA-256 checksums.
