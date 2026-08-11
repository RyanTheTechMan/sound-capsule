#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "helper"))

from soundcapsule.flp import (  # noqa: E402
    CHANNEL_OWNED_EVENT_IDS,
    EVENT_CHANNEL_NEW,
    EVENT_PATTERN_NEW,
    FLPFile,
    FLPUnsupportedError,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit a directory of real FLP fixtures without modifying them")
    parser.add_argument("root", type=Path, help="FL Studio application/data directory or fixture directory")
    args = parser.parse_args()
    paths = sorted(args.root.expanduser().rglob("*.flp"))
    if not paths:
        parser.error(f"no FLP files found under {args.root}")

    failures: list[dict[str, str]] = []
    counters = {
        "files": 0,
        "channels": 0,
        "preview": 0,
        "append": 0,
        "override": 0,
        "mixer_inserts": 0,
        "mixer_preview": 0,
        "mixer_restore": 0,
    }
    versions: set[str] = set()
    for path in paths:
        try:
            source = path.read_bytes()
            project = FLPFile.from_bytes(source)
            counters["files"] += 1
            versions.add(project.fl_version)
            if project.to_bytes() != source:
                raise AssertionError("unmodified serialization is not byte-identical")
            sections = project.channel_sections()
            if len(sections) != project.channel_count:
                raise AssertionError(f"found {len(sections)} channel sections; header says {project.channel_count}")
            counters["channels"] += len(sections)
            unprofiled = sorted(
                {event.id for section in sections for event in section.events if event.id not in CHANNEL_OWNED_EVENT_IDS}
            )
            if unprofiled:
                raise AssertionError(f"unprofiled channel event IDs: {unprofiled}")

            channels_by_insert: dict[int, list[int]] = {}
            for candidate in sections:
                if candidate.channel_type in (3, 5) or candidate.mixer_insert == 0:
                    continue
                channels_by_insert.setdefault(candidate.mixer_insert, []).append(
                    candidate.iid
                )
            mixer_requests = []
            for insert_index, channel_ids in sorted(channels_by_insert.items()):
                state = FLPFile.from_bytes(
                    project.mixer_insert_state(insert_index).to_bytes()
                )
                state.validate_mixer_insert_state()
                mixer_requests.append((state, channel_ids))
                counters["mixer_inserts"] += 1
            if mixer_requests:
                try:
                    restored, allocated = project.restore_mixer_insert_states(
                        mixer_requests
                    )
                except FLPUnsupportedError as error:
                    if "pristine mixer insert" not in str(error):
                        raise
                    if project.to_bytes() != source:
                        raise AssertionError(
                            "failed mixer allocation mutated the source project"
                        )
                else:
                    reparsed_mixer = FLPFile.from_bytes(restored.to_bytes())
                    if allocated != sorted(allocated):
                        raise AssertionError(
                            "mixer inserts were not allocated in ascending order"
                        )
                    routed = {
                        item.iid: item.mixer_insert
                        for item in reparsed_mixer.channel_sections()
                    }
                    for allocated_index, (_, channel_ids) in zip(
                        allocated, mixer_requests, strict=True
                    ):
                        if any(routed[channel_id] != allocated_index for channel_id in channel_ids):
                            raise AssertionError(
                                "restored mixer insert did not preserve generator sharing"
                            )
                    counters["mixer_restore"] += len(allocated)

            section = next((item for item in sections if item.channel_type not in (3, 5)), None)
            if section is None:
                continue
            pattern = project.current_pattern
            pattern_notes = project.pattern_notes().get(pattern, [])
            notes = [
                note for note in pattern_notes
                if note.rack_channel == section.iid
            ]
            preview_source = next(
                (
                    candidate for candidate in sections
                    if candidate.channel_type not in (3, 5)
                    and any(note.rack_channel == candidate.iid for note in pattern_notes)
                ),
                None,
            )
            if preview_source is not None:
                preview = FLPFile.from_bytes(
                    project.isolated_preview_project([preview_source.iid], pattern).to_bytes()
                )
                preview_sections = preview.channel_sections()
                if any(
                    note.rack_channel != preview_source.iid
                    for note in preview.pattern_notes().get(pattern, [])
                ):
                    raise AssertionError("isolated preview retained notes from an unselected channel")
                for preview_section in preview_sections:
                    enabled = next(
                        (event.scalar for event in preview_section.events if event.id == 0), 1
                    )
                    if preview_section.iid == preview_source.iid:
                        route = next(
                            (event.scalar for event in preview_section.events if event.id == 22), 0
                        )
                        if not enabled or route != 0:
                            raise AssertionError("preview channel is disabled or not routed to Master")
                    elif enabled:
                        raise AssertionError("isolated preview retained an enabled unselected channel")
                counters["preview"] += 1
                if preview_source.mixer_insert > 0:
                    wet_preview = FLPFile.from_bytes(
                        project.isolated_preview_project(
                            [preview_source.iid],
                            pattern,
                            preserve_mixer_inserts=True,
                        ).to_bytes()
                    )
                    wet_channel = {
                        item.iid: item for item in wet_preview.channel_sections()
                    }[preview_source.iid]
                    if wet_channel.mixer_insert != preview_source.mixer_insert:
                        raise AssertionError(
                            "effect-aware preview discarded the selected mixer insert"
                        )
                    wet_insert = {
                        item.index: item
                        for item in wet_preview.mixer_insert_sections()
                    }[preview_source.mixer_insert]
                    if wet_insert.routes_to() != {0}:
                        raise AssertionError(
                            "effect-aware preview retained non-Master sends"
                        )
                    counters["mixer_preview"] += 1

            appended, _, new_pattern = project.append_capsule(
                [section], {section.iid: notes}, source_ppq=project.ppq, pattern_name="Corpus validation"
            )
            reparsed = FLPFile.from_bytes(appended.to_bytes())
            if reparsed.channel_count != project.channel_count + 1:
                raise AssertionError("append did not increment channel count")
            first_channel = next(i for i, event in enumerate(reparsed.events) if event.id == EVENT_CHANNEL_NEW)
            inserted_pattern = next(
                i for i, event in enumerate(reparsed.events)
                if event.id == EVENT_PATTERN_NEW and event.scalar == new_pattern
            )
            if inserted_pattern >= first_channel:
                raise AssertionError("new pattern was not inserted before the Channel Rack region")
            counters["append"] += 1

            overridden = project.override_capsule(
                [section], {section.iid: notes}, [section.iid],
                source_ppq=project.ppq, pattern_id=pattern,
            )
            reparsed_override = FLPFile.from_bytes(overridden.to_bytes())
            if reparsed_override.channel_count != project.channel_count:
                raise AssertionError("override changed channel count")
            counters["override"] += 1
        except Exception as error:
            failures.append({"path": str(path), "error": f"{type(error).__name__}: {error}"})

    report = {**counters, "versions": sorted(versions), "failures": failures}
    print(json.dumps(report, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
