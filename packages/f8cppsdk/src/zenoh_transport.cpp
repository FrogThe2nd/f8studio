#include "f8cppsdk/zenoh_transport.h"

#include "f8cppsdk/f8_naming.h"
#include "f8cppsdk/zenoh_naming.h"

#include <chrono>
#include <condition_variable>
#include <mutex>
#include <optional>
#include <string>
#include <thread>
#include <unordered_map>
#include <utility>
#include <vector>

#include <nlohmann/json.hpp>
#include <spdlog/spdlog.h>

#if F8_WITH_ZENOH
#include <zenoh.hxx>
#endif

namespace f8::cppsdk {
namespace {

constexpr std::chrono::milliseconds kSubscriptionSettle{10};

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
  options.reliability = Z_RELIABILITY_BEST_EFFORT;
  options.is_express = true;
  return options;
}

zenoh::Session::GetOptions request_options(const RuntimeBytes& payload, std::chrono::milliseconds timeout) {
  zenoh::Session::GetOptions options = zenoh::Session::GetOptions::create_default();
  options.payload = bytes_to_payload(payload);
  options.timeout_ms = static_cast<std::uint64_t>(timeout.count() > 0 ? timeout.count() : 1);
  options.congestion_control = Z_CONGESTION_CONTROL_BLOCK;
  options.priority = Z_PRIORITY_INTERACTIVE_HIGH;
  options.is_express = true;
  return options;
}

std::string json_array_for_endpoints(const std::vector<std::string>& endpoints) {
  return nlohmann::json(endpoints).dump();
}

class ZenohSubscriberHandle final : public RuntimeSubscription {
 public:
  explicit ZenohSubscriberHandle(zenoh::Subscriber<void>&& subscriber) : subscriber_(std::move(subscriber)) {}
  ~ZenohSubscriberHandle() override { stop(); }

  void stop() override {
    std::lock_guard<std::mutex> lock(mu_);
    if (!subscriber_.has_value()) {
      return;
    }
    try {
      std::move(*subscriber_).undeclare();
    } catch (const std::exception& exc) {
      spdlog::warn("zenoh subscriber undeclare failed: {}", exc.what());
    } catch (...) {
      spdlog::warn("zenoh subscriber undeclare failed: unknown error");
    }
    subscriber_.reset();
  }

  bool valid() const override {
    std::lock_guard<std::mutex> lock(mu_);
    return subscriber_.has_value();
  }

 private:
  mutable std::mutex mu_;
  std::optional<zenoh::Subscriber<void>> subscriber_;
};

class ZenohQueryableHandle final : public RuntimeSubscription {
 public:
  explicit ZenohQueryableHandle(zenoh::Queryable<void>&& queryable) : queryable_(std::move(queryable)) {}
  ~ZenohQueryableHandle() override { stop(); }

  void stop() override {
    std::lock_guard<std::mutex> lock(mu_);
    if (!queryable_.has_value()) {
      return;
    }
    try {
      std::move(*queryable_).undeclare();
    } catch (const std::exception& exc) {
      spdlog::warn("zenoh queryable undeclare failed: {}", exc.what());
    } catch (...) {
      spdlog::warn("zenoh queryable undeclare failed: unknown error");
    }
    queryable_.reset();
  }

  bool valid() const override {
    std::lock_guard<std::mutex> lock(mu_);
    return queryable_.has_value();
  }

 private:
  mutable std::mutex mu_;
  std::optional<zenoh::Queryable<void>> queryable_;
};
#endif

}  // namespace

