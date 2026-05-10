add_library(f8cppengine_core STATIC
  src/cppengine_service.cpp
  src/core_operators.cpp
  src/operator_common.cpp
  src/operators.cpp
  src/script_operators.cpp
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
