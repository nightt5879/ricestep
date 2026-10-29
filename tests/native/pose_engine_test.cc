// Host-only test: consumes verified files; never opens a camera/device.
#include "pose_engine.h"
#include "tensorflow/lite/micro/schema/schema_generated.h"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdint>
#include <vector>

static unsigned checks = 0;
#define CHECK(condition) do { ++checks; if (!(condition)) { \
    std::fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #condition); std::exit(1); \
} } while (0)

struct Buffer {
    std::vector<uint8_t> memory;
    uint8_t *data;
    size_t bytes;
    explicit Buffer(size_t count) : memory(count + 32), bytes(count) {
        data = reinterpret_cast<uint8_t *>((reinterpret_cast<uintptr_t>(memory.data()) + 15u) & ~uintptr_t(15));
    }
};
static void read_file(const char *path, uint8_t *target, size_t bytes) {
    FILE *file = std::fopen(path, "rb");
    CHECK(file != nullptr);
    CHECK(std::fread(target, 1, bytes, file) == bytes);
    CHECK(std::fgetc(file) == EOF);
    CHECK(std::fclose(file) == 0);
}
template <typename T, typename Table>
static void set_scalar(const Table *table, flatbuffers::voffset_t field, T value) {
    auto *mutable_table = reinterpret_cast<flatbuffers::Table *>(const_cast<Table *>(table));
    CHECK(mutable_table->SetField<T>(field, value));
}