class ZenohTransport::Impl final {
 public:
  bool connect(const RuntimeBackendConfig& config, const std::string& service_id) {
#if F8_WITH_ZENOH
    std::lock_guard<std::mutex> lock(mu_);
    close_locked();

    service_id_ = ensure_token(service_id, "service_id");
    config_ = normalize_runtime_backend_config(config);

    try {
      zenoh::Config zenoh_config = config_.zenoh_config_path.empty() ? zenoh::Config::create_default()
                                                                     : zenoh::Config::from_file(config_.zenoh_config_path);
      if (!config_.zenoh_connect.empty()) {
        zenoh_config.insert_json5("connect/endpoints", json_array_for_endpoints(config_.zenoh_connect));
      }
      if (!config_.zenoh_listen.empty()) {
        zenoh_config.insert_json5("listen/endpoints", json_array_for_endpoints(config_.zenoh_listen));
      }
      zenoh_config.insert_json5("transport/shared_memory/enabled", "true");
      if (config_.zenoh_shm_pool_bytes > 0) {
        try {
          zenoh_config.insert_json5("transport/shared_memory/pool_size",
                                    std::to_string(config_.zenoh_shm_pool_bytes));
        } catch (const std::exception& exc) {
          spdlog::debug("zenoh config does not expose shared-memory pool_size serviceId={}: {}", service_id_,
                        exc.what());
        }
      }

      auto session = std::make_unique<zenoh::Session>(zenoh::Session::open(std::move(zenoh_config)));
      session_ = std::move(session);
      liveliness_token_ =
          session_->liveliness_declare_token(zenoh::KeyExpr(zenoh_service_liveliness_key(service_id_)));
      start_kv_queryables_locked();
      return true;
    } catch (const std::exception& exc) {
      spdlog::error("zenoh transport connect failed serviceId={}: {}", service_id_, exc.what());
      close_locked();
      return false;
    } catch (...) {
      spdlog::error("zenoh transport connect failed serviceId={}: unknown error", service_id_);
      close_locked();
      return false;
    }
#else
    (void)config;
    (void)service_id;
    spdlog::error("Zenoh C++ transport requested but f8cppsdk was built without F8_WITH_ZENOH");
    return false;
#endif
  }

  void close() {
    std::lock_guard<std::mutex> lock(mu_);
    close_locked();
  }

  bool publish(const std::string& subject, const RuntimeBytes& payload) {
#if F8_WITH_ZENOH
    try {
      std::lock_guard<std::mutex> lock(mu_);
      if (!session_) {
        return false;
      }
      session_->put(zenoh::KeyExpr(subject_to_zenoh_key(subject)), bytes_to_payload(payload), realtime_drop_options());
      return true;
    } catch (const std::exception& exc) {
      spdlog::error("zenoh publish failed subject={}: {}", subject, exc.what());
      return false;
    } catch (...) {
      spdlog::error("zenoh publish failed subject={}: unknown error", subject);
      return false;
    }
#else
    (void)subject;
    (void)payload;
    return false;
#endif
  }

  std::unique_ptr<RuntimeSubscription> subscribe(const std::string& subject, RuntimeMessageHandler handler) {
#if F8_WITH_ZENOH
    std::lock_guard<std::mutex> lock(mu_);
    if (!session_) {
      return nullptr;
    }
    try {
      auto subscriber = session_->declare_subscriber(
          zenoh::KeyExpr(subject_to_zenoh_key(subject)),
          [handler = std::move(handler)](zenoh::Sample& sample) {
            try {
              RuntimeMessage msg;
              msg.subject = zenoh_key_to_subject(std::string(sample.get_keyexpr().as_string_view()));
              msg.payload = payload_to_bytes(sample.get_payload());
              handler(msg);
            } catch (const std::exception& exc) {
              spdlog::error("zenoh subscriber callback failed: {}", exc.what());
            } catch (...) {
              spdlog::error("zenoh subscriber callback failed: unknown error");
            }
          },
          []() {});
      std::this_thread::sleep_for(kSubscriptionSettle);
      return std::make_unique<ZenohSubscriberHandle>(std::move(subscriber));
    } catch (const std::exception& exc) {
      spdlog::error("zenoh subscribe failed subject={}: {}", subject, exc.what());
      return nullptr;
    } catch (...) {
      spdlog::error("zenoh subscribe failed subject={}: unknown error", subject);
      return nullptr;
    }
#else
    (void)subject;
    (void)handler;
    return nullptr;
#endif
  }

  std::optional<RuntimeBytes> request(const std::string& subject, const RuntimeBytes& payload,
                                      std::chrono::milliseconds timeout) {
#if F8_WITH_ZENOH
    struct RequestState {
      std::mutex mu;
      std::condition_variable cv;
      bool done = false;
      std::optional<RuntimeBytes> reply;
    };

    auto state = std::make_shared<RequestState>();
    try {
      {
        std::lock_guard<std::mutex> lock(mu_);
        if (!session_) {
          return std::nullopt;
        }
        session_->get(
            zenoh::KeyExpr(subject_to_zenoh_key(subject)), "",
            [state](zenoh::Reply& reply) {
              if (!reply.is_ok()) {
                return;
              }
              {
                std::lock_guard<std::mutex> state_lock(state->mu);
                if (!state->reply.has_value()) {
                  state->reply = payload_to_bytes(reply.get_ok().get_payload());
                }
              }
              state->cv.notify_all();
            },
            [state]() {
              {
                std::lock_guard<std::mutex> state_lock(state->mu);
                state->done = true;
              }
              state->cv.notify_all();
            },
            request_options(payload, timeout));
      }

      std::unique_lock<std::mutex> state_lock(state->mu);
      const auto wait_timeout = timeout.count() > 0 ? timeout : std::chrono::milliseconds(1);
      (void)state->cv.wait_for(state_lock, wait_timeout,
                               [state]() { return state->done || state->reply.has_value(); });
      return state->reply;
    } catch (const std::exception& exc) {
      spdlog::error("zenoh request failed subject={}: {}", subject, exc.what());
      return std::nullopt;
    } catch (...) {
      spdlog::error("zenoh request failed subject={}: unknown error", subject);
      return std::nullopt;
    }
#else
    (void)subject;
    (void)payload;
    (void)timeout;
    return std::nullopt;
#endif
  }

