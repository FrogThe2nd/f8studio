#pragma once

#include <memory>
#include <string>

#include "f8cppsdk/runtime_transport.h"

namespace f8::cppsdk {

class ZenohTransport final : public RuntimeTransport {
 public:
  ZenohTransport();
  ~ZenohTransport() override;

  ZenohTransport(const ZenohTransport&) = delete;
  ZenohTransport& operator=(const ZenohTransport&) = delete;
  ZenohTransport(ZenohTransport&&) = delete;
  ZenohTransport& operator=(ZenohTransport&&) = delete;

  bool connect(const RuntimeBackendConfig& config, const std::string& service_id) override;
  void close() override;

  bool publish(const std::string& key, const RuntimeBytes& payload) override;
  std::unique_ptr<RuntimeSubscription> subscribe(const std::string& key_expr, RuntimeMessageHandler handler) override;
  std::optional<RuntimeBytes> request(const std::string& key, const RuntimeBytes& payload,
                                      std::chrono::milliseconds timeout) override;
  std::unique_ptr<RuntimeSubscription> serve(const std::string& key, RuntimeRequestHandler handler) override;

  bool retained_put(const std::string& key, const RuntimeBytes& payload) override;
  std::optional<RuntimeBytes> retained_get(const std::string& key) override;
  std::unique_ptr<RuntimeSubscription> retained_watch(const std::string& key_expr,
                                                      RuntimeRetainedWatchHandler handler) override;

 private:
  class Impl;
  std::unique_ptr<Impl> impl_;
};

}  // namespace f8::cppsdk
