# Camera image → pose tensor

Portable C11 preprocessing, with no allocation, camera calls, network, model
execution, or hidden rotation/mirroring. This component is **not yet connected to
Mini3 capture or inference**. Host tests use synthetic pixels and do not establish
pose accuracy, camera colour order, or board performance.

## Source format evidence

The pinned Mini3 mirror `c0a95bcf3e45c7bd783ba4e997df19c96ffaaf35` was inspected:

- `arcs-sdk/drivers/lisa_camera/sensors/src/gc0328.c`, `set_pixformat()`:
  RGB565 selects register `0x44` value `0x06` and register `0x49` bit `0x20`,
  explicitly commented **little endian**.
- `apps/arcs-mini/services/service_camera.c`, `service_camera_capture()`:
  copies 320×240 RGB565 into the caller's buffer and **swaps every pixel's two
  bytes** (`d[0]=s[1]; d[1]=s[0]`). Mini3 does not rotate this output; camera
  mirror/flip settings can already have changed its orientation.
- Therefore service output is expected to be **RGB565_BE**, whereas direct
  sensor/framebuffer input is expected LE. DMA byte order and actual colour must
  still be checked against a captured colour chart before choosing the adapter's
  format. A bare SDK `RGB565` enum is insufficient to choose the byte order.

See [camera research](../../../docs/camera-research.md) for source provenance and
the gap between the community mirror and factory 3.1.0. The API deliberately has
no default camera format. Select `PI_RGB565_LE`, `PI_RGB565_BE`, or `PI_RGB888`.
RGB565 is a 16-bit `RRRRRGGG GGGBBBBB` value; channels expand using bit replication
(`r5<<3 | r5>>2`, likewise `g6<<2 | g6>>4`). RGB888 is three bytes R, G, B.
Unaligned byte buffers are supported; there are no native-endian word loads.

## Model input and resize contract

`pi_prepare()` writes exactly **110,592 bytes**, NHWC **[1,192,192,3]**, RGB INT8.
The extracted MoveNet Lightning backbone input contract is **scale 1, zero point
−128**: convert each RGB uint8 channel with `int8 = uint8 - 128`, without changing
channel order or applying another normalization. Black padding is `−128` in all
three channels. This must be rechecked if a different model is substituted.

The longer dimension fills 192 pixels; the shorter dimension is rounded half-up
to an integer, with a minimum of one pixel. The frame is centered, assigning any
odd padding pixel to the right/bottom. There is no crop. Rounding can slightly
change the resized aspect; the inverse transform uses the actual integer sizes.
For 320×240 input, the active image is 192×144 with 24 black rows above and below.

Sampling is deliberately deterministic **nearest neighbour at output centres**:

```text
source_x = floor((destination_x + 0.5) × source_width / resized_width)
source_y = floor((destination_y + 0.5) × source_height / resized_height)
```

Integer arithmetic implements this expression exactly. Upsampling is permitted.
Nearest sampling is not anti-aliased; compare real pose results before changing
the production resize policy. This is a bounded, testable preprocessing path,
not an accuracy claim or a substitute for testing real frames.

## Geometry passed to motion_logic

Keep the `pi_transform` associated with **that exact frame/inference**, even when
capture is asynchronous. `pi_unletterbox()` accepts decoded normalized **x, y**
within the full square tensor. The decoder itself emits **y, x, score**; the
adapter must pass the correct two fields. No additional half pixel is added to
decoded points. Centre sampling belongs only to image resampling.

The inverse is `x = (192 × model_x - pad_left) / resized_width` and similarly
for y, up to float rounding. Comparisons/interpolation share the representable
normalized content edges so non-exact float boundaries (such as `164/192`) remain
valid. No out-of-range point is clamped. Padding, nonfinite points, invalid
geometry and out-of-image coordinates fail without modifying the output point.
The caller must set rejected landmark confidence to zero, rather than reuse an
old point or project it onto an image edge. Boundary coordinates 0 and 1 are valid.

Successful results contain original-frame normalized `x/y` and
`image_aspect = source_width/source_height`, matching `ml_pose`. Confidence,
COCO17 ordering, a stable subject ID and capture timestamps remain the caller's
responsibility. “Original frame” means the supplied buffer after any orientation
already performed by the camera service, not raw sensor coordinates.

## Buffer ownership and bounds

- Caller supplies frame storage, tensor storage, and a transform. They must not
  overlap; input bytes must remain stable until the function returns.
- Dimensions are 1–4096, stride is explicit and at most 16,384 bytes, and stride
  must contain a complete row. The last row needs only its pixel bytes, not its
  trailing padding. Checked size arithmetic prevents row/address wraparound.
- The tensor's extra capacity is untouched. On any validation failure, neither
  tensor nor transform is changed. The returned status must be checked.
- The module keeps no state, pointers, scratch allocations or camera ownership.
  Release a borrowed camera framebuffer only after preprocessing finishes, or
  use a stable service-owned copy. Capture integration must enforce this lifetime.

Run the native tests from the repository root:

```powershell
pwsh -File scripts/test-pose-image.ps1 -Compiler C:/MinGW/bin/gcc.exe
pwsh -File scripts/test-pose-image.ps1 -Compiler 'C:/Program Files/LLVM/bin/clang.exe'
```

The fixtures verify RGB565 byte order/bit expansion, RGB888 identity and striding,
quantization endpoints, Mini3-size letterboxing and exact centre samples, portrait
and odd padding, maximum/narrow dimensions, inverse coordinates, nonfinite/padded
landmarks, buffer overlap, truncated input/output, invalid settings, unchanged
input, and output sentinels. They do not read camera hardware.
