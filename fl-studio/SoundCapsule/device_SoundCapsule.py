# name=Sound Capsule
# url=https://github.com/sound-capsule/fl-sound-capsule

"""FL Studio MIDI bridge for Sound Capsule.

This script only uses FL Studio's documented MIDI scripting surface. It
publishes selection/project metadata and never reads or mutates FLP bytes.
"""

import json
import os
import sys
import time

import channels
import general
import midi
import patterns
import transport


if sys.platform == "win32":
    _ROOT = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~/AppData/Local")), "SoundCapsule")
else:
    _ROOT = os.path.expanduser("~/Library/Application Support/SoundCapsule")

_BRIDGE = os.path.join(_ROOT, "Bridge")
_SESSION = os.path.join(_BRIDGE, "session.json")
_COMMAND = os.path.join(_BRIDGE, "command.json")
_last_publish = 0.0
_last_command_id = ""
_save_sequence = 0
_last_save_requested_at = 0.0
_load_sequence = 0
_last_load_status = -1
_last_load_at = 0.0

_SYSEX_PREFIX = bytes((0x7D, 0x53, 0x43, 0x41, 0x50))  # Non-commercial ID + "SCAP"
_COMMAND_SAVE = 1


def _ensure_directories():
    for path in (_ROOT, _BRIDGE):
        if not os.path.isdir(path):
            os.makedirs(path)


def _atomic_json(path, payload):
    path_bytes = path.encode("utf-8")
    data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    # FL's embedded Python can return NULL from io.FileIO even for writable
    # user-data paths. Use the low-level OS API, which is supported by the MIDI
    # scripting runtime. FL's os.rename/os.replace wrappers are also broken on
    # this host, so the helper performs bounded retries if it catches a read
    # during this tiny direct update.
    descriptor = os.open(path_bytes, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        offset = 0
        while offset < len(data):
            written = os.write(descriptor, data[offset:])
            if written <= 0:
                raise OSError("could not write Sound Capsule bridge session")
            offset += written
    finally:
        os.close(descriptor)


def _read_json(path):
    descriptor = os.open(path.encode("utf-8"), os.O_RDONLY)
    try:
        chunks = []
        while True:
            chunk = os.read(descriptor, 4096)
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        os.close(descriptor)
    return json.loads(b"".join(chunks).decode("utf-8"))


def _selected_channels():
    selected = []
    names = []
    count = channels.channelCount(1)
    for index in range(count):
        if channels.isChannelSelected(index, True):
            selected.append(index)
            names.append(channels.getChannelName(index, True))
    return selected, names


def _midi_api_version():
    try:
        return int(general.getVersion())
    except Exception:
        return 0


def _publish_session(force=False):
    global _last_publish
    now = time.time()
    if not force and now - _last_publish < 0.25:
        return
    selected, names = _selected_channels()
    pattern = patterns.patternNumber()
    payload = {
        "timestamp": now,
        "project_title": general.getProjectTitle(),
        "midi_api_version": _midi_api_version(),
        "selected_channels": selected,
        "selected_channel_names": names,
        "current_pattern": pattern,
        "pattern_name": patterns.getPatternName(pattern),
        # FL 25.2 reports this timeline in Channel Rack step units (four per
        # quarter-note beat), despite older API text calling the value beats.
        "pattern_length_steps": patterns.getPatternLength(pattern),
        "ppq": general.getRecPPQ(),
        "changed": general.getChangedFlag(),
        "save_sequence": _save_sequence,
        "last_save_requested_at": _last_save_requested_at,
        "load_sequence": _load_sequence,
        "last_load_status": _last_load_status,
        "last_load_at": _last_load_at,
    }
    _atomic_json(_SESSION, payload)
    _last_publish = now


def OnInit():
    _ensure_directories()
    _publish_session(True)


def OnDeInit():
    pass


def OnIdle():
    _ensure_directories()
    _process_command()
    _publish_session()


def _perform_save():
    global _save_sequence, _last_save_requested_at
    transport.globalTransport(midi.FPT_Save, 1)
    _save_sequence += 1
    _last_save_requested_at = time.time()
    _publish_session(True)


def _process_command():
    global _last_command_id
    try:
        payload = _read_json(_COMMAND)
        request_id = str(payload.get("request_id", ""))
        if not request_id or request_id == _last_command_id:
            return
        expires_at = float(payload.get("expires_at", 0.0))
        if payload.get("command") != "save" or expires_at <= time.time():
            _last_command_id = request_id
            return
        _perform_save()
        _last_command_id = request_id
    except (OSError, ValueError, TypeError):
        # No command, a concurrent atomic replacement, or malformed data. A
        # valid unexpired request is retried on the next idle callback.
        return


def OnSysEx(eventData):
    try:
        payload = bytes(eventData.sysex)
        if payload.startswith(bytes((0xF0,))):
            payload = payload[1:]
        if payload.endswith(bytes((0xF7,))):
            payload = payload[:-1]
        if len(payload) != len(_SYSEX_PREFIX) + 1 or not payload.startswith(_SYSEX_PREFIX):
            return
        if payload[-1] != _COMMAND_SAVE:
            return
        eventData.handled = True
        _perform_save()
    except Exception:
        # Keep controller scripts alive if FL rejects a command while a modal
        # dialog or project transition is already in progress.
        return


def OnRefresh(flags):
    _publish_session(True)


def OnDirtyChannel(channel, flags):
    _publish_session(True)


def OnProjectLoad(status):
    global _load_sequence, _last_load_status, _last_load_at
    _load_sequence += 1
    _last_load_status = int(status)
    _last_load_at = time.time()
    _publish_session(True)