  std::unique_ptr<RuntimeSubscription> serve(const std::string& subject, RuntimeRequestHandler handler) {
#if F8_WITH_ZENOH
    std::lock_guard<std::mutex> lock(mu_);
    if (!session_) {
      return nullptr;
    }
    try {
      auto queryable = session_->declare_queryable(
          zenoh::KeyExpr(subject_to_zenoh_key(subject)),
          [handler = std::move(handler)](zenoh::Query& query) {
            try {
              RuntimeMessage msg;
              msg.subject = zenoh_key_to_subject(std::string(query.get_keyexpr().as_string_view()));
              const auto payload = query.get_payload();
              if (payload.has_value()) {
                msg.payload = payload_to_bytes(payload->get());
              }
              RuntimeBytes response = handler(msg);
              query.reply(query.get_keyexpr(), bytes_to_payload(response));
            } catch (const std::exception& exc) {
              spdlog::error("zenoh queryable callback failed: {}", exc.what());
              try {
                query.reply_err(zenoh::Bytes(std::string(exc.what())));
              } catch (const std::exception& reply_exc) {
                spdlog::warn("zenoh queryable error reply failed: {}", reply_exc.what());
              }
            } catch (...) {
              spdlog::error("zenoh queryable callback failed: unknown error");
              try {
                query.reply_err(zenoh::Bytes("unknown error"));
              } catch (const std::exception& reply_exc) {
                spdlog::warn("zenoh queryable error reply failed: {}", reply_exc.what());
              }
            }
          },
          []() {});
      return std::make_unique<ZenohQueryableHandle>(std::move(queryable));
    } catch (const std::exception& exc) {
      spdlog::error("zenoh serve failed subject={}: {}", subject, exc.what());
      return nullptr;
    } catch (...) {
      spdlog::error("zenoh serve failed subject={}: unknown error", subject);
      return nullptr;
    }
#else
    (void)subject;
    (void)handler;
    return nullptr;
#endif
  }

  bool kv_put(const std::string& key, const RuntimeBytes& payload) {
#if F8_WITH_ZENOH
    const std::string normalized_key = trim_runtime_string(key);
    try {
      std::lock_guard<std::mutex> lock(mu_);
      if (!session_) {
        return false;
      }
      kv_[normalized_key] = payload;
      session_->put(zenoh::KeyExpr(zenoh_kv_key(service_id_, normalized_key)), bytes_to_payload(payload),
                    realtime_drop_options());
      return true;
    } catch (const std::exception& exc) {
      spdlog::error("zenoh kv_put failed key={}: {}", key, exc.what());
      return false;
    } catch (...) {
      spdlog::error("zenoh kv_put failed key={}: unknown error", key);
      return false;
    }
#else
    (void)key;
    (void)payload;
    return false;
#endif
  }

  std::optional<RuntimeBytes> kv_get(const std::string& key) {
    const std::string normalized_key = trim_runtime_string(key);
    std::lock_guard<std::mutex> lock(mu_);
    const auto it = kv_.find(normalized_key);
    if (it == kv_.end()) {
      return std::nullopt;
    }
    return it->second;
  }

