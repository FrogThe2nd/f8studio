#pragma once

#include <cstdlib>
#include <cstdint>
#include <string>
#include <utility>
#include <vector>

#include <cxxopts.hpp>

#include "f8cppsdk/runtime_backend.h"

namespace f8::cppsdk {

inline void add_runtime_backend_options(cxxopts::Options& options,
                                        const RuntimeBackendConfig& defaults = runtime_backend_config_from_env()) {
  const RuntimeBackendConfig normalized = normalize_runtime_backend_config(defaults);
  options.add_options()(
      "bus-backend", "Runtime bus backend: zenoh|nats|mem (env: F8_BUS_BACKEND, default: zenoh)",
      cxxopts::value<std::string>()->default_value(bus_backend_to_string(normalized.bus_backend)))(
      "nats-url", "Deprecated: NATS server URL; only used with --bus-backend nats (env: F8_NATS_URL)",
      cxxopts::value<std::string>()->default_value(normalized.nats_url))(
      "zenoh-config", "Zenoh config file path (env: F8_ZENOH_CONFIG)",
      cxxopts::value<std::string>()->default_value(normalized.zenoh_config_path))(
      "zenoh-connect", "Zenoh endpoint to connect to; repeatable or comma-separated (env: F8_ZENOH_CONNECT)",
      cxxopts::value<std::vector<std::string>>())(
      "zenoh-listen", "Zenoh endpoint to listen on; repeatable or comma-separated (env: F8_ZENOH_LISTEN)",
      cxxopts::value<std::vector<std::string>>())(
      "zenoh-shm-pool-bytes", "Zenoh shared-memory pool bytes (env: F8_ZENOH_SHM_POOL_BYTES)",
      cxxopts::value<std::uint64_t>()->default_value(std::to_string(normalized.zenoh_shm_pool_bytes)));
}

inline bool read_runtime_backend_options(const cxxopts::ParseResult& result, RuntimeBackendConfig& config,
                                         std::string& error_message) {
  RuntimeBackendConfig out = runtime_backend_config_from_env();

  BusBackend parsed_backend = BusBackend::kZenoh;
  const std::string backend_text = result["bus-backend"].as<std::string>();
  if (!parse_bus_backend(backend_text, parsed_backend)) {
    error_message = "Invalid --bus-backend: " + backend_text + " (expected zenoh, nats, or mem)";
    return false;
  }
  out.bus_backend = parsed_backend;
  out.nats_url = result["nats-url"].as<std::string>();
  out.zenoh_config_path = result["zenoh-config"].as<std::string>();

  if (result.count("zenoh-connect") > 0) {
    out.zenoh_connect = normalize_endpoint_list(result["zenoh-connect"].as<std::vector<std::string>>());
  }
  if (result.count("zenoh-listen") > 0) {
    out.zenoh_listen = normalize_endpoint_list(result["zenoh-listen"].as<std::vector<std::string>>());
  }
  out.zenoh_shm_pool_bytes = result["zenoh-shm-pool-bytes"].as<std::uint64_t>();

  config = normalize_runtime_backend_config(std::move(out));
  error_message.clear();
  return true;
}

inline bool runtime_nats_url_was_supplied(const cxxopts::ParseResult& result) {
  if (result.count("nats-url") > 0) {
    return true;
  }
  const char* raw = std::getenv("F8_NATS_URL");
  return raw != nullptr && !trim_runtime_string(raw).empty();
}

inline bool should_warn_ignored_nats_url(const cxxopts::ParseResult& result,
                                         const RuntimeBackendConfig& config) {
  return config.bus_backend != BusBackend::kNats && runtime_nats_url_was_supplied(result);
}

}  // namespace f8::cppsdk
