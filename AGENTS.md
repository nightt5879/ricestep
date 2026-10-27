# RiceStep development

- Use authenticated local `gh` and `git` for GitHub work; do not use GitHub connectors.
- This is a LingClaw Lua miniapp. Use APIs documented in the pinned official SDK.
- Keep tool versions and archive SHA-256 hashes in `toolchain.lock.json`.
- Do not commit downloaded tools, build outputs, credentials, device serials, or private device data.
- Run `pwsh -File scripts/build.ps1` after Lua or build changes; run `scripts/test.ps1` for logic and interaction changes. Check rendered previews when changing UI.
- Use `pwsh -File scripts/dev.ps1` for real window/input checks; headless rendering does not prove GUI input or physical hardware support.
- The device is confirmed as Arcs Mini3, firmware 3.1.0, Lua API 5, 256 shapes/frame. The old desktop simulator is API 4 with 128 rectangles, so it uses lower-detail art.
- The user is reconsidering the core gameplay around the whale character (2026-10-27); do not pursue phone step sync until requested again. No board motion-sensor API was found. Never present timers, key clicks or test samples as measured steps.
- The user confirmed the character displays and the single-click selection logic is OK after rereading it. Preserve single-click selection / double-click confirmation unless a new interaction is requested.
- Source art is encoded into compact character strings; regenerate with `npm run assets`. Device startup rejected the original large-table form.
- Keep environment setup separate from device deployment. ADB upload immediately replaces the current miniapp.
- Save verified, coherent changes in commits; do not claim a build or physical test that was not run.
