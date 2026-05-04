#pragma once

#include <atomic>
#include <condition_variable>
#include <cstdint>
#include <deque>
#include <functional>
#include <optional>
#include <unordered_map>
#include <unordered_set>
#include <cstddef>
#include <memory>
#include <mutex>
#include <thread>
#include <string>
#include <utility>
#include <vector>

#include <nlohmann/json.hpp>

#include "f8cppsdk/capabilities.h"
#include "f8cppsdk/main_thread_queue.h"
#include "f8cppsdk/rungraph_routes.h"
#include "f8cppsdk/runtime_backend.h"
#include "f8cppsdk/runtime_transport.h"
#include "f8cppsdk/kv_store.h"
#include "f8cppsdk/nats_client.h"
#include "f8cppsdk/service_control_plane.h"
#include "f8cppsdk/service_control_plane_server.h"

namespace f8::cppsdk {

// Minimal, protocol-compatible service bus for C++ services.
//
// Goals (phase 1):
// - Own NATS+KV connections and lifecycle state (`active`).
// - Expose built-in micro endpoints via `ServiceControlPlaneServer`.
// - Provide a terminate/quit latch to let services exit gracefully.
// - Keep wire protocol compatible with f8pysdk (KV keys, endpoint subjects, payload schema).
class ServiceBus final : public ServiceControlHandler {
 public:
  using json = nlohmann::json;

  enum class DataDeliveryMode {
    kPull,
    kPush,
    kBoth,
  };

  struct StateRead {
    bool found = false;
    json value = json(nullptr);
    std::optional<std::int64_t> ts_ms;
  };

  struct Config {
    std::string service_id;
    BusBackend bus_backend = BusBackend::kZenoh;
    std::string nats_url = kDefaultNatsUrl;
    std::string zenoh_config_path;
    std::vector<std::string> zenoh_connect;
    std::vector<std::string> zenoh_listen;
    std::uint64_t zenoh_shm_pool_bytes = kDefaultZenohShmPoolBytes;
    bool kv_memory_storage = true;
    std::string service_name;
    std::string service_class;
    bool publish_all_data = true;
    DataDeliveryMode data_delivery = DataDeliveryMode::kBoth;
    bool monitor_enabled = true;
    std::int64_t monitor_interval_ms = 1000;
    std::int64_t monitor_window_ms = 30000;
    bool monitor_gpu_enabled = true;

    void apply_runtime_backend(const RuntimeBackendConfig& runtime_backend) {
      bus_backend = runtime_backend.bus_backend;
      nats_url = runtime_backend.nats_url;
      zenoh_config_path = runtime_backend.zenoh_config_path;
      zenoh_connect = runtime_backend.zenoh_connect;
      zenoh_listen = runtime_backend.zenoh_listen;
      zenoh_shm_pool_bytes = runtime_backend.zenoh_shm_pool_bytes;
    }

    RuntimeBackendConfig runtime_backend_config() const {
      RuntimeBackendConfig runtime_backend;
      runtime_backend.bus_backend = bus_backend;
      runtime_backend.nats_url = nats_url;
      runtime_backend.zenoh_config_path = zenoh_config_path;
      runtime_backend.zenoh_connect = zenoh_connect;
      runtime_backend.zenoh_listen = zenoh_listen;
      runtime_backend.zenoh_shm_pool_bytes = zenoh_shm_pool_bytes;
      return normalize_runtime_backend_config(std::move(runtime_backend));
    }
  };

  explicit ServiceBus(Config cfg);
  ~ServiceBus();
  ServiceBus(const ServiceBus&) = delete;
  ServiceBus& operator=(const ServiceBus&) = delete;
  ServiceBus(ServiceBus&&) = delete;
  ServiceBus& operator=(ServiceBus&&) = delete;

  // Handlers are optional; unhandled calls will be rejected.
  void add_lifecycle_node(LifecycleNode* node);
  void add_stateful_node(StatefulNode* node);
  void add_data_node(DataReceivableNode* node);
  void add_set_state_node(SetStateHandlerNode* node);
  void add_rungraph_node(RungraphHandlerNode* node);
  void add_command_node(CommandableNode* node, const json& service_spec = json::object());

  bool start();
  void stop();

