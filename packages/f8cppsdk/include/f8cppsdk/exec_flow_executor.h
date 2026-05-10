#pragma once

#include <atomic>
#include <condition_variable>
#include <cstdint>
#include <deque>
#include <mutex>
#include <optional>
#include <string>
#include <thread>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

#include <nlohmann/json.hpp>

#include "f8cppsdk/generated/protocol_models.h"
#include "f8cppsdk/runtime_node.h"
#include "f8cppsdk/service_bus.h"

namespace f8::cppsdk {

struct ExecRouteKey {
  std::string node_id;
  std::string port;
  bool operator==(const ExecRouteKey& other) const { return node_id == other.node_id && port == other.port; }
};

struct ExecRouteKeyHash {
  std::size_t operator()(const ExecRouteKey& key) const noexcept {
    return std::hash<std::string>{}(key.node_id) ^ (std::hash<std::string>{}(key.port) << 1);
  }
};

using ExecRouteMap = std::unordered_map<ExecRouteKey, ExecRouteKey, ExecRouteKeyHash>;

ExecRouteMap validate_exec_topology_or_throw(const generated::F8RuntimeGraph& graph, const std::string& service_id);

class ExecFlowExecutor final {
 public:
  explicit ExecFlowExecutor(ServiceBus& bus);
  ~ExecFlowExecutor();

  ExecFlowExecutor(const ExecFlowExecutor&) = delete;
  ExecFlowExecutor& operator=(const ExecFlowExecutor&) = delete;
  ExecFlowExecutor(ExecFlowExecutor&&) = delete;
  ExecFlowExecutor& operator=(ExecFlowExecutor&&) = delete;

  void register_node(OperatorNode* node);
  void unregister_node(const std::string& node_id);
  void clear_nodes();

  void apply_rungraph(const nlohmann::json& graph_obj);
  void set_active(bool active);
  bool active() const { return active_.load(std::memory_order_acquire); }

  void trigger_exec_nowait(const std::string& node_id, const std::string& out_port, std::int64_t exec_id);
  void trigger_exec(const std::string& node_id, const std::string& out_port, std::int64_t exec_id);

  std::vector<std::string> current_entrypoint_node_ids() const;
  void stop_all_entrypoints();

 private:
  struct ExecTrigger {
    std::string node_id;
    std::string out_port;
    std::int64_t exec_id = 0;
    bool wait = false;
    bool done = false;
    std::condition_variable* done_cv = nullptr;
  };

  void start_worker_locked();
  void stop_worker();
  void worker_loop();
  void drain_queue_locked();
  void propagate_exec_dfs(const std::string& node_id, const std::string& out_port, std::int64_t exec_id);
  void emit_half_edge_outputs(const std::string& node_id, std::int64_t exec_id);
  void rebuild_half_out_ports(const generated::F8RuntimeGraph& graph);
  std::vector<std::string> entrypoint_node_ids_for_graph(const generated::F8RuntimeGraph& graph) const;
  void start_entrypoints_for_graph(const generated::F8RuntimeGraph& graph);
  void start_entrypoint(const std::string& node_id);
  void stop_entrypoint(const std::string& node_id);

  ServiceBus& bus_;
  std::string service_id_;
  std::atomic<bool> active_{true};
  std::atomic<bool> stop_requested_{false};

  mutable std::mutex mu_;
  std::condition_variable cv_;
  std::thread worker_;
  bool worker_running_ = false;
  std::deque<std::shared_ptr<ExecTrigger>> queue_;
  ExecRouteMap exec_out_;
  std::unordered_map<std::string, OperatorNode*> nodes_;
  std::unordered_map<std::string, std::unordered_set<std::string>> half_out_ports_;
  std::unordered_set<std::string> entrypoint_node_ids_;
  std::optional<generated::F8RuntimeGraph> graph_;
};

}  // namespace f8::cppsdk
