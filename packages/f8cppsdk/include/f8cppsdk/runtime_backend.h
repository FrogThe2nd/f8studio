#pragma once

#include <cstdint>
#include <string>
#include <string_view>
#include <vector>

namespace f8::cppsdk {

inline constexpr const char* kDefaultNatsUrl = "nats://127.0.0.1:4222";
inline constexpr std::uint64_t kDefaultZenohShmPoolBytes = 256ULL * 1024ULL * 1024ULL;

enum class BusBackend {
  kZenoh,
  kNats,
  kMem,
};

enum class VideoTransportBackend {
  kAuto,
  kZenoh,
  kLegacyShm,
};

struct RuntimeBackendConfig {
  BusBackend bus_backend = BusBackend::kZenoh;
  std::string nats_url = kDefaultNatsUrl;
  std::string zenoh_config_path;
  std::vector<std::string> zenoh_connect;
  std::vector<std::string> zenoh_listen;
  std::uint64_t zenoh_shm_pool_bytes = kDefaultZenohShmPoolBytes;
};

std::string bus_backend_to_string(BusBackend backend);
bool parse_bus_backend(std::string_view value, BusBackend& backend);
std::string video_transport_backend_to_string(VideoTransportBackend backend);
bool parse_video_transport_backend(std::string_view value, VideoTransportBackend& backend);
std::string trim_runtime_string(std::string_view value);
std::vector<std::string> normalize_endpoint_list(const std::vector<std::string>& values);
std::vector<std::string> parse_endpoint_list(std::string_view value);
RuntimeBackendConfig normalize_runtime_backend_config(RuntimeBackendConfig config);
RuntimeBackendConfig runtime_backend_config_with_legacy_nats_url(RuntimeBackendConfig config,
                                                                std::string_view legacy_nats_url);
RuntimeBackendConfig runtime_backend_config_from_env();
VideoTransportBackend video_transport_backend_from_env();

}  // namespace f8::cppsdk
