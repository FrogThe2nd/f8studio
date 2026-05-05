#include "f8cppsdk/runtime_backend.h"

#include <algorithm>
#include <cctype>
#include <cstdlib>
#include <limits>
#include <utility>

namespace f8::cppsdk {
namespace {

std::string lower_ascii(std::string_view value) {
  std::string out;
  out.reserve(value.size());
  for (const char ch : value) {
    const auto uch = static_cast<unsigned char>(ch);
    out.push_back(static_cast<char>(std::tolower(uch)));
  }
  return out;
}

const char* env_value(const char* name) {
  const char* value = std::getenv(name);
  if (value == nullptr || value[0] == '\0') {
    return nullptr;
  }
  return value;
}

std::uint64_t parse_u64_or_default(std::string_view value, std::uint64_t fallback) {
  const std::string trimmed = trim_runtime_string(value);
  if (trimmed.empty()) {
    return fallback;
  }
  std::uint64_t result = 0;
  for (const char ch : trimmed) {
    if (ch < '0' || ch > '9') {
      return fallback;
    }
    const std::uint64_t digit = static_cast<std::uint64_t>(ch - '0');
    if (result > (std::numeric_limits<std::uint64_t>::max() - digit) / 10ULL) {
      return fallback;
    }
    result = result * 10ULL + digit;
  }
  return result;
}

}  // namespace

std::string bus_backend_to_string(BusBackend backend) {
  switch (backend) {
    case BusBackend::kZenoh:
      return "zenoh";
    case BusBackend::kNats:
      return "nats";
    case BusBackend::kMem:
      return "mem";
  }
  return "zenoh";
}

bool parse_bus_backend(std::string_view value, BusBackend& backend) {
  const std::string text = lower_ascii(trim_runtime_string(value));
  if (text == "zenoh") {
    backend = BusBackend::kZenoh;
    return true;
  }
  if (text == "nats") {
    backend = BusBackend::kNats;
    return true;
  }
  if (text == "mem") {
    backend = BusBackend::kMem;
    return true;
  }
  return false;
}

std::string video_transport_backend_to_string(VideoTransportBackend backend) {
  switch (backend) {
    case VideoTransportBackend::kAuto:
      return "auto";
    case VideoTransportBackend::kZenoh:
      return "zenoh";
    case VideoTransportBackend::kLegacyShm:
      return "legacy_shm";
  }
  return "auto";
}

bool parse_video_transport_backend(std::string_view value, VideoTransportBackend& backend) {
  const std::string text = lower_ascii(trim_runtime_string(value));
  if (text == "auto" || text.empty()) {
    backend = VideoTransportBackend::kAuto;
    return true;
  }
  if (text == "zenoh") {
    backend = VideoTransportBackend::kZenoh;
    return true;
  }
  if (text == "legacy_shm" || text == "legacy-shm" || text == "shm") {
    backend = VideoTransportBackend::kLegacyShm;
    return true;
  }
  return false;
}

std::string audio_transport_backend_to_string(AudioTransportBackend backend) {
  switch (backend) {
    case AudioTransportBackend::kAuto:
      return "auto";
    case AudioTransportBackend::kZenoh:
      return "zenoh";
    case AudioTransportBackend::kLegacyShm:
      return "legacy_shm";
  }
  return "auto";
}

bool parse_audio_transport_backend(std::string_view value, AudioTransportBackend& backend) {
  const std::string text = lower_ascii(trim_runtime_string(value));
  if (text == "auto" || text.empty()) {
    backend = AudioTransportBackend::kAuto;
    return true;
  }
  if (text == "zenoh") {
    backend = AudioTransportBackend::kZenoh;
    return true;
  }
  if (text == "legacy_shm" || text == "legacy-shm" || text == "shm") {
    backend = AudioTransportBackend::kLegacyShm;
    return true;
  }
  return false;
}

std::string trim_runtime_string(std::string_view value) {
  std::size_t begin = 0;
  while (begin < value.size()) {
    const auto ch = static_cast<unsigned char>(value[begin]);
    if (!std::isspace(ch)) {
      break;
    }
    ++begin;
  }

  std::size_t end = value.size();
  while (end > begin) {
    const auto ch = static_cast<unsigned char>(value[end - 1]);
    if (!std::isspace(ch)) {
      break;
    }
    --end;
  }

  return std::string(value.substr(begin, end - begin));
}

std::vector<std::string> normalize_endpoint_list(const std::vector<std::string>& values) {
  std::vector<std::string> out;
  for (const std::string& value : values) {
    std::size_t begin = 0;
    while (begin <= value.size()) {
      const std::size_t comma = value.find(',', begin);
      const std::size_t end = comma == std::string::npos ? value.size() : comma;
      const std::string item = trim_runtime_string(std::string_view(value).substr(begin, end - begin));
      if (!item.empty()) {
        out.push_back(item);
      }
      if (comma == std::string::npos) {
        break;
      }
      begin = comma + 1;
    }
  }
  return out;
}

std::vector<std::string> parse_endpoint_list(std::string_view value) {
  return normalize_endpoint_list(std::vector<std::string>{std::string(value)});
}

RuntimeBackendConfig normalize_runtime_backend_config(RuntimeBackendConfig config) {
  config.nats_url = trim_runtime_string(config.nats_url);
  if (config.nats_url.empty()) {
    config.nats_url = kDefaultNatsUrl;
  }
  config.zenoh_config_path = trim_runtime_string(config.zenoh_config_path);
  config.zenoh_connect = normalize_endpoint_list(config.zenoh_connect);
  config.zenoh_listen = normalize_endpoint_list(config.zenoh_listen);
  return config;
}

RuntimeBackendConfig runtime_backend_config_with_legacy_nats_url(RuntimeBackendConfig config,
                                                                std::string_view legacy_nats_url) {
  const std::string legacy = trim_runtime_string(legacy_nats_url);
  if (!legacy.empty() && config.nats_url == kDefaultNatsUrl && legacy != kDefaultNatsUrl) {
    config.nats_url = legacy;
  }
  return normalize_runtime_backend_config(std::move(config));
}

RuntimeBackendConfig runtime_backend_config_from_env() {
  RuntimeBackendConfig config;

  const char* backend_text = env_value("F8_BUS_BACKEND");
  if (backend_text != nullptr) {
    BusBackend parsed = BusBackend::kZenoh;
    if (parse_bus_backend(backend_text, parsed)) {
      config.bus_backend = parsed;
    }
  }

  const char* nats_url = env_value("F8_NATS_URL");
  if (nats_url != nullptr) {
    config.nats_url = nats_url;
  }

  const char* zenoh_config = env_value("F8_ZENOH_CONFIG");
  if (zenoh_config != nullptr) {
    config.zenoh_config_path = zenoh_config;
  }

  const char* zenoh_connect = env_value("F8_ZENOH_CONNECT");
  if (zenoh_connect != nullptr) {
    config.zenoh_connect = parse_endpoint_list(zenoh_connect);
  }

  const char* zenoh_listen = env_value("F8_ZENOH_LISTEN");
  if (zenoh_listen != nullptr) {
    config.zenoh_listen = parse_endpoint_list(zenoh_listen);
  }

  const char* shm_pool_bytes = env_value("F8_ZENOH_SHM_POOL_BYTES");
  if (shm_pool_bytes != nullptr) {
    config.zenoh_shm_pool_bytes = parse_u64_or_default(shm_pool_bytes, config.zenoh_shm_pool_bytes);
  }

  return normalize_runtime_backend_config(std::move(config));
}

VideoTransportBackend video_transport_backend_from_env() {
  const char* backend_text = env_value("F8_VIDEO_BACKEND");
  if (backend_text == nullptr) {
    return VideoTransportBackend::kAuto;
  }
  VideoTransportBackend backend = VideoTransportBackend::kAuto;
  if (parse_video_transport_backend(backend_text, backend)) {
    return backend;
  }
  return VideoTransportBackend::kAuto;
}

AudioTransportBackend audio_transport_backend_from_env() {
  const char* backend_text = env_value("F8_AUDIO_BACKEND");
  if (backend_text == nullptr) {
    return AudioTransportBackend::kAuto;
  }
  AudioTransportBackend backend = AudioTransportBackend::kAuto;
  if (parse_audio_transport_backend(backend_text, backend)) {
    return backend;
  }
  return AudioTransportBackend::kAuto;
}

}  // namespace f8::cppsdk
