# Changelog

All notable changes to Sound Capsule are recorded here. The project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html), and each released
version's section is also used as its GitHub release notes.

## [Unreleased]

No changes yet.

## [0.2.0] - 2026-07-12

### Added

- Native macOS PKG and Windows x64 MSI installers alongside the existing manual
  ZIP downloads.
- Signed and notarized macOS installer packaging for macOS 13+ with an optional
  VST3 component.
- Per-machine Windows installation in Program Files with Apps & Features,
  Start Menu, optional desktop shortcut, major upgrades, and optional VST3.
- Installer bootstrap setup that locates or installs uv 0.11.28 and provisions
  the current user's helper environment and FL Studio MIDI bridge.
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
