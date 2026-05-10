#pragma once

#include <memory>
#include <string>
#include <unordered_map>
#include <vector>

#include <nlohmann/json.hpp>

#include "f8cppsdk/capabilities.h"
#include "f8cppsdk/generated/protocol_models.h"
#include "f8cppsdk/runtime_node_registry.h"
#include "f8cppsdk/service_bus.h"

namespace f8::cppsdk {

class ServiceHost final : public LifecycleNode,
                          public StatefulNode,
                          public DataReceivableNode,
                          public SetStateHandlerNode {
 public:
  ServiceHost(ServiceBus& bus, RuntimeNodeRegistry& registry, std::string service_class);
  ~ServiceHost() override;

  ServiceHost(const ServiceHost&) = delete;
  ServiceHost& operator=(const ServiceHost&) = delete;
  ServiceHost(ServiceHost&&) = delete;
  ServiceHost& operator=(ServiceHost&&) = delete;

  void start();
  void stop();
  bool apply_rungraph(const nlohmann::json& graph_obj, std::string& error_code, std::string& error_message);

  RuntimeNode* get_node(const std::string& node_id) const;
  std::vector<OperatorNode*> operator_nodes() const;

  void on_lifecycle(bool active, const nlohmann::json& meta) override;
  void on_state(const std::string& node_id, const std::string& field, const nlohmann::json& value, std::int64_t ts_ms,
                const nlohmann::json& meta) override;
  void on_data(const std::string& node_id, const std::string& port, const nlohmann::json& value, std::int64_t ts_ms,
               const nlohmann::json& meta) override;
  bool on_set_state(const std::string& node_id, const std::string& field, const nlohmann::json& value,
                    const nlohmann::json& meta, std::string& error_code, std::string& error_message) override;

 private:
  static std::vector<std::string> data_port_names(const std::optional<std::vector<generated::F8DataPortSpec>>& ports);
  static std::vector<std::string> state_field_names(const std::optional<std::vector<generated::F8StateSpec>>& fields);
  static std::vector<std::string> string_vector(const std::optional<std::vector<std::string>>& values);
  static nlohmann::json initial_state(const generated::F8RuntimeNode& node);
  static bool needs_recreate(const OperatorNode& node, const generated::F8RuntimeNode& snapshot);

  void ensure_service_node();
  void close_node(RuntimeNode& node);

  ServiceBus& bus_;
  RuntimeNodeRegistry& registry_;
  std::string service_class_;
  bool started_ = false;
  std::unique_ptr<RuntimeNode> service_node_;
  std::unordered_map<std::string, std::unique_ptr<OperatorNode>> operator_nodes_;
};

}  // namespace f8::cppsdk
