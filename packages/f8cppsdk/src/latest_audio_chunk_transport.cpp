#include "f8cppsdk/latest_audio_chunk_transport.h"

#include <condition_variable>
#include <cstring>
#include <limits>
#include <mutex>
#include <optional>
#include <utility>
#include <vector>

#include <nlohmann/json.hpp>
#include <spdlog/spdlog.h>

#if F8_WITH_ZENOH
#include <zenoh.hxx>
#endif

namespace f8::cppsdk {
namespace {

void set_error(std::string* error_message, std::string value) {
  if (error_message != nullptr) {
    *error_message = std::move(value);
  }
}

void append_u32_le(RuntimeBytes& out, std::uint32_t value) {
  out.push_back(static_cast<std::uint8_t>(value & 0xFFu));
  out.push_back(static_cast<std::uint8_t>((value >> 8u) & 0xFFu));
  out.push_back(static_cast<std::uint8_t>((value >> 16u) & 0xFFu));
  out.push_back(static_cast<std::uint8_t>((value >> 24u) & 0xFFu));
}

void append_u64_le(RuntimeBytes& out, std::uint64_t value) {
  for (unsigned shift = 0; shift < 64; shift += 8) {
    out.push_back(static_cast<std::uint8_t>((value >> shift) & 0xFFu));
  }
}

void append_i64_le(RuntimeBytes& out, std::int64_t value) {
  append_u64_le(out, static_cast<std::uint64_t>(value));
}

bool read_u32_le(const RuntimeBytes& data, std::size_t offset, std::uint32_t& out) {
  if (offset > data.size() || data.size() - offset < 4) {
    return false;
  }
  out = static_cast<std::uint32_t>(data[offset]) | (static_cast<std::uint32_t>(data[offset + 1]) << 8u) |
        (static_cast<std::uint32_t>(data[offset + 2]) << 16u) |
        (static_cast<std::uint32_t>(data[offset + 3]) << 24u);
  return true;
}

bool read_u64_le(const RuntimeBytes& data, std::size_t offset, std::uint64_t& out) {
  if (offset > data.size() || data.size() - offset < 8) {
    return false;
  }
  out = 0;
  for (unsigned index = 0; index < 8; ++index) {
    out |= static_cast<std::uint64_t>(data[offset + index]) << (index * 8u);
  }
  return true;
}

bool read_i64_le(const RuntimeBytes& data, std::size_t offset, std::int64_t& out) {
  std::uint64_t value = 0;
  if (!read_u64_le(data, offset, value)) {
    return false;
  }
  out = static_cast<std::int64_t>(value);
  return true;
}

std::string json_array_for_endpoints(const std::vector<std::string>& endpoints) {
  return nlohmann::json(endpoints).dump();
}

#if F8_WITH_ZENOH
RuntimeBytes payload_to_bytes(const zenoh::Bytes& payload) {
  return payload.as_vector();
}

zenoh::Bytes bytes_to_payload(const RuntimeBytes& payload) {
  return zenoh::Bytes(payload);
}

zenoh::Session::PutOptions realtime_drop_options() {
  zenoh::Session::PutOptions options = zenoh::Session::PutOptions::create_default();
  options.congestion_control = Z_CONGESTION_CONTROL_DROP;
  options.priority = Z_PRIORITY_REAL_TIME;
  options.is_express = true;
  return options;
}
#endif

}  // namespace

bool encode_zenoh_audio_chunk(const AudioChunkView& chunk, RuntimeBytes& out, std::string* error_message) {
  out.clear();
  if (chunk.sample_rate == 0 || chunk.channels == 0 || chunk.frames == 0 || chunk.bytes_per_frame == 0) {
    set_error(error_message, "sample_rate, channels, frames, and bytes_per_frame must be positive");
    return false;
  }
  if (chunk.format == 0) {
    set_error(error_message, "format must be positive");
    return false;
  }
  if (chunk.seq == 0) {
    set_error(error_message, "seq must be positive");
    return false;
  }
  if (chunk.payload == nullptr) {
    set_error(error_message, "payload must be non-null");
    return false;
  }
  const std::size_t expected_payload_bytes =
      static_cast<std::size_t>(chunk.frames) * static_cast<std::size_t>(chunk.bytes_per_frame);
  if (expected_payload_bytes == 0 || chunk.payload_bytes < expected_payload_bytes) {
    set_error(error_message, "payload is smaller than frames * bytes_per_frame");
    return false;
  }
  if (expected_payload_bytes > static_cast<std::size_t>(std::numeric_limits<std::uint32_t>::max())) {
    set_error(error_message, "payload is too large for zenoh audio chunk schema v1");
    return false;
  }

  out.reserve(static_cast<std::size_t>(kZenohAudioChunkHeaderBytes) + expected_payload_bytes);
  append_u32_le(out, kZenohAudioChunkMagic);
  append_u32_le(out, kZenohAudioChunkSchemaVersion);
  append_u32_le(out, kZenohAudioChunkHeaderBytes);
  append_u32_le(out, chunk.sample_rate);
  append_u32_le(out, chunk.channels);
  append_u32_le(out, chunk.format);
  append_u32_le(out, chunk.frames);
  append_u32_le(out, chunk.bytes_per_frame);
  append_u32_le(out, static_cast<std::uint32_t>(expected_payload_bytes));
  append_u64_le(out, chunk.seq);
  append_u64_le(out, chunk.frame_index);
  append_i64_le(out, chunk.ts_ms);
  const auto* begin = reinterpret_cast<const std::uint8_t*>(chunk.payload);
  out.insert(out.end(), begin, begin + expected_payload_bytes);
  return true;
}

bool decode_zenoh_audio_chunk(const RuntimeBytes& raw, LatestAudioChunk& out, std::string* error_message) {
  out = LatestAudioChunk{};
  if (raw.size() < kZenohAudioChunkHeaderBytes) {
    set_error(error_message, "payload is smaller than zenoh audio chunk header");
    return false;
  }

  std::uint32_t magic = 0;
  std::uint32_t version = 0;
  std::uint32_t header_bytes = 0;
  std::uint32_t sample_rate = 0;
  std::uint32_t channels = 0;
  std::uint32_t format = 0;
  std::uint32_t frames = 0;
  std::uint32_t bytes_per_frame = 0;
  std::uint32_t payload_bytes = 0;
  std::uint64_t seq = 0;
  std::uint64_t frame_index = 0;
  std::int64_t ts_ms = 0;
  if (!read_u32_le(raw, 0, magic) || !read_u32_le(raw, 4, version) || !read_u32_le(raw, 8, header_bytes) ||
      !read_u32_le(raw, 12, sample_rate) || !read_u32_le(raw, 16, channels) ||
      !read_u32_le(raw, 20, format) || !read_u32_le(raw, 24, frames) ||
      !read_u32_le(raw, 28, bytes_per_frame) || !read_u32_le(raw, 32, payload_bytes) ||
      !read_u64_le(raw, 36, seq) || !read_u64_le(raw, 44, frame_index) || !read_i64_le(raw, 52, ts_ms)) {
    set_error(error_message, "payload header is truncated");
    return false;
  }
  if (magic != kZenohAudioChunkMagic || version != kZenohAudioChunkSchemaVersion) {
    set_error(error_message, "unsupported zenoh audio chunk schema");
    return false;
  }
  if (header_bytes < kZenohAudioChunkHeaderBytes) {
    set_error(error_message, "invalid zenoh audio chunk header size");
    return false;
  }
  if (sample_rate == 0 || channels == 0 || format == 0 || frames == 0 || bytes_per_frame == 0 || seq == 0) {
    set_error(error_message, "invalid zenoh audio chunk metadata");
    return false;
  }
  const std::size_t expected_payload_bytes =
      static_cast<std::size_t>(frames) * static_cast<std::size_t>(bytes_per_frame);
  if (payload_bytes != expected_payload_bytes) {
    set_error(error_message, "zenoh audio chunk payload size does not match frames * bytes_per_frame");
    return false;
  }
  if (static_cast<std::size_t>(header_bytes) > raw.size() ||
      raw.size() - static_cast<std::size_t>(header_bytes) < static_cast<std::size_t>(payload_bytes)) {
    set_error(error_message, "zenoh audio chunk payload is truncated");
    return false;
  }

  out.sample_rate = sample_rate;
  out.channels = channels;
  out.format = format;
  out.frames = frames;
  out.bytes_per_frame = bytes_per_frame;
  out.seq = seq;
  out.frame_index = frame_index;
  out.ts_ms = ts_ms;
  out.payload.resize(payload_bytes);
  std::memcpy(out.payload.data(), raw.data() + header_bytes, payload_bytes);
  return true;
}

class ZenohLatestAudioChunkPublisher::Impl final {
 public:
  bool open(const RuntimeBackendConfig& config, const std::string& key_expr) {
#if F8_WITH_ZENOH
    std::lock_guard<std::mutex> lock(mu_);
    close_locked();

    RuntimeBackendConfig normalized = normalize_runtime_backend_config(config);
    const std::string key = trim_runtime_string(key_expr);
    if (key.empty()) {
      spdlog::error("zenoh audio publisher requires a non-empty key expression");
      return false;
    }

    try {
      zenoh::Config zenoh_config = normalized.zenoh_config_path.empty()
                                       ? zenoh::Config::create_default()
                                       : zenoh::Config::from_file(normalized.zenoh_config_path);
      if (!normalized.zenoh_connect.empty()) {
        zenoh_config.insert_json5("connect/endpoints", json_array_for_endpoints(normalized.zenoh_connect));
      }
      if (!normalized.zenoh_listen.empty()) {
        zenoh_config.insert_json5("listen/endpoints", json_array_for_endpoints(normalized.zenoh_listen));
      }
      zenoh_config.insert_json5("transport/shared_memory/enabled", "true");
      if (normalized.zenoh_shm_pool_bytes > 0) {
        try {
          zenoh_config.insert_json5("transport/shared_memory/pool_size",
                                    std::to_string(normalized.zenoh_shm_pool_bytes));
        } catch (const std::exception& exc) {
          spdlog::debug("zenoh C++ config does not expose shared-memory pool_size: {}", exc.what());
        }
      }

      session_ = std::make_unique<zenoh::Session>(zenoh::Session::open(std::move(zenoh_config)));
      key_expr_ = key;
      publish_failure_reported_ = false;
      return true;
    } catch (const std::exception& exc) {
      spdlog::error("zenoh audio publisher open failed key={}: {}", key, exc.what());
      close_locked();
      return false;
    } catch (...) {
      spdlog::error("zenoh audio publisher open failed key={}: unknown error", key);
      close_locked();
      return false;
    }
#else
    (void)config;
    (void)key_expr;
    spdlog::error("Zenoh audio publisher requested but f8cppsdk was built without F8_WITH_ZENOH");
    return false;
#endif
  }

