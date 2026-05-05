#include "f8cppsdk/latest_video_frame_transport.h"

#include "zenoh_config_internal.h"

#include <algorithm>
#include <chrono>
#include <condition_variable>
#include <cstring>
#include <limits>
#include <mutex>
#include <optional>
#include <string>
#include <thread>
#include <utility>
#include <variant>
#include <vector>

#include <nlohmann/json.hpp>
#include <spdlog/spdlog.h>

#if F8_WITH_ZENOH
#include <zenoh.hxx>
#endif

namespace f8::cppsdk {
namespace {

constexpr std::chrono::milliseconds kSubscriptionSettle{10};

void set_error(std::string* error_message, std::string value) {
  if (error_message != nullptr) {
    *error_message = std::move(value);
  }
}

void write_u32_le(std::uint8_t* out, std::size_t offset, std::uint32_t value) {
  out[offset] = static_cast<std::uint8_t>(value & 0xFFu);
  out[offset + 1] = static_cast<std::uint8_t>((value >> 8u) & 0xFFu);
  out[offset + 2] = static_cast<std::uint8_t>((value >> 16u) & 0xFFu);
  out[offset + 3] = static_cast<std::uint8_t>((value >> 24u) & 0xFFu);
}

void write_u64_le(std::uint8_t* out, std::size_t offset, std::uint64_t value) {
  for (unsigned shift = 0; shift < 64; shift += 8) {
    out[offset + shift / 8] = static_cast<std::uint8_t>((value >> shift) & 0xFFu);
  }
}

void write_i64_le(std::uint8_t* out, std::size_t offset, std::int64_t value) {
  write_u64_le(out, offset, static_cast<std::uint64_t>(value));
}

bool read_u32_le(const std::uint8_t* data, std::size_t size, std::size_t offset, std::uint32_t& out) {
  if (data == nullptr || offset > size || size - offset < 4) {
    return false;
  }
  out = static_cast<std::uint32_t>(data[offset]) | (static_cast<std::uint32_t>(data[offset + 1]) << 8u) |
        (static_cast<std::uint32_t>(data[offset + 2]) << 16u) |
        (static_cast<std::uint32_t>(data[offset + 3]) << 24u);
  return true;
}

bool read_u64_le(const std::uint8_t* data, std::size_t size, std::size_t offset, std::uint64_t& out) {
  if (data == nullptr || offset > size || size - offset < 8) {
    return false;
  }
  out = 0;
  for (unsigned index = 0; index < 8; ++index) {
    out |= static_cast<std::uint64_t>(data[offset + index]) << (index * 8u);
  }
  return true;
}

bool read_i64_le(const std::uint8_t* data, std::size_t size, std::size_t offset, std::int64_t& out) {
  std::uint64_t value = 0;
  if (!read_u64_le(data, size, offset, value)) {
    return false;
  }
  out = static_cast<std::int64_t>(value);
  return true;
}

std::string json_array_for_endpoints(const std::vector<std::string>& endpoints) {
  return nlohmann::json(endpoints).dump();
}

bool validate_zenoh_video_frame(const VideoFrameView& frame, std::size_t& frame_bytes, std::string* error_message) {
  frame_bytes = 0;
  if (frame.width == 0 || frame.height == 0 || frame.pitch == 0) {
    set_error(error_message, "width, height, and pitch must be positive");
    return false;
  }
  if (frame.format == 0) {
    set_error(error_message, "format must be positive");
    return false;
  }
  if (frame.frame_id == 0) {
    set_error(error_message, "frame_id must be positive");
    return false;
  }
  if (frame.payload == nullptr) {
    set_error(error_message, "payload must be non-null");
    return false;
  }
  frame_bytes = static_cast<std::size_t>(frame.pitch) * static_cast<std::size_t>(frame.height);
  if (frame_bytes == 0 || frame.payload_bytes < frame_bytes) {
    set_error(error_message, "payload is smaller than pitch * height");
    return false;
  }
  if (frame_bytes > static_cast<std::size_t>(std::numeric_limits<std::uint32_t>::max())) {
    set_error(error_message, "payload is too large for zenoh video frame schema v1");
    return false;
  }
  return true;
}

void write_zenoh_video_frame_unchecked(const VideoFrameView& frame, std::size_t frame_bytes, std::uint8_t* out) {
  write_u32_le(out, 0, kZenohVideoFrameMagic);
  write_u32_le(out, 4, kZenohVideoFrameSchemaVersion);
  write_u32_le(out, 8, kZenohVideoFrameHeaderBytes);
  write_u32_le(out, 12, frame.width);
  write_u32_le(out, 16, frame.height);
  write_u32_le(out, 20, frame.pitch);
  write_u32_le(out, 24, frame.format);
  write_u32_le(out, 28, static_cast<std::uint32_t>(frame_bytes));
  write_u64_le(out, 32, frame.frame_id);
  write_i64_le(out, 40, frame.ts_ms);
  const auto* payload = reinterpret_cast<const std::uint8_t*>(frame.payload);
  std::memcpy(out + kZenohVideoFrameHeaderBytes, payload, frame_bytes);
}

bool decode_zenoh_video_frame_from_buffer(const std::uint8_t* raw, std::size_t raw_size, LatestVideoFrame& out,
                                          std::string* error_message) {
  out = LatestVideoFrame{};
  if (raw == nullptr || raw_size < kZenohVideoFrameHeaderBytes) {
    set_error(error_message, "payload is smaller than zenoh video frame header");
    return false;
  }

  std::uint32_t magic = 0;
  std::uint32_t version = 0;
  std::uint32_t header_bytes = 0;
  std::uint32_t width = 0;
  std::uint32_t height = 0;
  std::uint32_t pitch = 0;
  std::uint32_t format = 0;
  std::uint32_t payload_bytes = 0;
  std::uint64_t frame_id = 0;
  std::int64_t ts_ms = 0;
  if (!read_u32_le(raw, raw_size, 0, magic) || !read_u32_le(raw, raw_size, 4, version) ||
      !read_u32_le(raw, raw_size, 8, header_bytes) || !read_u32_le(raw, raw_size, 12, width) ||
      !read_u32_le(raw, raw_size, 16, height) || !read_u32_le(raw, raw_size, 20, pitch) ||
      !read_u32_le(raw, raw_size, 24, format) || !read_u32_le(raw, raw_size, 28, payload_bytes) ||
      !read_u64_le(raw, raw_size, 32, frame_id) || !read_i64_le(raw, raw_size, 40, ts_ms)) {
    set_error(error_message, "payload header is truncated");
    return false;
  }
  if (magic != kZenohVideoFrameMagic || version != kZenohVideoFrameSchemaVersion) {
    set_error(error_message, "unsupported zenoh video frame schema");
    return false;
  }
  if (header_bytes < kZenohVideoFrameHeaderBytes) {
    set_error(error_message, "invalid zenoh video frame header size");
    return false;
  }
  if (width == 0 || height == 0 || pitch == 0 || format == 0 || frame_id == 0) {
    set_error(error_message, "invalid zenoh video frame metadata");
    return false;
  }
  const std::size_t expected_payload_bytes = static_cast<std::size_t>(pitch) * static_cast<std::size_t>(height);
  if (payload_bytes != expected_payload_bytes) {
    set_error(error_message, "zenoh video frame payload size does not match pitch * height");
    return false;
  }
  if (static_cast<std::size_t>(header_bytes) > raw_size ||
      raw_size - static_cast<std::size_t>(header_bytes) < static_cast<std::size_t>(payload_bytes)) {
    set_error(error_message, "zenoh video frame payload is truncated");
    return false;
  }

  out.width = width;
  out.height = height;
  out.pitch = pitch;
  out.format = format;
  out.frame_id = frame_id;
  out.ts_ms = ts_ms;
  out.payload.resize(payload_bytes);
  std::memcpy(out.payload.data(), raw + header_bytes, payload_bytes);
  return true;
}

#if F8_WITH_ZENOH
zenoh::Bytes bytes_to_payload(const RuntimeBytes& payload) {
  return zenoh::Bytes(payload);
}

zenoh::Session::PutOptions realtime_drop_options() {
  zenoh::Session::PutOptions options = zenoh::Session::PutOptions::create_default();
  options.congestion_control = Z_CONGESTION_CONTROL_DROP;
  options.priority = Z_PRIORITY_REAL_TIME;
  options.reliability = Z_RELIABILITY_BEST_EFFORT;
  options.is_express = true;
  return options;
}

bool decode_zenoh_video_frame_from_payload(const zenoh::Bytes& payload, LatestVideoFrame& out,
                                           std::string* error_message) {
#if defined(Z_FEATURE_UNSTABLE_API)
  const auto view = payload.get_contiguous_view();
  if (view.has_value()) {
    return decode_zenoh_video_frame_from_buffer(view->data, view->len, out, error_message);
  }
#endif
  RuntimeBytes raw = payload.as_vector();
  return decode_zenoh_video_frame_from_buffer(raw.data(), raw.size(), out, error_message);
}

#if defined(Z_FEATURE_SHARED_MEMORY) && defined(Z_FEATURE_UNSTABLE_API)
const char* shm_provider_state_name(zenoh::ShmProviderNotReadyState state) {
  switch (state) {
    case zenoh::ShmProviderNotReadyState::SHM_PROVIDER_DISABLED:
      return "disabled";
    case zenoh::ShmProviderNotReadyState::SHM_PROVIDER_INITIALIZING:
      return "initializing";
    case zenoh::ShmProviderNotReadyState::SHM_PROVIDER_ERROR:
      return "error";
  }
  return "unknown";
}
#endif
#endif

}  // namespace

bool encode_zenoh_video_frame(const VideoFrameView& frame, RuntimeBytes& out, std::string* error_message) {
  out.clear();
  std::size_t frame_bytes = 0;
  if (!validate_zenoh_video_frame(frame, frame_bytes, error_message)) {
    return false;
  }

  out.resize(static_cast<std::size_t>(kZenohVideoFrameHeaderBytes) + frame_bytes);
  write_zenoh_video_frame_unchecked(frame, frame_bytes, out.data());
  return true;
}

bool decode_zenoh_video_frame(const RuntimeBytes& raw, LatestVideoFrame& out, std::string* error_message) {
  return decode_zenoh_video_frame_from_buffer(raw.data(), raw.size(), out, error_message);
}

class ZenohLatestVideoFramePublisher::Impl final {
 public:
  bool open(const RuntimeBackendConfig& config, const std::string& key_expr) {
#if F8_WITH_ZENOH
    std::lock_guard<std::mutex> lock(mu_);
    close_locked();

    RuntimeBackendConfig normalized = normalize_runtime_backend_config(config);
    const std::string key = trim_runtime_string(key_expr);
    if (key.empty()) {
      spdlog::error("zenoh video publisher requires a non-empty key expression");
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
      zenoh_internal::apply_shared_memory_config(zenoh_config, normalized.zenoh_shm_pool_bytes, key);

      session_ = std::make_unique<zenoh::Session>(zenoh::Session::open(std::move(zenoh_config)));
      key_expr_ = key;
      publish_failure_reported_ = false;
      shm_fallback_reported_ = false;
      obtain_shm_provider_locked();
      return true;
    } catch (const std::exception& exc) {
      spdlog::error("zenoh video publisher open failed key={}: {}", key, exc.what());
      close_locked();
      return false;
    } catch (...) {
      spdlog::error("zenoh video publisher open failed key={}: unknown error", key);
      close_locked();
      return false;
    }
#else
    (void)config;
    (void)key_expr;
    spdlog::error("Zenoh video publisher requested but f8cppsdk was built without F8_WITH_ZENOH");
    return false;
#endif
  }

