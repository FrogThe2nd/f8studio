#include "f8cppsdk/latest_binary_stream_transport.h"

#include "zenoh_config_internal.h"

#include <chrono>
#include <condition_variable>
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

std::string json_array_for_endpoints(const std::vector<std::string>& endpoints) {
  return nlohmann::json(endpoints).dump();
}

#if F8_WITH_ZENOH
zenoh::Bytes bytes_to_payload(const RuntimeBytes& payload) {
  return zenoh::Bytes(payload);
}

RuntimeBytes payload_to_bytes(const zenoh::Bytes& payload) {
  return payload.as_vector();
}

zenoh::Session::PutOptions realtime_drop_options() {
  zenoh::Session::PutOptions options = zenoh::Session::PutOptions::create_default();
  options.congestion_control = Z_CONGESTION_CONTROL_DROP;
  options.priority = Z_PRIORITY_REAL_TIME;
  options.reliability = Z_RELIABILITY_BEST_EFFORT;
  options.is_express = true;
  return options;
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

class ZenohLatestBinaryStreamPublisher::Impl final {
 public:
  explicit Impl(std::string log_context) : log_context_(std::move(log_context)) {
    if (log_context_.empty()) {
      log_context_ = "stream";
    }
  }

  bool open(const RuntimeBackendConfig& config, const std::string& key_expr) {
#if F8_WITH_ZENOH
    std::lock_guard<std::mutex> lock(mu_);
    close_locked();

    RuntimeBackendConfig normalized = normalize_runtime_backend_config(config);
    const std::string key = trim_runtime_string(key_expr);
    if (key.empty()) {
      spdlog::error("zenoh {} publisher requires a non-empty key expression", log_context_);
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
      spdlog::error("zenoh {} publisher open failed key={}: {}", log_context_, key, exc.what());
      close_locked();
      return false;
    } catch (...) {
      spdlog::error("zenoh {} publisher open failed key={}: unknown error", log_context_, key);
      close_locked();
      return false;
    }
#else
    (void)config;
    (void)key_expr;
    spdlog::error("Zenoh {} publisher requested but f8cppsdk was built without F8_WITH_ZENOH", log_context_);
    return false;
#endif
  }

  void close() {
    std::lock_guard<std::mutex> lock(mu_);
    close_locked();
  }

  bool publish_bytes(const RuntimeBytes& payload) {
#if F8_WITH_ZENOH
    try {
      std::lock_guard<std::mutex> lock(mu_);
      if (!session_ || key_expr_.empty()) {
        return false;
      }
      session_->put(zenoh::KeyExpr(key_expr_), bytes_to_payload(payload), realtime_drop_options());
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
    (void)payload;
    report_publish_failure("f8cppsdk was built without F8_WITH_ZENOH");
    return false;
#endif
  }

  bool publish_payload(std::size_t payload_bytes, const LatestBinaryPayloadWriter& writer) {
    std::string failure;
#if F8_WITH_ZENOH
    try {
      std::lock_guard<std::mutex> lock(mu_);
      if (!session_ || key_expr_.empty()) {
        return false;
      }
      if (!writer) {
        failure = "payload writer is empty";
      } else {
        std::optional<zenoh::Bytes> payload = encode_payload_locked(payload_bytes, writer, &failure);
        if (payload.has_value()) {
          session_->put(zenoh::KeyExpr(key_expr_), std::move(payload.value()), realtime_drop_options());
          publish_failure_reported_ = false;
          return true;
        }
      }
    } catch (const std::exception& exc) {
      failure = exc.what();
    } catch (...) {
      failure = "unknown error";
    }
    report_publish_failure(failure.empty() ? "encode failed" : failure);
    return false;
#else
    if (!writer) {
      report_publish_failure("payload writer is empty");
      return false;
    }
    RuntimeBytes encoded(payload_bytes);
    if (!writer(encoded.data(), encoded.size(), &failure)) {
      report_publish_failure("encode failed: " + failure);
      return false;
    }
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
#if F8_WITH_ZENOH
  std::optional<zenoh::Bytes> encode_payload_locked(std::size_t payload_bytes, const LatestBinaryPayloadWriter& writer,
                                                    std::string* error_message) {
#if defined(Z_FEATURE_SHARED_MEMORY) && defined(Z_FEATURE_UNSTABLE_API)
    if (!shm_provider_.has_value()) {
      obtain_shm_provider_locked();
    }
    if (shm_provider_.has_value()) {
      auto allocation = shm_provider_->shm_provider().alloc_gc_defrag(payload_bytes);
      auto* shm = std::get_if<zenoh::ZShmMut>(&allocation);
      if (shm != nullptr && shm->data() != nullptr && shm->len() >= payload_bytes) {
        if (!writer(shm->data(), payload_bytes, error_message)) {
          return std::nullopt;
        }
        return zenoh::Bytes(std::move(*shm));
      }
      if (!shm_fallback_reported_) {
        shm_fallback_reported_ = true;
        spdlog::warn("zenoh {} SHM payload allocation failed key={}; falling back to copied payload", log_context_,
                     key_expr_);
      }
    }
#endif
    encoded_buffer_.assign(payload_bytes, 0);
    if (!writer(encoded_buffer_.data(), encoded_buffer_.size(), error_message)) {
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
        spdlog::debug("zenoh {} publisher using SHM provider key={}", log_context_, key_expr_);
        return;
      }
      const auto* state = std::get_if<zenoh::ShmProviderNotReadyState>(&provider_state);
      if (state != nullptr && *state == zenoh::ShmProviderNotReadyState::SHM_PROVIDER_INITIALIZING) {
        std::this_thread::sleep_for(std::chrono::milliseconds(5));
        continue;
      }
      if (!shm_fallback_reported_) {
        shm_fallback_reported_ = true;
        spdlog::debug("zenoh {} SHM provider unavailable key={} state={}", log_context_, key_expr_,
                      state == nullptr ? "unknown" : shm_provider_state_name(*state));
      }
      return;
    }
    if (!shm_fallback_reported_) {
      shm_fallback_reported_ = true;
      spdlog::debug("zenoh {} SHM provider still initializing key={}", log_context_, key_expr_);
    }
  }
#endif
#endif

  void close_locked() {
#if F8_WITH_ZENOH
#if defined(Z_FEATURE_SHARED_MEMORY) && defined(Z_FEATURE_UNSTABLE_API)
    shm_provider_.reset();
#endif
    if (session_) {
      try {
        session_->close();
      } catch (const std::exception& exc) {
        spdlog::warn("zenoh {} publisher session close failed key={}: {}", log_context_, key_expr_, exc.what());
      } catch (...) {
        spdlog::warn("zenoh {} publisher session close failed key={}: unknown error", log_context_, key_expr_);
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
    spdlog::error("zenoh {} publish failed key={}: {}", log_context_, key_expr_, message);
  }

  mutable std::mutex mu_;
  std::string log_context_;
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

class ZenohLatestBinaryStreamSubscriber::Impl final {
 public:
  explicit Impl(std::string log_context) : log_context_(std::move(log_context)) {
    if (log_context_.empty()) {
      log_context_ = "stream";
    }
  }

  bool open(const RuntimeBackendConfig& config, const std::string& key_expr) {
#if F8_WITH_ZENOH
    std::lock_guard<std::mutex> lock(mu_);
    close_locked();

    RuntimeBackendConfig normalized = normalize_runtime_backend_config(config);
    const std::string key = trim_runtime_string(key_expr);
    if (key.empty()) {
      spdlog::error("zenoh {} subscriber requires a non-empty key expression", log_context_);
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
      latest_raw_.clear();
      latest_seq_ = 0;
      delivered_seq_ = 0;
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
              spdlog::error("zenoh {} subscriber callback failed key={}: {}", log_context_, key_expr_, exc.what());
            } catch (...) {
              spdlog::error("zenoh {} subscriber callback failed key={}: unknown error", log_context_, key_expr_);
            }
          },
          []() {});
      std::this_thread::sleep_for(kSubscriptionSettle);
      return true;
    } catch (const std::exception& exc) {
      spdlog::error("zenoh {} subscriber open failed key={}: {}", log_context_, key, exc.what());
      close_locked();
      return false;
    } catch (...) {
      spdlog::error("zenoh {} subscriber open failed key={}: unknown error", log_context_, key);
      close_locked();
      return false;
    }
#else
    (void)config;
    (void)key_expr;
    spdlog::error("Zenoh {} subscriber requested but f8cppsdk was built without F8_WITH_ZENOH", log_context_);
    return false;
#endif
  }

  void close() {
    std::lock_guard<std::mutex> lock(mu_);
    close_locked();
    cv_.notify_all();
  }

  std::optional<RuntimeBytes> poll_latest() {
    std::lock_guard<std::mutex> lock(mu_);
    if (latest_seq_ == delivered_seq_ || latest_raw_.empty()) {
      return std::nullopt;
    }
    RuntimeBytes raw = std::move(latest_raw_);
    latest_raw_.clear();
    delivered_seq_ = latest_seq_;
    return raw;
  }

  std::optional<RuntimeBytes> wait_latest(std::chrono::milliseconds timeout) {
    if (auto raw = poll_latest()) {
      return raw;
    }
    if (timeout.count() <= 0) {
      return std::nullopt;
    }

    std::unique_lock<std::mutex> lock(mu_);
    const bool ready = cv_.wait_for(lock, timeout, [this]() {
      return closed_ || (latest_seq_ != delivered_seq_ && !latest_raw_.empty());
    });
    if (!ready || closed_ || latest_seq_ == delivered_seq_ || latest_raw_.empty()) {
      return std::nullopt;
    }
    RuntimeBytes raw = std::move(latest_raw_);
    latest_raw_.clear();
    delivered_seq_ = latest_seq_;
    return raw;
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
        spdlog::warn("zenoh {} subscriber undeclare failed key={}: {}", log_context_, key_expr_, exc.what());
      } catch (...) {
        spdlog::warn("zenoh {} subscriber undeclare failed key={}: unknown error", log_context_, key_expr_);
      }
      subscriber_.reset();
    }
    if (session_) {
      try {
        session_->close();
      } catch (const std::exception& exc) {
        spdlog::warn("zenoh {} subscriber session close failed key={}: {}", log_context_, key_expr_, exc.what());
      } catch (...) {
        spdlog::warn("zenoh {} subscriber session close failed key={}: unknown error", log_context_, key_expr_);
      }
    }
    session_.reset();
#endif
    closed_ = true;
    key_expr_.clear();
    latest_raw_.clear();
    latest_seq_ = 0;
    delivered_seq_ = 0;
  }

  mutable std::mutex mu_;
  std::condition_variable cv_;
  std::string log_context_;
  std::string key_expr_;
  bool closed_ = true;
  RuntimeBytes latest_raw_;
  std::uint64_t latest_seq_ = 0;
  std::uint64_t delivered_seq_ = 0;
#if F8_WITH_ZENOH
  std::unique_ptr<zenoh::Session> session_;
  std::optional<zenoh::Subscriber<void>> subscriber_;
#endif
};

