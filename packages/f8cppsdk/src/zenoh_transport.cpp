#include "f8cppsdk/zenoh_transport.h"

#include "f8cppsdk/f8_naming.h"
#include "f8cppsdk/zenoh_naming.h"

#include "zenoh_config_internal.h"

#include <chrono>
#include <condition_variable>
#include <mutex>
#include <optional>
#include <cstdint>
#include <string>
#include <thread>
#include <unordered_map>
#include <utility>
#include <vector>

#include <nlohmann/json.hpp>
#include <spdlog/spdlog.h>

#if F8_WITH_ZENOH
#include <zenoh.hxx>
#include <zenoh/api/ext/session_ext.hxx>
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

zenoh::ext::SessionExt::AdvancedPublisherOptions retained_state_publisher_options() {
  zenoh::ext::SessionExt::AdvancedPublisherOptions options =
      zenoh::ext::SessionExt::AdvancedPublisherOptions::create_default();
  options.publisher_options.congestion_control = Z_CONGESTION_CONTROL_BLOCK;
  options.publisher_options.priority = Z_PRIORITY_INTERACTIVE_HIGH;
  options.publisher_options.reliability = Z_RELIABILITY_RELIABLE;
  options.publisher_options.is_express = true;
  auto cache = zenoh::ext::SessionExt::AdvancedPublisherOptions::CacheOptions::create_default();
  cache.max_samples = 1;
  cache.congestion_control = Z_CONGESTION_CONTROL_BLOCK;
  cache.priority = Z_PRIORITY_INTERACTIVE_HIGH;
  cache.is_express = true;
  options.cache = cache;
  options.publisher_detection = true;
  return options;
}