  bool active() const { return active_.load(std::memory_order_acquire); }
  bool terminate_requested() const { return terminate_.load(std::memory_order_acquire); }

  void report_error(const std::string& node_id, const std::string& code, const std::string& message,
                    const std::string& severity = "error", const std::string& fingerprint = "",
                    std::int64_t ts_ms = 0);
  void clear_error(const std::string& node_id, const std::string& fingerprint = "", std::int64_t ts_ms = 0);

  // Block until terminate/quit is requested.
  void wait_terminate();

  // Expose underlying transports for high-performance services.
  NatsClient& nats() { return nats_; }
  const NatsClient& nats() const { return nats_; }
  KvStore& kv() { return kv_; }
  const KvStore& kv() const { return kv_; }

  // Pump tasks that must run on the service main/tick thread.
  std::size_t drain_main_thread(std::size_t max_tasks = 0);

  // Apply lifecycle locally and persist to KV (best-effort).
  void set_active_local(bool active, const json& meta, const std::string& source = "cmd");

  // ---- data -----------------------------------------------------------
  // Publish a data sample (wire-compatible with f8pysdk ServiceBus.emit_data).
  bool emit_data(const std::string& from_node_id, const std::string& port_id, const json& value,
                 std::int64_t ts_ms = 0);

  // Pull buffered inbound data for (node,port). Returns nullopt if empty/stale.
  std::optional<json> pull_data(const std::string& node_id, const std::string& port_id);

  // ---- state ----------------------------------------------------------
  // Read state from local cache/KV (wire-compatible with f8pysdk get_state).
  StateRead get_state(const std::string& node_id, const std::string& field);
  bool publish_state(const std::string& node_id, const std::string& field, const json& value,
                     const std::string& source = "runtime", const json& meta = json::object(),
                     std::int64_t ts_ms = 0, const std::string& origin = "runtime");

 // ---- ServiceControlHandler (endpoints) ------------------------------
  bool is_active() const override;
  void on_activate(const json& meta) override;
  void on_deactivate(const json& meta) override;
  void on_set_active(bool active, const json& meta) override;
  bool on_set_state(const std::string& node_id, const std::string& field, const json& value, const json& meta,
                    std::string& error_code, std::string& error_message) override;
  bool on_set_rungraph(const json& graph_obj, const json& meta, std::string& error_code,
                       std::string& error_message) override;
  bool on_command(const std::string& call, const json& args, const json& meta, json& result, std::string& error_code,
                  std::string& error_message) override;

 private:
  bool start_nats_backend();
  bool start_zenoh_backend();
  bool start_runtime_control_endpoints();
  void stop_runtime_control_endpoints();
  RuntimeBytes handle_runtime_control_request(const std::string& endpoint, const RuntimeMessage& msg);
  bool runtime_publish_data(const std::string& from_node_id, const std::string& port_id, const json& value,
                            std::int64_t ts_ms = 0);
  bool runtime_kv_put(const std::string& key, const RuntimeBytes& bytes);
  std::optional<RuntimeBytes> runtime_kv_get(const std::string& key);
  std::optional<RuntimeBytes> runtime_kv_get_in_bucket(const std::string& bucket, const std::string& key);
  bool runtime_set_ready(bool ready, const std::string& reason = "", std::int64_t ts_ms = 0);
  bool runtime_set_node_state(const std::string& node_id, const std::string& field, const json& value,
                              const std::string& source = "runtime",
                              const json& extra_meta = json::object(), std::int64_t ts_ms = 0,
                              const std::string& origin = "runtime");
  void handle_data_payload(const std::string& subject, const RuntimeBytes& bytes);
  void handle_peer_state_payload(const std::string& peer, const std::string& key, const RuntimeBytes& bytes);
  void load_active_from_kv();
  void apply_data_routes_from_rungraph(const json& graph_obj);
  void apply_rungraph_local(const json& graph_obj, std::string& error_code, std::string& error_message);
  void publish_state_local(const std::string& node_id, const std::string& field, const json& value, std::int64_t ts_ms,
                           const std::string& source, const json& meta, const std::string& origin,
                           bool deliver_local, bool allow_state_fanout);
  void deliver_state_local(const std::string& node_id, const std::string& field, const json& value, std::int64_t ts_ms,
                           const json& meta, bool allow_state_fanout);
  void route_intra_state_edges(const std::string& from_node_id, const std::string& from_field, const json& value,
                               std::int64_t ts_ms);
  void rebuild_command_bindings_locked();
  void schedule_command_input_dispatch(const std::string& node_id, const std::string& field, const json& value,
                                       std::int64_t ts_ms, const json& meta);
  void run_command_input_dispatch(const std::string& node_id, const std::string& field);
  bool dispatch_command_call(const std::string& call, const json& args, const json& meta, json& result,
                             std::string& error_code, std::string& error_message);
  void write_command_output(const std::string& node_id, const std::string& call, const json& result,
                            std::int64_t ts_ms, const json& meta);
  void start_monitor_thread();
  void stop_monitor_thread();
  void monitor_loop();
  void request_monitor_publish_once();
  void monitor_record_observed(const std::string& port);
  void monitor_record_processed(const std::string& port, std::int64_t emit_ts_ms, std::int64_t now_ts_ms);
  void monitor_record_wait_ms(double wait_ms);
  void monitor_record_dropped(std::int64_t dropped_count);
  void monitor_record_error(const std::string& code, const std::string& message, std::int64_t ts_ms = 0);
  std::size_t monitor_queue_depth() const;

