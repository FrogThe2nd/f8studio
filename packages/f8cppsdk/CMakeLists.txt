add_library(f8cppsdk
  src/describe_schema.cpp
  src/nats_client.cpp
  src/kv_store.cpp
  src/service_control_plane.cpp
  src/service_bus.cpp
  src/runtime_backend.cpp
  src/zenoh_naming.cpp
  src/zenoh_transport.cpp
  src/state_kv.cpp
  src/data_bus.cpp
  src/msg_codec.cpp
  src/rungraph_routes.cpp
  src/time_utils.cpp
  src/f8_naming.cpp
  src/shm_region.cpp
  src/shm_event_wait.cpp
  src/describe_builtins.cpp
  src/latest_video_frame_transport.cpp
  src/video_shared_memory_sink.cpp
  src/audio_shared_memory_sink.cpp
)

target_include_directories(f8cppsdk
  PUBLIC
    # Prefer generated headers from the build tree over any source-tree remnants.
    $<BUILD_INTERFACE:${CMAKE_CURRENT_BINARY_DIR}/generated>
    $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
)

target_compile_features(f8cppsdk PUBLIC cxx_std_17)

find_package(Python3 COMPONENTS Interpreter REQUIRED)

# Protocol model codegen (OpenAPI -> C++ header), similar to pysdk datamodel-code-generator.
set(F8CPP_PROTOCOL_MODELS_OUT
    ${CMAKE_CURRENT_BINARY_DIR}/generated/f8cppsdk/generated/protocol_models.h
)
add_custom_command(
  OUTPUT ${F8CPP_PROTOCOL_MODELS_OUT}
  COMMAND ${Python3_EXECUTABLE}
          ${CMAKE_SOURCE_DIR}/scripts/gen_cpp_protocol_models.py
          --protocol ${CMAKE_SOURCE_DIR}/schemas/protocol.yml
          --out ${F8CPP_PROTOCOL_MODELS_OUT}
  DEPENDS
    ${CMAKE_SOURCE_DIR}/scripts/gen_cpp_protocol_models.py
    ${CMAKE_SOURCE_DIR}/schemas/protocol.yml
  VERBATIM
)
add_custom_target(f8cppsdk_codegen_protocol_models DEPENDS ${F8CPP_PROTOCOL_MODELS_OUT})
add_dependencies(f8cppsdk f8cppsdk_codegen_protocol_models)

find_package(cnats REQUIRED)
find_package(nlohmann_json REQUIRED)
find_package(spdlog REQUIRED)

set(_f8_zenoh_link_targets "")
if(F8_WITH_ZENOH)
  find_package(zenohc CONFIG QUIET)
  set(_f8_zenoh_have_cxx FALSE)
  set(_f8_zenoh_have_c FALSE)
  if(TARGET zenohc::zenohc)
    set(_f8_zenoh_have_c TRUE)
    list(APPEND _f8_zenoh_link_targets zenohc::zenohc)
  elseif(TARGET zenohc::lib)
    set(_f8_zenoh_have_c TRUE)
    list(APPEND _f8_zenoh_link_targets zenohc::lib)
  endif()
  if(_f8_zenoh_have_c)
    find_package(zenohcxx CONFIG QUIET)
  endif()
  if(TARGET zenohcxx::zenohc)
    set(_f8_zenoh_have_cxx TRUE)
    list(APPEND _f8_zenoh_link_targets zenohcxx::zenohc)
  elseif(TARGET zenohcxx)
    set(_f8_zenoh_have_cxx TRUE)
    list(APPEND _f8_zenoh_link_targets zenohcxx)
  endif()
  if(_f8_zenoh_have_cxx AND _f8_zenoh_have_c)
    target_compile_definitions(f8cppsdk PUBLIC F8_WITH_ZENOH=1)
    target_link_libraries(f8cppsdk PUBLIC ${_f8_zenoh_link_targets})
  else()
    message(WARNING "F8_WITH_ZENOH=ON but zenohcxx/zenohc CMake packages were not found; building C++ Zenoh stubs only.")
    target_compile_definitions(f8cppsdk PUBLIC F8_WITH_ZENOH=0)
  endif()
else()
  target_compile_definitions(f8cppsdk PUBLIC F8_WITH_ZENOH=0)
endif()

target_link_libraries(f8cppsdk
  PUBLIC
    cnats::nats_static
    nlohmann_json::nlohmann_json
    spdlog::spdlog
)
