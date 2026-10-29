#include "pose_engine.h"
#include "pose_pointwise.h"

#include <cstdint>
#include <new>

#include "flatbuffers/verifier.h"
#include "tensorflow/lite/micro/flatbuffer_utils.h"
#include "tensorflow/lite/micro/micro_interpreter.h"
#include "tensorflow/lite/micro/micro_mutable_op_resolver.h"
#include "tensorflow/lite/micro/schema/schema_generated.h"

#ifdef __FAST_MATH__
#error "The pose engine requires exact floating point semantics; disable fast-math."
#endif

namespace {
using Interpreter = tflite::MicroInterpreter;
using Resolver = tflite::MicroMutableOpResolver<6>;
using Tensor = tflite::micro::TfLiteTensor;

struct TensorProfile {
    int index;
    int shape[4];
    float scale;
    int zero_point;
    size_t bytes;
};
constexpr TensorProfile kInput = {175, {1, 192, 192, 3}, 1.0f, -128,
                                  POSE_ENGINE_INPUT_BYTES};
constexpr TensorProfile kOutputs[4] = {
    {259, {1, 48, 48, 1}, 0.2822169065475464f, 114, POSE_MOVENET_CENTER_BYTES},
    {276, {1, 48, 48, 17}, 0.28092747926712036f, 115, POSE_MOVENET_HEATMAP_BYTES},
    {282, {1, 48, 48, 34}, 0.19978104531764984f, -9, POSE_MOVENET_VECTOR_BYTES},
    {285, {1, 48, 48, 34}, 0.6884398460388184f, -21, POSE_MOVENET_VECTOR_BYTES}
};
struct OpProfile {
    tflite::BuiltinOperator code;
    tflite::BuiltinOptions options;
    int version;
    unsigned count;
};
constexpr OpProfile kOps[6] = {
    {tflite::BuiltinOperator_SUB, tflite::BuiltinOptions_SubOptions, 2, 2},
    {tflite::BuiltinOperator_MUL, tflite::BuiltinOptions_MulOptions, 2, 1},
    {tflite::BuiltinOperator_CONV_2D, tflite::BuiltinOptions_Conv2DOptions, 3, 50},
    {tflite::BuiltinOperator_DEPTHWISE_CONV_2D, tflite::BuiltinOptions_DepthwiseConv2DOptions, 3, 24},
    {tflite::BuiltinOperator_ADD, tflite::BuiltinOptions_AddOptions, 2, 13},
    {tflite::BuiltinOperator_RESIZE_BILINEAR, tflite::BuiltinOptions_ResizeBilinearOptions, 3, 3}
};

bool aligned(const void *p, size_t alignment) {
    return reinterpret_cast<uintptr_t>(p) % alignment == 0;
}
bool valid_region(const void *p, size_t bytes) {
    return p && bytes && reinterpret_cast<uintptr_t>(p) <= UINTPTR_MAX - bytes;
}
bool overlaps(const void *a, size_t a_bytes, const void *b, size_t b_bytes) {
    const uintptr_t x = reinterpret_cast<uintptr_t>(a);
    const uintptr_t y = reinterpret_cast<uintptr_t>(b);
    return x < y + b_bytes && y < x + a_bytes;
}

bool model_tensor_matches(const tflite::Tensor *tensor, const TensorProfile &p) {
    if (!tensor || tensor->type() != tflite::TensorType_INT8 ||
        tensor->is_variable() || !tensor->shape() || tensor->shape()->size() != 4)
        return false;
    for (unsigned i = 0; i < 4; ++i)
        if (tensor->shape()->Get(i) != p.shape[i]) return false;
    const auto *q = tensor->quantization();
    return q && q->scale() && q->scale()->size() == 1 &&
           q->zero_point() && q->zero_point()->size() == 1 &&
           q->scale()->Get(0) == p.scale && q->zero_point()->Get(0) == p.zero_point;
}

bool valid_indices(const flatbuffers::Vector<int32_t> *indices,
                   unsigned tensors, bool optional) {
    if (!indices || !indices->size()) return false;
    for (auto index : *indices)
        if ((index < 0 && !(optional && index == -1)) ||
            (index >= 0 && static_cast<unsigned>(index) >= tensors)) return false;
    return true;
}

// FlatBuffer verification bounds all table reads. This is a fixed-model profile
// check, not a replacement for the caller's whole-model SHA256 verification.
bool model_matches(const tflite::Model *model) {
    if (model->version() != TFLITE_SCHEMA_VERSION || !model->subgraphs() ||
        model->subgraphs()->size() != 1 || !model->operator_codes() ||
        !model->buffers() || !model->buffers()->size() ||
        (model->external_buffers() && model->external_buffers()->size()) ||
        (model->external_buffer_groups() && model->external_buffer_groups()->size()))
        return false;
    const auto *graph = model->subgraphs()->Get(0);
    if (!graph || !graph->tensors() || graph->tensors()->size() != 333 ||
        !graph->operators() || graph->operators()->size() != 93 ||
        !graph->inputs() || graph->inputs()->size() != 1 ||
        graph->inputs()->Get(0) != kInput.index || !graph->outputs() ||
        graph->outputs()->size() != 4) return false;
    if (!model_tensor_matches(graph->tensors()->Get(kInput.index), kInput)) return false;
    for (unsigned i = 0; i < 4; ++i) {
        if (graph->outputs()->Get(i) != kOutputs[i].index ||
            !model_tensor_matches(graph->tensors()->Get(kOutputs[i].index), kOutputs[i]))
            return false;
    }
    for (const auto *buffer : *model->buffers())
        if (!buffer || buffer->offset() || buffer->size()) return false;
    for (const auto *tensor : *graph->tensors())
        if (!tensor || tensor->is_variable() ||
            tensor->buffer() >= model->buffers()->size()) return false;
    unsigned counts[6] = {};
    for (const auto *op : *graph->operators()) {
        if (!op || op->opcode_index() >= model->operator_codes()->size() ||
            !valid_indices(op->inputs(), graph->tensors()->size(), true) ||
            !valid_indices(op->outputs(), graph->tensors()->size(), false)) return false;
        const auto *code = model->operator_codes()->Get(op->opcode_index());
        if (!code) return false;
        const auto builtin = tflite::micro::GetBuiltinCode(code);
        unsigned found = 6;
        for (unsigned i = 0; i < 6; ++i) if (builtin == kOps[i].code) found = i;
        if (found == 6 || code->version() != kOps[found].version ||
            op->builtin_options_type() != kOps[found].options || !op->builtin_options())
            return false;
        ++counts[found];
    }
    for (unsigned i = 0; i < 6; ++i) if (counts[i] != kOps[i].count) return false;
    return true;
}

bool runtime_tensor_matches(const Tensor *tensor, const TensorProfile &p) {
    if (!tensor || !tensor->data.int8 || tensor->type != tflite::micro::kTfLiteInt8 ||
        tensor->is_variable || tensor->bytes != p.bytes || !tensor->dims ||
        tensor->dims->size != 4 || tensor->params.scale != p.scale ||
        tensor->params.zero_point != p.zero_point) return false;
    for (unsigned i = 0; i < 4; ++i) if (tensor->dims->data[i] != p.shape[i]) return false;
    return true;
}
}  // namespace