  std::optional<RuntimeBytes> kv_get_in_bucket(const std::string& bucket, const std::string& key,
                                               std::chrono::milliseconds timeout) {
#if F8_WITH_ZENOH
    const std::string peer_service_id = kv_bucket_to_service_id(bucket);
    if (peer_service_id == service_id_) {
      std::lock_guard<std::mutex> lock(mu_);
      const auto it = kv_.find(trim_runtime_string(key));
      if (it == kv_.end()) {
        return std::nullopt;
      }
      return it->second;
    }
    const std::string selector = zenoh_kv_key(peer_service_id, key);
    struct RequestState {
      std::mutex mu;
      std::condition_variable cv;
      bool done = false;
      std::optional<RuntimeBytes> reply;
    };

    auto state = std::make_shared<RequestState>();
    try {
      const auto wait_timeout = timeout.count() > 0 ? timeout : std::chrono::milliseconds(1);
      {
        std::lock_guard<std::mutex> lock(mu_);
        if (!session_) {
          return std::nullopt;
        }
        zenoh::Session::GetOptions options = zenoh::Session::GetOptions::create_default();
        options.timeout_ms = static_cast<std::uint64_t>(wait_timeout.count());
        options.is_express = true;
        session_->get(
            zenoh::KeyExpr(selector), "",
            [state](zenoh::Reply& reply) {
              if (!reply.is_ok()) {
                return;
              }
              {
                std::lock_guard<std::mutex> state_lock(state->mu);
                if (!state->reply.has_value()) {
                  state->reply = payload_to_bytes(reply.get_ok().get_payload());
                }
              }
              state->cv.notify_all();
            },
            [state]() {
              {
                std::lock_guard<std::mutex> state_lock(state->mu);
                state->done = true;
              }
              state->cv.notify_all();
            },
            std::move(options));
      }

      std::unique_lock<std::mutex> state_lock(state->mu);
      (void)state->cv.wait_for(state_lock, wait_timeout,
                               [state]() { return state->done || state->reply.has_value(); });
      return state->reply;
    } catch (const std::exception& exc) {
      spdlog::error("zenoh kv_get_in_bucket failed bucket={} key={}: {}", bucket, key, exc.what());
      return std::nullopt;
    } catch (...) {
      spdlog::error("zenoh kv_get_in_bucket failed bucket={} key={}: unknown error", bucket, key);
      return std::nullopt;
    }
#else
    (void)bucket;
    (void)key;
    return std::nullopt;
#endif
  }

  std::unique_ptr<RuntimeSubscription> kv_watch_in_bucket(const std::string& bucket, const std::string& pattern,
                                                          RuntimeKvWatchHandler handler) {
#if F8_WITH_ZENOH
    const std::string peer_service_id = kv_bucket_to_service_id(bucket);
    const std::string key_expr = zenoh_kv_pattern(peer_service_id, pattern);
    std::lock_guard<std::mutex> lock(mu_);
    if (!session_) {
      return nullptr;
    }
    try {
      auto subscriber = session_->declare_subscriber(
          zenoh::KeyExpr(key_expr),
          [handler = std::move(handler)](zenoh::Sample& sample) {
            try {
              const auto kv_key = zenoh_key_to_kv_key(std::string(sample.get_keyexpr().as_string_view()));
              if (!kv_key.has_value()) {
                return;
              }
              handler(*kv_key, payload_to_bytes(sample.get_payload()));
            } catch (const std::exception& exc) {
              spdlog::error("zenoh kv watcher callback failed: {}", exc.what());
            } catch (...) {
              spdlog::error("zenoh kv watcher callback failed: unknown error");
            }
          },
          []() {});
      std::this_thread::sleep_for(kSubscriptionSettle);
      return std::make_unique<ZenohSubscriberHandle>(std::move(subscriber));
    } catch (const std::exception& exc) {
      spdlog::error("zenoh kv_watch_in_bucket failed bucket={} pattern={}: {}", bucket, pattern, exc.what());
      return nullptr;
    } catch (...) {
      spdlog::error("zenoh kv_watch_in_bucket failed bucket={} pattern={}: unknown error", bucket, pattern);
      return nullptr;
    }
#else
    (void)bucket;
    (void)pattern;
    (void)handler;
    return nullptr;
#endif
  }

 private:
  void close_locked() {
#if F8_WITH_ZENOH
    internal_queryables_.clear();
    if (liveliness_token_.has_value()) {
      try {
        std::move(*liveliness_token_).undeclare();
      } catch (const std::exception& exc) {
        spdlog::warn("zenoh liveliness token undeclare failed serviceId={}: {}", service_id_, exc.what());
      } catch (...) {
        spdlog::warn("zenoh liveliness token undeclare failed serviceId={}: unknown error", service_id_);
      }
      liveliness_token_.reset();
    }
    if (session_) {
      try {
        session_->close();
      } catch (const std::exception& exc) {
        spdlog::warn("zenoh session close failed: {}", exc.what());
      } catch (...) {
        spdlog::warn("zenoh session close failed: unknown error");
      }
    }
    session_.reset();
#endif
    kv_.clear();
    service_id_.clear();
  }

