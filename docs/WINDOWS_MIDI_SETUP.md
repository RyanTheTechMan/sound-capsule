# Windows MIDI setup

Sound Capsule uses a manually configured virtual MIDI port on Windows so FL
Studio can host the Sound Capsule controller script. The port carries endpoint
plumbing only; save requests continue to use Sound Capsule's local JSON bridge.

## loopMIDI

[loopMIDI](https://www.tobias-erichsen.de/software/loopmidi.html) is a
third-party virtual MIDI cable. Sound Capsule does not ship or control it.

Sound Capsule detects whether loopMIDI is installed separately from MIDI-port
enumeration. If it finds the application, setup offers to open it even when no
port is currently running. Otherwise, setup links to the official website and
does not automatically download or run an installer. Any other virtual MIDI
output that JUCE enumerates may also be selected.

1. Download and install loopMIDI from its official website.
2. Open loopMIDI.
3. Enter `Sound Capsule MIDI` under **New port-name**.
4. Press the plus button to create the port.
5. Leave loopMIDI running. Enabling its autostart option is recommended.
6. Return to Sound Capsule and press **Refresh MIDI devices**.
7. Select **Sound Capsule MIDI** as the MIDI output.
8. In FL Studio, open **Options -> MIDI settings**.
9. Under Input, select **Sound Capsule MIDI** and press **Enable**.
10. Choose **Sound Capsule (user)** as the controller type.

Existing installations that used **Sound Capsule Control** keep that saved port
name. If the saved port is not running, Sound Capsule reports **Selected MIDI
port is unavailable** without discarding the selection.

## Manual validation

Before release, verify startup with loopMIDI running and stopped, refresh after
creating or restarting the port, FL Studio launched before and after Sound
Capsule, application restart, controller-script assignment, and clean, dirty,
and first-save projects through the JSON bridge.