  void close() {
    std::lock_guard<std::mutex> lock(mu_);
    close_locked();
  }

  bool publish_chunk(const AudioChunkView& chunk) {
    RuntimeBytes encoded;
    std::string error;
    if (!encode_zenoh_audio_chunk(chunk, encoded, &error)) {
      report_publish_failure("encode failed: " + error);
      return false;
    }

#if F8_WITH_ZENOH
    try {
      std::lock_guard<std::mutex> lock(mu_);
      if (!session_ || key_expr_.empty()) {
        return false;
      }
      session_->put(zenoh::KeyExpr(key_expr_), bytes_to_payload(encoded), realtime_drop_options());
      publish_failure_reported_ = false;
      return true;
    } catch (const std::exception& exc) {
      report_publish_failure(exc.what());
      return false;
    } catch (...) {
      report_publish_failure("unknown error");
      return false;
    }
#else
    (void)encoded;
    report_publish_failure("f8cppsdk was built without F8_WITH_ZENOH");
    return false;
#endif
  }

  bool valid() const {
    std::lock_guard<std::mutex> lock(mu_);
#if F8_WITH_ZENOH
    return session_ != nullptr && !key_expr_.empty();
#else
    return false;
#endif
  }

  std::string key_expr() const {
    std::lock_guard<std::mutex> lock(mu_);
    return key_expr_;
  }

