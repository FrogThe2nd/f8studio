#include "f8cppsdk/latest_video_frame_transport.h"

#include <algorithm>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iomanip>
#include <iostream>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

namespace {

using clock_type = std::chrono::steady_clock;

struct Args {
  unsigned width = 1920;
  unsigned height = 1080;
  int iterations = 120;
  int firehose_iterations = 180;
  int warmup_iterations = 5;
  std::uint64_t zenoh_shm_pool_bytes = 512ULL * 1024ULL * 1024ULL;
};

struct Stats {
  std::string name;
  std::string backend;
  std::string category;
  std::size_t payload_bytes = 0;
  int iterations = 0;
  int delivered = 0;
  int lost = 0;
  double elapsed_s = 0.0;
  double publish_elapsed_s = 0.0;
  double throughput_ops_s = 0.0;
  double publish_throughput_ops_s = 0.0;
  double throughput_mib_s = 0.0;
  double publish_throughput_mib_s = 0.0;
  std::optional<double> p50_ms;
  std::optional<double> p95_ms;
  std::optional<double> p99_ms;
  bool ok = true;
  std::string note;
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

std::optional<std::int64_t> read_i64_le(const std::vector<std::byte>& payload) {
  if (payload.size() < 8) {
    return std::nullopt;
  }
  std::uint64_t raw = 0;
  for (unsigned index = 0; index < 8; ++index) {
    raw |= static_cast<std::uint64_t>(std::to_integer<unsigned char>(payload[index])) << (index * 8u);
  }
  return static_cast<std::int64_t>(raw);
}

std::optional<double> percentile(std::vector<double> sorted, double pct) {
  if (sorted.empty()) {
    return std::nullopt;
  }
  if (sorted.size() == 1) {
    return sorted.front();
  }
  std::sort(sorted.begin(), sorted.end());
  const double rank = (pct / 100.0) * static_cast<double>(sorted.size() - 1);
  const auto low = static_cast<std::size_t>(rank);
  const auto high = std::min<std::size_t>(low + 1, sorted.size() - 1);
  const double frac = rank - static_cast<double>(low);
  return sorted[low] * (1.0 - frac) + sorted[high] * frac;
}

Stats make_stats(std::string name, std::string backend, std::string category, std::size_t payload_bytes,
                 int iterations, int delivered, double elapsed_s, double publish_elapsed_s,
                 const std::vector<double>& latencies_ms, bool ok, std::string note = {}) {
  const double delivered_mib = static_cast<double>(delivered) * static_cast<double>(payload_bytes) / 1048576.0;
  const double published_mib = static_cast<double>(iterations) * static_cast<double>(payload_bytes) / 1048576.0;
  Stats out;
  out.name = std::move(name);
  out.backend = std::move(backend);
  out.category = std::move(category);
  out.payload_bytes = payload_bytes;
  out.iterations = iterations;
  out.delivered = delivered;
  out.lost = std::max(0, iterations - delivered);
  out.elapsed_s = elapsed_s;
  out.publish_elapsed_s = publish_elapsed_s;
  out.throughput_ops_s = elapsed_s > 0.0 ? static_cast<double>(delivered) / elapsed_s : 0.0;
  out.publish_throughput_ops_s = publish_elapsed_s > 0.0 ? static_cast<double>(iterations) / publish_elapsed_s : 0.0;
  out.throughput_mib_s = elapsed_s > 0.0 ? delivered_mib / elapsed_s : 0.0;
  out.publish_throughput_mib_s = publish_elapsed_s > 0.0 ? published_mib / publish_elapsed_s : 0.0;
  out.p50_ms = percentile(latencies_ms, 50.0);
  out.p95_ms = percentile(latencies_ms, 95.0);
  out.p99_ms = percentile(latencies_ms, 99.0);
  out.ok = ok;
  out.note = std::move(note);
  return out;
}

std::string optional_text(const std::optional<double>& value) {
  if (!value.has_value()) {
    return "";
  }
  std::ostringstream out;
  out << std::fixed << std::setprecision(6) << value.value();
  return out.str();
}

void print_stats(const Stats& item) {
  std::cout << item.name << "," << item.backend << "," << item.category << "," << item.payload_bytes << ","
            << item.iterations << "," << item.delivered << "," << item.lost << "," << std::fixed
            << std::setprecision(3) << item.throughput_ops_s << "," << item.publish_throughput_ops_s << ","
            << item.throughput_mib_s << "," << item.publish_throughput_mib_s << "," << optional_text(item.p50_ms)
            << "," << optional_text(item.p95_ms) << "," << optional_text(item.p99_ms) << ","
            << (item.ok ? "true" : "false") << "," << item.note << "\n";
}

std::vector<std::byte> make_payload(std::size_t payload_bytes) {
  std::vector<std::byte> payload(payload_bytes);
  for (std::size_t index = 8; index < std::min<std::size_t>(payload.size(), 4096); ++index) {
    payload[index] = static_cast<std::byte>(index % 251);
  }
  return payload;
}

f8::cppsdk::RuntimeBackendConfig zenoh_config(const Args& args) {
  f8::cppsdk::RuntimeBackendConfig config;
  config.bus_backend = f8::cppsdk::BusBackend::kZenoh;
  config.zenoh_shm_pool_bytes = args.zenoh_shm_pool_bytes;
  return config;
}

Stats bench_zenoh_roundtrip(const Args& args, unsigned channels, const std::string& run_id) {
  const unsigned pitch = args.width * channels;
  const std::size_t payload_bytes = static_cast<std::size_t>(pitch) * args.height;
  std::vector<std::byte> payload = make_payload(payload_bytes);
  const std::uint32_t format = channels == 4 ? f8::cppsdk::kVideoFormatBgra32 : 99u;
  const std::string key = "f8/bench/cpp/video/" + run_id + "/zenoh/" + std::to_string(channels);

  f8::cppsdk::ZenohLatestVideoFrameSubscriber subscriber;
  f8::cppsdk::ZenohLatestVideoFramePublisher publisher;
  if (!subscriber.open(zenoh_config(args), key) || !publisher.open(zenoh_config(args), key)) {
    return make_stats("zenoh_cpp.video_roundtrip." + std::to_string(args.width) + "x" +
                          std::to_string(args.height) + "x" + std::to_string(channels),
                      "zenoh", "video_roundtrip", payload_bytes, args.iterations, 0, 0.0, 0.0, {}, false,
                      "failed to open zenoh latest-frame transport");
  }

  for (int index = 0; index < args.warmup_iterations; ++index) {
    write_i64_le(payload, steady_now_ns());
    const f8::cppsdk::VideoFrameView frame{
        args.width, args.height, pitch, format, static_cast<std::uint64_t>(index + 1), wall_now_ms(), payload.data(),
        payload.size()};
    (void)publisher.publish_frame(frame);
    (void)subscriber.wait_latest(std::chrono::milliseconds(1000));
  }

  std::vector<double> latencies_ms;
  int delivered = 0;
  double publish_elapsed_s = 0.0;
  const auto start = clock_type::now();
  for (int index = 0; index < args.iterations; ++index) {
    write_i64_le(payload, steady_now_ns());
    const f8::cppsdk::VideoFrameView frame{
        args.width,
        args.height,
        pitch,
        format,
        static_cast<std::uint64_t>(args.warmup_iterations + index + 1),
        wall_now_ms(),
        payload.data(),
        payload.size(),
    };
    const auto publish_start = clock_type::now();
    if (!publisher.publish_frame(frame)) {
      continue;
    }
    publish_elapsed_s += seconds_since(publish_start);
    auto received = subscriber.wait_latest(std::chrono::milliseconds(1000));
    if (!received.has_value()) {
      continue;
    }
    const auto sent_ns = read_i64_le(received->payload);
    if (!sent_ns.has_value()) {
      continue;
    }
    latencies_ms.push_back(static_cast<double>(steady_now_ns() - sent_ns.value()) / 1'000'000.0);
    ++delivered;
  }
  const double elapsed_s = seconds_since(start);
  subscriber.close();
  publisher.close();
  return make_stats("zenoh_cpp.video_roundtrip." + std::to_string(args.width) + "x" + std::to_string(args.height) +
                        "x" + std::to_string(channels),
                    "zenoh", "video_roundtrip", payload_bytes, args.iterations, delivered, elapsed_s,
                    publish_elapsed_s, latencies_ms, delivered == args.iterations,
                    channels == 4 ? "BGRA32" : "BGR24 synthetic fmt=99");
}

Stats bench_zenoh_firehose(const Args& args, unsigned channels, const std::string& run_id) {
  const unsigned pitch = args.width * channels;
  const std::size_t payload_bytes = static_cast<std::size_t>(pitch) * args.height;
  std::vector<std::byte> payload = make_payload(payload_bytes);
  const std::uint32_t format = channels == 4 ? f8::cppsdk::kVideoFormatBgra32 : 99u;
  const std::string key = "f8/bench/cpp/video_firehose/" + run_id + "/zenoh/" + std::to_string(channels);

  f8::cppsdk::ZenohLatestVideoFrameSubscriber subscriber;
  f8::cppsdk::ZenohLatestVideoFramePublisher publisher;
  if (!subscriber.open(zenoh_config(args), key) || !publisher.open(zenoh_config(args), key)) {
    return make_stats("zenoh_cpp.video_firehose_latest." + std::to_string(args.width) + "x" +
                          std::to_string(args.height) + "x" + std::to_string(channels),
                      "zenoh", "video_firehose_latest", payload_bytes, args.firehose_iterations, 0, 0.0, 0.0, {},
                      false, "failed to open zenoh latest-frame transport");
  }

  for (int index = 0; index < args.warmup_iterations; ++index) {
    write_i64_le(payload, steady_now_ns());
    const f8::cppsdk::VideoFrameView frame{
        args.width, args.height, pitch, format, static_cast<std::uint64_t>(index + 1), wall_now_ms(), payload.data(),
        payload.size()};
    (void)publisher.publish_frame(frame);
    (void)subscriber.wait_latest(std::chrono::milliseconds(1000));
  }

  const auto start = clock_type::now();
  for (int index = 0; index < args.firehose_iterations; ++index) {
    write_i64_le(payload, steady_now_ns());
    const f8::cppsdk::VideoFrameView frame{
        args.width,
        args.height,
        pitch,
        format,
        static_cast<std::uint64_t>(args.warmup_iterations + index + 1),
        wall_now_ms(),
        payload.data(),
        payload.size(),
    };
    (void)publisher.publish_frame(frame);
  }
  const double publish_elapsed_s = seconds_since(start);

  std::optional<f8::cppsdk::LatestVideoFrame> latest;
  const auto deadline = clock_type::now() + std::chrono::seconds(2);
  while (clock_type::now() < deadline) {
    latest = subscriber.poll_latest();
    if (latest.has_value()) {
      break;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(1));
  }
  std::vector<double> latencies_ms;
  int delivered = 0;
  if (latest.has_value()) {
    const auto sent_ns = read_i64_le(latest->payload);
    if (sent_ns.has_value()) {
      latencies_ms.push_back(static_cast<double>(steady_now_ns() - sent_ns.value()) / 1'000'000.0);
      delivered = 1;
    }
  }
  const double elapsed_s = seconds_since(start);
  subscriber.close();
  publisher.close();
  return make_stats("zenoh_cpp.video_firehose_latest." + std::to_string(args.width) + "x" +
                        std::to_string(args.height) + "x" + std::to_string(channels),
                    "zenoh", "video_firehose_latest", payload_bytes, args.firehose_iterations, delivered, elapsed_s,
                    publish_elapsed_s, latencies_ms, delivered == 1,
                    "published frames; delivered count intentionally latest-slot only");
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
    if (flag == "--video-width") {
      args.width = static_cast<unsigned>(std::stoul(require_value(flag)));
    } else if (flag == "--video-height") {
      args.height = static_cast<unsigned>(std::stoul(require_value(flag)));
    } else if (flag == "--video-iterations") {
      args.iterations = std::stoi(require_value(flag));
    } else if (flag == "--video-firehose-iterations") {
      args.firehose_iterations = std::stoi(require_value(flag));
    } else if (flag == "--video-warmup-iterations") {
      args.warmup_iterations = std::stoi(require_value(flag));
    } else if (flag == "--zenoh-shm-pool-bytes") {
      args.zenoh_shm_pool_bytes = static_cast<std::uint64_t>(std::stoull(require_value(flag)));
    } else if (flag == "--help" || flag == "-h") {
      std::cout << "Usage: f8cpp_bench_latest_video_transport [--video-width N] [--video-height N]\n"
                << "       [--video-iterations N] [--video-firehose-iterations N]\n"
                << "       [--video-warmup-iterations N] [--zenoh-shm-pool-bytes N]\n";
      std::exit(0);
    } else {
      throw std::runtime_error("unknown argument: " + flag);
    }
  }
  args.iterations = std::max(1, args.iterations);
  args.firehose_iterations = std::max(1, args.firehose_iterations);
  args.warmup_iterations = std::max(0, args.warmup_iterations);
  return args;
}

}  // namespace

int main(int argc, char** argv) {
  try {
    const Args args = parse_args(argc, argv);
    const std::string run_id = std::to_string(static_cast<unsigned long long>(steady_now_ns()));
    std::cout << "name,backend,category,payload_bytes,iterations,delivered,lost,throughput_ops_s,"
                 "publish_throughput_ops_s,throughput_mib_s,publish_throughput_mib_s,p50_ms,p95_ms,p99_ms,ok,note\n";
    for (const unsigned channels : {3u, 4u}) {
      print_stats(bench_zenoh_roundtrip(args, channels, run_id));
      print_stats(bench_zenoh_firehose(args, channels, run_id));
    }
  } catch (const std::exception& exc) {
    std::cerr << "benchmark failed: " << exc.what() << "\n";
    return 2;
  }
  return 0;
}