  void close() {
    std::lock_guard<std::mutex> lock(mu_);
    close_locked();
  }

  bool publish_frame(const VideoFrameView& frame) {
#if F8_WITH_ZENOH
    std::string failure;
    try {
      std::lock_guard<std::mutex> lock(mu_);
      if (!session_ || key_expr_.empty()) {
        return false;
      }
      std::optional<zenoh::Bytes> payload = encode_payload_locked(frame, &failure);
      if (!payload.has_value()) {
        if (failure.empty()) {
          failure = "encode failed";
        }
      } else {
        session_->put(zenoh::KeyExpr(key_expr_), std::move(payload.value()), realtime_drop_options());
        publish_failure_reported_ = false;
        return true;
      }
    } catch (const std::exception& exc) {
      failure = exc.what();
    } catch (...) {
      failure = "unknown error";
    }
    report_publish_failure(failure);
    return false;
#else
    std::string error;
    RuntimeBytes encoded;
    if (!encode_zenoh_video_frame(frame, encoded, &error)) {
      report_publish_failure("encode failed: " + error);
      return false;
    }
    report_publish_failure("f8cppsdk was built without F8_WITH_ZENOH");
    return false;
#endif
  }

#if F8_WITH_ZENOH
  std::optional<zenoh::Bytes> encode_payload_locked(const VideoFrameView& frame, std::string* error_message) {
#if defined(Z_FEATURE_SHARED_MEMORY) && defined(Z_FEATURE_UNSTABLE_API)
    if (!shm_provider_.has_value()) {
      obtain_shm_provider_locked();
    }
    if (shm_provider_.has_value()) {
      std::optional<zenoh::Bytes> payload = encode_payload_with_shm_locked(frame, error_message);
      if (payload.has_value()) {
        return payload;
      }
      if (!shm_fallback_reported_) {
        shm_fallback_reported_ = true;
        spdlog::warn("zenoh video SHM payload allocation failed key={}; falling back to copied payload", key_expr_);
      }
    }
#endif
    encoded_buffer_.clear();
    if (!encode_zenoh_video_frame(frame, encoded_buffer_, error_message)) {
      return std::nullopt;
    }
    return bytes_to_payload(encoded_buffer_);
  }

#if defined(Z_FEATURE_SHARED_MEMORY) && defined(Z_FEATURE_UNSTABLE_API)
  void obtain_shm_provider_locked() {
    if (!session_ || shm_provider_.has_value()) {
      return;
    }
    for (int attempt = 0; attempt < 20; ++attempt) {
      auto provider_state = session_->obtain_shm_provider();
      if (auto* provider = std::get_if<zenoh::SharedShmProvider>(&provider_state)) {
        shm_provider_.emplace(std::move(*provider));
        spdlog::debug("zenoh video publisher using SHM provider key={}", key_expr_);
        return;
      }
      const auto* state = std::get_if<zenoh::ShmProviderNotReadyState>(&provider_state);
      if (state != nullptr && *state == zenoh::ShmProviderNotReadyState::SHM_PROVIDER_INITIALIZING) {
        std::this_thread::sleep_for(std::chrono::milliseconds(5));
        continue;
      }
      if (!shm_fallback_reported_) {
        shm_fallback_reported_ = true;
        spdlog::debug("zenoh video SHM provider unavailable key={} state={}", key_expr_,
                      state == nullptr ? "unknown" : shm_provider_state_name(*state));
      }
      return;
    }
    if (!shm_fallback_reported_) {
      shm_fallback_reported_ = true;
      spdlog::debug("zenoh video SHM provider still initializing key={}", key_expr_);
    }
  }