 private:
  void close_locked() {
#if F8_WITH_ZENOH
    if (session_) {
      try {
        session_->close();
      } catch (const std::exception& exc) {
        spdlog::warn("zenoh audio publisher session close failed key={}: {}", key_expr_, exc.what());
      } catch (...) {
        spdlog::warn("zenoh audio publisher session close failed key={}: unknown error", key_expr_);
      }
    }
    session_.reset();
#endif
    key_expr_.clear();
    publish_failure_reported_ = false;
  }

  void report_publish_failure(const std::string& message) {
    std::lock_guard<std::mutex> lock(mu_);
    if (publish_failure_reported_) {
      return;
    }
    publish_failure_reported_ = true;
    spdlog::error("zenoh audio publish failed key={}: {}", key_expr_, message);
  }

  mutable std::mutex mu_;
  std::string key_expr_;
  bool publish_failure_reported_ = false;
#if F8_WITH_ZENOH
  std::unique_ptr<zenoh::Session> session_;
#endif
};

class ZenohLatestAudioChunkSubscriber::Impl final {
 public:
  bool open(const RuntimeBackendConfig& config, const std::string& key_expr) {
#if F8_WITH_ZENOH
    std::lock_guard<std::mutex> lock(mu_);
    close_locked();

    RuntimeBackendConfig normalized = normalize_runtime_backend_config(config);
    const std::string key = trim_runtime_string(key_expr);
    if (key.empty()) {
      spdlog::error("zenoh audio subscriber requires a non-empty key expression");
      return false;
    }

    try {
      zenoh::Config zenoh_config = normalized.zenoh_config_path.empty()
                                       ? zenoh::Config::create_default()
                                       : zenoh::Config::from_file(normalized.zenoh_config_path);
      if (!normalized.zenoh_connect.empty()) {
        zenoh_config.insert_json5("connect/endpoints", json_array_for_endpoints(normalized.zenoh_connect));
      }
      if (!normalized.zenoh_listen.empty()) {
        zenoh_config.insert_json5("listen/endpoints", json_array_for_endpoints(normalized.zenoh_listen));
      }
      zenoh_config.insert_json5("transport/shared_memory/enabled", "true");
      if (normalized.zenoh_shm_pool_bytes > 0) {
        try {
          zenoh_config.insert_json5("transport/shared_memory/pool_size",
                                    std::to_string(normalized.zenoh_shm_pool_bytes));
        } catch (const std::exception& exc) {
          spdlog::debug("zenoh C++ config does not expose shared-memory pool_size: {}", exc.what());
        }
      }

      session_ = std::make_unique<zenoh::Session>(zenoh::Session::open(std::move(zenoh_config)));
      key_expr_ = key;
      closed_ = false;
      latest_raw_.clear();
      latest_seq_ = 0;
      delivered_seq_ = 0;
      decode_failure_reported_ = false;
      subscriber_ = session_->declare_subscriber(
          zenoh::KeyExpr(key_expr_),
          [this](zenoh::Sample& sample) {
            try {
              RuntimeBytes raw = payload_to_bytes(sample.get_payload());
              {
                std::lock_guard<std::mutex> sample_lock(mu_);
                if (closed_) {
                  return;
                }
                latest_raw_ = std::move(raw);
                ++latest_seq_;
              }
              cv_.notify_all();
            } catch (const std::exception& exc) {
              spdlog::error("zenoh audio subscriber callback failed key={}: {}", key_expr_, exc.what());
            } catch (...) {
              spdlog::error("zenoh audio subscriber callback failed key={}: unknown error", key_expr_);
            }
          },
          []() {});
      return true;
    } catch (const std::exception& exc) {
      spdlog::error("zenoh audio subscriber open failed key={}: {}", key, exc.what());
      close_locked();
      return false;
    } catch (...) {
      spdlog::error("zenoh audio subscriber open failed key={}: unknown error", key);
      close_locked();
      return false;
    }
#else
    (void)config;
    (void)key_expr;
    spdlog::error("Zenoh audio subscriber requested but f8cppsdk was built without F8_WITH_ZENOH");
    return false;
#endif
  }

