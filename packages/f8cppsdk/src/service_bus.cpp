#include "f8cppsdk/service_bus.h"

#include <algorithm>
#include <atomic>
#include <cmath>
#include <chrono>
#include <cctype>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <fstream>
#include <iomanip>
#include <sstream>
#include <stdexcept>
#include <tuple>
#include <unordered_set>
#include <utility>

#include <spdlog/spdlog.h>

#include "f8cppsdk/f8_naming.h"
#include "f8cppsdk/generated/protocol_models.h"
#include "f8cppsdk/msg_codec.h"
#include "f8cppsdk/rungraph_routes.h"
#include "f8cppsdk/state_kv.h"
#include "f8cppsdk/time_utils.h"
#include "f8cppsdk/zenoh_transport.h"

#if defined(__linux__)
#include <unistd.h>
#endif

namespace f8::cppsdk {

using json = nlohmann::json;

namespace {

constexpr std::chrono::milliseconds kRuntimeKvGetDefaultTimeout{1000};
constexpr std::chrono::milliseconds kZenohCrossStateInitialGetTimeout{80};
constexpr std::chrono::milliseconds kZenohCrossStateInitialSyncBudget{300};

bool state_debug_enabled() {
  const char* v = std::getenv("F8_STATE_DEBUG");
  if (v == nullptr) return false;
  std::string s(v);
  std::transform(s.begin(), s.end(), s.begin(), [](unsigned char ch) { return static_cast<char>(std::tolower(ch)); });
  return (s == "1" || s == "true" || s == "yes" || s == "on");
}

std::int64_t coerce_inbound_ts_ms(const json& payload, std::int64_t default_ts_ms) {
  auto read_int = [&](const char* key) -> std::optional<std::int64_t> {
    try {
      if (!payload.is_object() || !payload.contains(key)) return std::nullopt;
      const auto& v = payload.at(key);
      if (v.is_number_integer()) return v.get<std::int64_t>();
      if (v.is_number_float()) return static_cast<std::int64_t>(v.get<double>());
      if (v.is_string()) return std::stoll(v.get<std::string>());
    } catch (...) {
      return std::nullopt;
    }
    return std::nullopt;
  };

  std::optional<std::int64_t> ts = read_int("ts");
  if (!ts.has_value()) ts = read_int("ts_ms");
  if (!ts.has_value()) ts = read_int("tsMs");

  std::int64_t t = ts.value_or(default_ts_ms);
  if (t <= 0) return default_ts_ms;

  // Heuristics matching pysdk:
  // - seconds ~1e9, ms ~1e12 (2026), micros ~1e15, nanos ~1e18
  if (t < 100'000'000'000LL) return t * 1000LL;
  if (t >= 100'000'000'000'000'000LL) return t / 1'000'000LL;
  if (t >= 100'000'000'000'000LL) return t / 1000LL;
  return t;
}

bool state_origin_allows_access(const std::string& origin, const std::string& access) {
  // origin: "external" | "runtime" | "rungraph" | "system"
  // access: "rw" | "ro" | "wo"
  if (origin == "system") return true;
  if (origin == "runtime") return (access == "rw" || access == "ro");
  if (origin == "rungraph") return (access == "rw" || access == "wo");
  if (origin == "external") return (access == "rw" || access == "wo");
  return false;
}

std::string access_to_string(f8::cppsdk::generated::F8StateAccess a) {
  switch (a) {
    case f8::cppsdk::generated::F8StateAccess::rw:
      return "rw";
    case f8::cppsdk::generated::F8StateAccess::ro:
      return "ro";
    case f8::cppsdk::generated::F8StateAccess::wo:
      return "wo";
  }
  return "";
}

std::string trim_copy(std::string value) {
  value.erase(value.begin(),
              std::find_if(value.begin(), value.end(), [](unsigned char ch) { return !std::isspace(ch); }));
  value.erase(std::find_if(value.rbegin(), value.rend(), [](unsigned char ch) { return !std::isspace(ch); }).base(),
              value.end());
  return value;
}

std::string normalize_monitor_error_severity(std::string severity) {
  severity = trim_copy(std::move(severity));
  std::transform(severity.begin(), severity.end(), severity.begin(),
                 [](unsigned char ch) { return static_cast<char>(std::tolower(ch)); });
  if (severity == "info" || severity == "warning" || severity == "critical") {
    return severity;
  }
  return "error";
}

std::string derive_monitor_error_fingerprint(const std::string& node_id, const std::string& code,
                                             const std::string& message, const std::string& fingerprint) {
  const std::string explicit_fingerprint = trim_copy(fingerprint);
  if (!explicit_fingerprint.empty()) {
    return explicit_fingerprint;
  }
  return node_id + ":" + code + ":" + message;
}

std::uint32_t fnv1a32(const std::string& text) {
  std::uint32_t value = 0x811C9DC5u;
  for (unsigned char ch : text) {
    value ^= static_cast<std::uint32_t>(ch);
    value *= 0x01000193u;
  }
  return value;
}

std::string command_key_for_name(const std::string& name) {
  const std::string raw = trim_copy(name);
  std::string base;
  bool last_was_sep = false;
  for (unsigned char ch : raw) {
    const char lower = static_cast<char>(std::tolower(ch));
    if ((lower >= 'a' && lower <= 'z') || (lower >= '0' && lower <= '9')) {
      base.push_back(lower);
      last_was_sep = false;
      continue;
    }
    if (!last_was_sep) {
      base.push_back('_');
      last_was_sep = true;
    }
  }
  while (!base.empty() && base.front() == '_') base.erase(base.begin());
  while (!base.empty() && base.back() == '_') base.pop_back();
  if (base.empty()) base = "command";
  std::ostringstream out;
  out << base << "_" << std::hex << std::nouppercase << std::setw(8) << std::setfill('0') << fnv1a32(raw);
  return out.str();
}

std::string command_input_state_field(const std::string& name) {
  return "__cmd__." + command_key_for_name(name) + ".in";
}

std::string command_output_state_field(const std::string& name) {
  return "__cmd__." + command_key_for_name(name) + ".out";
}

std::string new_control_req_id() {
  return std::to_string(static_cast<long long>(now_ms()));
}

struct ControlEnvelope {
  std::string req_id;
  json raw = json::object();
  json args = json::object();
  json meta = json::object();
};

ControlEnvelope parse_control_envelope(const RuntimeBytes& bytes) {
  ControlEnvelope out;
  if (!bytes.empty() && !decode_json(bytes.data(), bytes.size(), out.raw)) {
    out.raw = json::object();
  }
  if (!out.raw.is_object()) {
    out.raw = json::object();
  }
  if (out.raw.contains("reqId") && out.raw["reqId"].is_string()) {
    out.req_id = out.raw["reqId"].get<std::string>();
  }
  if (out.req_id.empty()) {
    out.req_id = new_control_req_id();
  }
  if (out.raw.contains("args") && out.raw["args"].is_object()) {
    out.args = out.raw["args"];
  }
  if (out.raw.contains("meta") && out.raw["meta"].is_object()) {
    out.meta = out.raw["meta"];
  }
  return out;
}

RuntimeBytes encode_control_response(const std::string& req_id, bool ok, const json& result,
                                     const std::string& err_code, const std::string& err_message) {
  json payload;
  payload["reqId"] = req_id;
  payload["ok"] = ok;
  payload["result"] = ok ? result : json(nullptr);
  if (!ok) {
    payload["error"] = json{{"code", err_code.empty() ? "INTERNAL" : err_code}, {"message", err_message}};
  } else {
    payload["error"] = json(nullptr);
  }
  return encode_json(payload);
}

RuntimeBytes encode_ready_payload(const std::string& service_id, bool ready, const std::string& reason,
                                  std::int64_t ts_ms) {
  const std::int64_t ts = ts_ms > 0 ? ts_ms : now_ms();
  json payload = json::object();
  payload["serviceId"] = ensure_token(service_id, "service_id");
  payload["ready"] = ready;
  payload["reason"] = reason;
  payload["ts"] = ts;
  return encode_json(payload);
}

RuntimeBytes encode_node_state_payload(const std::string& service_id, const json& value, const std::string& source,
                                       const json& extra_meta, std::int64_t ts_ms, const std::string& origin) {
  const std::int64_t ts = ts_ms > 0 ? ts_ms : now_ms();
  json payload;
  payload["value"] = value;
  payload["actor"] = ensure_token(service_id, "service_id");
  payload["ts"] = ts;
  if (!source.empty()) {
    payload["source"] = source;
  }
  if (!origin.empty()) {
    payload["origin"] = origin;
  }
  if (extra_meta.is_object()) {
    for (auto it = extra_meta.begin(); it != extra_meta.end(); ++it) {
      const std::string k = it.key();
      if (k == "value" || k == "actor" || k == "ts" || k == "source" || k == "origin") {
        continue;
      }
      payload[k] = it.value();
    }
  }
  return encode_json(payload);
}

RuntimeBytes encode_data_payload(const json& value, std::int64_t ts_ms) {
  const std::int64_t ts = ts_ms > 0 ? ts_ms : now_ms();
  json payload;
  payload["value"] = value;
  payload["ts"] = ts;
  return encode_json(payload);
}

bool hidden_command_state_direction(const std::string& field, std::string& direction) {
  if (field.rfind("__cmd__.", 0) != 0) return false;
  if (field.size() > 3 && field.compare(field.size() - 3, 3, ".in") == 0) {
    direction = "in";
    return true;
  }
  if (field.size() > 4 && field.compare(field.size() - 4, 4, ".out") == 0) {
    direction = "out";
    return true;
  }
  return false;
}

struct ParsedCommandBinding {
  std::string node_id;
  std::string call;
  std::string input_field;
  std::string output_field;
  std::vector<std::string> param_names;
};

std::vector<ParsedCommandBinding> parse_command_bindings_from_spec(const json& spec, const std::string& node_id) {
  std::vector<ParsedCommandBinding> bindings;
  const json* service = nullptr;
  if (spec.is_object() && spec.contains("service") && spec.at("service").is_object()) {
    service = &spec.at("service");
  } else if (spec.is_object()) {
    service = &spec;
  }
  if (service == nullptr || !service->is_object()) return bindings;
  if (!service->contains("commands") || !service->at("commands").is_array()) return bindings;
  for (const auto& command : service->at("commands")) {
    if (!command.is_object()) continue;
    const std::string call = trim_copy(command.value("name", ""));
    if (call.empty()) continue;
    ParsedCommandBinding binding;
    binding.node_id = node_id;
    binding.call = call;
    binding.input_field = command_input_state_field(call);
    binding.output_field = command_output_state_field(call);
    if (command.contains("params") && command.at("params").is_array()) {
      for (const auto& param : command.at("params")) {
        if (!param.is_object()) continue;
        const std::string param_name = trim_copy(param.value("name", ""));
        if (!param_name.empty()) binding.param_names.push_back(param_name);
      }
    }
    bindings.push_back(std::move(binding));
  }
  return bindings;
}

json map_command_args(const json& value, const std::vector<std::string>& param_names) {
  json args = json::object();
  if (param_names.empty()) return args;
  if (value.is_object()) {
    for (const auto& name : param_names) {
      if (value.contains(name)) args[name] = value.at(name);
    }
    return args;
  }
  if (value.is_array()) {
    for (std::size_t i = 0; i < value.size() && i < param_names.size(); ++i) {
      args[param_names[i]] = value.at(i);
    }
    return args;
  }
  args[param_names.front()] = value;
  return args;
}

void prune_timed_values(std::deque<std::pair<std::int64_t, double>>& values, const std::int64_t now_ms,
                        const std::int64_t window_ms) {
  const std::int64_t cutoff = now_ms - std::max<std::int64_t>(1000, window_ms);
  while (!values.empty() && values.front().first < cutoff) {
    values.pop_front();
  }
}

void prune_timed_errors(std::deque<std::int64_t>& values, const std::int64_t now_ms, const std::int64_t window_ms) {
  const std::int64_t cutoff = now_ms - std::max<std::int64_t>(1000, window_ms);
  while (!values.empty() && values.front() < cutoff) {
    values.pop_front();
  }
}

double average_values(const std::deque<std::pair<std::int64_t, double>>& values) {
  if (values.empty()) return 0.0;
  double total = 0.0;
  for (const auto& item : values) {
    total += item.second;
  }
  return total / static_cast<double>(values.size());
}

double percentile95_values(const std::deque<std::pair<std::int64_t, double>>& values) {
  if (values.empty()) return 0.0;
  std::vector<double> ordered;
  ordered.reserve(values.size());
  for (const auto& item : values) {
    ordered.push_back(item.second);
  }
  std::sort(ordered.begin(), ordered.end());
  std::size_t idx = static_cast<std::size_t>(std::llround(static_cast<double>(ordered.size() - 1) * 0.95));
  if (idx >= ordered.size()) idx = ordered.size() - 1;
  return ordered[idx];
}

std::pair<std::int64_t, std::int64_t> sample_process_memory_bytes() {
#if defined(__linux__)
  std::ifstream in("/proc/self/statm");
  if (!in.is_open()) return {0, 0};
  std::uint64_t vms_pages = 0;
  std::uint64_t rss_pages = 0;
  in >> vms_pages >> rss_pages;
  const long page_size = sysconf(_SC_PAGESIZE);
  if (page_size <= 0) return {0, 0};
  const std::int64_t rss = static_cast<std::int64_t>(rss_pages) * static_cast<std::int64_t>(page_size);
  const std::int64_t vms = static_cast<std::int64_t>(vms_pages) * static_cast<std::int64_t>(page_size);
  return {std::max<std::int64_t>(0, rss), std::max<std::int64_t>(0, vms)};
#else
  return {0, 0};
#endif
}

struct StateEdgeKey {
  std::string service_id;
  std::string node_id;
  std::string field;
  bool operator==(const StateEdgeKey& other) const {
    return service_id == other.service_id && node_id == other.node_id && field == other.field;
  }
};
struct StateEdgeKeyHash {
  std::size_t operator()(const StateEdgeKey& k) const noexcept {
    std::size_t h1 = std::hash<std::string>{}(k.service_id);
    std::size_t h2 = std::hash<std::string>{}(k.node_id);
    std::size_t h3 = std::hash<std::string>{}(k.field);
    return h1 ^ (h2 << 1) ^ (h3 << 2);
  }
};

void validate_state_edges_or_throw(const f8::cppsdk::generated::F8RuntimeGraph& graph) {
  using namespace f8::cppsdk::generated;

  const auto edges = graph.edges.value_or(std::vector<F8Edge>{});
  std::unordered_map<StateEdgeKey, std::vector<StateEdgeKey>, StateEdgeKeyHash> out;
  std::unordered_map<StateEdgeKey, int, StateEdgeKeyHash> inbound_count;
  std::unordered_map<StateEdgeKey, StateEdgeKey, StateEdgeKeyHash> upstream_by_target;
  std::vector<StateEdgeKey> nodes;

  auto add_node = [&](const StateEdgeKey& k) {
    nodes.push_back(k);
  };

  for (const auto& e : edges) {
    if (e.kind != F8EdgeKindEnum::state) continue;
    const std::string from_sid = e.fromServiceId;
    const std::string to_sid = e.toServiceId;
    const std::string from_op = e.fromOperatorId.value_or("");
    const std::string to_op = e.toOperatorId.value_or("");
    const std::string from_field = e.fromPort;
    const std::string to_field = e.toPort;
    if (from_sid.empty() || to_sid.empty() || from_op.empty() || to_op.empty() || from_field.empty() || to_field.empty()) {
      continue;
    }

    StateEdgeKey from{from_sid, from_op, from_field};
    StateEdgeKey to{to_sid, to_op, to_field};

    auto it_prev = upstream_by_target.find(to);
    if (it_prev != upstream_by_target.end()) {
      const auto& prev = it_prev->second;
      if (!(prev == from)) {
        throw std::runtime_error("multiple upstreams for state field: " + to_sid + "." + to_op + "." + to_field);
      }
    } else {
      upstream_by_target.emplace(to, from);
    }

    out[from].push_back(to);
    inbound_count[to] = inbound_count[to] + 1;
    add_node(from);
    add_node(to);
  }

  if (out.empty()) return;

  std::unordered_map<StateEdgeKey, bool, StateEdgeKeyHash> visiting;
  std::unordered_map<StateEdgeKey, bool, StateEdgeKeyHash> visited;
  std::unordered_map<StateEdgeKey, std::optional<StateEdgeKey>, StateEdgeKeyHash> parent;

  auto fmt = [](const StateEdgeKey& k) { return k.service_id + "." + k.node_id + "." + k.field; };

  std::function<std::optional<std::vector<StateEdgeKey>>(const StateEdgeKey&)> dfs;

  dfs = [&](const StateEdgeKey& n) -> std::optional<std::vector<StateEdgeKey>> {
    visiting[n] = true;
    for (const auto& m : out[n]) {
      if (visited[m]) continue;
      if (visiting[m]) {
        std::vector<StateEdgeKey> cyc;
        cyc.push_back(m);
        cyc.push_back(n);
        auto cur = parent[n];
        while (cur.has_value() && !(cur.value() == m)) {
          cyc.push_back(cur.value());
          cur = parent[cur.value()];
        }
        cyc.push_back(m);
        std::reverse(cyc.begin(), cyc.end());
        return cyc;
      }
      parent[m] = n;
      auto r = dfs(m);
      if (r.has_value()) return r;
    }
    visiting[n] = false;
    visited[n] = true;
    return std::nullopt;
  };

  // Roots first.
  std::vector<StateEdgeKey> start;
  for (const auto& n : nodes) {
    if (inbound_count.find(n) == inbound_count.end()) {
      start.push_back(n);
    }
  }
  for (const auto& n : nodes) {
    if (std::find_if(start.begin(), start.end(), [&](const StateEdgeKey& x) { return x == n; }) == start.end()) {
      start.push_back(n);
    }
  }

  for (const auto& n : start) {
    if (visited[n]) continue;
    parent[n] = std::nullopt;
    auto cyc = dfs(n);
    if (cyc.has_value()) {
      std::string msg = "cyclic state-edge loop detected: ";
      for (std::size_t i = 0; i < cyc->size(); ++i) {
        if (i) msg += " -> ";
        msg += fmt(cyc->at(i));
      }
      throw std::runtime_error(msg);
    }
  }
}

}  // namespace

ServiceBus::ServiceBus(Config cfg) : cfg_(std::move(cfg)) {}

ServiceBus::~ServiceBus() {
  stop();
}

void ServiceBus::add_lifecycle_node(LifecycleNode* node) {
  if (node == nullptr) return;
  std::lock_guard<std::mutex> lock(lifecycle_mu_);
  lifecycle_nodes_.push_back(node);
}

void ServiceBus::add_stateful_node(StatefulNode* node) {
  if (node == nullptr) return;
  std::lock_guard<std::mutex> lock(handlers_mu_);
  stateful_nodes_.push_back(node);
}

void ServiceBus::add_data_node(DataReceivableNode* node) {
  if (node == nullptr) return;
  std::lock_guard<std::mutex> lock(handlers_mu_);
  data_nodes_.push_back(node);
}

void ServiceBus::add_set_state_node(SetStateHandlerNode* node) {
  if (node == nullptr) return;
  std::lock_guard<std::mutex> lock(handlers_mu_);
  set_state_nodes_.push_back(node);
}

void ServiceBus::add_rungraph_node(RungraphHandlerNode* node) {
  if (node == nullptr) return;
  std::lock_guard<std::mutex> lock(handlers_mu_);
  rungraph_nodes_.push_back(node);
}

void ServiceBus::add_command_node(CommandableNode* node, const json& service_spec) {
  if (node == nullptr) return;
  std::lock_guard<std::mutex> lock(handlers_mu_);
  command_nodes_.push_back(node);
  if (service_spec.is_object()) {
    command_specs_by_node_[node] = service_spec;
  }
}

std::size_t ServiceBus::drain_main_thread(std::size_t max_tasks) {
  return main_thread_.drain(max_tasks);
}

void ServiceBus::handle_data_payload(const std::string& subject, const RuntimeBytes& bytes) {
  json payload = json::object();
  if (!decode_json(bytes.data(), bytes.size(), payload)) {
    return;
  }
  if (!payload.is_object()) return;

  json value = payload.contains("value") ? payload["value"] : json();
  const std::int64_t ts_ms = coerce_inbound_ts_ms(payload, 0);

  json meta = payload;
  if (meta.is_object()) {
    meta.erase("value");
    meta["subject"] = subject;
  } else {
    meta = json::object({{"subject", subject}});
  }

  const auto value_ptr = std::make_shared<const json>(std::move(value));
  const auto snapshot = std::atomic_load(&data_routes_snapshot_);
  if (!snapshot) return;

  main_thread_.post([this, snapshot, subject, value_ptr, ts_ms, meta]() {
    const auto it = snapshot->by_subject.find(subject);
    if (it == snapshot->by_subject.end()) return;
    const auto& routes = it->second;
    if (routes.empty()) return;

    const std::int64_t now = now_ms();
    for (const auto& r : routes) {
      if (!r.buf) continue;
      if (r.timeout_ms > 0 && ts_ms > 0 && (now - ts_ms) > r.timeout_ms) {
        continue;
      }
      auto& buf = *r.buf;
      std::lock_guard<std::mutex> lock(buf.mu);
      std::int64_t dropped = 0;
      if (buf.strategy == EdgeStrategy::kLatest && !buf.queue.empty()) {
        dropped = static_cast<std::int64_t>(buf.queue.size());
      }
      buf.last_seen_value = value_ptr;
      buf.last_seen_ts_ms = ts_ms;
      if (buf.strategy == EdgeStrategy::kLatest) {
        buf.queue.clear();
      }
      buf.queue.emplace_back(value_ptr, ts_ms);
      monitor_record_observed(r.to_port);
      if (dropped > 0) {
        monitor_record_dropped(dropped);
      }
    }

    if (cfg_.data_delivery != DataDeliveryMode::kPush && cfg_.data_delivery != DataDeliveryMode::kBoth) {
      return;
    }

    std::vector<DataReceivableNode*> nodes;
    {
      std::lock_guard<std::mutex> lock(handlers_mu_);
      nodes = data_nodes_;
    }
    if (nodes.empty()) return;

    for (const auto& r : routes) {
      if (r.timeout_ms > 0 && ts_ms > 0 && (now - ts_ms) > r.timeout_ms) {
        continue;
      }
      json m = meta;
      m["fromServiceId"] = r.from_service_id;
      m["fromNodeId"] = r.from_node_id;
      m["fromPort"] = r.from_port;
      for (auto* n : nodes) {
        if (!n) continue;
        try {
          n->on_data(r.to_node_id, r.to_port, *value_ptr, ts_ms, m);
        } catch (const std::exception& exc) {
          monitor_record_error("DATA_CALLBACK_FAILED", exc.what(), ts_ms);
        } catch (...) {
          monitor_record_error("DATA_CALLBACK_FAILED", "on_data threw unknown exception", ts_ms);
        }
      }
    }
  });
}

void ServiceBus::handle_peer_state_payload(const std::string& peer, const std::string& key, const RuntimeBytes& bytes) {
  constexpr const char* kPrefix = "nodes.";
  constexpr const char* kStateMarker = ".state.";
  if (key.rfind(kPrefix, 0) != 0) return;
  const std::size_t marker = key.find(kStateMarker);
  if (marker == std::string::npos) return;

  const std::size_t node_begin = std::strlen(kPrefix);
  const std::size_t node_end = marker;
  if (node_end <= node_begin) return;
  const std::string remote_node_id = key.substr(node_begin, node_end - node_begin);

  const std::size_t field_begin = marker + std::strlen(kStateMarker);
  if (field_begin >= key.size()) return;
  const std::string remote_field = key.substr(field_begin);

  std::vector<_NodeFieldKey> targets;
  {
    std::lock_guard<std::mutex> lock(state_mu_);
    const auto it = cross_state_in_.find(_RemoteStateKey{peer, remote_node_id, remote_field});
    if (it == cross_state_in_.end()) return;
    targets = it->second;
  }
  if (targets.empty()) return;

  nlohmann::json payload = nlohmann::json::object();
  if (!decode_json(bytes.data(), bytes.size(), payload)) {
    return;
  }
  if (!payload.is_object()) return;

  const nlohmann::json value = payload.contains("value") ? payload["value"] : nlohmann::json();
  const std::int64_t ts_ms = coerce_inbound_ts_ms(payload, now_ms());
  nlohmann::json meta = payload;
  if (meta.is_object()) {
    meta.erase("value");
  } else {
    meta = nlohmann::json::object();
  }
  meta["peerServiceId"] = peer;
  meta["remoteKey"] = key;
  meta["fromNodeId"] = remote_node_id;
  meta["fromField"] = remote_field;

  if (state_debug_enabled()) {
    std::string v_s;
    try {
      v_s = value.dump();
    } catch (const std::exception& exc) {
      v_s = std::string("<json_dump_failed: ") + exc.what() + ">";
    } catch (...) {
      v_s = "<json_dump_failed: unknown error>";
    }
    if (v_s.size() > 160) v_s = v_s.substr(0, 157) + "...";
    spdlog::info("state_debug[{}] cross_state_watch peer={} key={} ts={} targets={} value={}", cfg_.service_id,
                 peer, key, ts_ms, targets.size(), v_s);
  }

  for (const auto& t : targets) {
    publish_state_local(t.node_id, t.field, value, ts_ms, "state_edge_cross", meta, "external", true, true);
  }
}

void ServiceBus::apply_data_routes_from_rungraph(const json& graph_obj) {
  auto new_routes = parse_cross_service_data_routes(graph_obj, cfg_.service_id);

  std::lock_guard<std::mutex> lock(data_mu_);

  // Build new routing snapshot + input buffers.
  auto next_snapshot = std::make_shared<_DataRoutingSnapshot>();
  auto next_inputs = std::unordered_map<_NodePortKey, std::shared_ptr<_InputBuffer>, _NodePortKeyHash>();

  for (const auto& kv : new_routes) {
    const std::string& subject = kv.first;
    auto& vec = next_snapshot->by_subject[subject];
    vec.reserve(kv.second.size());
    for (const auto& r : kv.second) {
      _NodePortKey key{r.to_node_id, r.to_port};
      auto it = next_inputs.find(key);
      if (it == next_inputs.end()) {
        it = next_inputs.emplace(key, std::make_shared<_InputBuffer>()).first;
      }
      auto& buf = *it->second;
      if (r.strategy == EdgeStrategy::kQueue) {
        buf.strategy = EdgeStrategy::kQueue;
      }
      if (r.timeout_ms > 0) {
        if (buf.timeout_ms <= 0) {
          buf.timeout_ms = r.timeout_ms;
        } else {
          buf.timeout_ms = std::min<std::int64_t>(buf.timeout_ms, r.timeout_ms);
        }
      }

      _RouteRuntime rr;
      rr.to_node_id = r.to_node_id;
      rr.to_port = r.to_port;
      rr.from_service_id = r.from_service_id;
      rr.from_node_id = r.from_node_id;
      rr.from_port = r.from_port;
      rr.strategy = r.strategy;
      rr.timeout_ms = r.timeout_ms;
      rr.buf = it->second;
      vec.push_back(std::move(rr));
    }
  }

  // Unsubscribe removed subjects.
  for (auto it = data_subs_.begin(); it != data_subs_.end();) {
    if (next_snapshot->by_subject.find(it->first) != next_snapshot->by_subject.end()) {
      ++it;
      continue;
    }
    try {
      it->second.unsubscribe();
    } catch (const std::exception& exc) {
      spdlog::warn("NATS data unsubscribe failed serviceId={} subject={}: {}", cfg_.service_id, it->first, exc.what());
    } catch (...) {
      spdlog::warn("NATS data unsubscribe failed serviceId={} subject={}: unknown error", cfg_.service_id, it->first);
    }
    it = data_subs_.erase(it);
  }
  for (auto it = runtime_data_subs_.begin(); it != runtime_data_subs_.end();) {
    if (next_snapshot->by_subject.find(it->first) != next_snapshot->by_subject.end()) {
      ++it;
      continue;
    }
    try {
      if (it->second) {
        it->second->stop();
      }
    } catch (const std::exception& exc) {
      spdlog::warn("runtime data unsubscribe failed serviceId={} subject={}: {}", cfg_.service_id, it->first,
                   exc.what());
    } catch (...) {
      spdlog::warn("runtime data unsubscribe failed serviceId={} subject={}: unknown error", cfg_.service_id,
                   it->first);
    }
    it = runtime_data_subs_.erase(it);
  }

  // Subscribe new subjects.
  for (const auto& kv : next_snapshot->by_subject) {
    const std::string& subject = kv.first;
    if (cfg_.bus_backend == BusBackend::kNats) {
      if (data_subs_.find(subject) != data_subs_.end()) {
        continue;
      }
      auto sub = nats_.subscribe(subject, [this, subject](natsMsg* msg) {
        if (msg == nullptr) return;
        const void* data = natsMsg_GetData(msg);
        const int len = natsMsg_GetDataLength(msg);
        if (data == nullptr || len < 0) return;
        RuntimeBytes bytes(static_cast<std::size_t>(len));
        std::memcpy(bytes.data(), data, static_cast<std::size_t>(len));
        handle_data_payload(subject, bytes);
      });
      if (sub.valid()) {
        data_subs_.emplace(subject, std::move(sub));
      }
      continue;
    }

    if (runtime_data_subs_.find(subject) != runtime_data_subs_.end()) {
      continue;
    }
    if (!runtime_transport_) {
      spdlog::warn("runtime data subscription skipped without transport serviceId={} subject={}", cfg_.service_id,
                   subject);
      continue;
    }
    auto sub = runtime_transport_->subscribe(subject, [this](const RuntimeMessage& msg) {
      handle_data_payload(msg.subject, msg.payload);
    });
    if (sub && sub->valid()) {
      runtime_data_subs_.emplace(subject, std::move(sub));
    } else {
      spdlog::warn("runtime data subscription failed serviceId={} subject={}", cfg_.service_id, subject);
    }
  }

  data_inputs_ = std::move(next_inputs);
  std::shared_ptr<const _DataRoutingSnapshot> next_snapshot_const = next_snapshot;
  std::atomic_store(&data_routes_snapshot_, std::move(next_snapshot_const));
}

bool ServiceBus::start() {
  stop();

  cfg_.service_id = ensure_token(cfg_.service_id, "service_id");
  if (cfg_.service_name.empty()) {
    cfg_.service_name = cfg_.service_id;
  }
  cfg_.apply_runtime_backend(cfg_.runtime_backend_config());
  terminate_.store(false, std::memory_order_release);
  ready_.store(false, std::memory_order_release);

  if (cfg_.bus_backend == BusBackend::kNats) {
    return start_nats_backend();
  }
  if (cfg_.bus_backend == BusBackend::kZenoh) {
    return start_zenoh_backend();
  }

  spdlog::error("service_bus backend={} is not implemented for C++ serviceId={}",
                bus_backend_to_string(cfg_.bus_backend), cfg_.service_id);
  return false;
}

bool ServiceBus::start_nats_backend() {
  if (!nats_.connect(cfg_.nats_url)) {
    return false;
  }

  KvConfig kvc;
  kvc.bucket = kv_bucket_for_service(cfg_.service_id);
  kvc.memory_storage = cfg_.kv_memory_storage;
  if (!kv_.open_or_create(nats_.jetstream(), kvc)) {
    nats_.close();
    return false;
  }

  ctrl_ = std::make_unique<ServiceControlPlaneServer>(
      ServiceControlPlaneServer::Config{cfg_.service_id, cfg_.nats_url, cfg_.service_name, cfg_.service_class}, &nats_,
      &kv_, this);
  if (!ctrl_->start()) {
    ctrl_.reset();
    kv_.close();
    nats_.close();
    return false;
  }

  load_active_from_kv();

  // Clear any stale ready flag from a previous run as early as possible.
  (void)kv_set_ready(kv_, cfg_.service_id, false, "starting");
  ready_.store(false, std::memory_order_release);

  // Watch node state changes and forward to stateful nodes (best-effort).
  // Key format: nodes.<node_id>.state.<field>
  (void)kv_.watch(
      "nodes.>",
      [this](const std::string& key, const std::vector<std::uint8_t>& bytes) {
        constexpr const char* kPrefix = "nodes.";
        constexpr const char* kStateMarker = ".state.";

        if (key.rfind(kPrefix, 0) != 0) return;
        const std::size_t marker = key.find(kStateMarker);
        if (marker == std::string::npos) return;

        const std::size_t node_begin = std::strlen(kPrefix);
        const std::size_t node_end = marker;
        if (node_end <= node_begin) return;
        const std::string node_id = key.substr(node_begin, node_end - node_begin);

        const std::size_t field_begin = marker + std::strlen(kStateMarker);
        if (field_begin >= key.size()) return;
        const std::string field = key.substr(field_begin);

        nlohmann::json payload = nlohmann::json::object();
        if (!decode_json(bytes.data(), bytes.size(), payload)) {
          return;
        }
        if (!payload.is_object()) return;

        // Avoid loopback: ignore updates authored by this service process.
        // External state changes (other actors) still flow through.
        if (payload.contains("actor") && payload["actor"].is_string()) {
          const std::string actor = payload["actor"].get<std::string>();
          if (!actor.empty() && actor == cfg_.service_id) {
            return;
          }
        }

        const nlohmann::json value = payload.contains("value") ? payload["value"] : nlohmann::json();
        const std::int64_t ts_ms = coerce_inbound_ts_ms(payload, 0);
        nlohmann::json meta = payload;
        if (meta.is_object()) {
          meta.erase("value");
        } else {
          meta = nlohmann::json::object();
        }

        {
          std::lock_guard<std::mutex> lock(state_mu_);
          state_cache_[{node_id, field}] = {value, ts_ms};
        }

        main_thread_.post([this, node_id, field, value, ts_ms, meta]() {
          deliver_state_local(node_id, field, value, ts_ms, meta, true);
        });
      },
      true);

  // Seed identity fields (best-effort). Specs should declare these as readonly.
  try {
    (void)kv_set_node_state(kv_, cfg_.service_id, cfg_.service_id, "svcId", cfg_.service_id, "system",
                            json{{"builtin", true}}, 0, "system");
  } catch (const std::exception& exc) {
    spdlog::warn("seed svcId state failed serviceId={}: {}", cfg_.service_id, exc.what());
  } catch (...) {
    spdlog::warn("seed svcId state failed serviceId={}: unknown error", cfg_.service_id);
  }
  try {
    (void)kv_set_node_state(kv_, cfg_.service_id, cfg_.service_id, "active", active_.load(std::memory_order_acquire),
                            "system", json{{"builtin", true}, {"bootstrap", true}}, 0, "runtime");
  } catch (const std::exception& exc) {
    spdlog::warn("seed active state failed serviceId={}: {}", cfg_.service_id, exc.what());
  } catch (...) {
    spdlog::warn("seed active state failed serviceId={}: unknown error", cfg_.service_id);
  }

  // Announce readiness after endpoints are up.
  (void)kv_set_ready(kv_, cfg_.service_id, true, "start");
  ready_.store(true, std::memory_order_release);
  start_monitor_thread();
  spdlog::info("service_bus started serviceId={} backend={} natsUrl={}", cfg_.service_id,
               bus_backend_to_string(cfg_.bus_backend), cfg_.nats_url);
  return true;
}

bool ServiceBus::start_zenoh_backend() {
  runtime_transport_ = std::make_unique<ZenohTransport>();
  if (!runtime_transport_->connect(cfg_.runtime_backend_config(), cfg_.service_id)) {
    runtime_transport_.reset();
    return false;
  }

  if (!start_runtime_control_endpoints()) {
    stop_runtime_control_endpoints();
    runtime_transport_->close();
    runtime_transport_.reset();
    return false;
  }

  load_active_from_kv();

  (void)runtime_set_ready(false, "starting");
  ready_.store(false, std::memory_order_release);

  (void)runtime_set_node_state(cfg_.service_id, "svcId", cfg_.service_id, "system", json{{"builtin", true}}, 0,
                               "system");
  (void)runtime_set_node_state(cfg_.service_id, "active", active_.load(std::memory_order_acquire), "system",
                               json{{"builtin", true}, {"bootstrap", true}}, 0, "runtime");

  (void)runtime_set_ready(true, "start");
  ready_.store(true, std::memory_order_release);
  start_monitor_thread();
  spdlog::info("service_bus started serviceId={} backend={}", cfg_.service_id, bus_backend_to_string(cfg_.bus_backend));
  return true;
}

bool ServiceBus::start_runtime_control_endpoints() {
  if (!runtime_transport_) {
    spdlog::error("runtime control endpoints require an active runtime transport serviceId={}", cfg_.service_id);
    return false;
  }

  struct EndpointRegistration {
    std::string endpoint;
    std::string subject;
  };

  const std::vector<EndpointRegistration> registrations = {
      {"activate", svc_endpoint_subject(cfg_.service_id, "activate")},
      {"deactivate", svc_endpoint_subject(cfg_.service_id, "deactivate")},
      {"set_active", svc_endpoint_subject(cfg_.service_id, "set_active")},
      {"status", svc_endpoint_subject(cfg_.service_id, "status")},
      {"terminate", svc_endpoint_subject(cfg_.service_id, "terminate")},
      {"quit", svc_endpoint_subject(cfg_.service_id, "quit")},
      {"cmd", cmd_channel_subject(cfg_.service_id)},
      {"set_state", svc_endpoint_subject(cfg_.service_id, "set_state")},
      {"set_rungraph", svc_endpoint_subject(cfg_.service_id, "set_rungraph")},
  };

  for (const EndpointRegistration& registration : registrations) {
    auto handle = runtime_transport_->serve(
        registration.subject,
        [this, endpoint = registration.endpoint](const RuntimeMessage& msg) {
          return handle_runtime_control_request(endpoint, msg);
        });
    if (!handle || !handle->valid()) {
      spdlog::error("runtime control endpoint registration failed serviceId={} endpoint={} subject={}",
                    cfg_.service_id, registration.endpoint, registration.subject);
      stop_runtime_control_endpoints();
      return false;
    }
    runtime_control_endpoints_.push_back(std::move(handle));
  }
  return true;
}

void ServiceBus::stop_runtime_control_endpoints() {
  for (auto& handle : runtime_control_endpoints_) {
    if (!handle) {
      continue;
    }
    try {
      handle->stop();
    } catch (const std::exception& exc) {
      spdlog::warn("runtime control endpoint stop failed serviceId={}: {}", cfg_.service_id, exc.what());
    } catch (...) {
      spdlog::warn("runtime control endpoint stop failed serviceId={}: unknown error", cfg_.service_id);
    }
  }
  runtime_control_endpoints_.clear();
}

RuntimeBytes ServiceBus::handle_runtime_control_request(const std::string& endpoint, const RuntimeMessage& msg) {
  const auto env = parse_control_envelope(msg.payload);
  std::string err_code;
  std::string err_msg;
  json result = json::object();

  auto ok_response = [&](const json& out) { return encode_control_response(env.req_id, true, out, "", ""); };
  auto error_response = [&](const std::string& code, const std::string& message) {
    return encode_control_response(env.req_id, false, json(nullptr), code, message);
  };

  try {
    if (endpoint == "activate") {
      on_activate(env.meta);
      return ok_response(json{{"active", true}});
    }
    if (endpoint == "deactivate") {
      on_deactivate(env.meta);
      return ok_response(json{{"active", false}});
    }
    if (endpoint == "set_active") {
      const json* src = nullptr;
      if (env.args.contains("active")) {
        src = &env.args;
      } else if (env.raw.contains("active")) {
        src = &env.raw;
      } else {
        return error_response("INVALID_ARGS", "missing active");
      }

      f8::cppsdk::generated::F8SetActiveArgs req;
      f8::cppsdk::generated::ParseError perr;
      if (!f8::cppsdk::generated::parse_F8SetActiveArgs(*src, req, perr)) {
        return error_response("INVALID_ARGS", perr.message.empty() ? "invalid request" : perr.message);
      }
      on_set_active(req.active, env.meta);
      return ok_response(json{{"active", req.active}});
    }
    if (endpoint == "status") {
      return ok_response(json{{"serviceId", cfg_.service_id}, {"active", is_active()}});
    }
    if (endpoint == "terminate" || endpoint == "quit") {
      spdlog::info("{} requested serviceId={}", endpoint, cfg_.service_id);
      json out;
      const bool ok = on_command("terminate", env.args, env.meta, out, err_code, err_msg);
      if (!ok) {
        return error_response(err_code, err_msg);
      }
      return ok_response(json{{"terminating", true}});
    }
    if (endpoint == "set_state") {
      const json* src = nullptr;
      if (env.args.contains("nodeId") || env.args.contains("field") || env.args.contains("value")) {
        src = &env.args;
      } else if (env.raw.contains("nodeId") || env.raw.contains("field") || env.raw.contains("value")) {
        src = &env.raw;
      } else {
        return error_response("INVALID_ARGS", "missing nodeId/field/value");
      }

      f8::cppsdk::generated::F8SetStateArgs req;
      f8::cppsdk::generated::ParseError perr;
      if (!f8::cppsdk::generated::parse_F8SetStateArgs(*src, req, perr)) {
        return error_response("INVALID_ARGS", perr.message.empty() ? "invalid request" : perr.message);
      }

      const bool ok = on_set_state(req.nodeId, req.field, req.value, env.meta, err_code, err_msg);
      if (!ok) {
        return error_response(err_code, err_msg);
      }
      return ok_response(json{{"nodeId", req.nodeId}, {"field", req.field}});
    }
    if (endpoint == "set_rungraph") {
      json graph_obj;
      f8::cppsdk::generated::ParseError perr;
      if (env.args.contains("graph") && env.args["graph"].is_object()) {
        f8::cppsdk::generated::F8SetRungraphArgs req;
        if (!f8::cppsdk::generated::parse_F8SetRungraphArgs(env.args, req, perr)) {
          return error_response("INVALID_ARGS", perr.message.empty() ? "invalid request" : perr.message);
        }
        graph_obj = env.args["graph"];
      } else if (env.raw.contains("graph") && env.raw["graph"].is_object()) {
        f8::cppsdk::generated::F8SetRungraphArgs req;
        if (!f8::cppsdk::generated::parse_F8SetRungraphArgs(env.raw, req, perr)) {
          return error_response("INVALID_ARGS", perr.message.empty() ? "invalid request" : perr.message);
        }
        graph_obj = env.raw["graph"];
      } else if (env.raw.is_object() && env.raw.contains("nodes") && env.raw.contains("edges")) {
        f8::cppsdk::generated::F8RuntimeGraph req;
        if (!f8::cppsdk::generated::parse_F8RuntimeGraph(env.raw, req, perr)) {
          return error_response("INVALID_ARGS", perr.message.empty() ? "invalid request" : perr.message);
        }
        graph_obj = env.raw;
      } else {
        return error_response("INVALID_ARGS", "missing graph");
      }

      const bool ok = on_set_rungraph(graph_obj, env.meta, err_code, err_msg);
      if (!ok) {
        return error_response(err_code, err_msg);
      }
      return ok_response(json{{"graphId", graph_obj.value("graphId", "")}});
    }
    if (endpoint == "cmd") {
      f8::cppsdk::generated::F8CommandInvokeRequest req;
      f8::cppsdk::generated::ParseError perr;
      if (!f8::cppsdk::generated::parse_F8CommandInvokeRequest(env.raw, req, perr)) {
        return error_response("INVALID_ARGS", perr.message.empty() ? "invalid request" : perr.message);
      }
      json out;
      const bool ok = on_command(req.call, req.args, req.meta, out, err_code, err_msg);
      if (!ok) {
        return error_response(err_code, err_msg);
      }
      return ok_response(out);
    }
  } catch (const std::exception& exc) {
    spdlog::error("runtime control endpoint failed serviceId={} endpoint={}: {}", cfg_.service_id, endpoint, exc.what());
    return error_response("INTERNAL", exc.what());
  } catch (...) {
    spdlog::error("runtime control endpoint failed serviceId={} endpoint={}: unknown error", cfg_.service_id, endpoint);
    return error_response("INTERNAL", "unknown error");
  }

  return error_response("NOT_FOUND", "unknown endpoint");
}

bool ServiceBus::runtime_publish_data(const std::string& from_node_id, const std::string& port_id, const json& value,
                                      std::int64_t ts_ms) {
  const auto subject = data_subject(cfg_.service_id, from_node_id, port_id);
  const auto bytes = encode_data_payload(value, ts_ms);
  if (cfg_.bus_backend == BusBackend::kNats) {
    return nats_.publish(subject, bytes.data(), bytes.size());
  }
  if (runtime_transport_) {
    return runtime_transport_->publish(subject, bytes);
  }
  return false;
}

bool ServiceBus::runtime_kv_put(const std::string& key, const RuntimeBytes& bytes) {
  if (cfg_.bus_backend == BusBackend::kNats) {
    return kv_.put(key, bytes.data(), bytes.size());
  }
  if (runtime_transport_) {
    return runtime_transport_->kv_put(key, bytes);
  }
  return false;
}

std::optional<RuntimeBytes> ServiceBus::runtime_kv_get(const std::string& key) {
  if (cfg_.bus_backend == BusBackend::kNats) {
    return kv_.get(key);
  }
  if (runtime_transport_) {
    return runtime_transport_->kv_get(key);
  }
  return std::nullopt;
}

std::optional<RuntimeBytes> ServiceBus::runtime_kv_get_in_bucket(const std::string& bucket, const std::string& key,
                                                                 std::chrono::milliseconds timeout) {
  if (cfg_.bus_backend == BusBackend::kNats) {
    (void)timeout;
    if (bucket == kv_bucket_for_service(cfg_.service_id)) {
      return kv_.get(key);
    }
    std::lock_guard<std::mutex> lock(state_mu_);
    constexpr const char* kBucketPrefix = "svc_";
    if (bucket.rfind(kBucketPrefix, 0) != 0) {
      spdlog::warn("unsupported runtime KV bucket serviceId={} bucket={}", cfg_.service_id, bucket);
      return std::nullopt;
    }
    const auto peer_service_id = bucket.substr(std::string(kBucketPrefix).size());
    const auto it = peer_kv_by_service_id_.find(peer_service_id);
    if (it != peer_kv_by_service_id_.end() && it->second) {
      return it->second->get(key);
    }
    return std::nullopt;
  }
  if (runtime_transport_) {
    return runtime_transport_->kv_get_in_bucket(bucket, key, timeout);
  }
  return std::nullopt;
}

bool ServiceBus::runtime_set_ready(bool ready, const std::string& reason, std::int64_t ts_ms) {
  const auto raw = encode_ready_payload(cfg_.service_id, ready, reason, ts_ms);
  return runtime_kv_put(kv_key_ready(), raw);
}

bool ServiceBus::runtime_set_node_state(const std::string& node_id, const std::string& field, const json& value,
                                        const std::string& source, const json& extra_meta, std::int64_t ts_ms,
                                        const std::string& origin) {
  const auto raw = encode_node_state_payload(cfg_.service_id, value, source, extra_meta, ts_ms, origin);
  return runtime_kv_put(kv_key_node_state(node_id, field), raw);
}

void ServiceBus::stop() {
  ready_.store(false, std::memory_order_release);
  stop_monitor_thread();
  if (kv_.valid()) {
    (void)kv_set_ready(kv_, cfg_.service_id, false, "stop");
  } else if (runtime_transport_ && !cfg_.service_id.empty()) {
    (void)runtime_set_ready(false, "stop");
  }

  {
    std::lock_guard<std::mutex> lock(state_mu_);
    for (auto& kv : peer_kv_by_service_id_) {
      try {
        if (kv.second) {
          kv.second->stop_watch();
          kv.second->close();
        }
      } catch (...) {
      }
    }
    peer_kv_by_service_id_.clear();
    for (auto& kv : peer_state_subs_by_service_id_) {
      try {
        if (kv.second) {
          kv.second->stop();
        }
      } catch (const std::exception& exc) {
        spdlog::warn("peer state subscription stop failed serviceId={} peer={}: {}", cfg_.service_id, kv.first,
                     exc.what());
      } catch (...) {
        spdlog::warn("peer state subscription stop failed serviceId={} peer={}: unknown error", cfg_.service_id,
                     kv.first);
      }
    }
    peer_state_subs_by_service_id_.clear();
    cross_state_in_.clear();
    cross_state_targets_.clear();
  }

  if (ctrl_) {
    try {
      ctrl_->stop();
    } catch (const std::exception& exc) {
      spdlog::warn("control plane stop failed serviceId={}: {}", cfg_.service_id, exc.what());
    } catch (...) {
      spdlog::warn("control plane stop failed serviceId={}: unknown error", cfg_.service_id);
    }
  }
  ctrl_.reset();
  stop_runtime_control_endpoints();

  try {
    kv_.stop_watch();
  } catch (const std::exception& exc) {
    spdlog::warn("KV watch stop failed serviceId={}: {}", cfg_.service_id, exc.what());
  } catch (...) {
    spdlog::warn("KV watch stop failed serviceId={}: unknown error", cfg_.service_id);
  }
  {
    std::lock_guard<std::mutex> lock(data_mu_);
    for (auto& kv : data_subs_) {
      try {
        kv.second.unsubscribe();
      } catch (const std::exception& exc) {
        spdlog::warn("NATS data subscription stop failed serviceId={} subject={}: {}", cfg_.service_id, kv.first,
                     exc.what());
      } catch (...) {
        spdlog::warn("NATS data subscription stop failed serviceId={} subject={}: unknown error", cfg_.service_id,
                     kv.first);
      }
    }
    data_subs_.clear();
    for (auto& kv : runtime_data_subs_) {
      try {
        if (kv.second) {
          kv.second->stop();
        }
      } catch (const std::exception& exc) {
        spdlog::warn("runtime data subscription stop failed serviceId={} subject={}: {}", cfg_.service_id, kv.first,
                     exc.what());
      } catch (...) {
        spdlog::warn("runtime data subscription stop failed serviceId={} subject={}: unknown error", cfg_.service_id,
                     kv.first);
      }
    }
    runtime_data_subs_.clear();
    data_inputs_.clear();
    std::atomic_store(&data_routes_snapshot_, std::shared_ptr<const _DataRoutingSnapshot>{});
  }
  {
    std::lock_guard<std::mutex> lock(state_mu_);
    state_cache_.clear();
    state_access_.clear();
    intra_state_out_.clear();
    cross_state_in_.clear();
    cross_state_targets_.clear();
    has_rungraph_ = false;
  }
  try {
    main_thread_.clear();
  } catch (const std::exception& exc) {
    spdlog::warn("main-thread queue clear failed serviceId={}: {}", cfg_.service_id, exc.what());
  } catch (...) {
    spdlog::warn("main-thread queue clear failed serviceId={}: unknown error", cfg_.service_id);
  }
  kv_.close();
  nats_.close();
  if (runtime_transport_) {
    runtime_transport_->close();
    runtime_transport_.reset();
  }
}

void ServiceBus::wait_terminate() {
  std::unique_lock<std::mutex> lock(term_mu_);
  term_cv_.wait(lock, [this]() { return terminate_.load(std::memory_order_acquire); });
}

void ServiceBus::set_active_local(bool active, const json& meta, const std::string& source) {
  active_.store(active, std::memory_order_release);

  // Persist `nodes.<serviceId>.state.active` (mirror pysdk).
  const json extra = meta.is_object() ? meta : json::object();
  (void)runtime_set_node_state(cfg_.service_id, "active", active, source, extra, 0, "runtime");

  std::vector<LifecycleNode*> nodes;
  {
    std::lock_guard<std::mutex> lock(lifecycle_mu_);
    nodes = lifecycle_nodes_;
  }
  for (const auto& n : nodes) {
    try {
      if (n) n->on_lifecycle(active, meta);
    } catch (...) {
      continue;
    }
  }
}

void ServiceBus::start_monitor_thread() {
  stop_monitor_thread();
  if (!cfg_.monitor_enabled) return;
  monitor_started_ts_ms_ = now_ms();
  {
    std::lock_guard<std::mutex> wake_lock(monitor_wake_mu_);
    monitor_publish_requested_ = false;
  }
  {
    std::lock_guard<std::mutex> lock(monitor_mu_);
    monitor_observed_ = 0;
    monitor_processed_ = 0;
    monitor_dropped_ = 0;
    monitor_wait_ms_.clear();
    monitor_process_ms_.clear();
    monitor_error_ts_ms_.clear();
    monitor_last_error_code_.clear();
    monitor_last_error_message_.clear();
    monitor_last_error_ts_ms_.reset();
  }
  monitor_running_.store(true, std::memory_order_release);
  monitor_thread_ = std::thread([this]() { monitor_loop(); });
}

void ServiceBus::stop_monitor_thread() {
  monitor_running_.store(false, std::memory_order_release);
  monitor_wake_cv_.notify_all();
  if (monitor_thread_.joinable()) {
    monitor_thread_.join();
  }
}

void ServiceBus::request_monitor_publish_once() {
  if (!cfg_.monitor_enabled) return;
  if (!monitor_running_.load(std::memory_order_acquire)) return;
  {
    std::lock_guard<std::mutex> wake_lock(monitor_wake_mu_);
    monitor_publish_requested_ = true;
  }
  monitor_wake_cv_.notify_all();
}

void ServiceBus::monitor_record_observed(const std::string& port) {
  if (!cfg_.monitor_enabled) return;
  if (port == "monitor") return;
  std::lock_guard<std::mutex> lock(monitor_mu_);
  ++monitor_observed_;
}

void ServiceBus::monitor_record_processed(const std::string& port, const std::int64_t emit_ts_ms,
                                          const std::int64_t now_ts_ms) {
  if (!cfg_.monitor_enabled) return;
  if (port == "monitor") return;
  std::lock_guard<std::mutex> lock(monitor_mu_);
  ++monitor_processed_;
  if (emit_ts_ms > 0 && now_ts_ms >= emit_ts_ms) {
    monitor_process_ms_.push_back({now_ts_ms, static_cast<double>(now_ts_ms - emit_ts_ms)});
  }
}

void ServiceBus::monitor_record_wait_ms(const double wait_ms) {
  if (!cfg_.monitor_enabled) return;
  if (wait_ms < 0.0) return;
  const std::int64_t ts = now_ms();
  std::lock_guard<std::mutex> lock(monitor_mu_);
  monitor_wait_ms_.push_back({ts, wait_ms});
}

void ServiceBus::monitor_record_dropped(const std::int64_t dropped_count) {
  if (!cfg_.monitor_enabled) return;
  if (dropped_count <= 0) return;
  std::lock_guard<std::mutex> lock(monitor_mu_);
  monitor_dropped_ += static_cast<std::uint64_t>(dropped_count);
}

void ServiceBus::report_error(const std::string& node_id, const std::string& code, const std::string& message,
                              const std::string& severity, const std::string& fingerprint, std::int64_t ts_ms) {
  if (!cfg_.monitor_enabled) return;
  if (ts_ms <= 0) ts_ms = now_ms();
  std::string node_id_s = trim_copy(node_id);
  if (node_id_s.empty()) node_id_s = cfg_.service_id;
  std::string code_s = trim_copy(code);
  if (code_s.empty()) code_s = "ERROR";
  const std::string severity_s = normalize_monitor_error_severity(severity);
  const std::string fingerprint_s = derive_monitor_error_fingerprint(node_id_s, code_s, message, fingerprint);

  std::lock_guard<std::mutex> lock(monitor_mu_);
  if (fingerprint_s == monitor_last_error_fingerprint_) {
    monitor_last_error_repeat_count_ = std::max<std::int64_t>(1, monitor_last_error_repeat_count_) + 1;
  } else {
    monitor_last_error_repeat_count_ = 1;
  }
  monitor_last_error_node_id_ = node_id_s;
  monitor_last_error_code_ = code_s;
  monitor_last_error_message_ = message;
  monitor_last_error_severity_ = severity_s;
  monitor_last_error_fingerprint_ = fingerprint_s;
  monitor_last_error_ts_ms_ = ts_ms;
  monitor_current_error_node_id_ = node_id_s;
  monitor_current_error_code_ = code_s;
  monitor_current_error_message_ = message;
  monitor_current_error_severity_ = severity_s;
  monitor_current_error_fingerprint_ = fingerprint_s;
  monitor_current_error_ts_ms_ = ts_ms;
  monitor_error_ts_ms_.push_back(ts_ms);
  request_monitor_publish_once();
}

void ServiceBus::clear_error(const std::string& node_id, const std::string& fingerprint, std::int64_t ts_ms) {
  if (!cfg_.monitor_enabled) return;
  std::string node_id_s = trim_copy(node_id);
  if (node_id_s.empty()) node_id_s = cfg_.service_id;
  const std::string fingerprint_s = trim_copy(fingerprint);
  std::lock_guard<std::mutex> lock(monitor_mu_);
  if (!monitor_current_error_node_id_.empty() && monitor_current_error_node_id_ != node_id_s) {
    return;
  }
  if (!fingerprint_s.empty() && !monitor_current_error_fingerprint_.empty() &&
      monitor_current_error_fingerprint_ != fingerprint_s) {
    return;
  }
  monitor_current_error_node_id_.clear();
  monitor_current_error_code_.clear();
  monitor_current_error_message_.clear();
  monitor_current_error_severity_.clear();
  monitor_current_error_fingerprint_.clear();
  monitor_current_error_ts_ms_.reset();
  (void)ts_ms;
  request_monitor_publish_once();
}

void ServiceBus::monitor_record_error(const std::string& code, const std::string& message, std::int64_t ts_ms) {
  report_error(cfg_.service_id, code, message, "error", "", ts_ms);
}

std::size_t ServiceBus::monitor_queue_depth() const {
  std::size_t depth = 0;
  std::lock_guard<std::mutex> lock(data_mu_);
  for (const auto& kv : data_inputs_) {
    const std::shared_ptr<_InputBuffer>& buf_ptr = kv.second;
    if (!buf_ptr) continue;
    std::lock_guard<std::mutex> buf_lock(buf_ptr->mu);
    depth += buf_ptr->queue.size();
  }
  return depth;
}

void ServiceBus::monitor_loop() {
  using clock = std::chrono::steady_clock;
  const std::int64_t interval_ms = std::max<std::int64_t>(200, cfg_.monitor_interval_ms);
  const std::int64_t window_ms = std::max<std::int64_t>(1000, cfg_.monitor_window_ms);
  std::uint32_t cpu_count_raw = std::thread::hardware_concurrency();
  if (cpu_count_raw == 0) {
    cpu_count_raw = 1;
  }
  const double cpu_count = static_cast<double>(cpu_count_raw);
  auto last_wall = clock::now();
  std::clock_t last_cpu = std::clock();

  while (monitor_running_.load(std::memory_order_acquire)) {
    {
      std::unique_lock<std::mutex> wake_lock(monitor_wake_mu_);
      monitor_wake_cv_.wait_for(wake_lock, std::chrono::milliseconds(interval_ms), [this]() {
        return !monitor_running_.load(std::memory_order_acquire) || monitor_publish_requested_;
      });
      monitor_publish_requested_ = false;
    }
    if (!monitor_running_.load(std::memory_order_acquire)) {
      break;
    }

    const std::int64_t ts = now_ms();
    const auto current_wall = clock::now();
    const std::clock_t current_cpu = std::clock();
    const double wall_s = std::chrono::duration<double>(current_wall - last_wall).count();
    const double cpu_s = static_cast<double>(current_cpu - last_cpu) / static_cast<double>(CLOCKS_PER_SEC);
    last_wall = current_wall;
    last_cpu = current_cpu;

    double process_percent = 0.0;
    if (wall_s > 0.0) {
      process_percent = (cpu_s / wall_s) * 100.0 / cpu_count;
      if (process_percent < 0.0) process_percent = 0.0;
    }

    const auto memory = sample_process_memory_bytes();
    std::uint64_t observed = 0;
    std::uint64_t processed = 0;
    std::uint64_t dropped = 0;
    double process_avg = 0.0;
    double process_p95 = 0.0;
    double wait_avg = 0.0;
    double wait_p95 = 0.0;
    std::size_t error_count_window = 0;
    std::string last_error_node_id;
    std::string last_error_code;
    std::string last_error_message;
    std::string last_error_severity = "error";
    std::string last_error_fingerprint;
    std::int64_t last_error_repeat_count = 0;
    std::optional<std::int64_t> last_error_ts_ms;
    std::string current_error_node_id;
    std::string current_error_code;
    std::string current_error_message;
    std::string current_error_severity;
    std::optional<std::int64_t> current_error_ts_ms;

    {
      std::lock_guard<std::mutex> lock(monitor_mu_);
      prune_timed_values(monitor_wait_ms_, ts, window_ms);
      prune_timed_values(monitor_process_ms_, ts, window_ms);
      prune_timed_errors(monitor_error_ts_ms_, ts, window_ms);
      observed = monitor_observed_;
      processed = monitor_processed_;
      dropped = monitor_dropped_;
      process_avg = average_values(monitor_process_ms_);
      process_p95 = percentile95_values(monitor_process_ms_);
      wait_avg = average_values(monitor_wait_ms_);
      wait_p95 = percentile95_values(monitor_wait_ms_);
      error_count_window = monitor_error_ts_ms_.size();
      last_error_node_id = monitor_last_error_node_id_;
      last_error_code = monitor_last_error_code_;
      last_error_message = monitor_last_error_message_;
      last_error_severity = monitor_last_error_severity_.empty() ? "error" : monitor_last_error_severity_;
      last_error_fingerprint = monitor_last_error_fingerprint_;
      last_error_repeat_count = monitor_last_error_repeat_count_;
      last_error_ts_ms = monitor_last_error_ts_ms_;
      current_error_node_id = monitor_current_error_node_id_;
      current_error_code = monitor_current_error_code_;
      current_error_message = monitor_current_error_message_;
      current_error_severity = monitor_current_error_severity_;
      current_error_ts_ms = monitor_current_error_ts_ms_;
    }

    const json gpu = json{
        {"vendor", cfg_.monitor_gpu_enabled ? "nvidia" : ""},
        {"deviceIndex", nullptr},
        {"utilPercent", nullptr},
        {"memoryUsedBytes", nullptr},
        {"memoryTotalBytes", nullptr},
        {"available", false},
    };
    const json snapshot = json{
        {"schemaVersion", "f8monitor/1"},
        {"serviceId", cfg_.service_id},
        {"serviceClass", cfg_.service_class},
        {"nodeId", cfg_.service_id},
        {"tsMs", ts},
        {"alive", true},
        {"ready", ready_.load(std::memory_order_acquire)},
        {"active", active_.load(std::memory_order_acquire)},
        {"uptimeMs", std::max<std::int64_t>(0, ts - monitor_started_ts_ms_)},
        {"cpu", json{{"processPercent", process_percent}, {"systemPercent", 0.0}}},
        {"memory", json{{"rssBytes", memory.first}, {"vmsBytes", memory.second}}},
        {"gpu", gpu},
        {"frame", json{{"observed", observed},
                       {"processed", processed},
                       {"dropped", dropped},
                       {"localOnlyEmits", 0},
                       {"routedCrossEmits", 0},
                       {"suppressedCrossPublishes", 0},
                       {"callbackDeliveries", 0},
                       {"bufferPullDeliveries", 0}}},
        {"timing", json{{"processMsAvg", process_avg},
                        {"processMsP95", process_p95},
                        {"waitMsAvg", wait_avg},
                        {"waitMsP95", wait_p95},
                        {"latencyMsAvg", nullptr},
                        {"latencyMsP95", nullptr}}},
        {"queue", json{{"depth", monitor_queue_depth()}}},
        {"error", json{{"countWindow", error_count_window},
                       {"lastNodeId", last_error_node_id},
                       {"lastCode", last_error_code},
                       {"lastMessage", last_error_message},
                       {"lastSeverity", last_error_severity},
                       {"lastFingerprint", last_error_fingerprint},
                       {"lastRepeatCount", last_error_repeat_count},
                       {"lastTsMs", last_error_ts_ms.has_value() ? json(last_error_ts_ms.value()) : json(nullptr)},
                       {"currentNodeId", current_error_node_id},
                       {"currentCode", current_error_code},
                       {"currentMessage", current_error_message},
                       {"currentSeverity", current_error_severity},
                       {"currentTsMs",
                        current_error_ts_ms.has_value() ? json(current_error_ts_ms.value()) : json(nullptr)}}},
    };
    {
      f8::cppsdk::generated::F8MonitorSnapshot parsed;
      f8::cppsdk::generated::ParseError perr;
      if (!f8::cppsdk::generated::parse_F8MonitorSnapshot(snapshot, parsed, perr)) {
        monitor_record_error("MONITOR_SCHEMA_INVALID", perr.message.empty() ? "invalid monitor snapshot" : perr.message, ts);
        continue;
      }
    }
    (void)runtime_publish_data(cfg_.service_id, "monitor", snapshot, ts);
  }
}

bool ServiceBus::is_active() const {
  return active_.load(std::memory_order_acquire);
}

void ServiceBus::on_activate(const json& meta) {
  set_active_local(true, meta, "cmd");
}
void ServiceBus::on_deactivate(const json& meta) {
  set_active_local(false, meta, "cmd");
}
void ServiceBus::on_set_active(bool active, const json& meta) {
  set_active_local(active, meta, "cmd");
}

bool ServiceBus::on_set_state(const std::string& node_id, const std::string& field, const json& value, const json& meta,
                              std::string& error_code, std::string& error_message) {
  if (field == "active") {
    if (!value.is_boolean()) {
      error_code = "INVALID_ARGS";
      error_message = "active must be boolean";
      return false;
    }
    set_active_local(value.get<bool>(), meta, "endpoint");
    return true;
  }

  std::string node_id_s;
  try {
    node_id_s = ensure_token(node_id, "node_id");
  } catch (...) {
    error_code = "INVALID_ARGS";
    error_message = "invalid nodeId";
    return false;
  }

  std::string field_s = trim_copy(field);
  if (field_s.empty()) {
    error_code = "INVALID_ARGS";
    error_message = "field must be non-empty";
    return false;
  }

  bool is_hidden_command_input = false;
  {
    std::lock_guard<std::mutex> lock(state_mu_);
    is_hidden_command_input = command_input_bindings_.find({node_id_s, field_s}) != command_input_bindings_.end();
  }
  if (is_hidden_command_input) {
    publish_state_local(node_id_s, field_s, value, now_ms(), "endpoint", meta, "external", true, true);
    error_code.clear();
    error_message.clear();
    return true;
  }

  std::vector<SetStateHandlerNode*> nodes;
  {
    std::lock_guard<std::mutex> lock(handlers_mu_);
    nodes = set_state_nodes_;
  }
  if (nodes.empty()) {
    std::string access;
    {
      std::lock_guard<std::mutex> lock(state_mu_);
      const auto it = state_access_.find({node_id_s, field_s});
      if (it != state_access_.end()) access = it->second;
      if (has_rungraph_ && it == state_access_.end()) {
        error_code = "UNKNOWN_FIELD";
        error_message = "unknown state field";
        return false;
      }
    }
    if (!access.empty() && !state_origin_allows_access("external", access)) {
      error_code = "FORBIDDEN";
      error_message = "state field not writable";
      return false;
    }
    publish_state_local(node_id_s, field_s, value, now_ms(), "endpoint", meta, "external", true, true);
    return true;
  }

  for (auto* n : nodes) {
    if (!n) continue;
    error_code.clear();
    error_message.clear();
    try {
      if (n->on_set_state(node_id, field, value, meta, error_code, error_message)) {
        return true;
      }
    } catch (...) {
      error_code = "INTERNAL_ERROR";
      error_message = "on_set_state threw";
      return false;
    }
  }
  error_code = "NOT_SUPPORTED";
  error_message = "set_state not supported";
  return false;
}

bool ServiceBus::on_set_rungraph(const json& graph_obj, const json& meta, std::string& error_code,
                                 std::string& error_message) {
  error_code.clear();
  error_message.clear();
  try {
    json persisted = graph_obj;
    if (!persisted.contains("meta") || !persisted["meta"].is_object()) {
      persisted["meta"] = json::object();
    }
    persisted["meta"]["ts"] = now_ms();
    apply_rungraph_local(persisted, error_code, error_message);
    if (!error_code.empty()) {
      return false;
    }
    const auto bytes = encode_json(persisted);
    (void)runtime_kv_put(kv_key_rungraph(), bytes);
  } catch (const std::exception& ex) {
    error_code = "INTERNAL";
    error_message = ex.what();
    return false;
  } catch (...) {
    error_code = "INTERNAL";
    error_message = "unknown error";
    return false;
  }
  std::vector<RungraphHandlerNode*> nodes;
  {
    std::lock_guard<std::mutex> lock(handlers_mu_);
    nodes = rungraph_nodes_;
  }
  // Best-effort hook calls (mirrors pysdk's rungraph hooks boundary).
  for (auto* n : nodes) {
    if (!n) continue;
    try {
      std::string _code;
      std::string _msg;
      (void)n->on_set_rungraph(graph_obj, meta, _code, _msg);
    } catch (const std::exception& exc) {
      spdlog::warn("rungraph hook failed serviceId={}: {}", cfg_.service_id, exc.what());
    } catch (...) {
      spdlog::warn("rungraph hook failed serviceId={}: unknown error", cfg_.service_id);
    }
  }
  return true;
}

void ServiceBus::rebuild_command_bindings_locked() {
  command_input_bindings_.clear();
  command_output_bindings_.clear();
  command_hidden_fields_.clear();
  for (const auto& entry : command_specs_by_node_) {
    const auto parsed = parse_command_bindings_from_spec(entry.second, cfg_.service_id);
    for (const auto& binding_in : parsed) {
      _CommandBinding binding;
      binding.node_id = binding_in.node_id;
      binding.call = binding_in.call;
      binding.input_field = binding_in.input_field;
      binding.output_field = binding_in.output_field;
      binding.param_names = binding_in.param_names;
      command_input_bindings_[{binding.node_id, binding.input_field}] = binding;
      command_output_bindings_[binding.call] = binding;
      command_hidden_fields_.insert({binding.node_id, binding.input_field});
      command_hidden_fields_.insert({binding.node_id, binding.output_field});
    }
  }
}

bool ServiceBus::dispatch_command_call(const std::string& call, const json& args, const json& meta, json& result,
                                       std::string& error_code, std::string& error_message) {
  std::vector<CommandableNode*> nodes;
  {
    std::lock_guard<std::mutex> lock(handlers_mu_);
    nodes = command_nodes_;
  }
  for (auto* n : nodes) {
    if (!n) continue;
    error_code.clear();
    error_message.clear();
    try {
      if (n->on_command(call, args, meta, result, error_code, error_message)) {
        return true;
      }
    } catch (...) {
      error_code = "INTERNAL_ERROR";
      error_message = "on_command threw";
      monitor_record_error("INTERNAL_ERROR", "on_command threw");
      return false;
    }
  }
  error_code = "UNKNOWN_CALL";
  error_message = "unknown call: " + call;
  return false;
}

void ServiceBus::write_command_output(const std::string& node_id, const std::string& call, const json& result,
                                      std::int64_t ts_ms, const json& meta) {
  _CommandBinding binding;
  {
    std::lock_guard<std::mutex> lock(state_mu_);
    const auto it = command_output_bindings_.find(call);
    if (it == command_output_bindings_.end()) return;
    binding = it->second;
  }
  json out_meta = meta.is_object() ? meta : json::object();
  out_meta["command"] = call;
  publish_state_local(node_id.empty() ? binding.node_id : node_id, binding.output_field, result,
                      ts_ms > 0 ? ts_ms : now_ms(), "cmd", out_meta, "runtime", true, true);
}

void ServiceBus::schedule_command_input_dispatch(const std::string& node_id, const std::string& field, const json& value,
                                                 std::int64_t ts_ms, const json& meta) {
  bool should_post = false;
  {
    std::lock_guard<std::mutex> lock(state_mu_);
    auto& dispatch = command_dispatch_[{node_id, field}];
    dispatch.latest_value = value;
    dispatch.latest_ts_ms = ts_ms;
    dispatch.latest_meta = meta.is_object() ? meta : json::object();
    dispatch.version += 1;
    if (!dispatch.running) {
      dispatch.running = true;
      should_post = true;
    }
  }
  if (!should_post) return;
  main_thread_.post([this, node_id, field]() { run_command_input_dispatch(node_id, field); });
}

void ServiceBus::run_command_input_dispatch(const std::string& node_id, const std::string& field) {
  while (true) {
    _CommandBinding binding;
    _CommandDispatchState dispatch;
    {
      std::lock_guard<std::mutex> lock(state_mu_);
      const auto it_binding = command_input_bindings_.find({node_id, field});
      const auto it_dispatch = command_dispatch_.find({node_id, field});
      if (it_binding == command_input_bindings_.end() || it_dispatch == command_dispatch_.end()) {
        if (it_dispatch != command_dispatch_.end()) it_dispatch->second.running = false;
        return;
      }
      binding = it_binding->second;
      dispatch = it_dispatch->second;
    }

    const json args = map_command_args(dispatch.latest_value, binding.param_names);
    json call_meta = dispatch.latest_meta.is_object() ? dispatch.latest_meta : json::object();
    call_meta["commandInputField"] = field;
    call_meta["source"] = "cmd";

    json result = json::object();
    std::string error_code;
    std::string error_message;
    const bool ok = dispatch_command_call(binding.call, args, call_meta, result, error_code, error_message);
    if (ok) {
      write_command_output(binding.node_id, binding.call, result, dispatch.latest_ts_ms, call_meta);
    } else if (!error_code.empty() || !error_message.empty()) {
      monitor_record_error(error_code.empty() ? "COMMAND_FAILED" : error_code,
                           error_message.empty() ? ("command failed: " + binding.call) : error_message,
                           dispatch.latest_ts_ms);
    }

    bool has_newer = false;
    {
      std::lock_guard<std::mutex> lock(state_mu_);
      auto it_dispatch = command_dispatch_.find({node_id, field});
      if (it_dispatch == command_dispatch_.end()) {
        return;
      }
      has_newer = it_dispatch->second.version != dispatch.version;
      if (!has_newer) {
        it_dispatch->second.running = false;
        return;
      }
    }
  }
}

bool ServiceBus::on_command(const std::string& call, const json& args, const json& meta, json& result,
                            std::string& error_code, std::string& error_message) {
  if (call == "terminate" || call == "quit") {
    spdlog::info("service_bus terminate requested serviceId={}", cfg_.service_id);
    terminate_.store(true, std::memory_order_release);
    term_cv_.notify_all();
    result = json::object();
    result["terminating"] = true;
    return true;
  }
  const bool ok = dispatch_command_call(call, args, meta, result, error_code, error_message);
  if (ok) {
    write_command_output(cfg_.service_id, call, result, now_ms(), meta);
  }
  return ok;
}

void ServiceBus::load_active_from_kv() {
  try {
    const auto key = kv_key_node_state(cfg_.service_id, "active");
    const auto raw = runtime_kv_get(key);
    if (!raw.has_value()) {
      return;
    }
    json payload = json::object();
    if (!decode_json(raw->data(), raw->size(), payload)) {
      return;
    }
    if (!payload.is_object() || !payload.contains("value")) {
      return;
    }
    const json v = payload["value"];
    if (!v.is_boolean()) {
      return;
    }
    set_active_local(v.get<bool>(), json::object({{"init", true}}), "kv");
  } catch (const std::exception& exc) {
    spdlog::warn("load active state failed serviceId={}: {}", cfg_.service_id, exc.what());
  } catch (...) {
    spdlog::warn("load active state failed serviceId={}: unknown error", cfg_.service_id);
  }
}

bool ServiceBus::emit_data(const std::string& from_node_id, const std::string& port_id, const json& value,
                           std::int64_t ts_ms) {
  if (!active()) return false;
  const std::string node = ensure_token(from_node_id, "from_node_id");
  const std::string port = ensure_token(port_id, "port_id");
  const std::int64_t now_ts = now_ms();
  const std::int64_t emit_ts = ts_ms > 0 ? ts_ms : now_ts;
  monitor_record_processed(port, emit_ts, now_ts);
  return runtime_publish_data(node, port, value, ts_ms);
}

std::optional<json> ServiceBus::pull_data(const std::string& node_id, const std::string& port_id) {
  const std::string nid = ensure_token(node_id, "node_id");
  const std::string pid = ensure_token(port_id, "port_id");

  std::shared_ptr<_InputBuffer> buf_ptr;
  {
    std::lock_guard<std::mutex> lock(data_mu_);
    const auto it = data_inputs_.find({nid, pid});
    if (it == data_inputs_.end()) {
      return std::nullopt;
    }
    buf_ptr = it->second;
  }
  if (!buf_ptr) return std::nullopt;

  const std::int64_t now = now_ms();
  auto& mut = *buf_ptr;
  std::lock_guard<std::mutex> lock(mut.mu);
  if (mut.timeout_ms > 0 && mut.last_seen_ts_ms > 0 && (now - mut.last_seen_ts_ms) > mut.timeout_ms) {
    return std::nullopt;
  }
  if (mut.strategy == EdgeStrategy::kQueue) {
    if (mut.queue.empty()) return std::nullopt;
    const auto& sample = mut.queue.front();
    json v = sample.first ? *sample.first : json(nullptr);
    if (sample.second > 0) {
      monitor_record_wait_ms(static_cast<double>(std::max<std::int64_t>(0, now - sample.second)));
    }
    mut.queue.pop_front();
    return v;
  }

  // latest
  if (!mut.queue.empty()) {
    const auto& sample = mut.queue.back();
    json v = sample.first ? *sample.first : json(nullptr);
    if (sample.second > 0) {
      monitor_record_wait_ms(static_cast<double>(std::max<std::int64_t>(0, now - sample.second)));
    }
    mut.queue.clear();
    return v;
  }
  if (mut.last_seen_ts_ms <= 0) return std::nullopt;
  if (!mut.last_seen_value) return std::nullopt;
  return *mut.last_seen_value;
}

ServiceBus::StateRead ServiceBus::get_state(const std::string& node_id, const std::string& field) {
  const std::string nid = ensure_token(node_id, "node_id");
  const std::string f = field;
  if (f.empty()) {
    return StateRead{false, json(nullptr), std::nullopt};
  }
  {
    std::lock_guard<std::mutex> lock(state_mu_);
    auto it = state_cache_.find({nid, f});
    if (it != state_cache_.end()) {
      return StateRead{true, it->second.first, it->second.second};
    }
  }

  const auto key = kv_key_node_state(nid, f);
  auto raw = runtime_kv_get(key);
  if (!raw.has_value()) {
    return StateRead{false, json(nullptr), std::nullopt};
  }
  json payload = json::object();
  (void)decode_json(raw->data(), raw->size(), payload);
  if (!payload.is_object() || !payload.contains("value")) {
    return StateRead{true, json::binary(*raw), std::int64_t{0}};
  }
  const json v = payload["value"];
  const std::int64_t ts = coerce_inbound_ts_ms(payload, 0);
  {
    std::lock_guard<std::mutex> lock(state_mu_);
    state_cache_[{nid, f}] = {v, ts};
  }
  return StateRead{true, v, ts};
}

bool ServiceBus::publish_state(const std::string& node_id, const std::string& field, const json& value,
                               const std::string& source, const json& meta, std::int64_t ts_ms,
                               const std::string& origin) {
  try {
    publish_state_local(node_id, field, value, ts_ms > 0 ? ts_ms : now_ms(), source, meta, origin, false, false);
    return true;
  } catch (const std::exception& exc) {
    spdlog::warn("publish_state failed serviceId={} nodeId={} field={}: {}", cfg_.service_id, node_id, field,
                 exc.what());
    return false;
  } catch (...) {
    spdlog::warn("publish_state failed serviceId={} nodeId={} field={}: unknown error", cfg_.service_id, node_id,
                 field);
    return false;
  }
}

void ServiceBus::apply_rungraph_local(const json& graph_obj, std::string& error_code, std::string& error_message) {
  using namespace f8::cppsdk::generated;

  F8RuntimeGraph graph{};
  ParseError perr{};
  if (!parse_F8RuntimeGraph(graph_obj, graph, perr)) {
    error_code = "INVALID_RUNGRAPH";
    error_message = perr.message.empty() ? "invalid rungraph" : perr.message;
    return;
  }
  try {
    validate_state_edges_or_throw(graph);
  } catch (const std::exception& ex) {
    error_code = "INVALID_RUNGRAPH";
    error_message = ex.what();
    return;
  }

  // Service/container nodes require nodeId == serviceId.
  for (const auto& n : graph.nodes.value_or(std::vector<F8RuntimeNode>{})) {
    if (!n.operatorClass.has_value() && n.nodeId != n.serviceId) {
      error_code = "INVALID_RUNGRAPH";
      error_message = "service node requires nodeId == serviceId";
      return;
    }
  }

  std::unordered_map<_NodeFieldKey, std::string, _NodeFieldKeyHash> state_access;
  std::unordered_map<_NodeFieldKey, std::vector<_NodeFieldKey>, _NodeFieldKeyHash> intra_state_out;
  std::unordered_map<_RemoteStateKey, std::vector<_NodeFieldKey>, _RemoteStateKeyHash> cross_state_in;
  std::unordered_set<_NodeFieldKey, _NodeFieldKeyHash> cross_state_targets;
  std::unordered_set<_RemoteStateKey, _RemoteStateKeyHash> cross_state_initial_read_set;
  std::vector<_RemoteStateKey> cross_state_initial_reads;

  const std::string sid = cfg_.service_id;

  // Build access map and validate rungraph-provided stateValues.
  for (const auto& n : graph.nodes.value_or(std::vector<F8RuntimeNode>{})) {
    if (n.serviceId != sid) continue;
    if (n.nodeId.empty()) {
      error_code = "INVALID_RUNGRAPH";
      error_message = "missing nodeId";
      return;
    }
    std::unordered_map<std::string, std::string> access_by_name;
    if (n.stateFields.has_value()) {
      for (const auto& sf : n.stateFields.value()) {
        const std::string name = sf.name;
        if (name.empty()) continue;
        const std::string access_s = access_to_string(sf.access);
        state_access[{n.nodeId, name}] = access_s;
        access_by_name[name] = access_s;
      }
    }
    if (n.stateValues.is_object()) {
      for (auto it = n.stateValues.begin(); it != n.stateValues.end(); ++it) {
        const std::string k = it.key();
        const auto a_it = access_by_name.find(k);
        if (a_it == access_by_name.end()) {
          error_code = "INVALID_RUNGRAPH";
          error_message = "unknown state value: " + n.nodeId + "." + k;
          return;
        }
        if (a_it->second == "ro") {
          error_code = "INVALID_RUNGRAPH";
          error_message = "read-only state cannot be set by rungraph: " + n.nodeId + "." + k;
          return;
        }
      }
    }
  }

  // Build intra-service state edge fanout table.
  for (const auto& e : graph.edges.value_or(std::vector<F8Edge>{})) {
    if (e.kind != F8EdgeKindEnum::state) continue;
    const std::string from_sid = e.fromServiceId;
    const std::string to_sid = e.toServiceId;
    if (to_sid != sid) continue;

    std::string from_node = e.fromOperatorId.value_or("");
    if (from_node.empty()) from_node = from_sid;
    std::string to_node = e.toOperatorId.value_or("");
    if (to_node.empty()) to_node = sid;
    const std::string from_field = e.fromPort;
    const std::string to_field = e.toPort;
    if (from_sid.empty() || to_node.empty() || from_node.empty() || from_field.empty() || to_field.empty()) continue;

    // Pre-filter to only writable targets for external propagation to reduce per-update overhead.
    {
      const auto it_access = state_access.find({to_node, to_field});
      if (it_access == state_access.end()) continue;
      if (it_access->second == "ro") continue;
    }

    if (from_sid == sid) {
      // Intra-service state edge.
      intra_state_out[{from_node, from_field}].push_back({to_node, to_field});
    } else {
      // Cross-service state binding (remote KV -> local field).
      const _RemoteStateKey remote_state_key{from_sid, from_node, from_field};
      cross_state_in[remote_state_key].push_back({to_node, to_field});
      cross_state_targets.insert({to_node, to_field});
      if (cross_state_initial_read_set.insert(remote_state_key).second) {
        cross_state_initial_reads.push_back(remote_state_key);
      }
    }
  }

  std::unordered_map<_NodeFieldKey, std::string, _NodeFieldKeyHash> state_access_snapshot;
  {
    std::lock_guard<std::mutex> lock(state_mu_);
    state_access_ = std::move(state_access);
    intra_state_out_ = std::move(intra_state_out);
    cross_state_in_ = std::move(cross_state_in);
    cross_state_targets_ = std::move(cross_state_targets);
    rebuild_command_bindings_locked();
    has_rungraph_ = true;
    state_access_snapshot = state_access_;
  }

  apply_data_routes_from_rungraph(graph_obj);

  // Ensure peer state watches are running for any cross-state dependencies.
  // This mirrors f8pysdk's cross-service state routing (remote watch + initial sync).
  std::unordered_set<std::string> want_peers;
  for (const auto& remote_state_key : cross_state_initial_reads) {
    want_peers.insert(remote_state_key.peer_service_id);
  }

  if (cfg_.bus_backend == BusBackend::kNats) {
    {
      std::lock_guard<std::mutex> lock(state_mu_);
      for (auto it = peer_kv_by_service_id_.begin(); it != peer_kv_by_service_id_.end();) {
        if (want_peers.find(it->first) != want_peers.end()) {
          ++it;
          continue;
        }
        try {
          if (it->second) {
            it->second->stop_watch();
            it->second->close();
          }
        } catch (const std::exception& exc) {
          spdlog::warn("peer KV close failed serviceId={} peer={}: {}", cfg_.service_id, it->first, exc.what());
        } catch (...) {
          spdlog::warn("peer KV close failed serviceId={} peer={}: unknown error", cfg_.service_id, it->first);
        }
        it = peer_kv_by_service_id_.erase(it);
      }
    }

    for (const auto& peer : want_peers) {
      bool has_peer = false;
      {
        std::lock_guard<std::mutex> lock(state_mu_);
        has_peer = (peer_kv_by_service_id_.find(peer) != peer_kv_by_service_id_.end());
      }
      if (has_peer) continue;

      auto kv_peer = std::make_unique<KvStore>();
      KvConfig kvc;
      kvc.bucket = kv_bucket_for_service(peer);
      kvc.memory_storage = true;
      kvc.history = 1;
      if (!kv_peer->open_or_create(nats_.jetstream(), kvc)) {
        spdlog::warn("peer KV open failed bucket={} peer={}", kvc.bucket, peer);
        continue;
      }

      const bool ok = kv_peer->watch(
          "nodes.>",
          [this, peer](const std::string& key, const std::vector<std::uint8_t>& bytes) {
            handle_peer_state_payload(peer, key, bytes);
          },
          true);

      if (!ok) {
        kv_peer->close();
        continue;
      }
      {
        std::lock_guard<std::mutex> lock(state_mu_);
        peer_kv_by_service_id_[peer] = std::move(kv_peer);
      }
      if (state_debug_enabled()) {
        spdlog::info("state_debug[{}] cross_state_watch_started peer={} bucket={}", cfg_.service_id, peer, kvc.bucket);
      }
    }
  } else {
    {
      std::lock_guard<std::mutex> lock(state_mu_);
      for (auto it = peer_state_subs_by_service_id_.begin(); it != peer_state_subs_by_service_id_.end();) {
        if (want_peers.find(it->first) != want_peers.end()) {
          ++it;
          continue;
        }
        try {
          if (it->second) {
            it->second->stop();
          }
        } catch (const std::exception& exc) {
          spdlog::warn("peer state subscription stop failed serviceId={} peer={}: {}", cfg_.service_id, it->first,
                       exc.what());
        } catch (...) {
          spdlog::warn("peer state subscription stop failed serviceId={} peer={}: unknown error", cfg_.service_id,
                       it->first);
        }
        it = peer_state_subs_by_service_id_.erase(it);
      }
    }

    for (const auto& peer : want_peers) {
      bool has_peer = false;
      {
        std::lock_guard<std::mutex> lock(state_mu_);
        has_peer = (peer_state_subs_by_service_id_.find(peer) != peer_state_subs_by_service_id_.end());
      }
      if (has_peer) continue;
      if (!runtime_transport_) {
        spdlog::warn("peer state subscription skipped without runtime transport serviceId={} peer={}", cfg_.service_id,
                     peer);
        continue;
      }

      const std::string bucket = kv_bucket_for_service(peer);
      auto sub = runtime_transport_->kv_watch_in_bucket(
          bucket, "nodes.>",
          [this, peer](const std::string& key, const RuntimeBytes& bytes) {
            handle_peer_state_payload(peer, key, bytes);
          });
      if (!sub || !sub->valid()) {
        spdlog::warn("peer state subscription failed serviceId={} peer={} bucket={}", cfg_.service_id, peer, bucket);
        continue;
      }
      {
        std::lock_guard<std::mutex> lock(state_mu_);
        peer_state_subs_by_service_id_[peer] = std::move(sub);
      }
      if (state_debug_enabled()) {
        spdlog::info("state_debug[{}] cross_state_watch_started peer={} bucket={}", cfg_.service_id, peer, bucket);
      }
    }
  }

  // Initial sync for cross-state targets: best-effort pull current remote values once.
  const bool bounded_initial_sync = cfg_.bus_backend != BusBackend::kNats;
  const auto initial_sync_started_at = std::chrono::steady_clock::now();
  std::size_t initial_sync_hits = 0;
  std::size_t initial_sync_misses = 0;
  std::size_t initial_sync_skipped = 0;
  for (std::size_t i = 0; i < cross_state_initial_reads.size(); ++i) {
    if (bounded_initial_sync) {
      const auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
          std::chrono::steady_clock::now() - initial_sync_started_at);
      if (elapsed >= kZenohCrossStateInitialSyncBudget) {
        initial_sync_skipped += cross_state_initial_reads.size() - i;
        break;
      }
    }

    const _RemoteStateKey& remote_state_key = cross_state_initial_reads[i];
    const std::string& peer = remote_state_key.peer_service_id;
    const std::string& remote_node_id = remote_state_key.remote_node_id;
    const std::string& remote_field = remote_state_key.remote_field;
    std::string remote_key;
    try {
      remote_key = kv_key_node_state(remote_node_id, remote_field);
    } catch (const std::exception& exc) {
      spdlog::warn("cross-state initial key build failed serviceId={} peer={}: {}", cfg_.service_id, peer, exc.what());
      continue;
    } catch (...) {
      spdlog::warn("cross-state initial key build failed serviceId={} peer={}: unknown error", cfg_.service_id, peer);
      continue;
    }

    std::chrono::milliseconds get_timeout = kRuntimeKvGetDefaultTimeout;
    if (bounded_initial_sync) {
      const auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
          std::chrono::steady_clock::now() - initial_sync_started_at);
      const auto remaining = kZenohCrossStateInitialSyncBudget - elapsed;
      if (remaining <= std::chrono::milliseconds(0)) {
        initial_sync_skipped += cross_state_initial_reads.size() - i;
        break;
      }
      get_timeout = std::min(kZenohCrossStateInitialGetTimeout, remaining);
    }

    const auto raw = runtime_kv_get_in_bucket(kv_bucket_for_service(peer), remote_key, get_timeout);
    if (!raw.has_value()) {
      ++initial_sync_misses;
      if (state_debug_enabled()) {
        spdlog::info("state_debug[{}] cross_state_initial_miss peer={} key={}", cfg_.service_id, peer, remote_key);
      }
      continue;
    }
    ++initial_sync_hits;
    if (state_debug_enabled()) {
      spdlog::info("state_debug[{}] cross_state_initial_hit peer={} key={} bytes={}", cfg_.service_id, peer,
                   remote_key, raw->size());
    }
    handle_peer_state_payload(peer, remote_key, *raw);
  }
  if (bounded_initial_sync && initial_sync_skipped > 0) {
    spdlog::info(
        "cross-state initial sync budget exhausted serviceId={} reads={} hits={} misses={} skipped={} budgetMs={}",
        cfg_.service_id, cross_state_initial_reads.size(), initial_sync_hits, initial_sync_misses,
        initial_sync_skipped, kZenohCrossStateInitialSyncBudget.count());
  }

