#include "f8cppsdk/runtime_node.h"

#include <utility>

namespace f8::cppsdk {

RuntimeNode::RuntimeNode(std::string node_id, std::vector<std::string> data_in_ports,
                         std::vector<std::string> data_out_ports, std::vector<std::string> state_fields)
    : node_id_(std::move(node_id)),
      data_in_ports_(std::move(data_in_ports)),
      data_out_ports_(std::move(data_out_ports)),
      state_fields_(std::move(state_fields)) {}

nlohmann::json RuntimeNode::validate_state(const std::string& field, const nlohmann::json& value, std::int64_t ts_ms,
                                           const nlohmann::json& meta) {
  (void)field;
  (void)ts_ms;
  (void)meta;
  return value;
}

void RuntimeNode::on_state(const std::string& field, const nlohmann::json& value, std::int64_t ts_ms,
                           const nlohmann::json& meta) {
  (void)field;
  (void)value;
  (void)ts_ms;
  (void)meta;
}

void RuntimeNode::on_data(const std::string& port, const nlohmann::json& value, std::int64_t ts_ms,
                          const nlohmann::json& meta) {
  (void)port;
  (void)value;
  (void)ts_ms;
  (void)meta;
}

void RuntimeNode::on_lifecycle(bool active, const nlohmann::json& meta) {
  (void)active;
  (void)meta;
}

bool RuntimeNode::emit(const std::string& port, const nlohmann::json& value, std::int64_t ts_ms) {
  if (bus_ == nullptr) return false;
  return bus_->emit_data(node_id_, port, value, ts_ms);
}

std::optional<nlohmann::json> RuntimeNode::pull(const std::string& port) const {
  if (bus_ == nullptr) return std::nullopt;
  return bus_->pull_data(node_id_, port);
}

bool RuntimeNode::set_state(const std::string& field, const nlohmann::json& value, std::int64_t ts_ms) {
  if (bus_ == nullptr) return false;
  return bus_->publish_state(node_id_, field, value, "runtime", nlohmann::json::object(), ts_ms, "runtime");
}

void RuntimeNode::report_error(const std::string& code, const std::string& message, const std::string& severity,
                               const std::string& fingerprint, std::int64_t ts_ms) {
  if (bus_ == nullptr) return;
  bus_->report_error(node_id_, code, message, severity, fingerprint, ts_ms);
}

void RuntimeNode::clear_error(const std::string& fingerprint, std::int64_t ts_ms) {
  if (bus_ == nullptr) return;
  bus_->clear_error(node_id_, fingerprint, ts_ms);
}

OperatorNode::OperatorNode(std::string node_id, std::vector<std::string> data_in_ports,
                           std::vector<std::string> data_out_ports, std::vector<std::string> state_fields,
                           std::vector<std::string> exec_in_ports, std::vector<std::string> exec_out_ports)
    : RuntimeNode(std::move(node_id), std::move(data_in_ports), std::move(data_out_ports), std::move(state_fields)),
      exec_in_ports_(std::move(exec_in_ports)),
      exec_out_ports_(std::move(exec_out_ports)) {}

std::vector<std::string> OperatorNode::on_exec(std::int64_t exec_id, const std::string& in_port) {
  (void)exec_id;
  (void)in_port;
  return {};
}

}  // namespace f8::cppsdk
