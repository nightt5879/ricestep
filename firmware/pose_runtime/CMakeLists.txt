cmake_minimum_required(VERSION 3.19)
if(CMAKE_SOURCE_DIR STREQUAL CMAKE_CURRENT_SOURCE_DIR)
    project(ricestep_pose_runtime LANGUAGES C CXX)
endif()

# Source preparation verifies archives and the upstream FlatBuffers patch.
# No network access, source downloads, or hardware actions occur in CMake.
foreach(required RICESTEP_TFLM_ROOT RICESTEP_FLATBUFFERS_ROOT RICESTEP_COMPONENTS_ROOT)
    if(NOT IS_DIRECTORY "${${required}}")
        message(FATAL_ERROR "Missing ${required}; run scripts/setup-pose-source.cjs")
    endif()
endforeach()
set(micro "${RICESTEP_TFLM_ROOT}/tensorflow/lite/micro")
set(core_sources
    debug_log.cc flatbuffer_conversions.cc flatbuffer_utils.cc
    memory_helpers.cc micro_allocation_info.cc micro_allocator.cc micro_common.cc
    micro_context.cc micro_interpreter_context.cc micro_interpreter_graph.cc
    micro_interpreter.cc micro_log.cc micro_op_resolver.cc micro_resource_variable.cc
    micro_utils.cc
    arena_allocator/non_persistent_arena_buffer_allocator.cc
    arena_allocator/persistent_arena_buffer_allocator.cc
    arena_allocator/single_arena_buffer_allocator.cc
    memory_planner/greedy_memory_planner.cc memory_planner/linear_memory_planner.cc
    memory_planner/non_persistent_buffer_planner_shim.cc
    kernels/internal/common.cc kernels/internal/quantization_util.cc
    kernels/internal/portable_tensor_utils.cc kernels/internal/tensor_ctypes.cc
    kernels/kernel_util.cc
    kernels/add.cc kernels/add_common.cc
    kernels/sub.cc kernels/sub_common.cc
    kernels/mul.cc kernels/mul_common.cc
    kernels/conv.cc kernels/conv_common.cc
    kernels/depthwise_conv.cc kernels/depthwise_conv_common.cc
    kernels/resize_bilinear.cc)
list(TRANSFORM core_sources PREPEND "${micro}/")
add_library(ricestep_tflm STATIC ${core_sources})
target_include_directories(ricestep_tflm SYSTEM PUBLIC
    "${RICESTEP_TFLM_ROOT}" "${RICESTEP_FLATBUFFERS_ROOT}/include")
target_compile_features(ricestep_tflm PUBLIC cxx_std_17)
target_compile_definitions(ricestep_tflm PUBLIC
    TFLM_REMOVE_VIRTUAL_DELETE TF_LITE_DISABLE_X86_NEON
    FLATBUFFERS_LOCALE_INDEPENDENT=0 FLATBUFFERS_PREFER_PRINTF=1)

add_library(ricestep_pose STATIC
    "${RICESTEP_COMPONENTS_ROOT}/pose_engine/pose_engine.cc"
    "${RICESTEP_COMPONENTS_ROOT}/pose_pointwise/pose_pointwise.cc"
    "${RICESTEP_COMPONENTS_ROOT}/pose_decode/pose_movenet.c"
    "${RICESTEP_COMPONENTS_ROOT}/pose_image/pose_image.c"
    "${RICESTEP_COMPONENTS_ROOT}/pose_resources/pose_resources.c"
    "${RICESTEP_COMPONENTS_ROOT}/motion_logic/motion_logic.c"
    "${RICESTEP_COMPONENTS_ROOT}/pose_session/pose_session.c")
target_include_directories(ricestep_pose PUBLIC
    "${RICESTEP_COMPONENTS_ROOT}/pose_engine"
    "${RICESTEP_COMPONENTS_ROOT}/pose_pointwise"
    "${RICESTEP_COMPONENTS_ROOT}/pose_decode"
    "${RICESTEP_COMPONENTS_ROOT}/pose_image"
    "${RICESTEP_COMPONENTS_ROOT}/pose_resources"
    "${RICESTEP_COMPONENTS_ROOT}/motion_logic"
    "${RICESTEP_COMPONENTS_ROOT}/pose_session")
target_compile_features(ricestep_pose PUBLIC c_std_11 cxx_std_17)
target_link_libraries(ricestep_pose PUBLIC ricestep_tflm)
foreach(target ricestep_tflm ricestep_pose)
    # The fixed model is SHA-verified by the worker and explicitly validated by
    # pose_engine before allocation/invoke. Remove repeated upstream debug
    # assertions consistently across these two libraries, without leaking
    # NDEBUG to the SDK/application or disabling our explicit error checks.
    target_compile_definitions(${target} PRIVATE NDEBUG)
    target_compile_options(${target} PRIVATE
        -Os -ffunction-sections -fdata-sections
        -fno-fast-math -ffp-contract=off
        $<$<COMPILE_LANGUAGE:CXX>:-fno-exceptions;-fno-rtti;-fno-threadsafe-statics>)
endforeach()

if(TARGET listenai_interface)
    # The SDK adds -ffast-math via its interface. Per-source options are emitted
    # last so that exact decode semantics survive all inherited SDK flags.
    foreach(target ricestep_tflm ricestep_pose)
        target_link_libraries(${target} PUBLIC listenai_interface)
        get_target_property(sources ${target} SOURCES)
        set_source_files_properties(${sources} PROPERTIES
            COMPILE_OPTIONS "-fno-fast-math;-ffp-contract=off")
        listenai_append_cmake_library(${target})
    endforeach()
    # A link-only diagnostic: retain callable entry points without invoking any
    # constructor, allocating an arena, opening a camera, or starting a task.
    target_link_options(ricestep_pose INTERFACE
        -Wl,-u,pose_engine_prepare -Wl,-u,pose_engine_invoke
        -Wl,-u,pose_engine_decode -Wl,-u,pose_engine_release
        -Wl,-u,pose_engine_input -Wl,-u,pose_engine_heads
        -Wl,-u,pose_engine_context_bytes -Wl,-u,pose_engine_context_alignment
        -Wl,-u,pose_engine_arena_used_bytes
        -Wl,-u,rp_model_validate -Wl,-u,pi_prepare)
endif()

# The reference convolution kernels otherwise make checked multidimensional
# Offset calls for every MAC under -Os. Keep exact arithmetic, but let these two
# hot upstream translation units inline/hoist indexing. The project pointwise
# kernel also needs O3 for its register-only dot loops. Applied after SDK flags.
set_property(SOURCE "${micro}/kernels/conv.cc" "${micro}/kernels/depthwise_conv.cc"
    "${RICESTEP_COMPONENTS_ROOT}/pose_pointwise/pose_pointwise.cc"
    APPEND PROPERTY COMPILE_OPTIONS -O3)
