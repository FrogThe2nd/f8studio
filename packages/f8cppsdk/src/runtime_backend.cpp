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
  if (text == "mem") {
    backend = BusBackend::kMem;
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
  config.runtime_instance_id = trim_runtime_string(config.runtime_instance_id);
  config.zenoh_config_path = trim_runtime_string(config.zenoh_config_path);
  config.zenoh_connect = normalize_endpoint_list(config.zenoh_connect);
  config.zenoh_listen = normalize_endpoint_list(config.zenoh_listen);
  return config;
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

}  // namespace f8::cppsdk