// All mutable state is caller-owned. The resolver is constructed before the
// interpreter and remains alive throughout its destruction.
struct pose_engine {
    Resolver resolver;
    alignas(Interpreter) uint8_t interpreter_storage[sizeof(Interpreter)];
    Interpreter *interpreter = nullptr;
    Tensor *input = nullptr;
    pose_movenet_heads heads = {};
    bool input_ready = false;
    bool result_ready = false;
    bool faulted = false;
    ~pose_engine() {
        if (interpreter) interpreter->~Interpreter();
    }
};

extern "C" size_t pose_engine_context_bytes(void) { return sizeof(pose_engine); }
extern "C" size_t pose_engine_context_alignment(void) { return alignof(pose_engine); }

extern "C" const char *pose_engine_status_string(pose_engine_status status) {
    switch (status) {
    case PE_OK: return "ok";
    case PE_INVALID_ARGUMENT: return "invalid argument or overlapping buffers";
    case PE_BAD_ALIGNMENT: return "buffer alignment mismatch";
    case PE_BAD_MODEL_SIZE: return "model size mismatch";
    case PE_INVALID_FLATBUFFER: return "invalid model FlatBuffer";
    case PE_MODEL_PROFILE_MISMATCH: return "fixed MoveNet model profile mismatch";
    case PE_RESOLVER_FAILURE: return "operator registration failed";
    case PE_ALLOCATION_FAILURE: return "tensor allocation failed";
    case PE_NOT_READY: return "engine/input not ready";
    case PE_INVOKE_FAILURE: return "inference failed; release and prepare again";
    case PE_NO_RESULT: return "no successful inference result";
    case PE_ARENA_TOO_SMALL: return "arena below the 2 MiB safety floor";
    default: return "unknown pose engine status";
    }
}

