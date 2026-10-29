# Exact int8 pointwise convolution

This project-owned registration reuses the pinned TFLM `CONV_2D` initialization
and preparation, replacing only invocation for eligible 1×1 convolutions.
It shares each input load across four output channels, then two channels, with
a scalar remainder. It does not allocate an additional tensor or change weights,
quantization, shapes, activation limits, or the order of per-channel arithmetic.

Eligibility requires prepared int8 tensors, int32 bias, unit stride/dilation,
zero padding, one group, matching positive dimensions and bounded size products.
An independent-of-input int64 bound covers every partial int32 accumulation,
bias addition, positive requantization shift and output-offset addition. Unknown
or unsupported configurations use the original reference invocation. These are
internal prepared-tensor primitives, not a general arbitrary-buffer API.

Production uses the same strict floating-point and private release settings as
the other inference components, with final `-O3` on this translation unit.
`RICESTEP_POINTWISE_TEST_STATS` is host-test-only; native packaging rejects it.
No device, camera, audio, or network operations are present here.

The host tests compare against the pinned reference kernel, including seeded
random/extreme values, odd channel counts, saturation, overflow boundaries,
unsupported shapes and buffer sentinels. Full-model tests separately check all
four output heads and routing: the current model uses 49 fast 1×1 operators and
one reference 3×3 operator per inference. Arena use stays at 1,945,552 bytes in
the source-built host runner. Host output parity does not establish pose accuracy
or board speed.

From the repository root on Linux/WSL, run
`python3 scripts/test-pose-pointwise.py`. The [runner](../../../scripts/test-pose-pointwise.py)
requires Python 3.10+, Node 20+, CMake 3.19+, GCC/G++, Make, Git and curl.
It uses the actual production CMake graph and the locked source setup helper;
no pose model is needed. Host source trees live separately under
`.cache/pose-pointwise-host-sources` to preserve Windows/Linux patch inventories.
Use `--check-only` after the sources are installed to prohibit downloads.
The Ubuntu CI job runs the same 10,067 checks (1,600 seeded random/extreme cases)
with ASan, UBSan and leak detection. Logs and the JSON report are written to
`.cache/pose-pointwise-ci`.

See [static native instruction audit](../../../docs/pose-performance.md) and
[real-frame validation](../../../docs/pose-exercise-validation.md). The current
model remains unsuitable for the 400 ms counting gate; this optimization is not
evidence that the exercise feature is ready.
