# Changelog

All notable changes to Sound Capsule are recorded here. The project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html), and each released
version's section is also used as its GitHub release notes.

## [Unreleased]

### Added

- Explicitly selected Channel Rack Automation Clips can now be captured with
  their selected generator targets, previewed in Song mode, and imported into
  the current Playlist arrangement at the playhead. Individual saves bundle
  each selected automation clip only with its selected target channel.

### Changed

- Capsule schema 3 stores Automation Clip target bindings and Playlist
  instances while preserving imports of schema 1 and 2 capsules.

### Fixed

- Projects first saved from an untitled FL session now replace FL's blank or
  placeholder title with the discovered `.flp` name. That association survives
  switching to another project and back without overriding a different current
  project that happens to have an identical Channel Rack.
- On macOS, newly saved projects in custom or cloud-backed locations can be
  identified before FL Studio flushes them to its on-disk recent-project lists;
  FL-generated Backup and autosave copies are excluded from consideration.

## [0.3.4] - 2026-07-13

### Changed

- Native Windows and macOS releases now bundle a self-contained helper built in
  GitHub Actions. End users no longer need to install Python or uv, and native
  setup no longer creates a venv or copies the Python source tree.
- The app now runs the packaged helper directly for `setup`, `serve`, and
  `uninstall`. First launch refreshes the FL Studio controller at Image-Line's
  registered user-data folder, so skipped installer post-actions self-repair.

### Fixed

- Windows setup no longer opens a transient PowerShell window or fails while uv
  traverses a junction-backed managed-Python alias.

## [0.3.3] - 2026-07-13

### Added

- Sound Capsule now follows FL Studio's current user-data folder automatically.
- Capsules are now stored and shared as playable `.flcapsule.wav` files. Their
  normal WAV audio contains a verified `SCAP` payload with the MIDI, channel
  states, manifest, and embedded sample assets.
- Verified legacy `.flcapsule` libraries upgrade atomically on startup, while
  legacy imports remain supported and failed conversions leave their sources
  unchanged.
- Capsule rows show a warning icon beside the title when they contain newer FL
  Studio project data than the connected host. Hovering explains the version
  mismatch, and importing requires explicit confirmation while still allowing
  the user to try it.

### Fixed

- Windows setup now resolves its payload and finds `uv` when launched by a GUI
  app or DAW with an empty `HOME`, an unset parameter-time `$PSScriptRoot`, or a
  PATH captured before `uv` was installed, including Astral and winget installs.
- Appended MIDI notes are now written in chronological event order. This keeps
  newly imported notes visible while zooming the Piano Roll and makes them play
  immediately without clicking each note to force FL Studio to rebuild it.
- Starting an import now stops the capsule preview immediately, including when
  loop playback is enabled or the user subsequently cancels a version warning.
- FL Studio 2026 on macOS can capture and import 25.2.5.5055-layout projects
  containing opaque per-channel event 251 without dropping its state.
- FL Studio 26.1 projects no longer treat the overloaded pre-rack event 64 as
  an extra channel, and blank project metadata now resolves against live MIDI
  Channel Rack names instead of silently selecting an unrelated recent FLP.
- Live FL Studio connection checks no longer wait for recent-project discovery;
  untitled FLP matching now runs in the background so large project histories
  cannot cause intermittent false disconnects in either FL Studio 25 or 26.
- Changing the selected Channel Rack channel no longer temporarily resets the
  displayed project to "Unnamed project" when the full rack identity is known,
  and one unsaved rename in an otherwise matching rack still resolves safely.
- Imported and restored projects reopen in the exact FL Studio application that
  published the live MIDI session instead of the operating system's default FL
  version; a missing host identity now fails safely rather than guessing.

## [0.2.1] - 2026-07-12

### Fixed

- Windows MSI helper provisioning no longer passes a quoted setup-directory
  argument ending in a backslash, which caused PowerShell to receive broken
  positional arguments after installation.
- The Windows helper bootstrap now runs with its console window hidden, avoiding
  a terminal flash during successful install and uninstall operations.

## [0.2.0] - 2026-07-12

### Added

- Native macOS PKG and Windows x64 MSI installers alongside the existing manual
  ZIP downloads.
- Signed and notarized macOS installer packaging for macOS 13+ with an optional
  VST3 component.
- Per-machine Windows installation in Program Files with Apps & Features,
  Start Menu, optional desktop shortcut, major upgrades, and optional VST3.
- Installer bootstrap setup that requires an existing uv installation, links to
  the official instructions when it is missing, and provisions the current user's
  helper environment and FL Studio MIDI bridge without automatically installing uv.
- Standalone in-app downloading, checksum verification, and handoff to the
  native PKG or MSI updater.

### Changed

- Native upgrades now preserve settings, capsule libraries, preferences, and
  component choices while migrating legacy per-user app and VST3 locations.
- The VST3 update action continues to open the GitHub release page so a loaded
  FL Studio plug-in is never replaced underneath the host.

## [0.1.0] - 2026-07-12

### Added

- A manual GitHub workflow that builds downloadable macOS and Windows packages
  and prepares a draft release.
- Developer ID signing, hardened runtime, secure timestamps, and Apple
  notarization for the macOS standalone app and VST3 release bundles.
- A manual **Check for Updates** action, a persisted **Check for Updates on
  startup** setting, and an in-app notification linking to a newer published
  GitHub release's notes and downloads.
- The standalone Sound Capsule library for capturing selected FL Studio
  generator channels, active-pattern MIDI, plug-in state, and rendered previews
  in portable `.flcapsule` files.
- Current-pattern, new-pattern, and selected-channel override import modes with
  validated backups and a time-limited Undo Import action.
- A documented FL Studio MIDI controller bridge and a local, standard-library
  Python helper with no hosted service dependency.
- Search, tags, favorites, sorting, waveform and MIDI previews, looped playback,
  and per-capsule usage tracking.
- An optional VST3 library surface alongside the primary standalone app.
- macOS and Windows support with per-user installation.
