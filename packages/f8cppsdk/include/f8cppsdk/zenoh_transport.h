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

  bool publish(const std::string& subject, const RuntimeBytes& payload) override;
  std::unique_ptr<RuntimeSubscription> subscribe(const std::string& subject, RuntimeMessageHandler handler) override;
  std::optional<RuntimeBytes> request(const std::string& subject, const RuntimeBytes& payload,
                                      std::chrono::milliseconds timeout) override;
  std::unique_ptr<RuntimeSubscription> serve(const std::string& subject, RuntimeRequestHandler handler) override;

  bool kv_put(const std::string& key, const RuntimeBytes& payload) override;
  std::optional<RuntimeBytes> kv_get(const std::string& key) override;
  std::optional<RuntimeBytes> kv_get_in_bucket(const std::string& bucket, const std::string& key) override;
  std::unique_ptr<RuntimeSubscription> kv_watch_in_bucket(const std::string& bucket, const std::string& pattern,
                                                          RuntimeKvWatchHandler handler) override;

 private:
  class Impl;
  std::unique_ptr<Impl> impl_;
};

}  // namespace f8::cppsdk