int main(int argc, char **argv) {
    if (argc < 3) {
        std::fprintf(stderr, "Usage: pose_engine_test verified-model.tflite fixture.bin [fixture.bin ...]\n");
        return 2;
    }
    Buffer model(POSE_ENGINE_MODEL_BYTES), changed(POSE_ENGINE_MODEL_BYTES);
    Buffer context(pose_engine_context_bytes()), arena(POSE_ENGINE_MIN_ARENA_BYTES);
    read_file(argv[1], model.data, model.bytes);
    CHECK(context.bytes > 0 && context.bytes < 16384);
    CHECK(pose_engine_context_alignment() <= 16);
    CHECK(pose_engine_arena_used_bytes(nullptr) == 0);
    CHECK(std::strcmp(pose_engine_status_string(PE_OK), "ok") == 0);
    pose_engine_release(nullptr);
    pose_engine *engine = nullptr;
    auto prepare = [&](const uint8_t *data) {
        engine = reinterpret_cast<pose_engine *>(uintptr_t(1));
        return pose_engine_prepare(context.data, context.bytes, data, model.bytes,
                                   arena.data, arena.bytes, &engine);
    };
    CHECK(pose_engine_prepare(context.data, context.bytes, model.data, model.bytes,
                              arena.data, arena.bytes, nullptr) == PE_INVALID_ARGUMENT);
    CHECK(pose_engine_prepare(nullptr, context.bytes, model.data, model.bytes,
                              arena.data, arena.bytes, &engine) == PE_INVALID_ARGUMENT);
    CHECK(engine == nullptr);

    // Reject an output-pointer alias without corrupting the model, arena, or
    // context, including the would-be NULL write on an otherwise invalid call.
    uint8_t saved[sizeof(pose_engine *)];
    for (uint8_t *region : {model.data, context.data, arena.data}) {
        std::memcpy(saved, region, sizeof(saved));
        CHECK(pose_engine_prepare(context.data, context.bytes, model.data, model.bytes,
                                  arena.data, arena.bytes,
                                  reinterpret_cast<pose_engine **>(region)) == PE_INVALID_ARGUMENT);
        CHECK(std::memcmp(saved, region, sizeof(saved)) == 0);
    }
    CHECK(pose_engine_prepare(context.data, context.bytes - 1, model.data, model.bytes,
                              arena.data, arena.bytes, &engine) == PE_INVALID_ARGUMENT);
    CHECK(pose_engine_prepare(context.data + 1, context.bytes, model.data, model.bytes,
                              arena.data, arena.bytes, &engine) == PE_BAD_ALIGNMENT);
    CHECK(pose_engine_prepare(context.data, context.bytes, model.data + 1, model.bytes,
                              arena.data, arena.bytes, &engine) == PE_BAD_ALIGNMENT);
    CHECK(pose_engine_prepare(context.data, context.bytes, model.data, model.bytes,
                              arena.data + 1, arena.bytes, &engine) == PE_BAD_ALIGNMENT);
    CHECK(pose_engine_prepare(context.data, context.bytes, model.data, model.bytes - 1,
                              arena.data, arena.bytes, &engine) == PE_BAD_MODEL_SIZE);
    CHECK(pose_engine_prepare(context.data, context.bytes, model.data, model.bytes,
                              arena.data, 16, &engine) == PE_ARENA_TOO_SMALL);
    CHECK(pose_engine_prepare(arena.data, context.bytes, model.data, model.bytes,
                              arena.data, arena.bytes, &engine) == PE_INVALID_ARGUMENT);
    CHECK(engine == nullptr);

    std::memcpy(changed.data, model.data, model.bytes);
    changed.data[4] = 'X';
    CHECK(prepare(changed.data) == PE_INVALID_FLATBUFFER && engine == nullptr);
    std::memcpy(changed.data, model.data, model.bytes);
    std::memset(changed.data, 255, 4);
    CHECK(prepare(changed.data) == PE_INVALID_FLATBUFFER && engine == nullptr);
    std::memcpy(changed.data, model.data, model.bytes);
    set_scalar<uint32_t>(tflite::GetModel(changed.data), tflite::Model::VT_VERSION, 99);
    CHECK(prepare(changed.data) == PE_MODEL_PROFILE_MISMATCH && engine == nullptr);
    std::memcpy(changed.data, model.data, model.bytes);
    auto *graph = tflite::GetModel(changed.data)->subgraphs()->Get(0);
    flatbuffers::WriteScalar(const_cast<int32_t *>(graph->outputs()->data()), int32_t(276));
    CHECK(prepare(changed.data) == PE_MODEL_PROFILE_MISMATCH && engine == nullptr);
    std::memcpy(changed.data, model.data, model.bytes);
    graph = tflite::GetModel(changed.data)->subgraphs()->Get(0);
    const auto *input = graph->tensors()->Get(175);
    flatbuffers::WriteScalar(const_cast<float *>(input->quantization()->scale()->data()), 2.0f);
    CHECK(prepare(changed.data) == PE_MODEL_PROFILE_MISMATCH && engine == nullptr);
    std::memcpy(changed.data, model.data, model.bytes);
    const auto *m = tflite::GetModel(changed.data);
    const auto *op = m->subgraphs()->Get(0)->operators()->Get(0);
    set_scalar<int32_t>(m->operator_codes()->Get(op->opcode_index()), tflite::OperatorCode::VT_VERSION, 99);
    CHECK(prepare(changed.data) == PE_MODEL_PROFILE_MISMATCH && engine == nullptr);

    CHECK(prepare(model.data) == PE_OK && engine != nullptr);
    const size_t arena_used = pose_engine_arena_used_bytes(engine);
    CHECK(arena_used > 1000000 && arena_used <= arena.bytes);
    pose_movenet_heads heads;
    pose_movenet_landmark decoded[17];
    CHECK(pose_engine_heads(engine, &heads) == PE_NO_RESULT);
    CHECK(pose_engine_decode(engine, decoded, nullptr) == PE_NO_RESULT);
    CHECK(pose_engine_invoke(engine) == PE_NOT_READY);
    CHECK(pose_engine_input(engine, nullptr, nullptr) == PE_INVALID_ARGUMENT);
    constexpr size_t fixture_bytes = POSE_ENGINE_INPUT_BYTES + POSE_MOVENET_CENTER_BYTES +
        POSE_MOVENET_HEATMAP_BYTES + 2 * POSE_MOVENET_VECTOR_BYTES + sizeof(decoded);
    Buffer fixture(fixture_bytes);
    for (int file = 2; file < argc; ++file) {
        read_file(argv[file], fixture.data, fixture.bytes);
        int8_t *data = nullptr;
        size_t bytes = 0;
        CHECK(pose_engine_input(engine, &data, &bytes) == PE_OK);
        CHECK(bytes == POSE_ENGINE_INPUT_BYTES && data != nullptr);
        CHECK(pose_engine_heads(engine, &heads) == PE_NO_RESULT);
        std::memcpy(data, fixture.data, bytes);
        CHECK(pose_engine_invoke(engine) == PE_OK);
        CHECK(pose_engine_heads(engine, &heads) == PE_OK);
        const pose_movenet_span spans[] = {heads.center, heads.heatmap, heads.offsets, heads.regressions};
        const size_t sizes[] = {POSE_MOVENET_CENTER_BYTES, POSE_MOVENET_HEATMAP_BYTES,
                               POSE_MOVENET_VECTOR_BYTES, POSE_MOVENET_VECTOR_BYTES};
        size_t offset = POSE_ENGINE_INPUT_BYTES;
        for (unsigned i = 0; i < 4; ++i) {
            CHECK(spans[i].data != nullptr && spans[i].bytes == sizes[i]);
            CHECK(std::memcmp(spans[i].data, fixture.data + offset, sizes[i]) == 0);
            offset += sizes[i];
        }
        CHECK(pose_engine_decode(engine, decoded, nullptr) == PE_OK);
        CHECK(std::memcmp(decoded, fixture.data + offset, sizeof(decoded)) == 0);
        // The caller must reacquire input before another run; an accidental
        // repeated invoke also invalidates the preceding result.
        CHECK(pose_engine_invoke(engine) == PE_NOT_READY);
        CHECK(pose_engine_heads(engine, &heads) == PE_NO_RESULT);
        std::printf("Exact four-head and 17-landmark parity: %s\n", argv[file]);
    }
    pose_engine_release(engine);
    CHECK(prepare(model.data) == PE_OK && engine != nullptr);
    CHECK(pose_engine_heads(engine, &heads) == PE_NO_RESULT);
    pose_engine_release(engine);
    int8_t *data = reinterpret_cast<int8_t *>(uintptr_t(1));
    size_t bytes = 1;
    CHECK(pose_engine_input(nullptr, &data, &bytes) == PE_NOT_READY);
    CHECK(data == nullptr && bytes == 0);
    std::printf("PASS %u checks; context=%zu arena_used=%zu capacity=%zu; host CPU only.\n",
                checks, context.bytes, arena_used, arena.bytes);
    return 0;
}
