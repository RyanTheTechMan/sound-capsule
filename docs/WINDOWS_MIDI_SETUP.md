# Windows MIDI setup

Sound Capsule uses [loopMIDI](https://www.tobias-erichsen.de/software/loopmidi.html) only
so FL Studio can host its controller script. The app never opens or sends data through
the port; commands use the local JSON bridge. loopMIDI is a third-party tool and is not
shipped, downloaded, installed, or controlled by Sound Capsule.

FL Setup lists only loopMIDI ports. Discovery and the final availability check
run in the background, and any custom-named loopMIDI port can be selected.

1. Download and install loopMIDI from its official website.
2. Open loopMIDI, create a port (suggested name: `Sound Capsule MIDI`), and
   leave loopMIDI running.
3. In Sound Capsule, open **Settings -> FL Setup**, refresh, select the port,
   and choose **Use Port**.
4. In FL Studio's MIDI settings, enable that port under Input and choose
   **Sound Capsule (user)** as its controller type.
5. Under Output, enable the same loopMIDI port.

The selection is used for the displayed FL instructions, not stored as a Sound
Capsule runtime preference. loopMIDI and FL Studio retain the actual port and
controller assignment.

Verify startup with loopMIDI running and stopped, refresh/close races, multiple
custom port names, controller-script assignment, and clean, dirty, and
first-save projects through the JSON bridge.
