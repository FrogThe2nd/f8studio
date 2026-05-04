#pragma once

#include <cstddef>
#include <cstdint>
#include <memory>
#include <string>

#include "f8cppsdk/runtime_backend.h"
#include "f8cppsdk/runtime_transport.h"
#include "f8cppsdk/video_shared_memory_sink.h"

namespace f8::cppsdk {

inline constexpr std::uint32_t kZenohVideoFrameMagic = 0xF85A1001u;
inline constexpr std::uint32_t kZenohVideoFrameSchemaVersion = 1u;
inline constexpr std::uint32_t kZenohVideoFrameHeaderBytes = 48u;

bool encode_zenoh_video_frame(const VideoFrameView& frame, RuntimeBytes& out, std::string* error_message = nullptr);

class ZenohLatestVideoFramePublisher final {
 public:
  ZenohLatestVideoFramePublisher();
  ~ZenohLatestVideoFramePublisher();
  ZenohLatestVideoFramePublisher(const ZenohLatestVideoFramePublisher&) = delete;
  ZenohLatestVideoFramePublisher& operator=(const ZenohLatestVideoFramePublisher&) = delete;
  ZenohLatestVideoFramePublisher(ZenohLatestVideoFramePublisher&&) = delete;
  ZenohLatestVideoFramePublisher& operator=(ZenohLatestVideoFramePublisher&&) = delete;

  bool open(const RuntimeBackendConfig& config, const std::string& key_expr);
  void close();
  bool publish_frame(const VideoFrameView& frame);

  bool valid() const;
  std::string key_expr() const;

 private:
  class Impl;
  std::unique_ptr<Impl> impl_;
};

}  // namespace f8::cppsdk