ZenohLatestBinaryStreamPublisher::ZenohLatestBinaryStreamPublisher(std::string log_context)
    : impl_(std::make_unique<Impl>(std::move(log_context))) {}

ZenohLatestBinaryStreamPublisher::~ZenohLatestBinaryStreamPublisher() {
  close();
}

bool ZenohLatestBinaryStreamPublisher::open(const RuntimeBackendConfig& config, const std::string& key_expr) {
  return impl_->open(config, key_expr);
}

void ZenohLatestBinaryStreamPublisher::close() {
  impl_->close();
}

bool ZenohLatestBinaryStreamPublisher::publish_bytes(const RuntimeBytes& payload) {
  return impl_->publish_bytes(payload);
}

bool ZenohLatestBinaryStreamPublisher::publish_payload(std::size_t payload_bytes, LatestBinaryPayloadWriter writer) {
  return impl_->publish_payload(payload_bytes, writer);
}

bool ZenohLatestBinaryStreamPublisher::valid() const {
  return impl_->valid();
}

std::string ZenohLatestBinaryStreamPublisher::key_expr() const {
  return impl_->key_expr();
}

ZenohLatestBinaryStreamSubscriber::ZenohLatestBinaryStreamSubscriber(std::string log_context)
    : impl_(std::make_unique<Impl>(std::move(log_context))) {}

ZenohLatestBinaryStreamSubscriber::~ZenohLatestBinaryStreamSubscriber() {
  close();
}

bool ZenohLatestBinaryStreamSubscriber::open(const RuntimeBackendConfig& config, const std::string& key_expr) {
  return impl_->open(config, key_expr);
}

void ZenohLatestBinaryStreamSubscriber::close() {
  impl_->close();
}

std::optional<RuntimeBytes> ZenohLatestBinaryStreamSubscriber::poll_latest() {
  return impl_->poll_latest();
}

std::optional<RuntimeBytes> ZenohLatestBinaryStreamSubscriber::wait_latest(std::chrono::milliseconds timeout) {
  return impl_->wait_latest(timeout);
}

bool ZenohLatestBinaryStreamSubscriber::valid() const {
  return impl_->valid();
}

std::string ZenohLatestBinaryStreamSubscriber::key_expr() const {
  return impl_->key_expr();
}

}  // namespace f8::cppsdk
