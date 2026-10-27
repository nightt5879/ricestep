# RiceStep development

- Use authenticated local `gh` and `git` for GitHub work; do not use GitHub connectors.
- This is a LingClaw Lua miniapp. Use APIs documented in the pinned official SDK.
- Keep tool versions and archive SHA-256 hashes in `toolchain.lock.json`.
- Do not commit downloaded tools, build outputs, credentials, device serials, or private device data.
- Run `pwsh -File scripts/build.ps1` after Lua or build changes. Check the rendered preview when changing UI.
- Use `pwsh -File scripts/dev.ps1` for real window/input checks; headless rendering does not prove GUI input or physical hardware support.
- Preserve the user's preference for a carried device that counts steps itself. Sensor availability and exact board revision remain unverified.
- Keep environment setup separate from device deployment. ADB upload immediately replaces the current miniapp.
- Save verified, coherent changes in commits; do not claim a build or physical test that was not run.
