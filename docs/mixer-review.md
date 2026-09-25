# Mixer usability review — 2026-09-25

The Mixer now has one EQ state, one route action, and a listening-device selector containing physical outputs only. Application rows expose volume and mute. The [Mixer screenshot](screenshots/mixer.png) shows the direct-output state.

| Previous behavior | User risk | Change |
| --- | --- | --- |
| “EQ-Space Filter Chain” appeared alongside speakers and headphones. | A virtual sink looked like a device to listen through; choosing it could leave playback without a working physical path. | Removed EQ-Space virtual sinks from listening-device choices. The EQ button checks that both filter playback channels reach the selected physical device before changing the system default. |
| The top badge, status text, and badge beside the device selector repeated route state. | Multiple indicators implied separate states and could disagree. | One short “EQ on/off” status and one “Turn on EQ / Use direct output” action. |
| Every application had its own EQ button, route badge, and sink selector. | The route badge was inferred from the system default, not the application's actual link; the selector could choose the virtual sink. | Removed those controls from this prototype. Application rows now show only volume and mute. |
| The listening-device selector started on the first sink, and volume controls could start at 100% despite a lower real value. | Selecting or moving a slider could affect the wrong device or jump its volume. | Selects the actual physical default, reads volume from `wpctl`, and disables an unreadable volume slider. Volume changes are debounced. |
| Presets remained “Active” after direct bypass, and startup could apply a saved profile without a click. | The UI claimed EQ was active when it was off, or changed audio on launch. | Preset status follows the EQ route. Startup loads the last saved settings into the editor without changing audio. |
| Active streams were disconnected before the new link was attempted, and a failed link was ignored. | An unsuccessful switch could leave playback with no route. | The new path is linked first, then the old path is removed. If a new link fails, the old path remains. The Mixer status reports active apps still outside the chosen route. |

## Verification

Offscreen tests cover physical-only choices, real default and volume selection, route and output failures, muted-state labels, safe relinking, preset status after bypass, and startup without routing. Live PipeWire checks applied two presets with one EQ filter sink at a time, then restored the Bluetooth output on quit. A silent playback stream was moved from Bluetooth through EQ and back to Bluetooth while it was running.

A Brave video or song loading spinner was reported by the user. A live link snapshot at the time showed Brave connected to the EQ sink and the EQ playback connected to Bluetooth, so that snapshot alone does not prove why the media player stalled. The UI and routing safeguards remove the misleading selection path; browser playback still needs a user listening check.
