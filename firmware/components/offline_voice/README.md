# Offline native voice pack

The generator packages the existing fourteen MP3 files into a native C11 resource. It preserves every byte: no speech synthesis, transcoding, network request or dependence on the workstation's voice server. The source MP3 payload totals **375,408 bytes (366.61 KiB)**. This is the resource size, not a measured final firmware size or decoder RAM budget.

```powershell
node scripts/generate-native-voice.cjs
node --test tests/native-voice-pack.test.cjs
```

Generated `voice_pack.c`, `voice_pack.h` and `voice_pack.inventory.json` go to ignored `dist/native/voice/`. An explicit `--out-dir DIRECTORY` can target an isolated native build directory. Commit the generator, tests and original MP3 pack; do not commit the large generated hexadecimal arrays.

The generator checks the manifest version, exact fourteen-key inventory, safe filenames, metadata, file sizes and pinned SHA-256 values before writing any output. Updating both a changed MP3 and its manifest hash does not bypass the pinned inventory. The feed and burnt recordings are the exact two clips the user auditioned and accepted; the other twelve use the same selected voice parameters, without implying they were individually auditioned. Generating the same assets produces identical output regardless of manifest ordering, machine path or time.

## Generated interface

`rice_voice_get(rice_voice_id)` returns a `const rice_voice_clip *`, or `NULL` for an invalid enum. `rice_voice_find(const char *key, size_t key_length)` accepts a readable, length-delimited key; it rejects `NULL`, empty/oversize spans, embedded NULs, case differences and unknown names. It does not accept filenames, addresses or arbitrary URLs. The caller must supply `key_length` readable bytes. Neither lookup allocates memory or performs I/O.

Each entry contains a stable enum, key and length, `const uint8_t *data`, exact `uint32_t size`, duration and SHA-256 string. MP3 arrays are static, constant and aligned to four bytes. They remain valid for the whole firmware lifetime; callers must neither modify nor free them. Persist key strings rather than numeric enum values across future pack revisions.

The host test compiles the generated C with a C11 compiler, exercises invalid lookup inputs and alignment, writes all fourteen arrays back out, and compares their SHA-256 and full byte content with the original files. This proves packaging integrity and the C API on the host. It does not prove Mini3 decoding, flash placement, boot behavior or offline audio playback.

## Native player evidence and adapter boundary

Source inspected: the local `.cache/native-mini3-sdk` checkout of [Shinku-Chen/listenai_arcs_mini](https://github.com/Shinku-Chen/listenai_arcs_mini/tree/c0a95bcf3e45c7bd783ba4e997df19c96ffaaf35), fixed commit `c0a95bcf3e45c7bd783ba4e997df19c96ffaaf35`. This is a public source mirror, not an independently verified match for the installed factory 3.1.0 binary.

- [`arcs-sdk/components/app_player/README.rst`](https://github.com/Shinku-Chen/listenai_arcs_mini/blob/c0a95bcf3e45c7bd783ba4e997df19c96ffaaf35/arcs-sdk/components/app_player/README.rst) describes `mem://addr=<decimal address>size=<byte count>` playback, including Flash resources, and MP3 decoding. There is **no `&` separator** between the address and `size` fields.
- [`app_player_core.c`](https://github.com/Shinku-Chen/listenai_arcs_mini/blob/c0a95bcf3e45c7bd783ba4e997df19c96ffaaf35/arcs-sdk/components/app_player/app_player_core.c) sets `is_stream_mode = false` for URL playback, then calls `lisa_player_seturl` and waits for preparation. The separate `app_player_play_stream` / `write_stream` route uses `stream://type=pcm...`; it is not required for these MP3 arrays and has different stop/pause restrictions.
- [`app_player.c`](https://github.com/Shinku-Chen/listenai_arcs_mini/blob/c0a95bcf3e45c7bd783ba4e997df19c96ffaaf35/arcs-sdk/components/app_player/app_player.c) may save a copied `pending_url` when audio focus is unavailable. `APP_PLAYER_OK` therefore does not imply immediate audible playback or completion. The URL-mode stop operation calls the synchronous core stop path, with separate handling while preparation is being interrupted.
- [`miniapp_runtime.c`](https://github.com/Shinku-Chen/listenai_arcs_mini/blob/c0a95bcf3e45c7bd783ba4e997df19c96ffaaf35/apps/arcs-mini/miniapp/miniapp_runtime.c) already builds a `mem://` URL for its buzzer WAV. It retains the backing memory and stops the player before overwriting it. The `lisa_player` implementation is supplied as a library, so assume the resource bytes are borrowed throughout deferred playback rather than copied by `seturl`.

Use the modern `app_player_t *` API from `arcs-sdk/components/app_player`. The separate legacy header `src/middleware/audio/app_player.h` has a `player_t` plus callback signature; these are incompatible interfaces despite sharing function names.

The standalone runtime adapter should resolve only an internal allowlisted key, format the selected static array's address/size into a bounded private URL buffer, and use its dedicated Mini3 miniapp player. Keep both the resource and URL buffer valid until the terminal playback state; static array storage satisfies the resource requirement. On the 32-bit RV32 target, use `uintptr_t` with a checked decimal formatting conversion, never a host-truncated pointer. Schedule preparation on a worker because it can wait; preserve alarm/system audio focus, cancel obsolete queued requests on app exit, and report actual player states. An invalid key or playback failure leaves the caption visible and produces no alternate TTS voice or network fallback.

This directory supplies the resource generator and integration contract. The standalone overlay owns the actual Lua/native player binding, boot entry and save-state behavior.

## Flash placement plan

The generated arrays use input section `.rodata.ricestep_voice` on GCC/Clang. `const` alone is not proof that a board link leaves data in Flash: some linker layouts copy `.rodata` into RAM. The native build must inspect its final map and place this input section in **CP-readable memory-mapped Flash/XIP** before any general `.rodata.*` catch-all that maps to RAM. Keep it outside startup copy/zero ranges and preserve four-byte alignment. Reference the lookup functions from the adapter so linker garbage collection retains the inventory and all referenced arrays; an explicit `KEEP` rule is also possible in a dedicated resource output section.

Budget at least 375,408 bytes for the exact compressed payload plus the small lookup table/strings and any section padding. Do not reserve 375 KiB of decoder RAM merely to copy these constants. The player's MP3 decoder, output PCM buffers, task stack and focus state still need their own measured RAM allowance. If the verified BSP cannot decode directly from its Flash mapping, a bounded per-clip copy is a fallback to evaluate explicitly, not an implicit full-pack RAM copy.

Before flashing, inspect the actual Mini3 partition table and linker map for resource VMA/LMA, total image bounds and neighboring partitions. Then validate the feed/burnt clips on the board with networking disabled, boot without a workstation, and confirm audio/UI/save-state coexistence. No flash or board test is performed by the generator or its host tests.
