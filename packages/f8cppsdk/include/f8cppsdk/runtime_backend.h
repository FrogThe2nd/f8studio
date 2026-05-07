#pragma once

#include <cstdint>
#include <string>
#include <string_view>
#include <vector>

namespace f8::cppsdk {

inline constexpr std::uint64_t kDefaultZenohShmPoolBytes = 256ULL * 1024ULL * 1024ULL;

enum class BusBackend {
  kZenoh,
  kMem,
};

struct RuntimeBackendConfig {
  BusBackend bus_backend = BusBackend::kZenoh;
  std::string zenoh_config_path;
  std::vector<std::string> zenoh_connect;
  std::vector<std::string> zenoh_listen;
  std::uint64_t zenoh_shm_pool_bytes = kDefaultZenohShmPoolBytes;
  bool announce_service_liveliness = false;
  std::string runtime_instance_id;
};

std::string bus_backend_to_string(BusBackend backend);
bool parse_bus_backend(std::string_view value, BusBackend& backend);
std::string trim_runtime_string(std::string_view value);
std::vector<std::string> normalize_endpoint_list(const std::vector<std::string>& values);
std::vector<std::string> parse_endpoint_list(std::string_view value);
RuntimeBackendConfig normalize_runtime_backend_config(RuntimeBackendConfig config);
RuntimeBackendConfig runtime_backend_config_from_env();

}  // namespace f8::cppsdk
