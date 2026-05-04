#pragma once

#include <chrono>
#include <cstdint>
#include <functional>
#include <memory>
#include <optional>
#include <string>
#include <vector>

#include "f8cppsdk/runtime_backend.h"

namespace f8::cppsdk {

using RuntimeBytes = std::vector<std::uint8_t>;

struct RuntimeMessage {
  std::string subject;
  RuntimeBytes payload;
};

using RuntimeMessageHandler = std::function<void(const RuntimeMessage&)>;
using RuntimeRequestHandler = std::function<RuntimeBytes(const RuntimeMessage&)>;
using RuntimeKvWatchHandler = std::function<void(const std::string& key, const RuntimeBytes& payload)>;

class RuntimeSubscription {
 public:
  virtual ~RuntimeSubscription() = default;
  virtual void stop() = 0;
  virtual bool valid() const = 0;
};

class RuntimeTransport {
 public:
  virtual ~RuntimeTransport() = default;

  virtual bool connect(const RuntimeBackendConfig& config, const std::string& service_id) = 0;
  virtual void close() = 0;

  virtual bool publish(const std::string& subject, const RuntimeBytes& payload) = 0;
  virtual std::unique_ptr<RuntimeSubscription> subscribe(const std::string& subject, RuntimeMessageHandler handler) = 0;
  virtual std::optional<RuntimeBytes> request(const std::string& subject, const RuntimeBytes& payload,
                                              std::chrono::milliseconds timeout) = 0;
  virtual std::unique_ptr<RuntimeSubscription> serve(const std::string& subject, RuntimeRequestHandler handler) = 0;

  virtual bool kv_put(const std::string& key, const RuntimeBytes& payload) = 0;
  virtual std::optional<RuntimeBytes> kv_get(const std::string& key) = 0;
  virtual std::optional<RuntimeBytes> kv_get_in_bucket(
      const std::string& bucket, const std::string& key,
      std::chrono::milliseconds timeout = std::chrono::milliseconds(1000)) = 0;
  virtual std::unique_ptr<RuntimeSubscription> kv_watch_in_bucket(const std::string& bucket, const std::string& pattern,
                                                                  RuntimeKvWatchHandler handler) = 0;
};

}  // namespace f8::cppsdk