extern "C" pose_engine_status pose_engine_prepare(
    void *context, size_t context_bytes, const uint8_t *model_data, size_t model_bytes,
    uint8_t *arena, size_t arena_bytes, pose_engine **out) {
    if (!valid_region(out, sizeof(*out)) || !aligned(out, alignof(pose_engine *)))
        return PE_INVALID_ARGUMENT;
    // The output handle itself must not overwrite immutable model bytes or
    // caller storage. Reject these aliases before attempting even a NULL write.
    if ((valid_region(context, context_bytes) && overlaps(out, sizeof(*out), context, context_bytes)) ||
        (valid_region(model_data, model_bytes) && overlaps(out, sizeof(*out), model_data, model_bytes)) ||
        (valid_region(arena, arena_bytes) && overlaps(out, sizeof(*out), arena, arena_bytes)))
        return PE_INVALID_ARGUMENT;
    *out = nullptr;
    if (!valid_region(context, context_bytes) || context_bytes < sizeof(pose_engine) ||
        !valid_region(model_data, model_bytes) || !valid_region(arena, arena_bytes) ||
        overlaps(context, context_bytes, model_data, model_bytes) ||
        overlaps(context, context_bytes, arena, arena_bytes) ||
        overlaps(model_data, model_bytes, arena, arena_bytes)) return PE_INVALID_ARGUMENT;
    if (!aligned(context, alignof(pose_engine)) ||
        !aligned(model_data, POSE_ENGINE_BUFFER_ALIGNMENT) ||
        !aligned(arena, POSE_ENGINE_BUFFER_ALIGNMENT)) return PE_BAD_ALIGNMENT;
    if (model_bytes != POSE_ENGINE_MODEL_BYTES) return PE_BAD_MODEL_SIZE;
    if (arena_bytes < POSE_ENGINE_MIN_ARENA_BYTES) return PE_ARENA_TOO_SMALL;
    flatbuffers::Verifier verifier(model_data, model_bytes);
    if (!tflite::VerifyModelBuffer(verifier)) return PE_INVALID_FLATBUFFER;
    const auto *model = tflite::GetModel(model_data);
    if (!model_matches(model)) return PE_MODEL_PROFILE_MISMATCH;

    auto *engine = new (context) pose_engine();
    if (engine->resolver.AddSub() != tflite::micro::kTfLiteOk ||
        engine->resolver.AddMul() != tflite::micro::kTfLiteOk ||
        engine->resolver.AddConv2D(ricestep::pointwise::RegisterPointwise()) != tflite::micro::kTfLiteOk ||
        engine->resolver.AddDepthwiseConv2D() != tflite::micro::kTfLiteOk ||
        engine->resolver.AddAdd() != tflite::micro::kTfLiteOk ||
        engine->resolver.AddResizeBilinear() != tflite::micro::kTfLiteOk) {
        engine->~pose_engine();
        return PE_RESOLVER_FAILURE;
    }
    engine->interpreter = new (engine->interpreter_storage)
        Interpreter(model, engine->resolver, arena, arena_bytes);
    if (engine->interpreter->initialization_status() != tflite::micro::kTfLiteOk ||
        engine->interpreter->AllocateTensors() != tflite::micro::kTfLiteOk) {
        engine->~pose_engine();
        return PE_ALLOCATION_FAILURE;
    }
    engine->input = engine->interpreter->input(0);
    bool valid = runtime_tensor_matches(engine->input, kInput);
    pose_movenet_span *spans[] = {&engine->heads.center, &engine->heads.heatmap,
                                &engine->heads.offsets, &engine->heads.regressions};
    for (unsigned i = 0; i < 4; ++i) {
        Tensor *tensor = engine->interpreter->output(i);
        if (!runtime_tensor_matches(tensor, kOutputs[i])) { valid = false; break; }
        *spans[i] = {tensor->data.int8, tensor->bytes};
    }
    if (!valid) {
        engine->~pose_engine();
        return PE_MODEL_PROFILE_MISMATCH;
    }
    *out = engine;
    return PE_OK;
}

extern "C" void pose_engine_release(pose_engine *engine) {
    if (engine) engine->~pose_engine();
}

extern "C" pose_engine_status pose_engine_input(pose_engine *engine, int8_t **data, size_t *bytes) {
    if (!data || !bytes) return PE_INVALID_ARGUMENT;
    *data = nullptr;
    *bytes = 0;
    if (!engine || engine->faulted) return PE_NOT_READY;
    engine->result_ready = false;
    engine->input_ready = true;
    *data = engine->input->data.int8;
    *bytes = engine->input->bytes;
    return PE_OK;
}

extern "C" pose_engine_status pose_engine_invoke(pose_engine *engine) {
    if (!engine || engine->faulted) return PE_NOT_READY;
    engine->result_ready = false;
    if (!engine->input_ready) return PE_NOT_READY;
    engine->input_ready = false;
    if (engine->interpreter->Invoke() != tflite::micro::kTfLiteOk) {
        engine->faulted = true;
        return PE_INVOKE_FAILURE;
    }
    engine->result_ready = true;
    return PE_OK;
}

extern "C" pose_engine_status pose_engine_heads(const pose_engine *engine, pose_movenet_heads *out) {
    if (!out) return PE_INVALID_ARGUMENT;
    if (!engine || engine->faulted) return PE_NOT_READY;
    if (!engine->result_ready) return PE_NO_RESULT;
    *out = engine->heads;
    return PE_OK;
}

extern "C" pose_engine_status pose_engine_decode(const pose_engine *engine,
    pose_movenet_landmark out[POSE_MOVENET_JOINTS], pose_movenet_trace *trace) {
    if (!out) return PE_INVALID_ARGUMENT;
    pose_movenet_heads heads;
    const auto status = pose_engine_heads(engine, &heads);
    if (status != PE_OK) return status;
    return pose_movenet_decode(&heads, out, trace) == POSE_MOVENET_OK
               ? PE_OK : PE_INVALID_ARGUMENT;
}

extern "C" size_t pose_engine_arena_used_bytes(const pose_engine *engine) {
    return engine ? engine->interpreter->arena_used_bytes() : 0;
}
