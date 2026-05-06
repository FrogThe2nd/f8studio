#include "f8cppsdk/f8_naming.h"
#include "f8cppsdk/runtime_backend.h"
#include "f8cppsdk/zenoh_transport.h"

#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

#include <nlohmann/json.hpp>

namespace {

struct Args {
  std::string mode;
  std::string service_id = "cpp_probe";
  std::string target_service_id;
  std::string peer_service_id;
  std::string endpoint = "echo";
  std::string payload = "ping";
  std::string state_key = "nodes.node.state.value";
  std::string state_payload = "cpp-state";
  std::string expected_payload;
  std::string ready_file;
  int timeout_ms = 3000;
  int duration_ms = 10000;
  std::uint64_t zenoh_shm_pool_bytes = 256ULL * 1024ULL * 1024ULL;
};

std::vector<std::uint8_t> bytes_from_string(const std::string& value) {
  return std::vector<std::uint8_t>(value.begin(), value.end());
}

std::string string_from_bytes(const std::vector<std::uint8_t>& bytes) {
  return std::string(bytes.begin(), bytes.end());
}

void touch_ready_file(const std::string& path) {
  if (path.empty()) {
    return;
  }
  FILE* file = std::fopen(path.c_str(), "wb");
  if (file == nullptr) {
    throw std::runtime_error("failed to create ready file: " + path);
  }
  std::fputs("ready\n", file);
  std::fclose(file);
}

f8::cppsdk::RuntimeBackendConfig make_config(const Args& args) {
  f8::cppsdk::RuntimeBackendConfig config;
  config.bus_backend = f8::cppsdk::BusBackend::kZenoh;
  config.zenoh_shm_pool_bytes = args.zenoh_shm_pool_bytes;
  return config;
}

void print_json(const nlohmann::json& payload) {
  std::cout << payload.dump() << std::endl;
}

Args parse_args(int argc, char** argv) {
  Args args;
  for (int index = 1; index < argc; ++index) {
    const std::string flag = argv[index];
    auto require_value = [&](const std::string& name) -> std::string {
      if (index + 1 >= argc) {
        throw std::runtime_error("missing value for " + name);
      }
      ++index;
      return argv[index];
    };
    if (flag == "--mode") {
      args.mode = require_value(flag);
    } else if (flag == "--service-id") {
      args.service_id = require_value(flag);
    } else if (flag == "--target-service-id") {
      args.target_service_id = require_value(flag);
    } else if (flag == "--peer-service-id") {
      args.peer_service_id = require_value(flag);
    } else if (flag == "--endpoint") {
      args.endpoint = require_value(flag);
    } else if (flag == "--payload") {
      args.payload = require_value(flag);
    } else if (flag == "--state-key") {
      args.state_key = require_value(flag);
    } else if (flag == "--state-payload") {
      args.state_payload = require_value(flag);
    } else if (flag == "--expected-payload") {
      args.expected_payload = require_value(flag);
    } else if (flag == "--ready-file") {
      args.ready_file = require_value(flag);
    } else if (flag == "--timeout-ms") {
      args.timeout_ms = std::stoi(require_value(flag));
    } else if (flag == "--duration-ms") {
      args.duration_ms = std::stoi(require_value(flag));
    } else if (flag == "--zenoh-shm-pool-bytes") {
      args.zenoh_shm_pool_bytes = static_cast<std::uint64_t>(std::stoull(require_value(flag)));
    } else if (flag == "--help" || flag == "-h") {
      std::cout
          << "Usage: f8cpp_crosslang_runtime_probe --mode server|request|watch [options]\n"
          << "  --service-id ID\n"
          << "  --target-service-id ID\n"
          << "  --peer-service-id ID\n"
          << "  --endpoint NAME\n"
          << "  --payload TEXT\n"
          << "  --state-key KEY\n"
          << "  --state-payload TEXT\n"
          << "  --expected-payload TEXT\n"
          << "  --ready-file PATH\n"
          << "  --timeout-ms N\n";
      std::exit(0);
    } else {
      throw std::runtime_error("unknown argument: " + flag);
    }
  }
  if (args.mode.empty()) {
    throw std::runtime_error("--mode is required");
  }
  return args;
}

int run_server(const Args& args) {
  f8::cppsdk::ZenohTransport transport;
  if (!transport.connect(make_config(args), args.service_id)) {
    throw std::runtime_error("failed to connect cpp server transport");
  }

  std::atomic<bool> terminate{false};
  auto echo_handle = transport.serve(
      f8::cppsdk::svc_endpoint_key(args.service_id, args.endpoint),
      [](const f8::cppsdk::RuntimeMessage& message) {
        std::vector<std::uint8_t> response = bytes_from_string("cpp:");
        response.insert(response.end(), message.payload.begin(), message.payload.end());
        return response;
      });
  auto terminate_handle = transport.serve(
      f8::cppsdk::svc_endpoint_key(args.service_id, "terminate"),
      [&terminate](const f8::cppsdk::RuntimeMessage& message) {
        terminate.store(true, std::memory_order_release);
        return bytes_from_string("bye:" + string_from_bytes(message.payload));
      });
  if (!echo_handle || !echo_handle->valid() || !terminate_handle || !terminate_handle->valid()) {
    throw std::runtime_error("failed to serve cpp command endpoints");
  }
  if (!transport.retained_put(args.state_key, bytes_from_string(args.state_payload))) {
    throw std::runtime_error("failed to publish cpp retained state");
  }

  touch_ready_file(args.ready_file);
  const auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(args.duration_ms);
  while (!terminate.load(std::memory_order_acquire) && std::chrono::steady_clock::now() < deadline) {
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
  }

  print_json({{"mode", "server"}, {"terminated", terminate.load(std::memory_order_acquire)}});
  transport.close();
  return 0;
}

int run_request(const Args& args) {
  if (args.target_service_id.empty()) {
    throw std::runtime_error("--target-service-id is required for request mode");
  }
  f8::cppsdk::ZenohTransport transport;
  if (!transport.connect(make_config(args), args.service_id)) {
    throw std::runtime_error("failed to connect cpp request transport");
  }
  const auto response = transport.request(
      f8::cppsdk::svc_endpoint_key(args.target_service_id, args.endpoint),
      bytes_from_string(args.payload),
      std::chrono::milliseconds(args.timeout_ms));
  print_json(
      {{"mode", "request"},
       {"ok", response.has_value()},
       {"response", response.has_value() ? string_from_bytes(*response) : std::string()}});
  transport.close();
  return response.has_value() ? 0 : 2;
}

int run_watch(const Args& args) {
  if (args.peer_service_id.empty()) {
    throw std::runtime_error("--peer-service-id is required for watch mode");
  }
  f8::cppsdk::ZenohTransport transport;
  if (!transport.connect(make_config(args), args.service_id)) {
    throw std::runtime_error("failed to connect cpp watch transport");
  }

  std::atomic<bool> seen{false};
  std::string seen_key;
  std::string seen_payload;
  auto handle = transport.retained_watch(
      args.state_key,
      [&](const std::string& key, const f8::cppsdk::RuntimeBytes& payload) {
        seen_key = key;
        seen_payload = string_from_bytes(payload);
        if (args.expected_payload.empty() || seen_payload == args.expected_payload) {
          seen.store(true, std::memory_order_release);
        }
      });
  if (!handle || !handle->valid()) {
    throw std::runtime_error("failed to watch retained state");
  }
  touch_ready_file(args.ready_file);

  const auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(args.timeout_ms);
  while (!seen.load(std::memory_order_acquire) && std::chrono::steady_clock::now() < deadline) {
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
  }
  const bool ok = seen.load(std::memory_order_acquire);
  print_json({{"mode", "watch"}, {"ok", ok}, {"key", seen_key}, {"payload", seen_payload}});
  transport.close();
  return ok ? 0 : 3;
}

}  // namespace

int main(int argc, char** argv) {
  try {
    const Args args = parse_args(argc, argv);
    if (args.mode == "server") {
      return run_server(args);
    }
    if (args.mode == "request") {
      return run_request(args);
    }
    if (args.mode == "watch") {
      return run_watch(args);
    }
    throw std::runtime_error("unsupported mode: " + args.mode);
  } catch (const std::exception& exc) {
    std::cerr << "f8cpp_crosslang_runtime_probe: " << exc.what() << std::endl;
    return 1;
  }
}