  // Apply per-node stateValues (best-effort reconcile using rungraph meta.ts).
  std::int64_t rungraph_ts = 0;
  try {
    if (graph.meta.has_value()) rungraph_ts = graph.meta->ts.value_or(0);
  } catch (...) {
    rungraph_ts = 0;
  }

  for (const auto& n : graph.nodes.value_or(std::vector<F8RuntimeNode>{})) {
    if (n.serviceId != sid) continue;
    const std::string node_id = n.nodeId;
    if (!n.stateValues.is_object()) continue;
    for (auto it = n.stateValues.begin(); it != n.stateValues.end(); ++it) {
      const std::string field = it.key();
      const json v = it.value();

      // Cross-service state edges are directional: downstream follows upstream.
      // Do not apply rungraph stateValues to fields that are cross-state targets,
      // otherwise local UI defaults (often empty) can clobber remote-propagated values.
      if (cross_state_targets_.find(_NodeFieldKey{node_id, field}) != cross_state_targets_.end()) {
        if (state_debug_enabled()) {
          spdlog::info("state_debug[{}] cross_state_skip_rungraph node={}.{} value={}", cfg_.service_id, node_id, field,
                       v.dump());
        }
        continue;
      }

      if (rungraph_ts > 0) {
        const auto existing = get_state(node_id, field);
        if (existing.found) {
          try {
            if (existing.value == v) {
              continue;
            }
          } catch (...) {
          }
          if (existing.ts_ms.has_value() && existing.ts_ms.value() >= rungraph_ts) {
            continue;
          }
        }
      }
      publish_state_local(node_id, field, v, rungraph_ts > 0 ? rungraph_ts : now_ms(), "rungraph",
                          json{{"via", "rungraph"}, {"rungraphReconcile", true}}, "rungraph",
                          true, false);
    }
  }

