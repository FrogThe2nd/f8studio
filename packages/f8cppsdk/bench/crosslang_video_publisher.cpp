#include "f8cppsdk/latest_video_frame_transport.h"
#include "f8cppsdk/video_shared_memory_sink.h"

#include <algorithm>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

#include <nlohmann/json.hpp>

namespace {

using clock_type = std::chrono::steady_clock;

struct Args {
  std::string backend = "zenoh";
  std::string key_expr;
  std::string shm_name;
  std::string ready_file;
  unsigned width = 1920;
  unsigned height = 1080;
  unsigned channels = 3;
  int iterations = 240;
  int warmup_iterations = 10;
  double fps = 120.0;
  int start_delay_ms = 500;
  std::uint64_t zenoh_shm_pool_bytes = 512ULL * 1024ULL * 1024ULL;
};

std::int64_t steady_now_ns() {
  return std::chrono::duration_cast<std::chrono::nanoseconds>(clock_type::now().time_since_epoch()).count();
}

std::int64_t wall_now_ms() {
  return std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::system_clock::now().time_since_epoch())
      .count();
}

double seconds_since(clock_type::time_point start) {
  return std::chrono::duration<double>(clock_type::now() - start).count();
}

void write_i64_le(std::vector<std::byte>& payload, std::int64_t value) {
  if (payload.size() < 8) {
    return;
  }
  const auto raw = static_cast<std::uint64_t>(value);
  for (unsigned shift = 0; shift < 64; shift += 8) {
    payload[shift / 8] = static_cast<std::byte>((raw >> shift) & 0xFFULL);
  }
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

std::vector<std::byte> make_payload(std::size_t payload_bytes) {
  std::vector<std::byte> payload(payload_bytes);
  for (std::size_t index = 8; index < std::min<std::size_t>(payload.size(), 4096); ++index) {
    payload[index] = static_cast<std::byte>(index % 251);
  }
  return payload;
}

std::size_t shm_capacity(std::size_t frame_bytes) {
  return std::max<std::size_t>(256ULL * 1024ULL * 1024ULL, frame_bytes * 4ULL + 64ULL);
}

f8::cppsdk::RuntimeBackendConfig zenoh_config(const Args& args) {
  f8::cppsdk::RuntimeBackendConfig config;
  config.bus_backend = f8::cppsdk::BusBackend::kZenoh;
  config.zenoh_shm_pool_bytes = args.zenoh_shm_pool_bytes;
  return config;
}

nlohmann::json publish_zenoh(const Args& args) {
  const unsigned pitch = args.width * args.channels;
  const std::size_t payload_bytes = static_cast<std::size_t>(pitch) * args.height;
  const std::uint32_t format = args.channels == 4 ? f8::cppsdk::kVideoFormatBgra32 : 99u;
  std::vector<std::byte> payload = make_payload(payload_bytes);
  f8::cppsdk::ZenohLatestVideoFramePublisher publisher;
  if (!publisher.open(zenoh_config(args), args.key_expr)) {
    throw std::runtime_error("failed to open zenoh publisher");
  }
  touch_ready_file(args.ready_file);
  if (args.start_delay_ms > 0) {
    std::this_thread::sleep_for(std::chrono::milliseconds(args.start_delay_ms));
  }

  const int total = std::max(0, args.warmup_iterations) + std::max(1, args.iterations);
  const auto period = args.fps > 0.0 ? std::chrono::duration<double>(1.0 / args.fps) : std::chrono::duration<double>(0);
  auto next_publish = clock_type::now();
  int published = 0;
  int failed = 0;
  const auto start = clock_type::now();
  for (int index = 0; index < total; ++index) {
    if (args.fps > 0.0) {
      std::this_thread::sleep_until(next_publish);
      next_publish += std::chrono::duration_cast<clock_type::duration>(period);
    }
    write_i64_le(payload, steady_now_ns());
    const f8::cppsdk::VideoFrameView frame{
        args.width,
        args.height,
        pitch,
        format,
        static_cast<std::uint64_t>(index + 1),
        wall_now_ms(),
        payload.data(),
        payload.size(),
    };
    if (publisher.publish_frame(frame)) {
      ++published;
    } else {
      ++failed;
    }
  }
  const double elapsed_s = seconds_since(start);
  publisher.close();
  return nlohmann::json{
      {"backend", "zenoh"},
      {"payload_bytes", payload_bytes},
      {"published", published},
      {"failed", failed},
      {"elapsed_s", elapsed_s},
  };
}

nlohmann::json publish_legacy_shm(const Args& args) {
  const unsigned pitch = args.width * args.channels;
  const std::size_t payload_bytes = static_cast<std::size_t>(pitch) * args.height;
  const std::size_t capacity = shm_capacity(payload_bytes);
  const std::uint32_t format = args.channels == 4 ? f8::cppsdk::kVideoFormatBgra32 : 99u;
  std::vector<std::byte> payload = make_payload(payload_bytes);

  f8::cppsdk::VideoSharedMemorySink sink;
  if (!sink.initialize(args.shm_name, capacity, 2) ||
      !sink.ensureConfigurationForFormat(args.width, args.height, format, args.channels)) {
    throw std::runtime_error("failed to open legacy SHM publisher");
  }
  sink.set_unlink_on_close(true);
  touch_ready_file(args.ready_file);
  if (args.start_delay_ms > 0) {
    std::this_thread::sleep_for(std::chrono::milliseconds(args.start_delay_ms));
  }

  const int total = std::max(0, args.warmup_iterations) + std::max(1, args.iterations);
  const auto period = args.fps > 0.0 ? std::chrono::duration<double>(1.0 / args.fps) : std::chrono::duration<double>(0);
  auto next_publish = clock_type::now();
  int published = 0;
  int failed = 0;
  const auto start = clock_type::now();
  for (int index = 0; index < total; ++index) {
    if (args.fps > 0.0) {
      std::this_thread::sleep_until(next_publish);
      next_publish += std::chrono::duration_cast<clock_type::duration>(period);
    }
    write_i64_le(payload, steady_now_ns());
    if (sink.writeFrameWithFormat(payload.data(), pitch, format)) {
      ++published;
    } else {
      ++failed;
    }
  }
  const double elapsed_s = seconds_since(start);
  return nlohmann::json{
      {"backend", "legacy_shm"},
      {"payload_bytes", payload_bytes},
      {"published", published},
      {"failed", failed},
      {"elapsed_s", elapsed_s},
  };
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
    if (flag == "--backend") {
      args.backend = require_value(flag);
    } else if (flag == "--key") {
      args.key_expr = require_value(flag);
    } else if (flag == "--shm-name") {
      args.shm_name = require_value(flag);
    } else if (flag == "--ready-file") {
      args.ready_file = require_value(flag);
    } else if (flag == "--video-width") {
      args.width = static_cast<unsigned>(std::stoul(require_value(flag)));
    } else if (flag == "--video-height") {
      args.height = static_cast<unsigned>(std::stoul(require_value(flag)));
    } else if (flag == "--channels") {
      args.channels = static_cast<unsigned>(std::stoul(require_value(flag)));
    } else if (flag == "--iterations") {
      args.iterations = std::stoi(require_value(flag));
    } else if (flag == "--warmup-iterations") {
      args.warmup_iterations = std::stoi(require_value(flag));
    } else if (flag == "--fps") {
      args.fps = std::stod(require_value(flag));
    } else if (flag == "--start-delay-ms") {
      args.start_delay_ms = std::stoi(require_value(flag));
    } else if (flag == "--zenoh-shm-pool-bytes") {
      args.zenoh_shm_pool_bytes = static_cast<std::uint64_t>(std::stoull(require_value(flag)));
    } else if (flag == "--help" || flag == "-h") {
      std::cout << "Usage: f8cpp_crosslang_video_publisher --backend zenoh|legacy_shm [--key KEY]\n"
                << "       [--shm-name NAME] [--ready-file PATH] [--video-width N] [--video-height N]\n"
                << "       [--channels 3|4] [--iterations N] [--warmup-iterations N] [--fps N]\n";
      std::exit(0);
    } else {
      throw std::runtime_error("unknown argument: " + flag);
    }
  }
  if (args.channels != 3 && args.channels != 4) {
    throw std::runtime_error("--channels must be 3 or 4");
  }
  if (args.backend != "zenoh" && args.backend != "legacy_shm") {
    throw std::runtime_error("--backend must be zenoh or legacy_shm");
  }
  if (args.backend == "zenoh" && args.key_expr.empty()) {
    throw std::runtime_error("--key is required for zenoh");
  }
  if (args.backend == "legacy_shm" && args.shm_name.empty()) {
    throw std::runtime_error("--shm-name is required for legacy_shm");
  }
  args.iterations = std::max(1, args.iterations);
  args.warmup_iterations = std::max(0, args.warmup_iterations);
  args.start_delay_ms = std::max(0, args.start_delay_ms);
  return args;
}

}  // namespace

int main(int argc, char** argv) {
  try {
    const Args args = parse_args(argc, argv);
    nlohmann::json result = args.backend == "zenoh" ? publish_zenoh(args) : publish_legacy_shm(args);
    result["width"] = args.width;
    result["height"] = args.height;
    result["channels"] = args.channels;
    result["iterations"] = args.iterations;
    result["warmup_iterations"] = args.warmup_iterations;
    result["fps"] = args.fps;
    std::cout << result.dump() << "\n";
  } catch (const std::exception& exc) {
    std::cerr << "cross-language video publisher failed: " << exc.what() << "\n";
    return 2;
  }
  return 0;
}
