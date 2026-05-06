#pragma once

#include <chrono>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <memory>
#include <optional>
#include <string>

#include "f8cppsdk/runtime_backend.h"
#include "f8cppsdk/runtime_transport.h"

namespace f8::cppsdk {

using LatestBinaryPayloadWriter = std::function<bool(std::uint8_t* out, std::size_t size, std::string* error_message)>;

class ZenohLatestBinaryStreamPublisher final {
 public:
  explicit ZenohLatestBinaryStreamPublisher(std::string log_context = "stream");
  ~ZenohLatestBinaryStreamPublisher();
  ZenohLatestBinaryStreamPublisher(const ZenohLatestBinaryStreamPublisher&) = delete;
  ZenohLatestBinaryStreamPublisher& operator=(const ZenohLatestBinaryStreamPublisher&) = delete;
  ZenohLatestBinaryStreamPublisher(ZenohLatestBinaryStreamPublisher&&) = delete;
  ZenohLatestBinaryStreamPublisher& operator=(ZenohLatestBinaryStreamPublisher&&) = delete;

  bool open(const RuntimeBackendConfig& config, const std::string& key_expr);
  void close();
  bool publish_bytes(const RuntimeBytes& payload);
  bool publish_payload(std::size_t payload_bytes, LatestBinaryPayloadWriter writer);

  bool valid() const;
  std::string key_expr() const;

 private:
  class Impl;
  std::unique_ptr<Impl> impl_;
};

class ZenohLatestBinaryStreamSubscriber final {
 public:
  explicit ZenohLatestBinaryStreamSubscriber(std::string log_context = "stream");
  ~ZenohLatestBinaryStreamSubscriber();
  ZenohLatestBinaryStreamSubscriber(const ZenohLatestBinaryStreamSubscriber&) = delete;
  ZenohLatestBinaryStreamSubscriber& operator=(const ZenohLatestBinaryStreamSubscriber&) = delete;
  ZenohLatestBinaryStreamSubscriber(ZenohLatestBinaryStreamSubscriber&&) = delete;
  ZenohLatestBinaryStreamSubscriber& operator=(ZenohLatestBinaryStreamSubscriber&&) = delete;

  bool open(const RuntimeBackendConfig& config, const std::string& key_expr);
  void close();
  std::optional<RuntimeBytes> poll_latest();
  std::optional<RuntimeBytes> wait_latest(std::chrono::milliseconds timeout);

  bool valid() const;
  std::string key_expr() const;

 private:
  class Impl;
  std::unique_ptr<Impl> impl_;
};

}  // namespace f8::cppsdk
