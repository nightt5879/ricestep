#pragma once
#include "tensorflow/lite/micro/kernels/conv.h"
#include "tensorflow/lite/micro/kernels/kernel_util.h"
namespace ricestep::pointwise {
struct DotPair { int32_t first, second; };
DotPair Dot2(const int8_t*, const int8_t*, int, int);
void Dot4(const int8_t*, const int8_t*, int, int, int32_t*);
bool Eligible(const tflite::micro::TfLiteEvalTensor*,
              const tflite::micro::TfLiteEvalTensor*,
              const tflite::micro::TfLiteEvalTensor*,
              const tflite::micro::TfLiteEvalTensor*,
              const tflite::micro::TfLiteConvParams&,
              const tflite::micro::OpDataConv&);
// Internal prepared-tensor primitives: Eligible must succeed before Pointwise.
// Dot2/Dot4 require positive depth and two/four complete filter rows. They do
// not accept arbitrary buffers or allocate memory. Exposed for host parity tests.
void Pointwise(int pixels, int depth, int channels, const int8_t* input,
               const int8_t* weights, const int32_t* bias,
               const tflite::micro::ConvParams& params,
               const int32_t* multiplier, const int32_t* shift, int8_t* output);
tflite::micro::TFLMRegistration RegisterPointwise();
#ifdef RICESTEP_POINTWISE_TEST_STATS
void AuditCounts(unsigned long long* fast, unsigned long long* fallback);
#endif
}
