# Included from the SDK's existing app-miniapp library after its Lua target.
listenai_include_directories(${CMAKE_CURRENT_LIST_DIR})
listenai_library_sources(
    ${CMAKE_CURRENT_LIST_DIR}/rice_standalone.c
    ${CMAKE_CURRENT_LIST_DIR}/rice_audio.c
    ${CMAKE_CURRENT_LIST_DIR}/rice_storage.c
    ${CMAKE_CURRENT_LIST_DIR}/voice_pack.c
    ${CMAKE_CURRENT_LIST_DIR}/miniapp_bundle.c
)
target_compile_features(app-miniapp PRIVATE c_std_11)
listenai_add_linker_section(
    FILE ${CMAKE_CURRENT_LIST_DIR}/rice_assets.ld
    SLOT ROM
    SORT_KEY "50-ricestep-assets"
)

include(${CMAKE_CURRENT_LIST_DIR}/pose-config.cmake)
if(RICESTEP_POSE_LINK_CHECK)
    add_subdirectory(${CMAKE_CURRENT_LIST_DIR}/pose_runtime
                     ${CMAKE_CURRENT_BINARY_DIR}/pose_runtime)
endif()