  void close() {
    std::lock_guard<std::mutex> lock(mu_);
    close_locked();
    cv_.notify_all();
  }

  std::optional<LatestAudioChunk> poll_latest() {
    RuntimeBytes raw;
    {
      std::lock_guard<std::mutex> lock(mu_);
      if (latest_seq_ == delivered_seq_ || latest_raw_.empty()) {
        return std::nullopt;
      }
      raw = latest_raw_;
      delivered_seq_ = latest_seq_;
    }
    return decode_latest(raw);
  }

  std::optional<LatestAudioChunk> wait_latest(std::chrono::milliseconds timeout) {
    if (auto chunk = poll_latest()) {
      return chunk;
    }
    if (timeout.count() <= 0) {
      return std::nullopt;
    }

    RuntimeBytes raw;
    {
      std::unique_lock<std::mutex> lock(mu_);
      const bool ready = cv_.wait_for(lock, timeout, [this]() {
        return closed_ || (latest_seq_ != delivered_seq_ && !latest_raw_.empty());
      });
      if (!ready || closed_ || latest_seq_ == delivered_seq_ || latest_raw_.empty()) {
        return std::nullopt;
      }
      raw = latest_raw_;
      delivered_seq_ = latest_seq_;
    }
    return decode_latest(raw);
  }