  std::optional<zenoh::Bytes> encode_payload_with_shm_locked(const VideoFrameView& frame,
                                                             std::string* error_message) {
    if (!shm_provider_.has_value()) {
      return std::nullopt;
    }
    std::size_t frame_bytes = 0;
    if (!validate_zenoh_video_frame(frame, frame_bytes, error_message)) {
      return std::nullopt;
    }
    const std::size_t encoded_bytes = static_cast<std::size_t>(kZenohVideoFrameHeaderBytes) + frame_bytes;
    auto allocation = shm_provider_->shm_provider().alloc_gc_defrag(encoded_bytes);
    auto* shm = std::get_if<zenoh::ZShmMut>(&allocation);
    if (shm == nullptr) {
      set_error(error_message, "SHM pool allocation failed");
      return std::nullopt;
    }
    if (shm->data() == nullptr || shm->len() < encoded_bytes) {
      set_error(error_message, "SHM pool returned a buffer that is too small");
      return std::nullopt;
    }
    write_zenoh_video_frame_unchecked(frame, frame_bytes, shm->data());
    return zenoh::Bytes(std::move(*shm));
  }
#endif
#endif

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
#if defined(Z_FEATURE_SHARED_MEMORY) && defined(Z_FEATURE_UNSTABLE_API)
    shm_provider_.reset();
#endif
    if (session_) {
      try {
        session_->close();
      } catch (const std::exception& exc) {
        spdlog::warn("zenoh video publisher session close failed key={}: {}", key_expr_, exc.what());
      } catch (...) {
        spdlog::warn("zenoh video publisher session close failed key={}: unknown error", key_expr_);
      }
    }
    session_.reset();
#endif
    key_expr_.clear();
    publish_failure_reported_ = false;
    shm_fallback_reported_ = false;
  }

