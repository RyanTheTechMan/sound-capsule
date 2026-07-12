# Changelog

All notable changes to Sound Capsule are recorded here. The project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html), and each released
version's section is also used as its GitHub release notes.

## [Unreleased]

No changes yet.

## [0.1.0] - 2026-07-12

### Added

- A manual GitHub workflow that builds downloadable macOS and Windows packages
  and prepares a draft release.
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
