#pragma once

#include <cstdint>
#include <string>
#include <string_view>

#include <spdlog/spdlog.h>

#if F8_WITH_ZENOH
#include <zenoh.hxx>

namespace f8::cppsdk::zenoh_internal {

inline void insert_optional_json5(zenoh::Config& config, std::string_view key, std::string_view value,
                                  std::string_view context) {
  try {
    config.insert_json5(std::string(key), std::string(value));
  } catch (const std::exception& exc) {
    spdlog::debug("zenoh config key unavailable context={} key={}: {}", context, key, exc.what());
  }
}

inline void apply_shared_memory_config(zenoh::Config& config, std::uint64_t pool_bytes, std::string_view context) {
  config.insert_json5("transport/shared_memory/enabled", "true");
  insert_optional_json5(config, "transport/shared_memory/mode", "\"init\"", context);
  insert_optional_json5(config, "transport/shared_memory/transport_optimization/enabled", "true", context);
  if (pool_bytes == 0) {
    return;
  }
  const std::string pool_json = std::to_string(pool_bytes);
  insert_optional_json5(config, "transport/shared_memory/transport_optimization/pool_size", pool_json, context);
  insert_optional_json5(config, "transport/shared_memory/pool_size", pool_json, context);
}

inline void apply_timestamping_config(zenoh::Config& config, std::string_view context) {
  insert_optional_json5(config, "timestamping/enabled", "true", context);
}

}  // namespace f8::cppsdk::zenoh_internal
#endif