  bool valid() const {
    std::lock_guard<std::mutex> lock(mu_);
#if F8_WITH_ZENOH
    return session_ != nullptr && subscriber_.has_value() && !key_expr_.empty() && !closed_;
#else
    return false;
#endif
  }

  std::string key_expr() const {
    std::lock_guard<std::mutex> lock(mu_);
    return key_expr_;
  }

 private:
  void close_locked() {
#if F8_WITH_ZENOH
    if (subscriber_.has_value()) {
      try {
        std::move(*subscriber_).undeclare();
      } catch (const std::exception& exc) {
        spdlog::warn("zenoh audio subscriber undeclare failed key={}: {}", key_expr_, exc.what());
      } catch (...) {
        spdlog::warn("zenoh audio subscriber undeclare failed key={}: unknown error", key_expr_);
      }
      subscriber_.reset();
    }
    if (session_) {
      try {
        session_->close();
      } catch (const std::exception& exc) {
        spdlog::warn("zenoh audio subscriber session close failed key={}: {}", key_expr_, exc.what());
      } catch (...) {
        spdlog::warn("zenoh audio subscriber session close failed key={}: unknown error", key_expr_);
      }
    }
    session_.reset();
#endif
    closed_ = true;
    key_expr_.clear();
    latest_raw_.clear();
    latest_seq_ = 0;
    delivered_seq_ = 0;
    decode_failure_reported_ = false;
  }

  std::optional<LatestAudioChunk> decode_latest(const RuntimeBytes& raw) {
    LatestAudioChunk chunk;
    std::string error;
    if (!decode_zenoh_audio_chunk(raw, chunk, &error)) {
      std::lock_guard<std::mutex> lock(mu_);
      if (!decode_failure_reported_) {
        decode_failure_reported_ = true;
        spdlog::error("zenoh audio chunk decode failed key={}: {}", key_expr_, error);
      }
      return std::nullopt;
    }
    {
      std::lock_guard<std::mutex> lock(mu_);
      decode_failure_reported_ = false;
    }
    return chunk;
  }

  mutable std::mutex mu_;
  std::condition_variable cv_;
  std::string key_expr_;
  bool closed_ = true;
  RuntimeBytes latest_raw_;
  std::uint64_t latest_seq_ = 0;
  std::uint64_t delivered_seq_ = 0;
  bool decode_failure_reported_ = false;
#if F8_WITH_ZENOH
  std::unique_ptr<zenoh::Session> session_;
  std::optional<zenoh::Subscriber<void>> subscriber_;
#endif
};

ZenohLatestAudioChunkPublisher::ZenohLatestAudioChunkPublisher() : impl_(std::make_unique<Impl>()) {}
ZenohLatestAudioChunkPublisher::~ZenohLatestAudioChunkPublisher() {
  close();
}

bool ZenohLatestAudioChunkPublisher::open(const RuntimeBackendConfig& config, const std::string& key_expr) {
  return impl_->open(config, key_expr);
}

void ZenohLatestAudioChunkPublisher::close() {
  impl_->close();
}

bool ZenohLatestAudioChunkPublisher::publish_chunk(const AudioChunkView& chunk) {
  return impl_->publish_chunk(chunk);
}

bool ZenohLatestAudioChunkPublisher::valid() const {
  return impl_->valid();
}

std::string ZenohLatestAudioChunkPublisher::key_expr() const {
  return impl_->key_expr();
}

ZenohLatestAudioChunkSubscriber::ZenohLatestAudioChunkSubscriber() : impl_(std::make_unique<Impl>()) {}
ZenohLatestAudioChunkSubscriber::~ZenohLatestAudioChunkSubscriber() {
  close();
}

bool ZenohLatestAudioChunkSubscriber::open(const RuntimeBackendConfig& config, const std::string& key_expr) {
  return impl_->open(config, key_expr);
}

void ZenohLatestAudioChunkSubscriber::close() {
  impl_->close();
}

std::optional<LatestAudioChunk> ZenohLatestAudioChunkSubscriber::poll_latest() {
  return impl_->poll_latest();
}

std::optional<LatestAudioChunk> ZenohLatestAudioChunkSubscriber::wait_latest(std::chrono::milliseconds timeout) {
  return impl_->wait_latest(timeout);
}

bool ZenohLatestAudioChunkSubscriber::valid() const {
  return impl_->valid();
}

std::string ZenohLatestAudioChunkSubscriber::key_expr() const {
  return impl_->key_expr();
}

}  // namespace f8::cppsdk
