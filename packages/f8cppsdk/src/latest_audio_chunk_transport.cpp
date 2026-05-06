#include "f8cppsdk/latest_audio_chunk_transport.h"

#include "f8cppsdk/latest_binary_stream_transport.h"

#include <chrono>
#include <cstring>
#include <limits>
#include <optional>
#include <utility>
#include <vector>

#include <spdlog/spdlog.h>

namespace f8::cppsdk {
namespace {

void set_error(std::string* error_message, std::string value) {
  if (error_message != nullptr) {
    *error_message = std::move(value);
  }
}

void append_u32_le(RuntimeBytes& out, std::uint32_t value) {
  out.push_back(static_cast<std::uint8_t>(value & 0xFFu));
  out.push_back(static_cast<std::uint8_t>((value >> 8u) & 0xFFu));
  out.push_back(static_cast<std::uint8_t>((value >> 16u) & 0xFFu));
  out.push_back(static_cast<std::uint8_t>((value >> 24u) & 0xFFu));
}

void append_u64_le(RuntimeBytes& out, std::uint64_t value) {
  for (unsigned shift = 0; shift < 64; shift += 8) {
    out.push_back(static_cast<std::uint8_t>((value >> shift) & 0xFFu));
  }
}

void append_i64_le(RuntimeBytes& out, std::int64_t value) {
  append_u64_le(out, static_cast<std::uint64_t>(value));
}

bool read_u32_le(const RuntimeBytes& data, std::size_t offset, std::uint32_t& out) {
  if (offset > data.size() || data.size() - offset < 4) {
    return false;
  }
  out = static_cast<std::uint32_t>(data[offset]) | (static_cast<std::uint32_t>(data[offset + 1]) << 8u) |
        (static_cast<std::uint32_t>(data[offset + 2]) << 16u) |
        (static_cast<std::uint32_t>(data[offset + 3]) << 24u);
  return true;
}

bool read_u64_le(const RuntimeBytes& data, std::size_t offset, std::uint64_t& out) {
  if (offset > data.size() || data.size() - offset < 8) {
    return false;
  }
  out = 0;
  for (unsigned index = 0; index < 8; ++index) {
    out |= static_cast<std::uint64_t>(data[offset + index]) << (index * 8u);
  }
  return true;
}

bool read_i64_le(const RuntimeBytes& data, std::size_t offset, std::int64_t& out) {
  std::uint64_t value = 0;
  if (!read_u64_le(data, offset, value)) {
    return false;
  }
  out = static_cast<std::int64_t>(value);
  return true;
}

}  // namespace

bool encode_zenoh_audio_chunk(const AudioChunkView& chunk, RuntimeBytes& out, std::string* error_message) {
  out.clear();
  if (chunk.sample_rate == 0 || chunk.channels == 0 || chunk.frames == 0 || chunk.bytes_per_frame == 0) {
    set_error(error_message, "sample_rate, channels, frames, and bytes_per_frame must be positive");
    return false;
  }
  if (chunk.format == 0) {
    set_error(error_message, "format must be positive");
    return false;
  }
  if (chunk.seq == 0) {
    set_error(error_message, "seq must be positive");
    return false;
  }
  if (chunk.payload == nullptr) {
    set_error(error_message, "payload must be non-null");
    return false;
  }
  const std::size_t expected_payload_bytes =
      static_cast<std::size_t>(chunk.frames) * static_cast<std::size_t>(chunk.bytes_per_frame);
  if (expected_payload_bytes == 0 || chunk.payload_bytes < expected_payload_bytes) {
    set_error(error_message, "payload is smaller than frames * bytes_per_frame");
    return false;
  }
  if (expected_payload_bytes > static_cast<std::size_t>(std::numeric_limits<std::uint32_t>::max())) {
    set_error(error_message, "payload is too large for zenoh audio chunk schema v1");
    return false;
  }

  out.reserve(static_cast<std::size_t>(kZenohAudioChunkHeaderBytes) + expected_payload_bytes);
  append_u32_le(out, kZenohAudioChunkMagic);
  append_u32_le(out, kZenohAudioChunkSchemaVersion);
  append_u32_le(out, kZenohAudioChunkHeaderBytes);
  append_u32_le(out, chunk.sample_rate);
  append_u32_le(out, chunk.channels);
  append_u32_le(out, chunk.format);
  append_u32_le(out, chunk.frames);
  append_u32_le(out, chunk.bytes_per_frame);
  append_u32_le(out, static_cast<std::uint32_t>(expected_payload_bytes));
  append_u64_le(out, chunk.seq);
  append_u64_le(out, chunk.frame_index);
  append_i64_le(out, chunk.ts_ms);
  const auto* begin = reinterpret_cast<const std::uint8_t*>(chunk.payload);
  out.insert(out.end(), begin, begin + expected_payload_bytes);
  return true;
}

bool decode_zenoh_audio_chunk(const RuntimeBytes& raw, LatestAudioChunk& out, std::string* error_message) {
  out = LatestAudioChunk{};
  if (raw.size() < kZenohAudioChunkHeaderBytes) {
    set_error(error_message, "payload is smaller than zenoh audio chunk header");
    return false;
  }

  std::uint32_t magic = 0;
  std::uint32_t version = 0;
  std::uint32_t header_bytes = 0;
  std::uint32_t sample_rate = 0;
  std::uint32_t channels = 0;
  std::uint32_t format = 0;
  std::uint32_t frames = 0;
  std::uint32_t bytes_per_frame = 0;
  std::uint32_t payload_bytes = 0;
  std::uint64_t seq = 0;
  std::uint64_t frame_index = 0;
  std::int64_t ts_ms = 0;
  if (!read_u32_le(raw, 0, magic) || !read_u32_le(raw, 4, version) || !read_u32_le(raw, 8, header_bytes) ||
      !read_u32_le(raw, 12, sample_rate) || !read_u32_le(raw, 16, channels) ||
      !read_u32_le(raw, 20, format) || !read_u32_le(raw, 24, frames) ||
      !read_u32_le(raw, 28, bytes_per_frame) || !read_u32_le(raw, 32, payload_bytes) ||
      !read_u64_le(raw, 36, seq) || !read_u64_le(raw, 44, frame_index) || !read_i64_le(raw, 52, ts_ms)) {
    set_error(error_message, "payload header is truncated");
    return false;
  }
  if (magic != kZenohAudioChunkMagic || version != kZenohAudioChunkSchemaVersion) {
    set_error(error_message, "unsupported zenoh audio chunk schema");
    return false;
  }
  if (header_bytes < kZenohAudioChunkHeaderBytes) {
    set_error(error_message, "invalid zenoh audio chunk header size");
    return false;
  }
  if (sample_rate == 0 || channels == 0 || format == 0 || frames == 0 || bytes_per_frame == 0 || seq == 0) {
    set_error(error_message, "invalid zenoh audio chunk metadata");
    return false;
  }
  const std::size_t expected_payload_bytes =
      static_cast<std::size_t>(frames) * static_cast<std::size_t>(bytes_per_frame);
  if (payload_bytes != expected_payload_bytes) {
    set_error(error_message, "zenoh audio chunk payload size does not match frames * bytes_per_frame");
    return false;
  }
  if (static_cast<std::size_t>(header_bytes) > raw.size() ||
      raw.size() - static_cast<std::size_t>(header_bytes) < static_cast<std::size_t>(payload_bytes)) {
    set_error(error_message, "zenoh audio chunk payload is truncated");
    return false;
  }

  out.sample_rate = sample_rate;
  out.channels = channels;
  out.format = format;
  out.frames = frames;
  out.bytes_per_frame = bytes_per_frame;
  out.seq = seq;
  out.frame_index = frame_index;
  out.ts_ms = ts_ms;
  out.payload.resize(payload_bytes);
  std::memcpy(out.payload.data(), raw.data() + header_bytes, payload_bytes);
  return true;
}

class ZenohLatestAudioChunkPublisher::Impl final {
 public:
  Impl() : publisher_("audio") {}