zenoh::ext::SessionExt::AdvancedSubscriberOptions retained_state_subscriber_options() {
  zenoh::ext::SessionExt::AdvancedSubscriberOptions options =
      zenoh::ext::SessionExt::AdvancedSubscriberOptions::create_default();
  auto history = zenoh::ext::SessionExt::AdvancedSubscriberOptions::HistoryOptions::create_default();
  history.detect_late_publishers = true;
  history.max_samples = 1;
  options.history = history;
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

class ZenohAdvancedSubscriberHandle final : public RuntimeSubscription {
 public:
  explicit ZenohAdvancedSubscriberHandle(zenoh::ext::AdvancedSubscriber<void>&& subscriber)
      : subscriber_(std::move(subscriber)) {}
  ~ZenohAdvancedSubscriberHandle() override { stop(); }

  void stop() override {
    std::lock_guard<std::mutex> lock(mu_);
    if (!subscriber_.has_value()) {
      return;
    }
    try {
      std::move(*subscriber_).undeclare();
    } catch (const std::exception& exc) {
      spdlog::warn("zenoh advanced subscriber undeclare failed: {}", exc.what());
    } catch (...) {
      spdlog::warn("zenoh advanced subscriber undeclare failed: unknown error");
    }
    subscriber_.reset();
  }

  bool valid() const override {
    std::lock_guard<std::mutex> lock(mu_);
    return subscriber_.has_value();
  }

 private:
  mutable std::mutex mu_;
  std::optional<zenoh::ext::AdvancedSubscriber<void>> subscriber_;
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

zenoh::Session::GetOptions query_get_options(const RuntimeBytes& payload, std::chrono::milliseconds timeout) {
  zenoh::Session::GetOptions options = zenoh::Session::GetOptions::create_default();
  options.target = Z_QUERY_TARGET_BEST_MATCHING;
  options.consolidation = zenoh::QueryConsolidation(Z_CONSOLIDATION_MODE_AUTO);
  options.congestion_control = Z_CONGESTION_CONTROL_BLOCK;
  options.priority = Z_PRIORITY_INTERACTIVE_HIGH;
  options.is_express = true;
  options.payload = bytes_to_payload(payload);
  options.encoding = zenoh::Encoding("application/octet-stream");
  options.timeout_ms = static_cast<std::uint64_t>(std::max<std::int64_t>(1, timeout.count()));
  return options;
}

zenoh::Session::QueryableOptions queryable_options() {
  zenoh::Session::QueryableOptions options = zenoh::Session::QueryableOptions::create_default();
  options.complete = true;
  return options;
}

RuntimeBytes query_payload_to_bytes(zenoh::Query& query) {
  auto payload = query.get_payload();
  if (!payload.has_value()) {
    return {};
  }
  return payload->get().as_vector();
}

std::string reply_error_to_string(const zenoh::Reply& reply) {
  try {
    return reply.get_err().get_payload().as_string();
  } catch (const std::exception& exc) {
    return exc.what();
  } catch (...) {
    return "unknown query error";
  }
}
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
      zenoh_internal::apply_shared_memory_config(zenoh_config, config_.zenoh_shm_pool_bytes, service_id_);
      zenoh_internal::apply_timestamping_config(zenoh_config, service_id_);

      auto session = std::make_unique<zenoh::Session>(zenoh::Session::open(std::move(zenoh_config)));
      session_ = std::move(session);
      if (config_.announce_service_liveliness) {
        const std::string runtime_instance_id = ensure_token(config_.runtime_instance_id, "runtime_instance_id");
        liveliness_token_ = session_->liveliness_declare_token(
            zenoh::KeyExpr(zenoh_service_liveliness_key(service_id_, runtime_instance_id)));
      }
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

  bool publish(const std::string& key, const RuntimeBytes& payload) {
#if F8_WITH_ZENOH
    try {
      std::lock_guard<std::mutex> lock(mu_);
      if (!session_) {
        return false;
      }
      session_->put(zenoh::KeyExpr(trim_runtime_string(key)), bytes_to_payload(payload), realtime_drop_options());
      return true;
    } catch (const std::exception& exc) {
      spdlog::error("zenoh publish failed key={}: {}", key, exc.what());
      return false;
    } catch (...) {
      spdlog::error("zenoh publish failed key={}: unknown error", key);
      return false;
    }
#else
    (void)key;
    (void)payload;
    return false;
#endif
  }

  std::unique_ptr<RuntimeSubscription> subscribe(const std::string& key_expr, RuntimeMessageHandler handler) {
#if F8_WITH_ZENOH
    std::lock_guard<std::mutex> lock(mu_);
    if (!session_) {
      return nullptr;
    }
    try {
      auto subscriber = session_->declare_subscriber(
          zenoh::KeyExpr(trim_runtime_string(key_expr)),
          [handler = std::move(handler)](zenoh::Sample& sample) {
            try {
              RuntimeMessage msg;
              msg.key = std::string(sample.get_keyexpr().as_string_view());
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
      spdlog::error("zenoh subscribe failed keyExpr={}: {}", key_expr, exc.what());
      return nullptr;
    } catch (...) {
      spdlog::error("zenoh subscribe failed keyExpr={}: unknown error", key_expr);
      return nullptr;
    }
#else
    (void)key_expr;
    (void)handler;
    return nullptr;
#endif
  }

  std::optional<RuntimeBytes> request(const std::string& key, const RuntimeBytes& payload,
                                      std::chrono::milliseconds timeout) {
#if F8_WITH_ZENOH
    constexpr const char* kNoReplyError = "query completed without reply";
    struct RequestState {
      std::mutex mu;
      std::condition_variable cv;
      bool done = false;
      bool ok = false;
      std::optional<RuntimeBytes> reply;
      std::string error;
    };

    const auto wait_timeout = timeout.count() > 0 ? timeout : std::chrono::milliseconds(1);
    const auto deadline = std::chrono::steady_clock::now() + wait_timeout;
    std::string last_error;
    try {
      const std::string query_key = trim_runtime_string(key);

      while (std::chrono::steady_clock::now() < deadline) {
        auto state = std::make_shared<RequestState>();
        const auto now = std::chrono::steady_clock::now();
        const auto remaining = std::chrono::duration_cast<std::chrono::milliseconds>(deadline - now);
        if (remaining.count() <= 0) {
          break;
        }

        {
          std::lock_guard<std::mutex> lock(mu_);
          if (!session_) {
            return std::nullopt;
          }
          session_->get(
              zenoh::KeyExpr(query_key),
              "",
              [state](zenoh::Reply& reply) {
                {
                  std::lock_guard<std::mutex> state_lock(state->mu);
                  state->done = true;
                  state->ok = reply.is_ok();
                  if (reply.is_ok()) {
                    state->reply = payload_to_bytes(reply.get_ok().get_payload());
                  } else {
                    state->error = reply_error_to_string(reply);
                  }
                }
                state->cv.notify_all();
              },
              [state, kNoReplyError]() {
                {
                  std::lock_guard<std::mutex> state_lock(state->mu);
                  if (!state->done) {
                    state->done = true;
                    state->ok = false;
                    state->error = kNoReplyError;
                  }
                }
                state->cv.notify_all();
              },
              query_get_options(payload, remaining));
        }

        bool done = false;
        bool ok = false;
        std::optional<RuntimeBytes> reply;
        std::string error;
        {
          std::unique_lock<std::mutex> state_lock(state->mu);
          (void)state->cv.wait_until(state_lock, deadline, [state]() { return state->done; });
          done = state->done;
          ok = state->ok;
          reply = state->reply;
          error = state->error;
        }
        if (done && ok) {
          return reply.value_or(RuntimeBytes{});
        }
        last_error = error;
        if (!done || error != kNoReplyError) {
          if (!error.empty()) {
            spdlog::debug("zenoh query request returned error key={}: {}", key, error);
          }
          return std::nullopt;
        }
        std::this_thread::sleep_for(std::min(std::chrono::milliseconds(20), remaining));
      }
      if (!last_error.empty()) {
        spdlog::debug("zenoh query request returned error key={}: {}", key, last_error);
      }
      return std::nullopt;
    } catch (const std::exception& exc) {
      spdlog::error("zenoh query request failed key={}: {}", key, exc.what());
      return std::nullopt;
    } catch (...) {
      spdlog::error("zenoh query request failed key={}: unknown error", key);
      return std::nullopt;
    }
#else
    (void)key;
    (void)payload;
    (void)timeout;
    return std::nullopt;
#endif
  }

  std::unique_ptr<RuntimeSubscription> serve(const std::string& key, RuntimeRequestHandler handler) {
#if F8_WITH_ZENOH
    std::lock_guard<std::mutex> lock(mu_);
    if (!session_) {
      return nullptr;
    }
    try {
      auto queryable = session_->declare_queryable(
          zenoh::KeyExpr(trim_runtime_string(key)),
          [key, handler = std::move(handler)](zenoh::Query& query) {
            try {
              RuntimeMessage msg;
              msg.key = key;
              msg.payload = query_payload_to_bytes(query);
              RuntimeBytes response = handler(msg);
              zenoh::Query::ReplyOptions options = zenoh::Query::ReplyOptions::create_default();
              options.encoding = zenoh::Encoding("application/octet-stream");
              options.is_express = true;
              query.reply(query.get_keyexpr(), bytes_to_payload(response), std::move(options));
            } catch (const std::exception& exc) {
              spdlog::error("zenoh queryable callback failed key={}: {}", key, exc.what());
              zenoh::Query::ReplyErrOptions options = zenoh::Query::ReplyErrOptions::create_default();
              options.encoding = zenoh::Encoding("text/plain");
              query.reply_err(zenoh::Bytes("query handler failed"), std::move(options));
            } catch (...) {
              spdlog::error("zenoh queryable callback failed key={}: unknown error", key);
              zenoh::Query::ReplyErrOptions options = zenoh::Query::ReplyErrOptions::create_default();
              options.encoding = zenoh::Encoding("text/plain");
              query.reply_err(zenoh::Bytes("query handler failed"), std::move(options));
            }
          },
          []() {}, queryable_options());
      std::this_thread::sleep_for(kSubscriptionSettle);
      return std::make_unique<ZenohQueryableHandle>(std::move(queryable));
    } catch (const std::exception& exc) {
      spdlog::error("zenoh queryable serve failed key={}: {}", key, exc.what());
      return nullptr;
    } catch (...) {
      spdlog::error("zenoh queryable serve failed key={}: unknown error", key);
      return nullptr;
    }
#else
    (void)key;
    (void)handler;
    return nullptr;
#endif
  }

  bool retained_put(const std::string& key, const RuntimeBytes& payload) {
#if F8_WITH_ZENOH
    const std::string normalized_key = trim_runtime_string(key);
    try {
      std::lock_guard<std::mutex> lock(mu_);
      if (!session_) {
        return false;
      }
      retained_[normalized_key] = payload;
      auto publisher_it = retained_state_publishers_.find(normalized_key);
      if (publisher_it == retained_state_publishers_.end()) {
        zenoh::ext::SessionExt ext(*session_);
        auto publisher = ext.declare_advanced_publisher(zenoh::KeyExpr(normalized_key), retained_state_publisher_options());
        publisher_it = retained_state_publishers_
                           .emplace(normalized_key, std::make_unique<zenoh::ext::AdvancedPublisher>(std::move(publisher)))
                           .first;
        std::this_thread::sleep_for(kSubscriptionSettle);
      }
      publisher_it->second->put(bytes_to_payload(payload));
      return true;
    } catch (const std::exception& exc) {
      spdlog::error("zenoh retained_put failed key={}: {}", key, exc.what());
      return false;
    } catch (...) {
      spdlog::error("zenoh retained_put failed key={}: unknown error", key);
      return false;
    }
#else
    (void)key;
    (void)payload;
    return false;
#endif
  }

  std::optional<RuntimeBytes> retained_get(const std::string& key) {
    const std::string normalized_key = trim_runtime_string(key);
    std::lock_guard<std::mutex> lock(mu_);
    const auto it = retained_.find(normalized_key);
    if (it == retained_.end()) {
      return std::nullopt;
    }
    return it->second;
  }

  std::unique_ptr<RuntimeSubscription> retained_watch(const std::string& key_expr,
                                                      RuntimeRetainedWatchHandler handler) {
#if F8_WITH_ZENOH
    std::lock_guard<std::mutex> lock(mu_);
    if (!session_) {
      return nullptr;
    }
    try {
      zenoh::ext::SessionExt ext(*session_);
      auto subscriber = ext.declare_advanced_subscriber(
          zenoh::KeyExpr(trim_runtime_string(key_expr)),
          [handler = std::move(handler)](const zenoh::Sample& sample) {
            try {
              handler(std::string(sample.get_keyexpr().as_string_view()), payload_to_bytes(sample.get_payload()));
            } catch (const std::exception& exc) {
              spdlog::error("zenoh retained watcher callback failed: {}", exc.what());
            } catch (...) {
              spdlog::error("zenoh retained watcher callback failed: unknown error");
            }
          },
          []() {}, retained_state_subscriber_options());
      std::this_thread::sleep_for(kSubscriptionSettle);
      return std::make_unique<ZenohAdvancedSubscriberHandle>(std::move(subscriber));
    } catch (const std::exception& exc) {
      spdlog::error("zenoh retained_watch failed keyExpr={}: {}", key_expr, exc.what());
      return nullptr;
    } catch (...) {
      spdlog::error("zenoh retained_watch failed keyExpr={}: unknown error", key_expr);
      return nullptr;
    }
#else
    (void)key_expr;
    (void)handler;
    return nullptr;
#endif
  }

 private:
  void close_locked() {
#if F8_WITH_ZENOH
    retained_state_publishers_.clear();
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
    retained_.clear();
    service_id_.clear();
  }

  std::mutex mu_;
  RuntimeBackendConfig config_;
  std::string service_id_;
  std::unordered_map<std::string, RuntimeBytes> retained_;
#if F8_WITH_ZENOH
  std::unique_ptr<zenoh::Session> session_;
  std::optional<zenoh::LivelinessToken> liveliness_token_;
  std::unordered_map<std::string, std::unique_ptr<zenoh::ext::AdvancedPublisher>> retained_state_publishers_;
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

bool ZenohTransport::publish(const std::string& key, const RuntimeBytes& payload) {
  return impl_->publish(key, payload);
}

std::unique_ptr<RuntimeSubscription> ZenohTransport::subscribe(const std::string& key_expr,
                                                               RuntimeMessageHandler handler) {
  return impl_->subscribe(key_expr, std::move(handler));
}

std::optional<RuntimeBytes> ZenohTransport::request(const std::string& key, const RuntimeBytes& payload,
                                                    std::chrono::milliseconds timeout) {
  return impl_->request(key, payload, timeout);
}

std::unique_ptr<RuntimeSubscription> ZenohTransport::serve(const std::string& key, RuntimeRequestHandler handler) {
  return impl_->serve(key, std::move(handler));
}

bool ZenohTransport::retained_put(const std::string& key, const RuntimeBytes& payload) {
  return impl_->retained_put(key, payload);
}

std::optional<RuntimeBytes> ZenohTransport::retained_get(const std::string& key) {
  return impl_->retained_get(key);
}

std::unique_ptr<RuntimeSubscription> ZenohTransport::retained_watch(const std::string& key_expr,
                                                                    RuntimeRetainedWatchHandler handler) {
  return impl_->retained_watch(key_expr, std::move(handler));
}

}  // namespace f8::cppsdk
