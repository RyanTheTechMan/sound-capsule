from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .capsule import Capsule
from .config import Settings
from .flp import FLPFile
from .project import CapsuleService
from .server import serve


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="soundcapsule")
    root.add_argument("--home", type=Path, help="override Sound Capsule data directory")
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("serve")
    commands.add_parser("list")
    inspect = commands.add_parser("inspect")
    inspect.add_argument("path", type=Path)
    capture = commands.add_parser("capture")
    capture.add_argument("name")
    capture.add_argument("--project", type=Path)
    capture.add_argument("--preview", type=Path)
    capture.add_argument("--individual", action="store_true")
    import_cmd = commands.add_parser("import")
    import_cmd.add_argument("id")
    import_cmd.add_argument("--mode", choices=("append", "override"), default="append")
    import_cmd.add_argument(
        "--destination",
        choices=("current_pattern", "new_pattern", "override_selection"),
    )
    import_cmd.add_argument("--project", type=Path)
    import_cmd.add_argument("--target", type=int, action="append", default=[])
    import_cmd.add_argument("--pattern", type=int)
    import_cmd.add_argument("--no-open", action="store_true")
    import_cmd.add_argument("--in-place", action="store_true", help="back up and replace the current FLP")
    undo = commands.add_parser("undo-import")
    undo.add_argument("--project", type=Path)
    undo.add_argument("--no-open", action="store_true")
    configure = commands.add_parser("configure")
    configure.add_argument("--project-root", type=Path, action="append")
    configure.add_argument("--library", type=Path)
    configure.add_argument("--fl", type=Path)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    settings = Settings.load(args.home)
    try:
        if args.command == "serve":
            serve(settings)
            return 0
        if args.command == "configure":
            if args.project_root:
                settings.project_roots = args.project_root
            if args.library:
                settings.library_dir = args.library
            if args.fl:
                settings.fl_executable = args.fl
            settings.save()
            _print({"settings": str(settings.config_path)})
            return 0
        service = CapsuleService(settings)
        if args.command == "list":
            service.library.reindex()
            _print({"capsules": service.library.list()})
        elif args.command == "inspect":
            if args.path.suffix == ".flcapsule":
                capsule = Capsule(args.path)
                capsule.verify()
                _print(capsule.manifest.to_dict())
            else:
                project = FLPFile.read(args.path)
                _print({
                    "format": project.format, "channels": project.channel_count, "ppq": project.ppq,
                    "fl_version": project.fl_version, "current_pattern": project.current_pattern,
                    "channel_sections": [
                        {"iid": section.iid, "name": section.name, "plugin": section.plugin_name, "type": section.channel_type}
                        for section in project.channel_sections()
                    ],
                })
        elif args.command == "capture":
            capsules = service.capture(
                args.name, project_path=args.project, preview_wav=args.preview, individually=args.individual
            )
            _print({"created": [str(item.path) for item in capsules]})
        elif args.command == "import":
            result = service.import_capsule(
                args.id, mode=args.mode, project_path=args.project,
                target_channels=args.target, pattern_id=args.pattern, open_project=not args.no_open,
                import_destination=args.destination, in_place=args.in_place,
            )
            _print({
                "merged": str(result.merged_project), "mapping": result.channel_mapping,
                "pattern": result.pattern_id, "backup": str(result.backup_project) if result.backup_project else None,
                "reload_confirmed": result.reload_confirmed,
            })
        elif args.command == "undo-import":
            result = service.undo_last_import(project_path=args.project, open_project=not args.no_open)
            _print({
                "project": str(result.project), "restored_from": str(result.restored_from),
                "safety_backup": str(result.safety_backup), "reload_confirmed": result.reload_confirmed,
            })
        return 0
    except Exception as error:
        print(f"soundcapsule: {error}", file=sys.stderr)
        return 1


def _print(payload: dict) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
