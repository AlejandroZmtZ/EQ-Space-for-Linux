# User guide

## Playback and effects

Start music, choose a physical output in Mixer, select a preset and click Apply. The Mixer status describes the enabled stages and reports route failures. A configured default sink alone does not confirm that every application's links follow it.

EQ, Spatial and LSP can be enabled or disabled independently. No particular order is required. The audio path always follows Spatial → EQ → LSP → physical output, omitting disabled stages. Mic is an independent experimental capture path.

Topology changes prepare and verify a replacement graph in the background. A loading indicator appears for longer operations. Wait for completion before requesting another topology change. A failure keeps or restores a verified working route where possible and displays an error.

Quit normally to restore direct output before unloading the app's modules. Tray availability depends on the desktop; use the main window if there is no tray icon.

## Parametric EQ

Drag a band handle or edit its type, frequency, gain and Q in the table. Active EQ edits update the native controls in the background. If EQ is bypassed, edits remain in the draft until Apply or EQ enabling.

Preamp is a manual gain setting. Automatic headroom is a separate calculated reserve based on the filter responses. The response graph and combined peak are estimates, not audio measurements or guaranteed clipping protection. With the optional LSP stage enabled, positive manual preamp can drive the limiter; excessive drive can reduce dynamics.

## Presets

Selecting a row previews it. Apply changes audio. Save current draft captures the editor, even if a different library row is selected.

- **EQ only:** restores EQ bands and gain while preserving current Spatial and LSP selections.
- **Full playback:** restores EQ enablement, Spatial selection/settings and LSP selection.

Built-in EQ presets preserve Spatial and LSP. Rename, duplicate and delete apply to user entries. Export works for built-in and user profiles. Saves use an atomic temporary-file replacement and validate the result.

Import accepts EQ-Space JSON and supported APO/AutoEQ parametric text: Preamp and ON/OFF PK, LS/LSC or HS/HSC filters with frequency, gain and Q. Import previews the result before saving. Convolution directives and other unsupported processing are rejected. A file described as AutoEQ may contain unsupported commands; check its preview and error message.

The profile folder must be writable. A read-only mount or restricted launcher cannot be repaired by saving into a temporary folder; use a normal user installation. Startup restores the last saved editor configuration without applying sound.

## Spatial

Select a profile and click Apply Spatial Audio. Selection alone does not change the applied graph. Applied output-level changes update live.

HS+ (Experimental) aims for a spacious, more speaker-like presentation with stable vocals and forward acoustic detail. Its tuning is fixed; the UI exposes output level. It remains experimental because its synthetic model and perceived benefits are not validated across headphones and recordings.

The level slider scales the processed output, including mute at zero for HS+. It is not a blend with unprocessed audio. The bundled models are synthetic; optional external SOFA extraction requires `pysofa`. Cinema offers stereo expansion or native multichannel input. See [current Spatial implementation](spatial-hybrid.md).

## Diagnostics

Use `eqspace --debug` when reproducing an error. Help offers Open logs and Copy diagnostic report. Review reports for personal identifiers before sharing. See [privacy](privacy.md) and [limiter setup](lv2-host.md).