  bool open(const RuntimeBackendConfig& config, const std::string& key_expr) {
    return publisher_.open(config, key_expr);
  }

  void close() {
    publisher_.close();
  }

  bool publish_chunk(const AudioChunkView& chunk) {
    RuntimeBytes encoded;
    std::string error;
    if (!encode_zenoh_audio_chunk(chunk, encoded, &error)) {
      report_publish_failure("encode failed: " + error);
      return false;
    }
    const bool ok = publisher_.publish_bytes(encoded);
    publish_failure_reported_ = !ok;
    return ok;
  }

  bool valid() const {
    return publisher_.valid();
  }

  std::string key_expr() const {
    return publisher_.key_expr();
  }

 private:
  void report_publish_failure(const std::string& message) {
    if (publish_failure_reported_) {
      return;
    }
    publish_failure_reported_ = true;
    spdlog::error("zenoh audio publish failed key={}: {}", publisher_.key_expr(), message);
  }

  ZenohLatestBinaryStreamPublisher publisher_;
  bool publish_failure_reported_ = false;
};

class ZenohLatestAudioChunkSubscriber::Impl final {
 public:
  Impl() : subscriber_("audio") {}

  bool open(const RuntimeBackendConfig& config, const std::string& key_expr) {
    decode_failure_reported_ = false;
    return subscriber_.open(config, key_expr);
  }