  void report_publish_failure(const std::string& message) {
    std::lock_guard<std::mutex> lock(mu_);
    if (publish_failure_reported_) {
      return;
    }
    publish_failure_reported_ = true;
    spdlog::error("zenoh video publish failed key={}: {}", key_expr_, message);
  }

  mutable std::mutex mu_;
  std::string key_expr_;
  bool publish_failure_reported_ = false;
  bool shm_fallback_reported_ = false;
#if F8_WITH_ZENOH
  RuntimeBytes encoded_buffer_;
  std::unique_ptr<zenoh::Session> session_;
#if defined(Z_FEATURE_SHARED_MEMORY) && defined(Z_FEATURE_UNSTABLE_API)
  std::optional<zenoh::SharedShmProvider> shm_provider_;
#endif
#endif
};

class ZenohLatestVideoFrameSubscriber::Impl final {
 public:
  bool open(const RuntimeBackendConfig& config, const std::string& key_expr) {
#if F8_WITH_ZENOH
    std::lock_guard<std::mutex> lock(mu_);
    close_locked();

    RuntimeBackendConfig normalized = normalize_runtime_backend_config(config);
    const std::string key = trim_runtime_string(key_expr);
    if (key.empty()) {
      spdlog::error("zenoh video subscriber requires a non-empty key expression");
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
      zenoh_internal::apply_shared_memory_config(zenoh_config, normalized.zenoh_shm_pool_bytes, key);

      session_ = std::make_unique<zenoh::Session>(zenoh::Session::open(std::move(zenoh_config)));
      key_expr_ = key;
      closed_ = false;
      latest_payload_.reset();
      latest_seq_ = 0;
      delivered_seq_ = 0;
      decode_failure_reported_ = false;
      subscriber_ = session_->declare_subscriber(
          zenoh::KeyExpr(key_expr_),
          [this](zenoh::Sample& sample) {
            try {
              zenoh::Bytes payload = sample.get_payload().clone();
              {
                std::lock_guard<std::mutex> sample_lock(mu_);
                if (closed_) {
                  return;
                }
                latest_payload_ = std::move(payload);
                ++latest_seq_;
              }
              cv_.notify_all();
            } catch (const std::exception& exc) {
              spdlog::error("zenoh video subscriber callback failed key={}: {}", key_expr_, exc.what());
            } catch (...) {
              spdlog::error("zenoh video subscriber callback failed key={}: unknown error", key_expr_);
            }
          },
          []() {});
      std::this_thread::sleep_for(kSubscriptionSettle);
      return true;
    } catch (const std::exception& exc) {
      spdlog::error("zenoh video subscriber open failed key={}: {}", key, exc.what());
      close_locked();
      return false;
    } catch (...) {
      spdlog::error("zenoh video subscriber open failed key={}: unknown error", key);
      close_locked();
      return false;
    }
#else
    (void)config;
    (void)key_expr;
    spdlog::error("Zenoh video subscriber requested but f8cppsdk was built without F8_WITH_ZENOH");
    return false;
#endif
  }

