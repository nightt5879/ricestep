# Camera image → pose tensor

Portable C11 preprocessing, with no heap allocation, camera calls, network, model
execution, or hidden rotation/mirroring. Capture and inference ownership remain
with the caller. Host pixel tests do not establish pose accuracy, camera colour
order, or board performance.

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

Sampling uses deterministic **half-pixel bilinear interpolation**, preserving
the entire input field of view:

```text
source_x = (destination_x + 0.5) × source_width / resized_width - 0.5
source_y = (destination_y + 0.5) × source_height / resized_height - 0.5
```

For each axis, represent this coordinate exactly as
`n / den = ((2*d+1)*source-resized) / (2*resized)`. Take mathematical floor
`i=floor(n/den)` (including negative upsampling coordinates), and remainder
`r=n-i*den`. Clamp neighbors `i` and `i+1` independently to the image edges.
Unpack RGB565 channels **before** interpolating. With `dx=2*resized_width`,
`dy=2*resized_height`, and remainders `rx/ry`, each output channel is:

```text
floor((p00*(dx-rx)*(dy-ry) + p01*rx*(dy-ry)
     + p10*(dx-rx)*ry      + p11*rx*ry) / (dx*dy))
```

There is one final positive integer division; no intermediate rounding, added
half-unit, floating-point resize, or antialias filter. Apply RGB−128 only after
this final floor. Upsampling and one-pixel dimensions are supported. The weights
sum to at most `384*384=147456`, so the largest weighted sum is
`255*147456=37,601,280`, within uint32. Source indices fit uint16 under the checked
4096-pixel dimension limit. A 192-entry axis table takes 1,152 bytes of stack;
there is no full intermediate RGB image or retained scratch storage.

The coordinate geometry matches the half-pixel branch of upstream TFLM
`ComputeInterpolationValues` (`align_corners=false`, `half_pixel_centers=true`),
in `tensorflow/lite/micro/kernels/internal/reference/resize_bilinear.h` at pinned
commit `fed64d19fb5af8d28ee108c07838d5c0ae3ddcbb`. **This is not a claim of
bit-for-bit TensorFlow float rounding or TFLite quantized-resize equivalence.**
The local implementation uses exact rational weights and a specified final
floor; floating-point arithmetic can cross an integer boundary by rounding.

A cached, host-only comparison on the 65-frame public squat clip described in
[exercise validation](../../../docs/pose-exercise-validation.md) tested this
integer policy against float64 bilinear followed by floor. All 65 prepared
inputs differed in some channels, by at most one RGB value (125,316 channel
differences in total). With the unchanged model, C decoder, session gates and
synthetic 100 ms processing age, integer interpolation yielded 3/65 frames with
a qualifying shoulder/hip/knee/ankle side; float64 yielded 2/65. Both counted zero
repetitions. Neither policy qualified a complete side on the additional yoga
photo. Those confidence results are not annotated accuracy measurements, and
do not show that exercise counting is ready. Local exploratory artifacts live
in ignored `.cache/pose-preprocess-floor-audit`; they require the cached model,
video and isolated interpreter dependencies and are not a fresh-clone test.
Earlier nearest-neighbor and round-to-nearest bilinear reports are historical
comparisons; neither describes the current pixel policy.

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
COCO17 ordering, track continuity and capture timestamps remain the caller's
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
- The module keeps no state, pointers, heap allocations or camera ownership.
  Release a borrowed camera framebuffer only after preprocessing finishes, or
  use a stable service-owned copy. Capture integration must enforce this lifetime.

Run the native tests from the repository root:

```powershell
pwsh -File scripts/test-pose-image.ps1 -Compiler C:/MinGW/bin/gcc.exe
pwsh -File scripts/test-pose-image.ps1 -Compiler 'C:/Program Files/LLVM/bin/clang.exe'
```

The fixtures verify RGB565 byte order/bit expansion, RGB888 identity and striding,
quantization endpoints, Mini3-size letterboxing and bilinear golden samples,
checkerboard averaging, final floor rather than nearest rounding, edge extension,
portrait and odd padding, maximum/narrow dimensions, inverse coordinates,
nonfinite/padded landmarks, buffer overlap, truncated input/output, invalid
settings, unchanged input, and output sentinels. An independent int64 oracle
scans source pixel centers using triangular reconstruction weights and compares
every output channel for 27 format/geometry combinations. Exactly sized,
unaligned allocations expose last-row/right-edge overreads to sanitizers.
GCC, Clang and GCC AddressSanitizer/UndefinedBehaviorSanitizer pass 4,697,607
checks. These tests do not read camera hardware.
