add_executable(f8cppengine_service
  src/main.cpp
  src/cppengine_service.cpp
  src/operators.cpp
)

target_compile_features(f8cppengine_service PRIVATE cxx_std_17)

target_include_directories(f8cppengine_service
  PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/include
)

find_package(cxxopts REQUIRED)
find_package(spdlog REQUIRED)
find_package(nlohmann_json REQUIRED)
find_package(mexce REQUIRED)

target_link_libraries(f8cppengine_service
  PRIVATE
    f8cppsdk
    cxxopts::cxxopts
    spdlog::spdlog
    nlohmann_json::nlohmann_json
    mexce::mexce
)

target_compile_definitions(f8cppengine_service PRIVATE NOMINMAX WIN32_LEAN_AND_MEAN)

f8_deploy_service_runtime(f8cppengine_service "f8/cppengine")
