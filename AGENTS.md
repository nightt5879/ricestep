# RiceStep development

- Use authenticated local `gh` and `git` for GitHub work; do not use GitHub connectors.
- This is a LingClaw Lua miniapp. Use APIs documented in the pinned official SDK.
- Keep tool versions and archive SHA-256 hashes in `toolchain.lock.json`.
- Do not commit downloaded tools, build outputs, credentials, device serials, or private device data.
- Run `pwsh -File scripts/build.ps1` after Lua or build changes; run `scripts/test.ps1` for logic and interaction changes. Check rendered previews when changing UI.
- Use `pwsh -File scripts/dev.ps1` for real window/input checks; headless rendering does not prove GUI input or physical hardware support.
- The device is confirmed as Arcs Mini3, firmware 3.1.0, Lua API 5, 256 shapes/frame. The old desktop simulator is API 4 with 128 rectangles, so it uses lower-detail art.
- The user chose desktop companionship + a cooking mini-game (2026-10-27). Current menus are feeding, focus, cooking and collection. Do not pursue phone step sync until requested again; the build excludes step-sync.lua and ignores .local-config.json. No board motion-sensor API was found. Never present timers, key clicks or test samples as measured steps.
- The user confirmed the character display/menu controls and, after the companion update, actual Mini3 cooking and rice rewards (2026-10-27). Menus retain single-click selection / double-click confirmation; active cooking uses one click to stop, with 350ms startup arming and 500ms result protection.
- Focus rewards one rice + a collection discovery. Perfect cooking rewards two rice + discovery, good cooking one rice, other outcomes zero without inventory loss. Persisted progress has 29 scalar fields; keep storage within 32 fields and 1KiB. Cooking rounds are transient; focus reloads paused.
- game.update(animation_dt, observed_dt) keeps cooking motion separate from elapsed focus time. When calibrated clock seconds are available, accept only 0..5 second deltas; reject backward/large jumps. The fallback is runtime dt, not fabricated wall time or steps.
- Source art is encoded into compact character strings; regenerate with `npm run assets`. Device startup rejected the original large-table form.
- Keep environment setup separate from device deployment. ADB upload immediately replaces the current miniapp.
- Save verified, coherent changes in commits; do not claim a build or physical test that was not run.
