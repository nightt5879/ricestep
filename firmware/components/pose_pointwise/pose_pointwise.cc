// Project-owned int8 1x1 path; all other cases retain the pinned TFLM reference
// registration. Arithmetic, quantization, model weights and tensor shapes are
// unchanged. This component neither allocates memory nor uses device I/O.
#include "pose_pointwise.h"
#include <algorithm>
#include <climits>
#include <cstdint>
#include "tensorflow/lite/micro/kernels/internal/common.h"

namespace ricestep::pointwise {
using namespace tflite::micro;
#ifdef RICESTEP_POINTWISE_TEST_STATS
static unsigned long long fast_count, fallback_count;
void AuditCounts(unsigned long long* fast, unsigned long long* fallback) {
  *fast = fast_count; *fallback = fallback_count;
}
#endif

// The accumulator stays independent of bias until the exact reference order's
// final addition. Eligibility bounds every partial sum and positive left shift.
__attribute__((noinline)) DotPair Dot2(const int8_t* input,
    const int8_t* weights, int depth, int offset) {
  const int8_t *end = input + depth, *w1 = weights + depth;
  int32_t a0 = 0, a1 = 0;
  do {
    const int32_t x = int32_t(*input++) + offset;
    a0 += x * int32_t(*weights++);
    a1 += x * int32_t(*w1++);
  } while (input != end);
  return {a0, a1};
}

__attribute__((noinline)) void Dot4(const int8_t* input,
    const int8_t* weights, int depth, int offset, int32_t* result) {
  const int8_t *end = input + depth, *w1 = weights + depth;
  const int8_t *w2 = w1 + depth, *w3 = w2 + depth;
  int32_t a0 = 0, a1 = 0, a2 = 0, a3 = 0;
  do {
    const int32_t x = int32_t(*input++) + offset;
    a0 += x * int32_t(*weights++);
    a1 += x * int32_t(*w1++);
    a2 += x * int32_t(*w2++);
    a3 += x * int32_t(*w3++);
  } while (input != end);
  result[0] = a0; result[1] = a1; result[2] = a2; result[3] = a3;
}

bool Eligible(const TfLiteEvalTensor* input, const TfLiteEvalTensor* filter,
              const TfLiteEvalTensor* bias, const TfLiteEvalTensor* output,
              const TfLiteConvParams& p, const OpDataConv& d) {
#ifdef USE_TFLM_COMPRESSION
  return false;
#endif
  if (!input || !filter || !bias || !output ||
      input->type != kTfLiteInt8 || filter->type != kTfLiteInt8 ||
      output->type != kTfLiteInt8 || bias->type != kTfLiteInt32 ||
      !input->dims || !filter->dims || !bias->dims || !output->dims ||
      input->dims->size != 4 || filter->dims->size != 4 ||
      output->dims->size != 4 || bias->dims->size != 1 ||
      !input->data.int8 || !filter->data.int8 || !bias->data.i32 ||
      !output->data.int8 || !d.per_channel_output_multiplier ||
      !d.per_channel_output_shift || p.stride_width != 1 ||
      p.stride_height != 1 || p.dilation_width_factor != 1 ||
      p.dilation_height_factor != 1 || d.padding.width || d.padding.height ||
      d.input_zero_point < -128 || d.input_zero_point > 127 ||
      d.filter_zero_point != 0 || d.output_zero_point < -128 ||
      d.output_zero_point > 127 || d.output_activation_min < -128 ||
      d.output_activation_max > 127 ||
      d.output_activation_min > d.output_activation_max) return false;
  const int *in = input->dims->data, *f = filter->dims->data;
  const int *out = output->dims->data;
  if (f[1] != 1 || f[2] != 1 || in[3] <= 0 || in[3] > 65535 ||
      f[3] != in[3] || out[3] <= 0 || out[3] != f[0] ||
      bias->dims->data[0] != out[3]) return false;
  int64_t pixels = 1;
  for (int i = 0; i < 3; ++i) {
    if (in[i] <= 0 || in[i] != out[i] || pixels > INT_MAX / in[i]) return false;
    pixels *= in[i];
  }
  if (pixels > INT_MAX / in[3] || pixels > INT_MAX / out[3] ||
      int64_t(out[3]) * in[3] > INT_MAX) return false;
  // Conservative independent-of-pixels proof, at most O(output_channels).
  // Includes -128 weights and both extreme input values, bias, the upstream
  // requantizer's left multiplication and the final output zero-point addition.
  const int offset = -d.input_zero_point;
  const int64_t max_x = std::max(128 - offset, 127 + offset);
  const int64_t dot_bound = max_x * 128 * in[3];
  for (int c = 0; c < out[3]; ++c) {
    const int s = d.per_channel_output_shift[c];
    const int64_t b = bias->data.i32[c];
    const int64_t bound = dot_bound + (b < 0 ? -b : b);
    if (s < -31 || s > 30 || d.per_channel_output_multiplier[c] < 0 ||
        bound > (int64_t(INT_MAX) - 255) / (int64_t(1) << std::max(0, s)))
      return false;
  }
  return true;
}

static int8_t Finish(int32_t dot, int channel, const int32_t* bias,
    const ConvParams& p, const int32_t* multiplier, const int32_t* shift) {
  int32_t acc = dot + bias[channel];
  acc = MultiplyByQuantizedMultiplier(acc, multiplier[channel], shift[channel]);
  acc += p.output_offset;
  acc = std::max(acc, p.quantized_activation_min);
  acc = std::min(acc, p.quantized_activation_max);
  return static_cast<int8_t>(acc);
}

void Pointwise(int pixels, int depth, int channels, const int8_t* input,
    const int8_t* weights, const int32_t* bias, const ConvParams& p,
    const int32_t* multiplier, const int32_t* shift, int8_t* output) {
  for (int pixel = 0; pixel < pixels; ++pixel) {
    int c = 0;
    for (; channels - c >= 4; c += 4) {
      int32_t accum[4];
      Dot4(input, weights + c * depth, depth, p.input_offset, accum);
      for (int k = 0; k < 4; ++k)
        output[c + k] = Finish(accum[k], c + k, bias, p, multiplier, shift);
    }
    if (channels - c >= 2) {
      const DotPair a = Dot2(input, weights + c * depth, depth, p.input_offset);
      output[c] = Finish(a.first, c, bias, p, multiplier, shift);
      output[c + 1] = Finish(a.second, c + 1, bias, p, multiplier, shift);
      c += 2;
    }
    if (c < channels) {
      const int8_t* w = weights + c * depth;
      int32_t acc = 0;
      for (int i = 0; i < depth; ++i)
        acc += (int32_t(input[i]) + p.input_offset) * int32_t(w[i]);
      output[c] = Finish(acc, c, bias, p, multiplier, shift);
    }
    input += depth; output += channels;
  }
}

static TfLiteStatus Invoke(TfLiteContext* context, TfLiteNode* node) {
  if (!context || !node || !node->builtin_data || !node->user_data)
    return kTfLiteError;
  const TfLiteEvalTensor *input = GetEvalInput(context, node, kConvInputTensor);
  const TfLiteEvalTensor *filter = GetEvalInput(context, node, kConvWeightsTensor);
  const TfLiteEvalTensor *bias = NumInputs(node) == 3
      ? GetEvalInput(context, node, kConvBiasTensor) : nullptr;
  TfLiteEvalTensor *output = GetEvalOutput(context, node, kConvOutputTensor);
  const auto& p = *static_cast<TfLiteConvParams*>(node->builtin_data);
  const auto& d = *static_cast<OpDataConv*>(node->user_data);
  if (!Eligible(input, filter, bias, output, p, d)) {
#ifdef RICESTEP_POINTWISE_TEST_STATS
    ++fallback_count;
#endif
    return Register_CONV_2D().invoke(context, node);
  }
#ifdef RICESTEP_POINTWISE_TEST_STATS
  ++fast_count;
#endif
  const int *in = input->dims->data;
  Pointwise(in[0] * in[1] * in[2], in[3], output->dims->data[3],
            input->data.int8, filter->data.int8, bias->data.i32,
            ConvParamsQuantized(p, d), d.per_channel_output_multiplier,
            d.per_channel_output_shift, output->data.int8);
  return kTfLiteOk;
}

TFLMRegistration RegisterPointwise() {
  TFLMRegistration r = Register_CONV_2D();
  r.invoke = Invoke; // Exact reference Init and Prepare; zero extra arena bytes.
  return r;
}
}
