cmake_minimum_required(VERSION 3.19)
project(ricestep_pose_session_tests LANGUAGES C)
get_filename_component(repo "${CMAKE_CURRENT_SOURCE_DIR}/../../.." ABSOLUTE)
set(components "${repo}/firmware/components")
add_executable(pose_session_test ../pose_session_test.c
    "${components}/pose_session/pose_session.c"
    "${components}/pose_image/pose_image.c"
    "${components}/motion_logic/motion_logic.c")
target_include_directories(pose_session_test PRIVATE
    "${components}/pose_session" "${components}/pose_image"
    "${components}/motion_logic" "${components}/pose_decode")
target_compile_features(pose_session_test PRIVATE c_std_11)
target_compile_options(pose_session_test PRIVATE -Wall -Wextra -Werror -fno-fast-math -ffp-contract=off -UNDEBUG)
if(NOT WIN32)
    target_link_libraries(pose_session_test PRIVATE m)
endif()
