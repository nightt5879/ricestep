#include "pose_pointwise.h"
#include "tensorflow/lite/micro/kernels/internal/reference/integer_ops/conv.h"
#include <climits>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>
using namespace tflite::micro;
static unsigned checks;
#define CHECK(x) do { ++checks; if (!(x)) { std::fprintf(stderr,"FAIL %d: %s\n", __LINE__, #x); std::exit(1); } } while(0)
static uint32_t seed = 0x1278ed19;
static uint32_t Random() { seed ^= seed << 13; seed ^= seed >> 17; seed ^= seed << 5; return seed; }
struct FixtureDims { int size; int data[4]; };
struct Fixture {
  FixtureDims in_shape, f_shape, out_shape, b_shape;
  std::vector<int8_t> input, filter, actual, expected;
  std::vector<int32_t> bias, multiplier, shift;
  TfLiteEvalTensor tensors[4]{};
  TfLiteConvParams params{};
  OpDataConv data{};
  Fixture(int n, int h, int w, int depth, int channels) :
    in_shape{4,{n,h,w,depth}}, f_shape{4,{channels,1,1,depth}},
    out_shape{4,{n,h,w,channels}}, b_shape{1,{channels,0,0,0}},
    input(n*h*w*depth), filter(channels*depth), actual(n*h*w*channels + 32, 85),
    expected(n*h*w*channels + 32, 85), bias(channels), multiplier(channels), shift(channels) {
    tensors[0] = {{}, reinterpret_cast<TfLiteIntArray*>(&in_shape), kTfLiteInt8};
    tensors[1] = {{}, reinterpret_cast<TfLiteIntArray*>(&f_shape), kTfLiteInt8};
    tensors[2] = {{}, reinterpret_cast<TfLiteIntArray*>(&b_shape), kTfLiteInt32};
    tensors[3] = {{}, reinterpret_cast<TfLiteIntArray*>(&out_shape), kTfLiteInt8};
    tensors[0].data.int8 = input.data(); tensors[1].data.int8 = filter.data();
    tensors[2].data.i32 = bias.data(); tensors[3].data.int8 = actual.data()+16;
    params.stride_width = params.stride_height = 1;
    params.dilation_width_factor = params.dilation_height_factor = 1;
    data.per_channel_output_multiplier = multiplier.data();
    data.per_channel_output_shift = shift.data();
    data.output_activation_min = -128; data.output_activation_max = 127;
    for (int c = 0; c < channels; ++c) multiplier[c] = 1 << 30;
  }
  bool eligible() { return ricestep::pointwise::Eligible(tensors,tensors+1,tensors+2,tensors+3,params,data); }
  void compare() {
    const auto p = ConvParamsQuantized(params, data);
    const int depth = in_shape.data[3], channels = out_shape.data[3];
    reference_integer_ops::ConvPerChannel(p, multiplier.data(), shift.data(),
      RuntimeShape(4,in_shape.data), input.data(), RuntimeShape(4,f_shape.data),
      filter.data(), RuntimeShape(1,b_shape.data), bias.data(),
      RuntimeShape(4,out_shape.data), expected.data()+16);
    ricestep::pointwise::Pointwise(in_shape.data[0]*in_shape.data[1]*in_shape.data[2], depth,
      channels, input.data(), filter.data(), bias.data(), p,
      multiplier.data(), shift.data(), actual.data()+16);
    CHECK(actual == expected);
    // Exact reference accumulation for both blocked helpers (before rounding).
    if (channels >= 4) {
      int32_t result[4]{};
      ricestep::pointwise::Dot4(input.data(),filter.data(),depth,p.input_offset,result);
      const auto pair = ricestep::pointwise::Dot2(input.data(),filter.data(),depth,p.input_offset);
      for (int c=0;c<4;++c) {
        int64_t sum=0;
        for (int i=0;i<depth;++i) sum += int64_t(int(input[i])+p.input_offset)*filter[c*depth+i];
        CHECK(result[c] == sum);
      }
      CHECK(pair.first == result[0] && pair.second == result[1]);
    }
  }
};
int main() {
  const auto original = Register_CONV_2D(), candidate = ricestep::pointwise::RegisterPointwise();
  CHECK(original.init == candidate.init && original.prepare == candidate.prepare);
  CHECK(original.free == candidate.free && original.reset == candidate.reset);
  CHECK(original.invoke != candidate.invoke);
  const int depths[] = {1,2,3,4,7,8,15,16,31,32,96,160,192,320,576,960};
  const int shifts[] = {-31,-15,-4,-1,0,1,3};
  const int offsets[] = {-127,0,128};
  for (int trial=0;trial<1600;++trial) {
    Fixture f(1+trial%2,1+trial%3,1+(trial/3)%3,depths[trial%16],1+trial%19);
    f.data.input_zero_point = -offsets[trial%3];
    f.data.output_zero_point = int(Random()%256)-128;
    for (auto& x : f.input) x = trial%7==0 ? -128 : trial%7==1 ? 127 : int(Random()%256)-128;
    for (auto& x : f.filter) x = trial%5==0 ? -128 : trial%5==1 ? 127 : int(Random()%256)-128;
    for (size_t c=0;c<f.bias.size();++c) {
      f.bias[c] = int(Random()%200001)-100000;
      f.multiplier[c] = c%5==0 ? INT_MAX : c%5==1 ? 0 : c%5==2 ? 1 : (Random() & INT_MAX);
      f.shift[c] = shifts[(trial+c)%7];
    }
    if (trial%11==0) { f.data.output_activation_min = -10; f.data.output_activation_max=7; }
    if (trial%13==0) { f.data.output_activation_min = 4; f.data.output_activation_max=4; }
    CHECK(f.eligible()); f.compare();
  }
  // Extreme legal int32 bias with zero dot is reference-defined; the safety
  // gate rejects it conservatively because other possible pixels could overflow.
  for (int b : {INT_MAX, INT_MIN, INT_MAX-255, INT_MIN+255}) {
    Fixture f(1,1,1,1,5);
    for(size_t c=0;c<f.bias.size();++c) { f.bias[c]=b; f.shift[c]=-1; }
    CHECK(!f.eligible()); f.compare();
  }
  // Individually exercise all eligibility boundaries without invoking invalid
  // reference tensors; unsupported shapes remain the upstream responsibility.
  Fixture base(2,3,4,8,7); CHECK(base.eligible());
#define REJECT(change,restore) do { change; CHECK(!base.eligible()); restore; CHECK(base.eligible()); } while(0)
  REJECT(base.params.stride_width=2,base.params.stride_width=1);
  REJECT(base.params.stride_height=2,base.params.stride_height=1);
  REJECT(base.params.dilation_width_factor=2,base.params.dilation_width_factor=1);
  REJECT(base.params.dilation_height_factor=2,base.params.dilation_height_factor=1);
  REJECT(base.data.padding.width=1,base.data.padding.width=0);
  REJECT(base.data.padding.height=1,base.data.padding.height=0);
  REJECT(base.f_shape.data[1]=3,base.f_shape.data[1]=1);
  REJECT(base.f_shape.data[2]=3,base.f_shape.data[2]=1);
  REJECT(base.f_shape.data[3]=4,base.f_shape.data[3]=8); // groups != 1
  REJECT(base.out_shape.data[0]=1,base.out_shape.data[0]=2);
  REJECT(base.out_shape.data[1]=2,base.out_shape.data[1]=3);
  REJECT(base.out_shape.data[2]=3,base.out_shape.data[2]=4);
  REJECT(base.out_shape.data[3]=6,base.out_shape.data[3]=7);
  REJECT(base.b_shape.data[0]=6,base.b_shape.data[0]=7);
  REJECT(base.in_shape.data[0]=0,base.in_shape.data[0]=2);
  REJECT(base.in_shape.size=3,base.in_shape.size=4);
  REJECT(base.tensors[0].type=kTfLiteFloat32,base.tensors[0].type=kTfLiteInt8);
  REJECT(base.tensors[1].type=kTfLiteInt4,base.tensors[1].type=kTfLiteInt8);
  REJECT(base.tensors[2].type=kTfLiteInt64,base.tensors[2].type=kTfLiteInt32);
  REJECT(base.tensors[3].type=kTfLiteInt16,base.tensors[3].type=kTfLiteInt8);
  REJECT(base.data.filter_zero_point=1,base.data.filter_zero_point=0);
  REJECT(base.data.input_zero_point=-129,base.data.input_zero_point=0);
  REJECT(base.data.output_zero_point=128,base.data.output_zero_point=0);
  REJECT(base.data.output_activation_min=-129,base.data.output_activation_min=-128);
  REJECT(base.data.output_activation_max=-129,base.data.output_activation_max=127);
  REJECT(base.shift[0]=31,base.shift[0]=0);
  REJECT(base.shift[0]=-32,base.shift[0]=0);
  REJECT(base.multiplier[0]=-1,base.multiplier[0]=1<<30);
  REJECT(base.bias[0]=INT_MIN,base.bias[0]=0);
  REJECT(base.data.output_activation_max=128,base.data.output_activation_max=127);
  REJECT(base.f_shape.size=3,base.f_shape.size=4);
  REJECT(base.b_shape.size=2,base.b_shape.size=1);
  REJECT(base.out_shape.size=3,base.out_shape.size=4);
  REJECT(base.data.per_channel_output_multiplier=nullptr,
         base.data.per_channel_output_multiplier=base.multiplier.data());
  REJECT(base.data.per_channel_output_shift=nullptr,
         base.data.per_channel_output_shift=base.shift.data());
  REJECT(base.tensors[0].data.int8=nullptr,base.tensors[0].data.int8=base.input.data());
  REJECT(base.tensors[1].data.int8=nullptr,base.tensors[1].data.int8=base.filter.data());
  REJECT(base.tensors[2].data.i32=nullptr,base.tensors[2].data.i32=base.bias.data());
  REJECT(base.tensors[3].data.int8=nullptr,base.tensors[3].data.int8=base.actual.data()+16);
  base.in_shape.data[0]=base.out_shape.data[0]=INT_MAX;
  CHECK(!base.eligible()); // product checked before multiplication/array access
  base.in_shape.data[0]=base.out_shape.data[0]=2; CHECK(base.eligible());
  base.f_shape.data[0]=base.out_shape.data[3]=base.b_shape.data[0]=INT_MAX;
  CHECK(!base.eligible()); // oversized channel/filter products reject before scan
  base.f_shape.data[0]=base.out_shape.data[3]=base.b_shape.data[0]=7;
  CHECK(base.eligible());
  REJECT(base.in_shape.data[3]=65536,base.in_shape.data[3]=8);
  // Exact conservative bound: one additional bias unit switches to reference.
  const int32_t largest_bias = INT_MAX - 255 - 8*128*128;
  base.bias[0]=largest_bias; CHECK(base.eligible()); base.compare();
  base.bias[0]=largest_bias+1; CHECK(!base.eligible());
  base.bias[0]=-largest_bias; CHECK(base.eligible()); base.compare();
  base.bias[0]=-largest_bias-1; CHECK(!base.eligible());
  base.bias[0]=0;
  base.shift[0]=1;
  base.bias[0]=(INT_MAX-255)/2-8*128*128; CHECK(base.eligible()); base.compare();
  ++base.bias[0]; CHECK(!base.eligible());
  base.bias[0]=0; base.shift[0]=0;
  CHECK(!ricestep::pointwise::Eligible(base.tensors,base.tensors+1,nullptr,base.tensors+3,base.params,base.data));
  CHECK(!ricestep::pointwise::Eligible(nullptr,base.tensors+1,base.tensors+2,base.tensors+3,base.params,base.data));
  std::printf("PASS %u checks; 1600 seeded random/extreme cases; reference exact, no devices.\n",checks);
}
