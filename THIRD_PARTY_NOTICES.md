# Third-party notices

Sound Capsule's optional VST3/standalone target fetches JUCE 8.0.9 at build
time. JUCE is dual-licensed under AGPL-3.0 and a commercial JUCE license. This
repository uses the AGPL-3.0 option. JUCE's own dependency notices are included
in its fetched source distribution.

The FLP event model was independently implemented using public reverse-
engineering references and validation fixtures. PyFLP (GPL-3.0) is used as a
development reference/test oracle; PyFLP is not vendored or imported at
runtime. Event-number constants and binary-format facts are not copied library
code.

The VST3 SDK reached through JUCE carries Steinberg's applicable VST3/GPL
licensing terms. See the fetched JUCE distribution for the complete notices.
