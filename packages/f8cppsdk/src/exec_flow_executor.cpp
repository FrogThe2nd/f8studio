#include "f8cppsdk/exec_flow_executor.h"

#include <algorithm>
#include <stdexcept>

#include <spdlog/spdlog.h>

#include "f8cppsdk/f8_naming.h"
#include "f8cppsdk/time_utils.h"

namespace f8::cppsdk {

namespace {

std::string node_or_service_id(const std::optional<std::string>& node_id, const std::string& service_id) {
  if (node_id.has_value() && !node_id->empty()) return node_id.value();
  return service_id;
}

void ensure_exec_acyclic(const std::unordered_map<std::string, std::unordered_set<std::string>>& adj) {
  std::unordered_set<std::string> visiting;
  std::unordered_set<std::string> visited;
  std::vector<std::string> stack;

  std::function<void(const std::string&)> visit = [&](const std::string& node) {
    if (visited.find(node) != visited.end()) return;
    if (visiting.find(node) != visiting.end()) {
      throw std::invalid_argument("exec graph has a cycle at node: " + node);
    }
    visiting.insert(node);
    stack.push_back(node);
    const auto it = adj.find(node);
    if (it != adj.end()) {
      std::vector<std::string> next(it->second.begin(), it->second.end());
      std::sort(next.begin(), next.end());
      for (const auto& child : next) {
        visit(child);
      }
    }
    stack.pop_back();
    visiting.erase(node);
    visited.insert(node);
  };

  std::vector<std::string> roots;
  roots.reserve(adj.size());
  for (const auto& item : adj) {
    roots.push_back(item.first);
  }
  std::sort(roots.begin(), roots.end());
  for (const auto& root : roots) {
    visit(root);
  }
}

}  // namespace

ExecRouteMap validate_exec_topology_or_throw(const generated::F8RuntimeGraph& graph, const std::string& service_id) {
  const std::string sid = ensure_token(service_id, "service_id");
  ExecRouteMap out;
  std::unordered_set<ExecRouteKey, ExecRouteKeyHash> in_seen;
  std::unordered_map<std::string, std::unordered_set<std::string>> adj;

  for (const auto& edge : graph.edges.value_or(std::vector<generated::F8Edge>{})) {
    if (edge.kind != generated::F8EdgeKindEnum::exec) continue;
    if (edge.fromServiceId != sid || edge.toServiceId != sid) continue;
    if (!edge.fromOperatorId.has_value() || !edge.toOperatorId.has_value()) continue;

    ExecRouteKey from{edge.fromOperatorId.value(), edge.fromPort};
    ExecRouteKey to{edge.toOperatorId.value(), edge.toPort};
    if (out.find(from) != out.end()) {
      throw std::invalid_argument("exec out port must be single-connected: " + from.node_id + "." + from.port);
    }
    if (in_seen.find(to) != in_seen.end()) {
      throw std::invalid_argument("exec in port must be single-connected: " + to.node_id + "." + to.port);
    }
    out[from] = to;
    in_seen.insert(to);
    adj[from.node_id].insert(to.node_id);
  }

  ensure_exec_acyclic(adj);
  for (const auto& node : graph.nodes.value_or(std::vector<generated::F8RuntimeNode>{})) {
    if (node.serviceId != sid) continue;
    const auto ins = node.execInPorts.value_or(std::vector<std::string>{});
    const auto outs = node.execOutPorts.value_or(std::vector<std::string>{});
    if (ins.empty() && !outs.empty()) {
      (void)ensure_token(node.nodeId, "node_id");
    }
  }
  return out;
}

ExecFlowExecutor::ExecFlowExecutor(ServiceBus& bus) : bus_(bus), service_id_(ensure_token(bus.config().service_id, "service_id")) {
  bus_.set_data_pull_resolver([this](const std::string& node_id, const std::string& port, std::int64_t ctx_id) {
    return resolve_data_pull(node_id, port, ctx_id);
  });
}

ExecFlowExecutor::~ExecFlowExecutor() {
  bus_.set_data_pull_resolver({});
  set_active(false);
  stop_worker();
}

void ExecFlowExecutor::register_node(OperatorNode* node) {
  if (node == nullptr) return;
  std::lock_guard<std::mutex> lock(mu_);
  nodes_[node->node_id()] = node;
}

void ExecFlowExecutor::unregister_node(const std::string& node_id) {
  std::lock_guard<std::mutex> lock(mu_);
  nodes_.erase(node_id);
}

void ExecFlowExecutor::clear_nodes() {
  std::lock_guard<std::mutex> lock(mu_);
  nodes_.clear();
}

void ExecFlowExecutor::apply_rungraph(const nlohmann::json& graph_obj) {
  generated::F8RuntimeGraph graph;
  generated::ParseError parse_error;
  if (!generated::parse_F8RuntimeGraph(graph_obj, graph, parse_error)) {
    throw std::invalid_argument(parse_error.message.empty() ? "invalid rungraph" : parse_error.message);
  }

  stop_worker();
  stop_all_entrypoints();
  {
    std::lock_guard<std::mutex> lock(mu_);
    drain_queue_locked();
    exec_out_ = validate_exec_topology_or_throw(graph, service_id_);
    graph_ = graph;
    rebuild_half_out_ports(graph);
    rebuild_local_data_routes(graph);
  }

  if (active()) {
    {
      std::lock_guard<std::mutex> lock(mu_);
      start_worker_locked();
    }
    start_entrypoints_for_graph(graph);
  }
}

void ExecFlowExecutor::set_active(bool active) {
  const bool old = active_.exchange(active, std::memory_order_acq_rel);
  if (old == active) return;
  if (!active) {
    stop_worker();
    stop_all_entrypoints();
    std::lock_guard<std::mutex> lock(mu_);
    drain_queue_locked();
    return;
  }

  std::optional<generated::F8RuntimeGraph> graph;
  {
    std::lock_guard<std::mutex> lock(mu_);
    start_worker_locked();
    graph = graph_;
  }
  if (graph.has_value()) {
    start_entrypoints_for_graph(graph.value());
  }
}

void ExecFlowExecutor::trigger_exec_nowait(const std::string& node_id, const std::string& out_port, std::int64_t exec_id) {
  if (!active()) return;
  auto trigger = std::make_shared<ExecTrigger>();
  trigger->node_id = node_id;
  trigger->out_port = out_port;
  trigger->exec_id = exec_id;

  std::lock_guard<std::mutex> lock(mu_);
  if (exec_out_.find({node_id, out_port}) == exec_out_.end()) return;
  start_worker_locked();
  queue_.push_back(trigger);
  cv_.notify_one();
}

void ExecFlowExecutor::trigger_exec(const std::string& node_id, const std::string& out_port, std::int64_t exec_id) {
  if (!active()) return;
  std::condition_variable done_cv;
  auto trigger = std::make_shared<ExecTrigger>();
  trigger->node_id = node_id;
  trigger->out_port = out_port;
  trigger->exec_id = exec_id;
  trigger->wait = true;
  trigger->done_cv = &done_cv;

  std::unique_lock<std::mutex> lock(mu_);
  if (exec_out_.find({node_id, out_port}) == exec_out_.end()) return;
  start_worker_locked();
  queue_.push_back(trigger);
  cv_.notify_one();
  done_cv.wait(lock, [&]() { return trigger->done; });
}

std::vector<std::string> ExecFlowExecutor::current_entrypoint_node_ids() const {
  std::lock_guard<std::mutex> lock(mu_);
  std::vector<std::string> out(entrypoint_node_ids_.begin(), entrypoint_node_ids_.end());
  std::sort(out.begin(), out.end());
  return out;
}

void ExecFlowExecutor::stop_all_entrypoints() {
  const auto ids = current_entrypoint_node_ids();
  for (const auto& node_id : ids) {
    stop_entrypoint(node_id);
  }
}

void ExecFlowExecutor::start_worker_locked() {
  if (worker_running_) return;
  stop_requested_.store(false, std::memory_order_release);
  worker_running_ = true;
  worker_ = std::thread([this]() { worker_loop(); });
}

void ExecFlowExecutor::stop_worker() {
  {
    std::lock_guard<std::mutex> lock(mu_);
    if (!worker_running_) return;
    stop_requested_.store(true, std::memory_order_release);
    cv_.notify_all();
  }
  if (worker_.joinable()) {
    worker_.join();
  }
  {
    std::lock_guard<std::mutex> lock(mu_);
    worker_running_ = false;
    stop_requested_.store(false, std::memory_order_release);
  }
}

void ExecFlowExecutor::worker_loop() {
  while (true) {
    std::shared_ptr<ExecTrigger> trigger;
    {
      std::unique_lock<std::mutex> lock(mu_);
      cv_.wait(lock, [&]() { return stop_requested_.load(std::memory_order_acquire) || !queue_.empty(); });
      if (stop_requested_.load(std::memory_order_acquire)) break;
      trigger = queue_.front();
      queue_.pop_front();
    }
    if (trigger && active() && bus_.active()) {
      propagate_exec_dfs(trigger->node_id, trigger->out_port, trigger->exec_id);
    }
    if (trigger && trigger->wait) {
      std::lock_guard<std::mutex> lock(mu_);
      trigger->done = true;
      if (trigger->done_cv != nullptr) trigger->done_cv->notify_all();
    }
  }
}

void ExecFlowExecutor::drain_queue_locked() {
  for (auto& trigger : queue_) {
    if (trigger && trigger->wait) {
      trigger->done = true;
      if (trigger->done_cv != nullptr) trigger->done_cv->notify_all();
    }
  }
  queue_.clear();
}

void ExecFlowExecutor::propagate_exec_dfs(const std::string& node_id, const std::string& out_port, std::int64_t exec_id) {
  std::vector<ExecRouteKey> stack;
  {
    std::lock_guard<std::mutex> lock(mu_);
    const auto it = exec_out_.find({node_id, out_port});
    if (it != exec_out_.end()) stack.push_back(it->second);
  }

  while (!stack.empty()) {
    ExecRouteKey target = stack.back();
    stack.pop_back();

    OperatorNode* node = nullptr;
    {
      std::lock_guard<std::mutex> lock(mu_);
      const auto node_it = nodes_.find(target.node_id);
      if (node_it != nodes_.end()) node = node_it->second;
    }
    if (node == nullptr) continue;

    std::vector<std::string> out_ports;
    try {
      out_ports = node->on_exec(exec_id, target.port);
    } catch (const std::exception& exc) {
      bus_.report_error(target.node_id, "EXEC_CALLBACK_FAILED", exc.what(), "error", "exec:" + target.node_id);
      continue;
    } catch (...) {
      bus_.report_error(target.node_id, "EXEC_CALLBACK_FAILED", "on_exec threw unknown exception", "error",
                        "exec:" + target.node_id);
      continue;
    }

    emit_half_edge_outputs(target.node_id, exec_id);

    std::lock_guard<std::mutex> lock(mu_);
    for (auto it = out_ports.rbegin(); it != out_ports.rend(); ++it) {
      const auto route_it = exec_out_.find({target.node_id, *it});
      if (route_it != exec_out_.end()) {
        stack.push_back(route_it->second);
      }
    }
  }
}

void ExecFlowExecutor::emit_half_edge_outputs(const std::string& node_id, std::int64_t exec_id) {
  std::vector<std::string> ports;
  OperatorNode* node = nullptr;
  {
    std::lock_guard<std::mutex> lock(mu_);
    const auto ports_it = half_out_ports_.find(node_id);
    if (ports_it != half_out_ports_.end()) {
      ports.assign(ports_it->second.begin(), ports_it->second.end());
      std::sort(ports.begin(), ports.end());
    }
    const auto node_it = nodes_.find(node_id);
    if (node_it != nodes_.end()) node = node_it->second;
  }
  if (ports.empty() || node == nullptr) return;
  auto* computable = dynamic_cast<ComputableNode*>(node);
  if (computable == nullptr) return;
  for (const auto& port : ports) {
    try {
      const nlohmann::json value = computable->compute_output(port, exec_id);
      if (!value.is_null()) {
        (void)bus_.emit_data(node_id, port, value, now_ms());
      }
    } catch (const std::exception& exc) {
      bus_.report_error(node_id, "COMPUTE_OUTPUT_FAILED", exc.what(), "error", "compute:" + node_id + ":" + port);
    }
  }
}

void ExecFlowExecutor::rebuild_half_out_ports(const generated::F8RuntimeGraph& graph) {
  half_out_ports_.clear();
  for (const auto& edge : graph.edges.value_or(std::vector<generated::F8Edge>{})) {
    if (edge.kind != generated::F8EdgeKindEnum::data) continue;
    if (!edge.direction.has_value() || edge.direction.value() != generated::F8EdgeDirection::out) continue;
    if (edge.fromServiceId != service_id_) continue;
    const std::string from_node = node_or_service_id(edge.fromOperatorId, edge.fromServiceId);
    if (from_node.empty() || edge.fromPort.empty()) continue;
    half_out_ports_[from_node].insert(edge.fromPort);
  }
}

void ExecFlowExecutor::rebuild_local_data_routes(const generated::F8RuntimeGraph& graph) {
  local_data_in_.clear();
  for (const auto& edge : graph.edges.value_or(std::vector<generated::F8Edge>{})) {
    if (edge.kind != generated::F8EdgeKindEnum::data) continue;
    if (edge.fromServiceId != service_id_ || edge.toServiceId != service_id_) continue;
    if (edge.direction.has_value()) continue;
    const std::string from_node = node_or_service_id(edge.fromOperatorId, edge.fromServiceId);
    const std::string to_node = node_or_service_id(edge.toOperatorId, edge.toServiceId);
    if (from_node.empty() || to_node.empty() || edge.fromPort.empty() || edge.toPort.empty()) continue;
    const ExecRouteKey to{to_node, edge.toPort};
    if (local_data_in_.find(to) == local_data_in_.end()) {
      local_data_in_[to] = ExecRouteKey{from_node, edge.fromPort};
    }
  }
}

std::optional<nlohmann::json> ExecFlowExecutor::resolve_data_pull(const std::string& node_id,
                                                                  const std::string& port,
                                                                  std::int64_t ctx_id) {
  thread_local std::vector<ExecRouteKey> resolving;
  const ExecRouteKey target{node_id, port};
  if (std::find(resolving.begin(), resolving.end(), target) != resolving.end()) {
    bus_.report_error(node_id, "DATA_PULL_CYCLE", "cycle while resolving local data input", "error",
                      "data_pull_cycle:" + node_id + ":" + port);
    return std::nullopt;
  }

  ExecRouteKey source;
  OperatorNode* node = nullptr;
  {
    std::lock_guard<std::mutex> lock(mu_);
    const auto route_it = local_data_in_.find(target);
    if (route_it == local_data_in_.end()) return std::nullopt;
    source = route_it->second;
    const auto node_it = nodes_.find(source.node_id);
    if (node_it != nodes_.end()) node = node_it->second;
  }
  if (node == nullptr) return std::nullopt;
  auto* computable = dynamic_cast<ComputableNode*>(node);
  if (computable == nullptr) return std::nullopt;

  resolving.push_back(target);
  try {
    nlohmann::json value = computable->compute_output(source.port, ctx_id);
    resolving.pop_back();
    bool should_emit = false;
    {
      std::lock_guard<std::mutex> lock(mu_);
      const auto ports_it = half_out_ports_.find(source.node_id);
      should_emit = ports_it != half_out_ports_.end() && ports_it->second.find(source.port) != ports_it->second.end();
    }
    if (should_emit && !value.is_null()) {
      (void)bus_.emit_data(source.node_id, source.port, value, now_ms());
    }
    return value;
  } catch (const std::exception& exc) {
    resolving.pop_back();
    bus_.report_error(source.node_id, "COMPUTE_OUTPUT_FAILED", exc.what(), "error",
                      "compute:" + source.node_id + ":" + source.port);
  } catch (...) {
    resolving.pop_back();
    bus_.report_error(source.node_id, "COMPUTE_OUTPUT_FAILED", "compute_output threw unknown exception", "error",
                      "compute:" + source.node_id + ":" + source.port);
  }
  return std::nullopt;
}

std::vector<std::string> ExecFlowExecutor::entrypoint_node_ids_for_graph(const generated::F8RuntimeGraph& graph) const {
  std::vector<std::string> out;
  for (const auto& node : graph.nodes.value_or(std::vector<generated::F8RuntimeNode>{})) {
    if (node.serviceId != service_id_) continue;
    const auto ins = node.execInPorts.value_or(std::vector<std::string>{});
    const auto outs = node.execOutPorts.value_or(std::vector<std::string>{});
    if (ins.empty() && !outs.empty()) out.push_back(node.nodeId);
  }
  std::sort(out.begin(), out.end());
  return out;
}

void ExecFlowExecutor::start_entrypoints_for_graph(const generated::F8RuntimeGraph& graph) {
  for (const auto& node_id : entrypoint_node_ids_for_graph(graph)) {
    start_entrypoint(node_id);
  }
}

void ExecFlowExecutor::start_entrypoint(const std::string& node_id) {
  if (!active()) return;
  OperatorNode* node = nullptr;
  {
    std::lock_guard<std::mutex> lock(mu_);
    if (entrypoint_node_ids_.find(node_id) != entrypoint_node_ids_.end()) return;
    const auto it = nodes_.find(node_id);
    if (it != nodes_.end()) node = it->second;
  }
  auto* entrypoint = dynamic_cast<EntrypointNode*>(node);
  if (entrypoint == nullptr) {
    throw std::invalid_argument("exec entrypoint node must implement EntrypointNode: " + node_id);
  }
  EntrypointContext ctx(node_id, [this, node_id](const std::string& out_port, std::int64_t exec_id) {
    trigger_exec_nowait(node_id, out_port, exec_id);
  });
  entrypoint->start_entrypoint(ctx);
  std::lock_guard<std::mutex> lock(mu_);
  entrypoint_node_ids_.insert(node_id);
}

void ExecFlowExecutor::stop_entrypoint(const std::string& node_id) {
  OperatorNode* node = nullptr;
  {
    std::lock_guard<std::mutex> lock(mu_);
    const auto active_it = entrypoint_node_ids_.find(node_id);
    if (active_it == entrypoint_node_ids_.end()) return;
    entrypoint_node_ids_.erase(active_it);
    const auto node_it = nodes_.find(node_id);
    if (node_it != nodes_.end()) node = node_it->second;
  }
  if (auto* entrypoint = dynamic_cast<EntrypointNode*>(node)) {
    entrypoint->stop_entrypoint();
  }
}

}  // namespace f8::cppsdk
