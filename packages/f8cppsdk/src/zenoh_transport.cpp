#include "f8cppsdk/zenoh_transport.h"

#include "f8cppsdk/f8_naming.h"
#include "f8cppsdk/msg_codec.h"
#include "f8cppsdk/time_utils.h"
#include "f8cppsdk/zenoh_naming.h"

#include "zenoh_config_internal.h"

#include <chrono>
#include <condition_variable>
#include <atomic>
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

struct CommandEnvelope {
  std::string req_id;
  std::string actor;
  std::int64_t ts_ms = 0;
  RuntimeBytes payload;
  std::string reply_key;
};

struct CommandReply {
  std::string req_id;
  bool ok = false;
  RuntimeBytes payload;
  std::string error;
};

RuntimeBytes json_binary_to_bytes(const nlohmann::json& value) {
  if (value.is_binary()) {
    const auto& binary = value.get_binary();
    return RuntimeBytes(binary.begin(), binary.end());
  }
  if (value.is_array()) {
    RuntimeBytes out;
    out.reserve(value.size());
    for (const auto& item : value) {
      out.push_back(static_cast<std::uint8_t>(item.get<int>()));
    }
    return out;
  }
  return {};
}

std::string new_runtime_req_id() {
  static std::atomic<std::uint64_t> seq{0};
  const auto ticks = std::chrono::steady_clock::now().time_since_epoch().count();
  return std::to_string(static_cast<long long>(ticks)) + "_" + std::to_string(seq.fetch_add(1));
}

RuntimeBytes encode_command_envelope(const CommandEnvelope& envelope) {
  nlohmann::json payload = nlohmann::json::object();
  payload["v"] = 1;
  payload["reqId"] = envelope.req_id;
  payload["actor"] = envelope.actor;
  payload["tsMs"] = envelope.ts_ms;
  payload["payload"] = nlohmann::json::binary(envelope.payload);
  payload["replyKey"] = envelope.reply_key;
  return encode_json(payload);
}

std::optional<CommandEnvelope> decode_command_envelope(const RuntimeBytes& bytes) {
  nlohmann::json payload;
  if (!decode_json(bytes.data(), bytes.size(), payload) || !payload.is_object()) {
    return std::nullopt;
  }
  CommandEnvelope envelope;
  if (payload.contains("reqId") && payload["reqId"].is_string()) {
    envelope.req_id = payload["reqId"].get<std::string>();
  }
  if (envelope.req_id.empty()) {
    return std::nullopt;
  }
  if (payload.contains("actor") && payload["actor"].is_string()) {
    envelope.actor = payload["actor"].get<std::string>();
  }
  if (payload.contains("tsMs") && payload["tsMs"].is_number_integer()) {
    envelope.ts_ms = payload["tsMs"].get<std::int64_t>();
  }
  if (payload.contains("payload")) {
    envelope.payload = json_binary_to_bytes(payload["payload"]);
  }
  if (payload.contains("replyKey") && payload["replyKey"].is_string()) {
    envelope.reply_key = payload["replyKey"].get<std::string>();
  }
  return envelope;
}

RuntimeBytes encode_command_reply(const CommandReply& reply) {
  nlohmann::json payload = nlohmann::json::object();
  payload["v"] = 1;
  payload["reqId"] = reply.req_id;
  payload["ok"] = reply.ok;
  payload["payload"] = nlohmann::json::binary(reply.payload);
  payload["error"] = reply.error;
  return encode_json(payload);
}

std::optional<CommandReply> decode_command_reply(const RuntimeBytes& bytes) {
  nlohmann::json payload;
  if (!decode_json(bytes.data(), bytes.size(), payload) || !payload.is_object()) {
    return std::nullopt;
  }
  CommandReply reply;
  if (payload.contains("reqId") && payload["reqId"].is_string()) {
    reply.req_id = payload["reqId"].get<std::string>();
  }
  if (reply.req_id.empty()) {
    return std::nullopt;
  }
  if (payload.contains("ok") && payload["ok"].is_boolean()) {
    reply.ok = payload["ok"].get<bool>();
  }
  if (payload.contains("payload")) {
    reply.payload = json_binary_to_bytes(payload["payload"]);
  }
  if (payload.contains("error") && payload["error"].is_string()) {
    reply.error = payload["error"].get<std::string>();
  }
  return reply;
}