  void close() {
    subscriber_.close();
    decode_failure_reported_ = false;
  }

  std::optional<LatestAudioChunk> poll_latest() {
    std::optional<RuntimeBytes> raw = subscriber_.poll_latest();
    if (!raw.has_value()) {
      return std::nullopt;
    }
    return decode_latest(*raw);
  }

  std::optional<LatestAudioChunk> wait_latest(std::chrono::milliseconds timeout) {
    std::optional<RuntimeBytes> raw = subscriber_.wait_latest(timeout);
    if (!raw.has_value()) {
      return std::nullopt;
    }
    return decode_latest(*raw);
  }

  bool valid() const {
    return subscriber_.valid();
  }

  std::string key_expr() const {
    return subscriber_.key_expr();
  }

 private:
  std::optional<LatestAudioChunk> decode_latest(const RuntimeBytes& raw) {
    LatestAudioChunk chunk;
    std::string error;
    if (!decode_zenoh_audio_chunk(raw, chunk, &error)) {
      if (!decode_failure_reported_) {
        decode_failure_reported_ = true;
        spdlog::error("zenoh audio chunk decode failed key={}: {}", subscriber_.key_expr(), error);
      }
      return std::nullopt;
    }
    decode_failure_reported_ = false;
    return chunk;
  }

  ZenohLatestBinaryStreamSubscriber subscriber_;
  bool decode_failure_reported_ = false;
};

ZenohLatestAudioChunkPublisher::ZenohLatestAudioChunkPublisher() : impl_(std::make_unique<Impl>()) {}
ZenohLatestAudioChunkPublisher::~ZenohLatestAudioChunkPublisher() {
  close();
}

bool ZenohLatestAudioChunkPublisher::open(const RuntimeBackendConfig& config, const std::string& key_expr) {
  return impl_->open(config, key_expr);
}

void ZenohLatestAudioChunkPublisher::close() {
  impl_->close();
}

bool ZenohLatestAudioChunkPublisher::publish_chunk(const AudioChunkView& chunk) {
  return impl_->publish_chunk(chunk);
}

bool ZenohLatestAudioChunkPublisher::valid() const {
  return impl_->valid();
}

std::string ZenohLatestAudioChunkPublisher::key_expr() const {
  return impl_->key_expr();
}

ZenohLatestAudioChunkSubscriber::ZenohLatestAudioChunkSubscriber() : impl_(std::make_unique<Impl>()) {}
ZenohLatestAudioChunkSubscriber::~ZenohLatestAudioChunkSubscriber() {
  close();
}

bool ZenohLatestAudioChunkSubscriber::open(const RuntimeBackendConfig& config, const std::string& key_expr) {
  return impl_->open(config, key_expr);
}

void ZenohLatestAudioChunkSubscriber::close() {
  impl_->close();
}

std::optional<LatestAudioChunk> ZenohLatestAudioChunkSubscriber::poll_latest() {
  return impl_->poll_latest();
}

std::optional<LatestAudioChunk> ZenohLatestAudioChunkSubscriber::wait_latest(std::chrono::milliseconds timeout) {
  return impl_->wait_latest(timeout);
}

bool ZenohLatestAudioChunkSubscriber::valid() const {
  return impl_->valid();
}

std::string ZenohLatestAudioChunkSubscriber::key_expr() const {
  return impl_->key_expr();
}

}  // namespace f8::cppsdk
