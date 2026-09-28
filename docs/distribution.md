# Installation and distribution

## Available formats

| Format | Current status |
| --- | --- |
| Source / Python wheel | Installable from this repository with Python 3.12+ and host PipeWire tools |
| Local Linux archive | Experimental developer build; see [bundle instructions](local-bundle.md) |
| Snap / Ubuntu App Center | No published store package or validated Snap build in this repository |
| Debian package | No Debian packaging in this repository |
| Flatpak | No Flatpak manifest in this repository |

The source build targets Ubuntu 24.04 LTS. It requires a working user PipeWire/WirePlumber session, `pw-cli`, `pw-dump`, `pw-link`, `pw-metadata`, `wpctl` and the desktop libraries needed by Qt. Python manages native modules and routes; shipping Python alone does not satisfy those requirements.

## Desktop integration

After installing the Python package, run:

```bash
.venv/bin/python tools/install-desktop-entry.py
```

The installer uses the installed package's desktop entry and icons. It writes to the current user's application and icon directories, renders standard PNG sizes from the SVG logo, and records the installed executable's absolute path. The app and launcher use the `eqspace` identity. Install the launcher before opening the app; a desktop may retain a generic icon for an already-running instance. Reinstall the launcher if the executable moves.

## Ubuntu store compatibility

Ubuntu's [App Center](https://github.com/ubuntu/app-center) is a store front end; a GitHub source update does not create a store listing.

EQ-Space needs permission to inspect and modify the host's native PipeWire graph, control other playback streams, own filter modules and restore direct routes. Canonical's [audio-playback interface](https://snapcraft.io/docs/reference/interfaces/audio-playback-interface/) permits audio playback; that alone does not establish the native graph-control access EQ-Space needs.

Canonical also documents a [pipewire interface](https://snapcraft.io/docs/reference/interfaces/pipewire-interface/) granting full access to the user's PipeWire socket. It does **not** auto-connect. This makes strict confinement a candidate for evaluation; this repository has not validated its required module, metadata, stream-routing, shared-memory and plugin behavior inside a snap. Store declarations and connection behavior must reflect the actual permissions of the shipped package.

If a package requires classic confinement, [Snapcraft requires Store-team approval](https://ubuntu.com/docs/snapcraft/9/how-to/crafting/enable-classic-confinement/) before distribution. Classic approval is not automatic and is not assumed here.

Any store package must include or supply compatible command-line tools, native filter-chain plugins and Qt libraries. It must save profiles and logs in writable per-user locations outside its read-only installation. Optional LSP hosting must use compatible native libraries and plugin resources; the host-local helper installer is not a validated sandbox installation method. Desktop/dock assets, GPL licensing and target-package [acceptance checks](testing.md) are also required.

There is no `snap install eqspace` instruction or store badge until a real package and listing exist.
