#pragma once

#include <cstdint>
#include <optional>
#include <string>
#include <utility>
#include <vector>

#include <nlohmann/json.hpp>

#include "f8cppsdk/capabilities.h"
#include "f8cppsdk/service_bus.h"

namespace f8::cppsdk {

class RuntimeNode {
 public:
  RuntimeNode(std::string node_id, std::vector<std::string> data_in_ports = {},
              std::vector<std::string> data_out_ports = {}, std::vector<std::string> state_fields = {});
  virtual ~RuntimeNode() = default;

  RuntimeNode(const RuntimeNode&) = delete;
  RuntimeNode& operator=(const RuntimeNode&) = delete;
  RuntimeNode(RuntimeNode&&) = delete;
  RuntimeNode& operator=(RuntimeNode&&) = delete;

  const std::string& node_id() const { return node_id_; }

  const std::vector<std::string>& data_in_ports() const { return data_in_ports_; }
  const std::vector<std::string>& data_out_ports() const { return data_out_ports_; }
  const std::vector<std::string>& state_fields() const { return state_fields_; }

  void set_data_in_ports(std::vector<std::string> ports) { data_in_ports_ = std::move(ports); }
  void set_data_out_ports(std::vector<std::string> ports) { data_out_ports_ = std::move(ports); }
  void set_state_fields(std::vector<std::string> fields) { state_fields_ = std::move(fields); }

  virtual void attach(ServiceBus* bus) { bus_ = bus; }
  virtual void detach() { bus_ = nullptr; }

  virtual nlohmann::json validate_state(const std::string& field, const nlohmann::json& value, std::int64_t ts_ms,
                                        const nlohmann::json& meta);
  virtual void on_state(const std::string& field, const nlohmann::json& value, std::int64_t ts_ms,
                        const nlohmann::json& meta);
  virtual void on_data(const std::string& port, const nlohmann::json& value, std::int64_t ts_ms,
                       const nlohmann::json& meta);
  virtual void on_lifecycle(bool active, const nlohmann::json& meta);

 protected:
  bool emit(const std::string& port, const nlohmann::json& value, std::int64_t ts_ms = 0);
  std::optional<nlohmann::json> pull(const std::string& port) const;
  std::optional<nlohmann::json> pull(const std::string& port, std::int64_t ctx_id) const;
  bool set_state(const std::string& field, const nlohmann::json& value, std::int64_t ts_ms = 0);
  void report_error(const std::string& code, const std::string& message, const std::string& severity = "error",
                    const std::string& fingerprint = "", std::int64_t ts_ms = 0);
  void clear_error(const std::string& fingerprint = "", std::int64_t ts_ms = 0);

  ServiceBus* bus() const { return bus_; }

 private:
  std::string node_id_;
  std::vector<std::string> data_in_ports_;
  std::vector<std::string> data_out_ports_;
  std::vector<std::string> state_fields_;
  ServiceBus* bus_ = nullptr;
};

class ServiceNode : public RuntimeNode {
 public:
  using RuntimeNode::RuntimeNode;
};

class OperatorNode : public RuntimeNode, public ExecutableNode {
 public:
  OperatorNode(std::string node_id, std::vector<std::string> data_in_ports = {},
               std::vector<std::string> data_out_ports = {}, std::vector<std::string> state_fields = {},
               std::vector<std::string> exec_in_ports = {}, std::vector<std::string> exec_out_ports = {});

  const std::vector<std::string>& exec_in_ports() const { return exec_in_ports_; }
  const std::vector<std::string>& exec_out_ports() const { return exec_out_ports_; }
  void set_exec_in_ports(std::vector<std::string> ports) { exec_in_ports_ = std::move(ports); }
  void set_exec_out_ports(std::vector<std::string> ports) { exec_out_ports_ = std::move(ports); }

  std::vector<std::string> on_exec(std::int64_t exec_id, const std::string& in_port) override;

 private:
  std::vector<std::string> exec_in_ports_;
  std::vector<std::string> exec_out_ports_;
};

}  // namespace f8::cppsdk
