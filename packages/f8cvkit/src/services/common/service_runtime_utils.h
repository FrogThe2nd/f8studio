#pragma once

#include <cerrno>
#include <cctype>
#include <cmath>
#include <cstdlib>
#include <limits>
#include <mutex>
#include <string>
#include <unordered_map>

#include <nlohmann/json.hpp>

#include "f8cppsdk/service_bus.h"
#include "f8cppsdk/state_kv.h"

namespace f8::cvkit::service_runtime {

using json = nlohmann::json;

inline std::string trim_copy(std::string value) {
  while (!value.empty() && std::isspace(static_cast<unsigned char>(value.front())) != 0) {
    value.erase(value.begin());
  }
  while (!value.empty() && std::isspace(static_cast<unsigned char>(value.back())) != 0) {
    value.pop_back();
  }
  return value;
}

inline std::string to_lower_ascii_copy(std::string value) {
  for (char& ch : value) {
    ch = static_cast<char>(std::tolower(static_cast<unsigned char>(ch)));
  }
  return value;
}

inline bool parse_int_text(const std::string& raw, int& out) {
  const std::string text = trim_copy(raw);
  if (text.empty()) {
    return false;
  }

  errno = 0;
  char* end = nullptr;
  const long long parsed = std::strtoll(text.c_str(), &end, 10);
  if (end == text.c_str() || end == nullptr || *end != '\0' || errno == ERANGE) {
    return false;
  }
  if (parsed < static_cast<long long>(std::numeric_limits<int>::min()) ||
      parsed > static_cast<long long>(std::numeric_limits<int>::max())) {
    return false;
  }

  out = static_cast<int>(parsed);
  return true;
}

inline bool parse_double_text(const std::string& raw, double& out) {
  const std::string text = trim_copy(raw);
  if (text.empty()) {
    return false;
  }

  errno = 0;
  char* end = nullptr;
  const double parsed = std::strtod(text.c_str(), &end);
  if (end == text.c_str() || end == nullptr || *end != '\0' || errno == ERANGE || !std::isfinite(parsed)) {
    return false;
  }

  out = parsed;
  return true;
}

inline bool parse_json_int(const json& value, int& out) {
  if (value.is_number_integer()) {
    const long long v = value.get<long long>();
    if (v < static_cast<long long>(std::numeric_limits<int>::min()) ||
        v > static_cast<long long>(std::numeric_limits<int>::max())) {
      return false;
    }
    out = static_cast<int>(v);
    return true;
  }
  if (value.is_number_unsigned()) {
    const unsigned long long v = value.get<unsigned long long>();
    if (v > static_cast<unsigned long long>(std::numeric_limits<int>::max())) {
      return false;
    }
    out = static_cast<int>(v);
    return true;
  }
  if (value.is_number_float()) {
    const double v = value.get<double>();
    if (!std::isfinite(v)) {
      return false;
    }
    const double rounded = std::lround(v);
    if (rounded < static_cast<double>(std::numeric_limits<int>::min()) ||
        rounded > static_cast<double>(std::numeric_limits<int>::max())) {
      return false;
    }
    out = static_cast<int>(rounded);
    return true;
  }
  if (value.is_string()) {
    return parse_int_text(value.get<std::string>(), out);
  }
  return false;
}

inline bool parse_json_double(const json& value, double& out) {
  if (value.is_number_float()) {
    const double v = value.get<double>();
    if (!std::isfinite(v)) {
      return false;
    }
    out = v;
    return true;
  }
  if (value.is_number_integer()) {
    out = static_cast<double>(value.get<long long>());
    return true;
  }
  if (value.is_number_unsigned()) {
    out = static_cast<double>(value.get<unsigned long long>());
    return true;
  }
  if (value.is_string()) {
    return parse_double_text(value.get<std::string>(), out);
  }
  return false;
}

inline void publish_state_if_changed(std::mutex& state_mu,
                                     std::unordered_map<std::string, json>& published_state,
                                     f8::cppsdk::ServiceBus* bus,
                                     const std::string& service_id,
                                     const std::string& field,
                                     const json& value,
                                     const std::string& source,
                                     const json& meta) {
  std::lock_guard<std::mutex> lock(state_mu);
  const auto it = published_state.find(field);
  if (it != published_state.end() && it->second == value) {
    return;
  }

  published_state[field] = value;
  if (bus != nullptr) {
    (void)f8::cppsdk::kv_set_node_state(bus->kv(), service_id, service_id, field, value, source, meta);
  }
}

}  // namespace f8::cvkit::service_runtime
