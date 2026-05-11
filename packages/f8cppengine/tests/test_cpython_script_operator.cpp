#include <cstdint>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

#include <nlohmann/json.hpp>

#include "f8cppengine/operators.h"
#include "f8cppsdk/exec_flow_executor.h"
#include "f8cppsdk/runtime_node_registry.h"
#include "f8cppsdk/service_bus.h"
#include "f8cppsdk/service_host.h"

using json = nlohmann::json;

namespace {

constexpr const char* kServiceId = "svc";
constexpr const char* kServiceClass = "f8.cppengine";

class ConstantNode final : public f8::cppsdk::OperatorNode, public f8::cppsdk::ComputableNode {
 public:
  ConstantNode(std::string node_id, json value)
      : OperatorNode(std::move(node_id), {}, {"out"}, {}, {}, {}), value_(std::move(value)) {}

  json compute_output(const std::string& port, std::int64_t ctx_id) override {
    last_ctx_id = ctx_id;
    if (port != "out") return nullptr;
    return value_;
  }

  std::int64_t last_ctx_id = 0;

 private:
  json value_;
};

class PullingSinkNode final : public f8::cppsdk::OperatorNode {
 public:
  explicit PullingSinkNode(std::string node_id)
      : OperatorNode(std::move(node_id), {"in"}, {}, {}, {"exec"}, {}) {}

  std::vector<std::string> on_exec(std::int64_t exec_id, const std::string& in_port) override {
    (void)in_port;
    last_exec_id = exec_id;
    last_value = pull("in", exec_id).value_or(nullptr);
    return {};
  }

  std::int64_t last_exec_id = 0;
  json last_value = nullptr;
};

json port_spec(const std::string& name) {
  return json{{"name", name}, {"valueSchema", json{{"type", "any"}}}};
}

json runtime_node(const std::string& node_id, const std::string& operator_class, json data_in_ports,
                  json data_out_ports, json exec_in_ports, json exec_out_ports, json state_fields = json::array(),
                  json state_values = json::object()) {
  return json{{"nodeId", node_id},
              {"serviceId", kServiceId},
              {"serviceClass", kServiceClass},
              {"operatorClass", operator_class},
              {"dataInPorts", std::move(data_in_ports)},
              {"dataOutPorts", std::move(data_out_ports)},
              {"execInPorts", std::move(exec_in_ports)},
              {"execOutPorts", std::move(exec_out_ports)},
              {"stateFields", std::move(state_fields)},
              {"stateValues", std::move(state_values)}};
}

json data_edge(const std::string& edge_id, const std::string& from_node, const std::string& from_port,
               const std::string& to_node, const std::string& to_port) {
  return json{{"edgeId", edge_id},
              {"kind", "data"},
              {"fromServiceId", kServiceId},
              {"fromOperatorId", from_node},
              {"fromPort", from_port},
              {"toServiceId", kServiceId},
              {"toOperatorId", to_node},
              {"toPort", to_port}};
}

json exec_edge(const std::string& edge_id, const std::string& from_node, const std::string& from_port,
               const std::string& to_node, const std::string& to_port) {
  return json{{"edgeId", edge_id},
              {"kind", "exec"},
              {"fromServiceId", kServiceId},
              {"fromOperatorId", from_node},
              {"fromPort", from_port},
              {"toServiceId", kServiceId},
              {"toOperatorId", to_node},
              {"toPort", to_port}};
}

}  // namespace

void expect(bool condition, const std::string& message) {
  if (!condition) throw std::runtime_error(message);
}

