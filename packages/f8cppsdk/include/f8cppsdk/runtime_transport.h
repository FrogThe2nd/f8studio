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
  std::string key;
  RuntimeBytes payload;
};

using RuntimeMessageHandler = std::function<void(const RuntimeMessage&)>;
using RuntimeRequestHandler = std::function<RuntimeBytes(const RuntimeMessage&)>;
using RuntimeRetainedWatchHandler = std::function<void(const std::string& key, const RuntimeBytes& payload)>;

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

  virtual bool publish(const std::string& key, const RuntimeBytes& payload) = 0;
  virtual std::unique_ptr<RuntimeSubscription> subscribe(const std::string& key_expr, RuntimeMessageHandler handler) = 0;
  virtual std::optional<RuntimeBytes> request(const std::string& key, const RuntimeBytes& payload,
                                              std::chrono::milliseconds timeout) = 0;
  virtual std::unique_ptr<RuntimeSubscription> serve(const std::string& key, RuntimeRequestHandler handler) = 0;

  virtual bool retained_put(const std::string& key, const RuntimeBytes& payload) = 0;
  virtual std::optional<RuntimeBytes> retained_get(const std::string& key) = 0;
  virtual std::unique_ptr<RuntimeSubscription> retained_watch(const std::string& key_expr,
                                                              RuntimeRetainedWatchHandler handler) = 0;
};

}  // namespace f8::cppsdk