  // Initial sync for intra-service state edges:
  // propagate existing root values once so edge-driven targets do not remain stale
  // until the next upstream change.
  struct IntraStateKey {
    std::string node_id;
    std::string field;
    bool operator==(const IntraStateKey& other) const { return node_id == other.node_id && field == other.field; }
  };
  struct IntraStateKeyHash {
    std::size_t operator()(const IntraStateKey& k) const noexcept {
      return std::hash<std::string>{}(k.node_id) ^ (std::hash<std::string>{}(k.field) << 1);
    }
  };

  std::unordered_map<IntraStateKey, std::vector<IntraStateKey>, IntraStateKeyHash> out_edges;
  std::unordered_set<IntraStateKey, IntraStateKeyHash> inbound;
  std::unordered_set<IntraStateKey, IntraStateKeyHash> nodes_set;
  std::vector<IntraStateKey> nodes;

  auto add_node = [&](const IntraStateKey& k) {
    if (nodes_set.find(k) != nodes_set.end()) return;
    nodes_set.insert(k);
    nodes.push_back(k);
  };

  for (const auto& e : graph.edges.value_or(std::vector<F8Edge>{})) {
    if (e.kind != F8EdgeKindEnum::state) continue;
    const std::string from_sid = e.fromServiceId;
    const std::string to_sid = e.toServiceId;
    if (from_sid != sid || to_sid != sid) continue;

    std::string from_node = e.fromOperatorId.value_or("");
    if (from_node.empty()) from_node = from_sid;
    std::string to_node = e.toOperatorId.value_or("");
    if (to_node.empty()) to_node = sid;
    const std::string from_field = e.fromPort;
    const std::string to_field = e.toPort;
    if (from_node.empty() || to_node.empty() || from_field.empty() || to_field.empty()) continue;

    auto it_access = state_access_snapshot.find({to_node, to_field});
    if (it_access == state_access_snapshot.end()) continue;
    if (it_access->second != "rw" && it_access->second != "wo") continue;

    IntraStateKey from_key{from_node, from_field};
    IntraStateKey to_key{to_node, to_field};
    out_edges[from_key].push_back(to_key);
    inbound.insert(to_key);
    add_node(from_key);
    add_node(to_key);
  }

