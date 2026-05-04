#include "f8cppsdk/latest_video_frame_transport.h"

#include <algorithm>
#include <limits>
#include <mutex>
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

std::string json_array_for_endpoints(const std::vector<std::string>& endpoints) {
  return nlohmann::json(endpoints).dump();
}

#if F8_WITH_ZENOH
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

bool encode_zenoh_video_frame(const VideoFrameView& frame, RuntimeBytes& out, std::string* error_message) {
  out.clear();
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
  const std::size_t frame_bytes = static_cast<std::size_t>(frame.pitch) * static_cast<std::size_t>(frame.height);
  if (frame_bytes == 0 || frame.payload_bytes < frame_bytes) {
    set_error(error_message, "payload is smaller than pitch * height");
    return false;
  }
  if (frame_bytes > static_cast<std::size_t>(std::numeric_limits<std::uint32_t>::max())) {
    set_error(error_message, "payload is too large for zenoh video frame schema v1");
    return false;
  }

  out.reserve(static_cast<std::size_t>(kZenohVideoFrameHeaderBytes) + frame_bytes);
  append_u32_le(out, kZenohVideoFrameMagic);
  append_u32_le(out, kZenohVideoFrameSchemaVersion);
  append_u32_le(out, kZenohVideoFrameHeaderBytes);
  append_u32_le(out, frame.width);
  append_u32_le(out, frame.height);
  append_u32_le(out, frame.pitch);
  append_u32_le(out, frame.format);
  append_u32_le(out, static_cast<std::uint32_t>(frame_bytes));
  append_u64_le(out, frame.frame_id);
  append_i64_le(out, frame.ts_ms);
  const auto* begin = reinterpret_cast<const std::uint8_t*>(frame.payload);
  out.insert(out.end(), begin, begin + frame_bytes);
  return true;
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
    RuntimeBytes encoded;
    std::string error;
    if (!encode_zenoh_video_frame(frame, encoded, &error)) {
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
        spdlog::warn("zenoh video publisher session close failed key={}: {}", key_expr_, exc.what());
      } catch (...) {
        spdlog::warn("zenoh video publisher session close failed key={}: unknown error", key_expr_);
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
    spdlog::error("zenoh video publish failed key={}: {}", key_expr_, message);
  }

  mutable std::mutex mu_;
  std::string key_expr_;
  bool publish_failure_reported_ = false;
#if F8_WITH_ZENOH
  std::unique_ptr<zenoh::Session> session_;
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

}  // namespace f8::cppsdk