void run_cpython_script_operator_smoke() {
  f8::cppsdk::ServiceBus::Config cfg;
  cfg.service_id = kServiceId;
  cfg.service_class = kServiceClass;
  cfg.bus_backend = f8::cppsdk::BusBackend::kMem;
  f8::cppsdk::ServiceBus bus(cfg);

  f8::cppsdk::RuntimeNodeRegistry registry;
  f8::cppengine::register_cppengine_specs(registry);
  registry.register_operator_spec(json{{"specKind", "operator"},
                                       {"serviceClass", kServiceClass},
                                       {"operatorClass", "f8.test_constant"},
                                       {"label", "Test Constant"}},
                                  true);
  registry.register_operator_factory(
      kServiceClass, "f8.test_constant",
      [](const std::string& node_id, const f8::cppsdk::generated::F8RuntimeNode& node, const json& initial_state) {
        (void)node;
        const auto value_it = initial_state.find("value");
        const json value = value_it == initial_state.end() ? json(nullptr) : *value_it;
        return std::make_unique<ConstantNode>(node_id, value);
      },
      true);
  registry.register_operator_spec(json{{"specKind", "operator"},
                                       {"serviceClass", kServiceClass},
                                       {"operatorClass", "f8.test_sink"},
                                       {"label", "Test Sink"}},
                                  true);
  registry.register_operator_factory(
      kServiceClass, "f8.test_sink",
      [](const std::string& node_id, const f8::cppsdk::generated::F8RuntimeNode& node, const json& initial_state) {
        (void)node;
        (void)initial_state;
        return std::make_unique<PullingSinkNode>(node_id);
      },
      true);

  f8::cppsdk::ServiceHost host(bus, registry, kServiceClass);
  f8::cppsdk::ExecFlowExecutor executor(bus);
  host.start();

  const std::string code =
      "def onExec(ctx, exec_in, inputs):\n"
      "    value = inputs.get('msg')\n"
      "    return {'outputs': {'out': {'seen': value, 'node': ctx.node_id}}, 'exec': ['exec']}\n";
  json graph{{"graphId", "g"},
             {"revision", "r"},
             {"nodes",
              json::array({runtime_node("source", "f8.test_constant", json::array(), json::array({port_spec("out")}),
                                        json::array(), json::array(),
                                        json::array({json{{"name", "value"},
                                                          {"access", "rw"},
                                                          {"valueSchema", json{{"type", "any"}}}}}),
                                        json{{"value", 41}}),
                           runtime_node("script", "f8.cpython_script", json::array({port_spec("msg")}),
                                        json::array({port_spec("out")}), json::array({"exec"}), json::array({"exec"}),
                                        json::array({json{{"name", "code"},
                                                          {"access", "rw"},
                                                          {"valueSchema", json{{"type", "string"}}}}}),
                                        json{{"code", code}}),
                           runtime_node("sink", "f8.test_sink", json::array({port_spec("in")}), json::array(),
                                        json::array({"exec"}), json::array())})},
             {"edges", json::array({data_edge("d1", "source", "out", "script", "msg"),
                                     data_edge("d2", "script", "out", "sink", "in"),
                                     exec_edge("e1", "script", "exec", "sink", "exec")})}};

  std::string error_code;
  std::string error_message;
  expect(host.apply_rungraph(graph, error_code, error_message),
         "apply_rungraph failed: " + error_code + ": " + error_message);
  executor.clear_nodes();
  for (f8::cppsdk::OperatorNode* node : host.operator_nodes()) {
    executor.register_node(node);
  }
  executor.apply_rungraph(graph);
  executor.trigger_exec("script", "exec", 77);

  auto* sink = dynamic_cast<PullingSinkNode*>(host.get_node("sink"));
  expect(sink != nullptr, "sink node was not created");
  expect(sink->last_exec_id == 77, "sink did not receive expected exec id");
  expect(sink->last_value == (json{{"seen", 41}, {"node", "script"}}),
         "sink received unexpected script output: " + sink->last_value.dump());

  auto* source = dynamic_cast<ConstantNode*>(host.get_node("source"));
  expect(source != nullptr, "source node was not created");
  expect(source->last_ctx_id == 77, "source was not pulled with the exec context id");
}

