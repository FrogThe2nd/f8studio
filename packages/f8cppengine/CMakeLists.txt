add_library(f8cppengine_core STATIC
  src/bandpass_filter_operator.cpp
  src/bone_filter_operator.cpp
  src/bone_selector_operator.cpp
  src/buttplug_out_operator.cpp
  src/cosine_operator.cpp
  src/cppengine_service.cpp
  src/cppengine_service_node.cpp
  src/core_operators.cpp
  src/data_expr_operator.cpp
  src/detrend_operator.cpp
  src/envelope_operator.cpp
  src/exec_sequence_operator.cpp
  src/handy_out_operator.cpp
  src/highpass_filter_operator.cpp
  src/lovense_mock_server_operator.cpp
  src/lovense_out_operator.cpp
  src/lowpass_filter_operator.cpp
  src/operator_common.cpp
  src/operators.cpp
  src/pending_operator_common.cpp
  src/periodicity_detector_operator.cpp
  src/phase_operator.cpp
  src/print_operator.cpp
  src/playback_sync_operator.cpp
  src/program_wave_operator.cpp
  src/quat_to_euler_operator.cpp
  src/range_map_operator.cpp
  src/rate_limiter_operator.cpp
  src/recorder_operator.cpp
  src/replayer_operator.cpp
  src/script_operators.cpp
  src/sequence_player_operator.cpp
  src/serial_out_operator.cpp
  src/silence_detector_operator.cpp
  src/skeleton_decoder_operator.cpp
  src/smooth_filter_operator.cpp
  src/state_expr_operator.cpp
  src/state_trigger_operator.cpp
  src/switch_mixer_operator.cpp
  src/tcode_operator.cpp
  src/tempest_operator.cpp
  src/tick_operator.cpp
  src/udp_in_operator.cpp
  src/udp_out_operator.cpp
  src/unsupported_operators.cpp
  src/vmc_decoder_operator.cpp
  src/wave_expr_operator.cpp
  src/wave_funscript_operator.cpp
  src/wave_pattern_operator.cpp
)

target_compile_features(f8cppengine_core PUBLIC cxx_std_17)

target_include_directories(f8cppengine_core
  PUBLIC
    ${CMAKE_CURRENT_SOURCE_DIR}/include
)

find_package(cxxopts REQUIRED)
find_package(spdlog REQUIRED)
find_package(nlohmann_json REQUIRED)
find_package(mexce REQUIRED)
find_package(pybind11 REQUIRED)

target_link_libraries(f8cppengine_core
  PUBLIC
    f8cppsdk
    nlohmann_json::nlohmann_json
  PRIVATE
    spdlog::spdlog
    mexce::mexce
    pybind11::embed
)

target_compile_definitions(f8cppengine_core PRIVATE NOMINMAX WIN32_LEAN_AND_MEAN)

add_executable(f8cppengine_service
  src/main.cpp
)

target_compile_features(f8cppengine_service PRIVATE cxx_std_17)

target_link_libraries(f8cppengine_service
  PRIVATE
    f8cppengine_core
    cxxopts::cxxopts
    spdlog::spdlog
    nlohmann_json::nlohmann_json
)

target_compile_definitions(f8cppengine_service PRIVATE NOMINMAX WIN32_LEAN_AND_MEAN)

f8_deploy_service_runtime(f8cppengine_service "f8/cppengine")

add_executable(f8cppengine_cpython_script_smoke EXCLUDE_FROM_ALL
  tests/test_cpython_script_operator.cpp
)

target_compile_features(f8cppengine_cpython_script_smoke PRIVATE cxx_std_17)

target_link_libraries(f8cppengine_cpython_script_smoke
  PRIVATE
    f8cppengine_core
)

target_compile_definitions(f8cppengine_cpython_script_smoke PRIVATE NOMINMAX WIN32_LEAN_AND_MEAN)