  if (!out_edges.empty()) {
    std::vector<IntraStateKey> roots;
    roots.reserve(nodes.size());
    for (const auto& k : nodes) {
      if (inbound.find(k) == inbound.end()) {
        roots.push_back(k);
      }
    }

    const std::int64_t init_ts = now_ms();
    for (const auto& root : roots) {
      const auto root_state = get_state(root.node_id, root.field);
      if (!root_state.found) continue;

      std::vector<std::pair<IntraStateKey, json>> queue;
      queue.push_back({root, root_state.value});
      std::size_t head = 0;
      std::unordered_set<IntraStateKey, IntraStateKeyHash> seen;

      while (head < queue.size()) {
        const auto from_key = queue[head].first;
        const auto from_value = queue[head].second;
        ++head;

        if (seen.find(from_key) != seen.end()) continue;
        seen.insert(from_key);

        const auto it = out_edges.find(from_key);
        if (it == out_edges.end()) continue;

        for (const auto& to_key : it->second) {
          if (seen.find(to_key) != seen.end()) continue;

          const auto to_state = get_state(to_key.node_id, to_key.field);
          if (to_state.found) {
            bool same = false;
            try {
              same = (to_state.value == from_value);
            } catch (...) {
              same = false;
            }
            if (same) {
              queue.push_back({to_key, to_state.value});
              continue;
            }
          }

          publish_state_local(
              to_key.node_id,
              to_key.field,
              from_value,
              init_ts,
              "state_edge_intra_init",
              json{{"fromNodeId", from_key.node_id}, {"fromField", from_key.field}},
              "external",
              true,
              true);

          const auto applied_state = get_state(to_key.node_id, to_key.field);
          if (applied_state.found) {
            queue.push_back({to_key, applied_state.value});
          } else {
            queue.push_back({to_key, from_value});
          }
        }
      }
    }
  }