  void close() {
    std::lock_guard<std::mutex> lock(mu_);
    close_locked();
    cv_.notify_all();
  }

  std::optional<LatestVideoFrame> poll_latest() {
#if F8_WITH_ZENOH
    std::optional<zenoh::Bytes> payload;
    {
      std::lock_guard<std::mutex> lock(mu_);
      if (latest_seq_ == delivered_seq_ || !latest_payload_.has_value()) {
        return std::nullopt;
      }
      payload = std::move(latest_payload_);
      latest_payload_.reset();
      delivered_seq_ = latest_seq_;
    }
    return decode_latest(*payload);
#else
    return std::nullopt;
#endif
  }

  std::optional<LatestVideoFrame> wait_latest(std::chrono::milliseconds timeout) {
    if (auto frame = poll_latest()) {
      return frame;
    }
    if (timeout.count() <= 0) {
      return std::nullopt;
    }

#if F8_WITH_ZENOH
    std::optional<zenoh::Bytes> payload;
    {
      std::unique_lock<std::mutex> lock(mu_);
      const bool ready = cv_.wait_for(lock, timeout, [this]() {
        return closed_ || (latest_seq_ != delivered_seq_ && latest_payload_.has_value());
      });
      if (!ready || closed_ || latest_seq_ == delivered_seq_ || !latest_payload_.has_value()) {
        return std::nullopt;
      }
      payload = std::move(latest_payload_);
      latest_payload_.reset();
      delivered_seq_ = latest_seq_;
    }
    return decode_latest(*payload);
#else
    return std::nullopt;
#endif
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
        spdlog::warn("zenoh video subscriber undeclare failed key={}: {}", key_expr_, exc.what());
      } catch (...) {
        spdlog::warn("zenoh video subscriber undeclare failed key={}: unknown error", key_expr_);
      }
      subscriber_.reset();
    }
    if (session_) {
      try {
        session_->close();
      } catch (const std::exception& exc) {
        spdlog::warn("zenoh video subscriber session close failed key={}: {}", key_expr_, exc.what());
      } catch (...) {
        spdlog::warn("zenoh video subscriber session close failed key={}: unknown error", key_expr_);
      }
    }
    session_.reset();
    latest_payload_.reset();