  void start_kv_queryables_locked() {
#if F8_WITH_ZENOH
    if (!session_) {
      return;
    }
    auto state_queryable = session_->declare_queryable(
        zenoh::KeyExpr(std::string("f8/svc/") + service_id_ + "/state/**"),
        [this](zenoh::Query& query) { reply_to_kv_query(query); }, []() {});
    internal_queryables_.push_back(std::make_unique<ZenohQueryableHandle>(std::move(state_queryable)));

    auto kv_queryable = session_->declare_queryable(
        zenoh::KeyExpr(std::string("f8/svc/") + service_id_ + "/kv/**"),
        [this](zenoh::Query& query) { reply_to_kv_query(query); }, []() {});
    internal_queryables_.push_back(std::make_unique<ZenohQueryableHandle>(std::move(kv_queryable)));
#endif
  }

#if F8_WITH_ZENOH
  void reply_to_kv_query(zenoh::Query& query) {
    try {
      const auto key = zenoh_key_to_kv_key(std::string(query.get_keyexpr().as_string_view()));
      if (!key.has_value()) {
        query.reply_err(zenoh::Bytes("invalid kv key"));
        return;
      }
      std::optional<RuntimeBytes> value;
      {
        std::lock_guard<std::mutex> lock(mu_);
        const auto it = kv_.find(*key);
        if (it != kv_.end()) {
          value = it->second;
        }
      }
      if (value.has_value()) {
        query.reply(query.get_keyexpr(), bytes_to_payload(*value));
      }
    } catch (const std::exception& exc) {
      spdlog::error("zenoh kv query reply failed: {}", exc.what());
      try {
        query.reply_err(zenoh::Bytes(std::string(exc.what())));
      } catch (const std::exception& reply_exc) {
        spdlog::warn("zenoh kv query error reply failed: {}", reply_exc.what());
      }
    } catch (...) {
      spdlog::error("zenoh kv query reply failed: unknown error");
      try {
        query.reply_err(zenoh::Bytes("unknown error"));
      } catch (const std::exception& reply_exc) {
        spdlog::warn("zenoh kv query error reply failed: {}", reply_exc.what());
      }
    }
  }
#endif

  std::mutex mu_;
  RuntimeBackendConfig config_;
  std::string service_id_;
  std::unordered_map<std::string, RuntimeBytes> kv_;
#if F8_WITH_ZENOH
  std::unique_ptr<zenoh::Session> session_;
  std::optional<zenoh::LivelinessToken> liveliness_token_;
  std::vector<std::unique_ptr<RuntimeSubscription>> internal_queryables_;
#endif
};

ZenohTransport::ZenohTransport() : impl_(std::make_unique<Impl>()) {}
ZenohTransport::~ZenohTransport() = default;

bool ZenohTransport::connect(const RuntimeBackendConfig& config, const std::string& service_id) {
  return impl_->connect(config, service_id);
}

void ZenohTransport::close() {
  impl_->close();
}

bool ZenohTransport::publish(const std::string& subject, const RuntimeBytes& payload) {
  return impl_->publish(subject, payload);
}

std::unique_ptr<RuntimeSubscription> ZenohTransport::subscribe(const std::string& subject,
                                                               RuntimeMessageHandler handler) {
  return impl_->subscribe(subject, std::move(handler));
}

std::optional<RuntimeBytes> ZenohTransport::request(const std::string& subject, const RuntimeBytes& payload,
                                                    std::chrono::milliseconds timeout) {
  return impl_->request(subject, payload, timeout);
}

std::unique_ptr<RuntimeSubscription> ZenohTransport::serve(const std::string& subject, RuntimeRequestHandler handler) {
  return impl_->serve(subject, std::move(handler));
}

bool ZenohTransport::kv_put(const std::string& key, const RuntimeBytes& payload) {
  return impl_->kv_put(key, payload);
}

std::optional<RuntimeBytes> ZenohTransport::kv_get(const std::string& key) {
  return impl_->kv_get(key);
}

std::optional<RuntimeBytes> ZenohTransport::kv_get_in_bucket(const std::string& bucket, const std::string& key,
                                                             std::chrono::milliseconds timeout) {
  return impl_->kv_get_in_bucket(bucket, key, timeout);
}

std::unique_ptr<RuntimeSubscription> ZenohTransport::kv_watch_in_bucket(const std::string& bucket,
                                                                        const std::string& pattern,
                                                                        RuntimeKvWatchHandler handler) {
  return impl_->kv_watch_in_bucket(bucket, pattern, std::move(handler));
}

}  // namespace f8::cppsdk