  Config cfg_;
  std::atomic<bool> active_{true};
  std::atomic<bool> ready_{false};
  std::atomic<bool> terminate_{false};
  std::atomic<bool> monitor_running_{false};

  mutable std::mutex term_mu_;
  std::condition_variable term_cv_;

  NatsClient nats_;
  KvStore kv_;
  std::unique_ptr<ServiceControlPlaneServer> ctrl_;
  std::unique_ptr<RuntimeTransport> runtime_transport_;
  std::vector<std::unique_ptr<RuntimeSubscription>> runtime_control_endpoints_;

  MainThreadQueue main_thread_;

  mutable std::mutex lifecycle_mu_;
  std::vector<LifecycleNode*> lifecycle_nodes_;

  mutable std::mutex handlers_mu_;
  std::vector<SetStateHandlerNode*> set_state_nodes_;
  std::vector<RungraphHandlerNode*> rungraph_nodes_;
  std::vector<CommandableNode*> command_nodes_;
  std::unordered_map<CommandableNode*, json> command_specs_by_node_;
  std::vector<StatefulNode*> stateful_nodes_;
  std::vector<DataReceivableNode*> data_nodes_;

  struct _NodePortKey {
    std::string node_id;
    std::string port;
    bool operator==(const _NodePortKey& other) const { return node_id == other.node_id && port == other.port; }
  };
  struct _NodePortKeyHash {
    std::size_t operator()(const _NodePortKey& k) const noexcept {
      return std::hash<std::string>{}(k.node_id) ^ (std::hash<std::string>{}(k.port) << 1);
    }
  };

  struct _NodeFieldKey {
    std::string node_id;
    std::string field;
    bool operator==(const _NodeFieldKey& other) const { return node_id == other.node_id && field == other.field; }
  };
  struct _NodeFieldKeyHash {
    std::size_t operator()(const _NodeFieldKey& k) const noexcept {
      return std::hash<std::string>{}(k.node_id) ^ (std::hash<std::string>{}(k.field) << 1);
    }
  };

  struct _RemoteStateKey {
    std::string peer_service_id;
    std::string remote_node_id;
    std::string remote_field;
    bool operator==(const _RemoteStateKey& other) const {
      return peer_service_id == other.peer_service_id && remote_node_id == other.remote_node_id &&
             remote_field == other.remote_field;
    }
  };

  struct _CommandBinding {
    std::string node_id;
    std::string call;
    std::string input_field;
    std::string output_field;
    std::vector<std::string> param_names;
  };

  struct _CommandDispatchState {
    bool running = false;
    std::size_t version = 0;
    json latest_value = json(nullptr);
    std::int64_t latest_ts_ms = 0;
    json latest_meta = json::object();
  };
  struct _RemoteStateKeyHash {
    std::size_t operator()(const _RemoteStateKey& k) const noexcept {
      std::size_t h1 = std::hash<std::string>{}(k.peer_service_id);
      std::size_t h2 = std::hash<std::string>{}(k.remote_node_id);
      std::size_t h3 = std::hash<std::string>{}(k.remote_field);
      return h1 ^ (h2 << 1) ^ (h3 << 2);
    }
  };