#endif
    closed_ = true;
    key_expr_.clear();
    latest_seq_ = 0;
    delivered_seq_ = 0;
    decode_failure_reported_ = false;
  }

#if F8_WITH_ZENOH
  std::optional<LatestVideoFrame> decode_latest(const zenoh::Bytes& payload) {
    LatestVideoFrame frame;
    std::string error;
    if (!decode_zenoh_video_frame_from_payload(payload, frame, &error)) {
      std::lock_guard<std::mutex> lock(mu_);
      if (!decode_failure_reported_) {
        decode_failure_reported_ = true;
        spdlog::error("zenoh video frame decode failed key={}: {}", key_expr_, error);
      }
      return std::nullopt;
    }
    {
      std::lock_guard<std::mutex> lock(mu_);
      decode_failure_reported_ = false;
    }
    return frame;
  }
#endif

  mutable std::mutex mu_;
  std::condition_variable cv_;
  std::string key_expr_;
  bool closed_ = true;
  std::uint64_t latest_seq_ = 0;
  std::uint64_t delivered_seq_ = 0;
  bool decode_failure_reported_ = false;
#if F8_WITH_ZENOH
  std::unique_ptr<zenoh::Session> session_;
  std::optional<zenoh::Subscriber<void>> subscriber_;
  std::optional<zenoh::Bytes> latest_payload_;
#endif
};

ZenohLatestVideoFramePublisher::ZenohLatestVideoFramePublisher() : impl_(std::make_unique<Impl>()) {}
ZenohLatestVideoFramePublisher::~ZenohLatestVideoFramePublisher() {
  close();
}

bool ZenohLatestVideoFramePublisher::open(const RuntimeBackendConfig& config, const std::string& key_expr) {
  return impl_->open(config, key_expr);
}

void ZenohLatestVideoFramePublisher::close() {
  impl_->close();
}

bool ZenohLatestVideoFramePublisher::publish_frame(const VideoFrameView& frame) {
  return impl_->publish_frame(frame);
}

bool ZenohLatestVideoFramePublisher::valid() const {
  return impl_->valid();
}

std::string ZenohLatestVideoFramePublisher::key_expr() const {
  return impl_->key_expr();
}

ZenohLatestVideoFrameSubscriber::ZenohLatestVideoFrameSubscriber() : impl_(std::make_unique<Impl>()) {}
ZenohLatestVideoFrameSubscriber::~ZenohLatestVideoFrameSubscriber() {
  close();
}

bool ZenohLatestVideoFrameSubscriber::open(const RuntimeBackendConfig& config, const std::string& key_expr) {
  return impl_->open(config, key_expr);
}

void ZenohLatestVideoFrameSubscriber::close() {
  impl_->close();
}

std::optional<LatestVideoFrame> ZenohLatestVideoFrameSubscriber::poll_latest() {
  return impl_->poll_latest();
}

std::optional<LatestVideoFrame> ZenohLatestVideoFrameSubscriber::wait_latest(std::chrono::milliseconds timeout) {
  return impl_->wait_latest(timeout);
}

bool ZenohLatestVideoFrameSubscriber::valid() const {
  return impl_->valid();
}

std::string ZenohLatestVideoFrameSubscriber::key_expr() const {
  return impl_->key_expr();
}

}  // namespace f8::cppsdk