  // Seed identity fields (`svcId`, `operatorId`) when declared in stateFields.
  for (const auto& n : graph.nodes.value_or(std::vector<F8RuntimeNode>{})) {
    if (n.serviceId != sid) continue;
    const std::string node_id = n.nodeId;
    bool has_svc_id = false;
    bool has_operator_id = false;
    {
      std::lock_guard<std::mutex> lock(state_mu_);
      has_svc_id = state_access_.find({node_id, "svcId"}) != state_access_.end();
      has_operator_id = n.operatorClass.has_value() && state_access_.find({node_id, "operatorId"}) != state_access_.end();
    }
    if (has_svc_id) {
      publish_state_local(node_id, "svcId", n.serviceId.empty() ? sid : n.serviceId, rungraph_ts > 0 ? rungraph_ts : now_ms(),
                          "system", json{{"builtin", true}}, "system", false, false);
    }
    if (has_operator_id) {
      publish_state_local(node_id, "operatorId", n.nodeId, rungraph_ts > 0 ? rungraph_ts : now_ms(), "system",
                          json{{"builtin", true}}, "system", false, false);
    }
  }
}

void ServiceBus::publish_state_local(const std::string& node_id, const std::string& field, const json& value,
                                     std::int64_t ts_ms, const std::string& source, const json& meta,
                                     const std::string& origin, bool deliver_local, bool allow_state_fanout) {
  const std::string nid = ensure_token(node_id, "node_id");
  const std::string f = field;
  if (f.empty()) return;

  // Value-dedupe.
  {
    std::lock_guard<std::mutex> lock(state_mu_);
    const auto it = state_cache_.find({nid, f});
    if (it != state_cache_.end()) {
      try {
        if (it->second.first == value) {
          return;
        }
      } catch (...) {
      }
    }
  }

  // Access enforcement when rungraph is known.
  std::string access;
  {
    std::lock_guard<std::mutex> lock(state_mu_);
    const auto it = state_access_.find({nid, f});
    if (it != state_access_.end()) access = it->second;
    if (has_rungraph_ && it == state_access_.end()) {
      return;
    }
  }
  if (!access.empty() && !state_origin_allows_access(origin, access)) {
    return;
  }

  const json extra = meta.is_object() ? meta : json::object();
  (void)runtime_set_node_state(nid, f, value, source, extra, ts_ms, origin);
  bool is_command_input = false;
  {
    std::lock_guard<std::mutex> lock(state_mu_);
    state_cache_[{nid, f}] = {value, ts_ms};
    is_command_input = command_input_bindings_.find({nid, f}) != command_input_bindings_.end();
  }
  if (deliver_local) {
    if (is_command_input) {
      schedule_command_input_dispatch(nid, f, value, ts_ms, extra);
      return;
    }
    main_thread_.post([this, nid, f, value, ts_ms, extra, allow_state_fanout]() {
      deliver_state_local(nid, f, value, ts_ms, extra, allow_state_fanout);
    });
  }
}

void ServiceBus::deliver_state_local(const std::string& node_id, const std::string& field, const json& value,
                                     std::int64_t ts_ms, const json& meta, bool allow_state_fanout) {
  std::string hidden_direction;
  const bool is_hidden_command = hidden_command_state_direction(field, hidden_direction);
  if (is_hidden_command && hidden_direction == "in") {
    return;
  }
  std::vector<StatefulNode*> nodes;
  if (!(is_hidden_command && hidden_direction == "out")) {
    {
      std::lock_guard<std::mutex> lock(handlers_mu_);
      nodes = stateful_nodes_;
    }
    for (auto* n : nodes) {
      if (!n) continue;
      try {
        n->on_state(node_id, field, value, ts_ms, meta);
      } catch (...) {
        continue;
      }
    }
  }
  if (allow_state_fanout) {
    route_intra_state_edges(node_id, field, value, ts_ms);
  }
}

void ServiceBus::route_intra_state_edges(const std::string& from_node_id, const std::string& from_field,
                                         const json& value, std::int64_t ts_ms) {
  std::vector<_NodeFieldKey> targets;
  {
    std::lock_guard<std::mutex> lock(state_mu_);
    const auto it = intra_state_out_.find({from_node_id, from_field});
    if (it != intra_state_out_.end()) {
      targets = it->second;
    }
  }
  if (targets.empty()) return;
  for (const auto& t : targets) {
    publish_state_local(t.node_id, t.field, value, ts_ms, "state_edge_intra", json{{"fromNodeId", from_node_id}, {"fromField", from_field}},
                        "external", true, true);
  }
}

}  // namespace f8::cppsdk