zenoh::Session::PutOptions realtime_drop_options() {
  zenoh::Session::PutOptions options = zenoh::Session::PutOptions::create_default();
  options.congestion_control = Z_CONGESTION_CONTROL_DROP;
  options.priority = Z_PRIORITY_REAL_TIME;
  options.reliability = Z_RELIABILITY_BEST_EFFORT;
  options.is_express = true;
  return options;
}

zenoh::Session::PutOptions command_put_options() {
  zenoh::Session::PutOptions options = zenoh::Session::PutOptions::create_default();
  options.congestion_control = Z_CONGESTION_CONTROL_BLOCK;
  options.priority = Z_PRIORITY_INTERACTIVE_HIGH;
  options.reliability = Z_RELIABILITY_RELIABLE;
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
      liveliness_token_ =
          session_->liveliness_declare_token(zenoh::KeyExpr(zenoh_service_liveliness_key(service_id_)));
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
      bool ok = false;
      std::optional<RuntimeBytes> reply;
      std::string error;
    };

    auto state = std::make_shared<RequestState>();
    std::optional<zenoh::Subscriber<void>> reply_subscriber;
    try {
      const std::string req_id = new_runtime_req_id();
      const std::string reply_key = zenoh_reply_key(service_id_, req_id);
      const std::string command_key = subject_to_zenoh_command_key(subject);
      const RuntimeBytes envelope = encode_command_envelope(
          CommandEnvelope{req_id, service_id_, static_cast<std::int64_t>(now_ms()), payload, reply_key});
      {
        std::lock_guard<std::mutex> lock(mu_);
        if (!session_) {
          return std::nullopt;
        }
        reply_subscriber = session_->declare_subscriber(
            zenoh::KeyExpr(reply_key),
            [state, req_id](zenoh::Sample& sample) {
              const auto decoded = decode_command_reply(payload_to_bytes(sample.get_payload()));
              if (!decoded.has_value() || decoded->req_id != req_id) {
                return;
              }
              {
                std::lock_guard<std::mutex> state_lock(state->mu);
                state->done = true;
                state->ok = decoded->ok;
                state->reply = decoded->payload;
                state->error = decoded->error;
              }
              state->cv.notify_all();
            },
            []() {});
        std::this_thread::sleep_for(kSubscriptionSettle);
        session_->put(zenoh::KeyExpr(command_key), bytes_to_payload(envelope), command_put_options());
      }

      const auto wait_timeout = timeout.count() > 0 ? timeout : std::chrono::milliseconds(1);
      bool done = false;
      bool ok = false;
      std::optional<RuntimeBytes> reply;
      std::string error;
      {
        std::unique_lock<std::mutex> state_lock(state->mu);
        (void)state->cv.wait_for(state_lock, wait_timeout, [state]() { return state->done; });
        done = state->done;
        ok = state->ok;
        reply = state->reply;
        error = state->error;
      }
      if (reply_subscriber.has_value()) {
        std::move(*reply_subscriber).undeclare();
      }
      if (!done || !ok) {
        if (!error.empty()) {
          spdlog::debug("zenoh command request returned error subject={}: {}", subject, error);
        }
        return std::nullopt;
      }
      return reply.value_or(RuntimeBytes{});
    } catch (const std::exception& exc) {
      if (reply_subscriber.has_value()) {
        try {
          std::move(*reply_subscriber).undeclare();
        } catch (const std::exception& undeclare_exc) {
          spdlog::warn("zenoh command reply subscriber undeclare failed: {}", undeclare_exc.what());
        }
      }
      spdlog::error("zenoh request failed subject={}: {}", subject, exc.what());
      return std::nullopt;
    } catch (...) {
      if (reply_subscriber.has_value()) {
        try {
          std::move(*reply_subscriber).undeclare();
        } catch (...) {
          spdlog::warn("zenoh command reply subscriber undeclare failed: unknown error");
        }
      }
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
      auto subscriber = session_->declare_subscriber(
          zenoh::KeyExpr(subject_to_zenoh_command_key(subject)),
          [this, subject, handler = std::move(handler)](zenoh::Sample& sample) {
            const auto envelope = decode_command_envelope(payload_to_bytes(sample.get_payload()));
            if (!envelope.has_value()) {
              spdlog::error("zenoh command envelope decode failed subject={}", subject);
              return;
            }
            try {
              RuntimeMessage msg;
              msg.subject = subject;
              msg.payload = envelope->payload;
              RuntimeBytes response = handler(msg);
              publish_command_reply(
                  envelope->reply_key,
                  CommandReply{envelope->req_id, true, response, std::string{}});
            } catch (const std::exception& exc) {
              spdlog::error("zenoh command callback failed subject={}: {}", subject, exc.what());
              publish_command_reply(
                  envelope->reply_key,
                  CommandReply{envelope->req_id, false, RuntimeBytes{}, std::string("command handler failed")});
            } catch (...) {
              spdlog::error("zenoh command callback failed subject={}: unknown error", subject);
              publish_command_reply(
                  envelope->reply_key,
                  CommandReply{envelope->req_id, false, RuntimeBytes{}, std::string("command handler failed")});
            }
          },
          []() {});
      std::this_thread::sleep_for(kSubscriptionSettle);
      return std::make_unique<ZenohSubscriberHandle>(std::move(subscriber));
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
      const std::string zenoh_key = zenoh_kv_key(service_id_, normalized_key);
      auto publisher_it = retained_state_publishers_.find(zenoh_key);
      if (publisher_it == retained_state_publishers_.end()) {
        zenoh::ext::SessionExt ext(*session_);
        auto publisher = ext.declare_advanced_publisher(zenoh::KeyExpr(zenoh_key), retained_state_publisher_options());
        publisher_it = retained_state_publishers_
                           .emplace(zenoh_key, std::make_unique<zenoh::ext::AdvancedPublisher>(std::move(publisher)))
                           .first;
        std::this_thread::sleep_for(kSubscriptionSettle);
      }
      publisher_it->second->put(bytes_to_payload(payload));
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
    (void)timeout;
    return std::nullopt;
#else
    (void)bucket;
    (void)key;
    (void)timeout;
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
      zenoh::ext::SessionExt ext(*session_);
      auto subscriber = ext.declare_advanced_subscriber(
          zenoh::KeyExpr(key_expr),
          [handler = std::move(handler)](const zenoh::Sample& sample) {
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
          []() {}, retained_state_subscriber_options());
      std::this_thread::sleep_for(kSubscriptionSettle);
      return std::make_unique<ZenohAdvancedSubscriberHandle>(std::move(subscriber));
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
  void publish_command_reply(const std::string& reply_key, const CommandReply& reply) {
#if F8_WITH_ZENOH
    if (reply_key.empty()) {
      return;
    }
    try {
      std::lock_guard<std::mutex> lock(mu_);
      if (!session_) {
        return;
      }
      session_->put(zenoh::KeyExpr(reply_key), bytes_to_payload(encode_command_reply(reply)), command_put_options());
    } catch (const std::exception& exc) {
      spdlog::error("zenoh command reply publish failed key={}: {}", reply_key, exc.what());
    } catch (...) {
      spdlog::error("zenoh command reply publish failed key={}: unknown error", reply_key);
    }
#else
    (void)reply_key;
    (void)reply;
#endif
  }

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
    kv_.clear();
    service_id_.clear();
  }

  std::mutex mu_;
  RuntimeBackendConfig config_;
  std::string service_id_;
  std::unordered_map<std::string, RuntimeBytes> kv_;
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