void run_script_placeholder_operator_smoke() {
  f8::cppsdk::ServiceBus::Config cfg;
  cfg.service_id = kServiceId;
  cfg.service_class = kServiceClass;
  cfg.bus_backend = f8::cppsdk::BusBackend::kMem;
  f8::cppsdk::ServiceBus bus(cfg);

  f8::cppsdk::RuntimeNodeRegistry registry;
  f8::cppengine::register_cppengine_specs(registry);

  f8::cppsdk::ServiceHost host(bus, registry, kServiceClass);
  host.start();

  json graph{{"graphId", "g"},
             {"revision", "r"},
             {"nodes",
              json::array({runtime_node("lua", "f8.lua_script", json::array({port_spec("msg")}),
                                        json::array({port_spec("out")}), json::array({"exec"}), json::array({"exec"})),
                           runtime_node("angelscript", "f8.angelscript", json::array({port_spec("msg")}),
                                        json::array({port_spec("out")}), json::array({"exec"}), json::array({"exec"}))})},
             {"edges", json::array()}};

  std::string error_code;
  std::string error_message;
  expect(host.apply_rungraph(graph, error_code, error_message),
         "apply_rungraph failed: " + error_code + ": " + error_message);

  auto* lua = dynamic_cast<f8::cppsdk::ComputableNode*>(host.get_node("lua"));
  expect(lua != nullptr, "lua_script must implement ComputableNode for external viz auto-sampling");
  expect(lua->compute_output("out", 123).is_null(), "lua_script placeholder output should be null until runtime is linked");

  auto* angelscript = dynamic_cast<f8::cppsdk::ComputableNode*>(host.get_node("angelscript"));
  expect(angelscript != nullptr, "angelscript must implement ComputableNode for external viz auto-sampling");
  expect(angelscript->compute_output("out", 124).is_null(),
         "angelscript placeholder output should be null until runtime is linked");
}

void run_all_data_output_operators_are_computable_smoke() {
  f8::cppsdk::ServiceBus::Config cfg;
  cfg.service_id = kServiceId;
  cfg.service_class = kServiceClass;
  cfg.bus_backend = f8::cppsdk::BusBackend::kMem;
  f8::cppsdk::ServiceBus bus(cfg);

  f8::cppsdk::RuntimeNodeRegistry registry;
  f8::cppengine::register_cppengine_specs(registry);
  const json describe = registry.describe(kServiceClass);

  f8::cppsdk::ServiceHost host(bus, registry, kServiceClass);
  host.start();

  json nodes = json::array();
  int index = 0;
  for (const auto& spec : describe.value("operators", json::array())) {
    const json data_out = spec.value("dataOutPorts", json::array());
    if (!data_out.is_array() || data_out.empty()) continue;

    const std::string operator_class = spec.value("operatorClass", "");
    expect(!operator_class.empty(), "operator with dataOutPorts is missing operatorClass");
    json data_in = spec.value("dataInPorts", json::array());
    json exec_in = spec.value("execInPorts", json::array());
    json exec_out = spec.value("execOutPorts", json::array());
    json states = spec.value("stateFields", json::array());
    json state_values = json::object();
    for (const auto& field : states) {
      const std::string name = field.value("name", "");
      if (name.empty()) continue;
      const auto schema_it = field.find("valueSchema");
      if (schema_it == field.end() || !schema_it->is_object()) continue;
      const auto default_it = schema_it->find("default");
      if (default_it != schema_it->end()) {
        state_values[name] = *default_it;
      }
    }
    nodes.push_back(runtime_node("node" + std::to_string(index), operator_class, std::move(data_in), data_out,
                                 std::move(exec_in), std::move(exec_out), std::move(states), std::move(state_values)));
    ++index;
  }

  json graph{{"graphId", "g"}, {"revision", "r"}, {"nodes", nodes}, {"edges", json::array()}};

  std::string error_code;
  std::string error_message;
  expect(host.apply_rungraph(graph, error_code, error_message),
         "apply_rungraph failed: " + error_code + ": " + error_message);

  for (const auto& node : nodes) {
    const std::string node_id = node.value("nodeId", "");
    const std::string operator_class = node.value("operatorClass", "");
    auto* computable = dynamic_cast<f8::cppsdk::ComputableNode*>(host.get_node(node_id));
    expect(computable != nullptr, operator_class + " declares dataOutPorts but does not implement ComputableNode");
    for (const auto& port : node.value("dataOutPorts", json::array())) {
      const std::string port_name = port.value("name", "");
      if (port_name.empty()) continue;
      (void)computable->compute_output(port_name, 9000);
    }
  }
}

int main() {
  try {
    run_cpython_script_operator_smoke();
    run_script_placeholder_operator_smoke();
    run_all_data_output_operators_are_computable_smoke();
  } catch (const std::exception& exc) {
    std::cerr << exc.what() << "\n";
    return 1;
  }
  return 0;
}