  struct _InputBuffer {
    using JsonPtr = std::shared_ptr<const json>;

    mutable std::mutex mu;
    std::deque<std::pair<JsonPtr, std::int64_t>> queue;
    JsonPtr last_seen_value;
    std::int64_t last_seen_ts_ms = 0;
    EdgeStrategy strategy = EdgeStrategy::kLatest;
    std::int64_t timeout_ms = 0;
  };

  mutable std::mutex data_mu_;
  std::unordered_map<std::string, NatsSubscription> data_subs_;
  std::unordered_map<std::string, std::unique_ptr<RuntimeSubscription>> runtime_data_subs_;
  std::unordered_map<_NodePortKey, std::shared_ptr<_InputBuffer>, _NodePortKeyHash> data_inputs_;

  struct _RouteRuntime {
    std::string to_node_id;
    std::string to_port;
    std::string from_service_id;
    std::string from_node_id;
    std::string from_port;
    EdgeStrategy strategy = EdgeStrategy::kLatest;
    std::int64_t timeout_ms = 0;
    std::shared_ptr<_InputBuffer> buf;
  };

  struct _DataRoutingSnapshot {
    std::unordered_map<std::string, std::vector<_RouteRuntime>> by_subject;
  };

  std::shared_ptr<const _DataRoutingSnapshot> data_routes_snapshot_;

  mutable std::mutex state_mu_;
  std::unordered_map<_NodeFieldKey, std::pair<json, std::int64_t>, _NodeFieldKeyHash> state_cache_;
  std::unordered_map<_NodeFieldKey, std::string, _NodeFieldKeyHash> state_access_;
  std::unordered_map<_NodeFieldKey, std::vector<_NodeFieldKey>, _NodeFieldKeyHash> intra_state_out_;
  std::unordered_map<_NodeFieldKey, _CommandBinding, _NodeFieldKeyHash> command_input_bindings_;
  std::unordered_map<std::string, _CommandBinding> command_output_bindings_;
  std::unordered_set<_NodeFieldKey, _NodeFieldKeyHash> command_hidden_fields_;
  std::unordered_map<_NodeFieldKey, _CommandDispatchState, _NodeFieldKeyHash> command_dispatch_;
  std::unordered_map<_RemoteStateKey, std::vector<_NodeFieldKey>, _RemoteStateKeyHash> cross_state_in_;
  std::unordered_set<_NodeFieldKey, _NodeFieldKeyHash> cross_state_targets_;
  std::unordered_map<std::string, std::unique_ptr<KvStore>> peer_kv_by_service_id_;
  std::unordered_map<std::string, std::unique_ptr<RuntimeSubscription>> peer_state_subs_by_service_id_;
  bool has_rungraph_ = false;

  std::thread monitor_thread_;
  mutable std::mutex monitor_wake_mu_;
  std::condition_variable monitor_wake_cv_;
  bool monitor_publish_requested_ = false;
  mutable std::mutex monitor_mu_;
  std::deque<std::pair<std::int64_t, double>> monitor_wait_ms_;
  std::deque<std::pair<std::int64_t, double>> monitor_process_ms_;
  std::deque<std::int64_t> monitor_error_ts_ms_;
  std::int64_t monitor_started_ts_ms_ = 0;
  std::uint64_t monitor_observed_ = 0;
  std::uint64_t monitor_processed_ = 0;
  std::uint64_t monitor_dropped_ = 0;
  std::string monitor_last_error_node_id_;
  std::string monitor_last_error_code_;
  std::string monitor_last_error_message_;
  std::string monitor_last_error_severity_ = "error";
  std::string monitor_last_error_fingerprint_;
  std::int64_t monitor_last_error_repeat_count_ = 0;
  std::optional<std::int64_t> monitor_last_error_ts_ms_;
  std::string monitor_current_error_node_id_;
  std::string monitor_current_error_code_;
  std::string monitor_current_error_message_;
  std::string monitor_current_error_severity_;
  std::string monitor_current_error_fingerprint_;
  std::optional<std::int64_t> monitor_current_error_ts_ms_;
};

}  // namespace f8::cppsdk
