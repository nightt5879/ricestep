# Offline Mini3 pose resource profile

This component and `scripts/prepare-pose-resources.cjs` prepare and validate local files. Neither accesses hardware, changes the SDK/stock profile, flashes a device, or installs a runtime hook. The output requires a future CP firmware that understands this resource profile.

From the repository root, with the pinned inputs already cached:

```powershell
node scripts/prepare-pose-resources.cjs
node scripts/prepare-pose-resources.cjs --verify dist/native/pose-resources
pwsh -File scripts/test-pose-resources.ps1
pwsh -File scripts/test-pose-resources.ps1 -Compiler clang
```

Inputs default to `.cache/stock-recovery-mini3/{tone,emoji,respak}.bin` and `.cache/pose-probe/movenet-heads-host-probe.tflite`; `--stock-root` and `--model` override only the file locations, never the pinned hashes. Output defaults to ignored `dist/native/pose-resources`; `--output` changes that directory. All input checks finish before output writes. The packager refuses input/SDK destinations.

`firmware/resource-profiles/mini3-pose-v1.json` specifies the versioned candidate layout. The packager independently pins its bounds, offsets, sizes and hashes. It produces exact stock `tone.bin`, `emoji.bin`, `respak.bin`, a padded 3 MiB `pose.bin`, the combined 5 MiB `resources.bin` covering Flash offsets `0x100000..0x600000`, and `manifest.json`. Remaining bytes are `FF`. No LPK, stock partition-table replacement or flashing script is emitted.

## Model header wire contract

The model window is 3 MiB at offset `0x180000`. Its first 4096 bytes use the following canonical format; integers are unsigned little endian. Payload starts at offset 4096, corresponding to mapped address `0x30181000`. Both base and payload have 16-byte alignment.

| Header offset | Bytes | Value |
| --- | ---: | --- |
| 0 | 8 | ASCII `RICEPOSE` |
| 8 | 4 | Header version `1` |
| 12 | 4 | Header length `4096` |
| 16 | 4 | Resource profile version `1` |
| 20 | 4 | Format `1` (TFLite FlatBuffer) |
| 24 | 4 | Model offset `4096` |
| 28 | 4 | Model length `2891896` |
| 32 | 4 | Window length `3145728` |
| 36 | 4 | Decoder contract version `1` |
| 40 | 4 | TFLite schema `3` |
| 44 | 4 | Input contract version `1` |
| 48 | 16 | Zero |
| 64 | 32 | Raw SHA-256 of the pinned four-head model (`9051a5ce…f9c68`) |
| 96 | 32 | Raw SHA-256 of the original Google model (`cd7cc22f…bb105`) |
| 128 | 3968 | Zero |

Decoder/input contract 1 identifies the current 192×192 NHWC RGB int8 input and four quantized outputs. The exact model hash pins tensor identities and quantization; the engine must still check its own schema, operators, tensors and contract before inference. Unknown metadata/reserved bytes fail closed rather than silently accepting a future format.

## C validator

```c
rp_status rp_model_validate(const uint8_t *window, size_t length,
                            rp_sha256_fn sha256, void *context,
                            rp_model_view *out);
```

The caller supplies a readable, immutable, aligned 3 MiB span and a **real SHA-256 callback**. The function checks the complete canonical header, model size/alignment, `TFL3` identifier and bounded root offset, then hashes exactly the payload and compares it with the pinned digest. Success exposes a `const uint8_t *` plus byte length; failure clears the view. The component allocates nothing and never casts a Flash address or initiates a Flash read. A production caller must choose and bounds-check the mapped region before calling it, then keep that region alive and immutable through the interpreter lifetime.

The injected callback returns zero only after filling all 32 digest bytes. A null callback, provider error or different digest is rejected. This is corruption/version validation, not a signature or authentication protocol. The C function does not inspect unused trailing `FF`; the file verifier does. Full FlatBuffer verification and tensor/operator validation belong to the model engine after this check.

## Validation and remaining work

The Node tests use real SHA-256 over the exact cached model and stock resources. They cover deterministic packing, every header field/reserved range, truncation, wrong payload/hash, erased padding, reserved-region bounds, malformed profiles, incorrect aggregate images and coordinated manifest edits. Tests that require the ignored original binaries explicitly skip when those inputs are absent.

The C test uses a hash-provider test double that compares the full payload against a separately loaded package already verified by Node; it covers callback behavior, errors, pointer lifetime contract, header parsing and failure views. It does **not** validate a production cryptographic library. Run the Node verifier before the C harness, as `test-pose-resources.ps1` does.

Still required: matching CP address patches and disabled legacy OTA/resource writers; actual ROMFS relocation tests; integration with an actual SHA-256 implementation, model engine and read-only Flash mapping; resource recovery plan and hardware validation. Do not flash this output into an unmodified stock firmware layout. See `docs/native-pose-integration.md` for the full capacity analysis.
