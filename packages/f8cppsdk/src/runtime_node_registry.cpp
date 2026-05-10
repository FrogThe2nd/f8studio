#include "f8cppsdk/runtime_node_registry.h"

#include <algorithm>

#include "f8cppsdk/describe_builtins.h"

namespace f8::cppsdk {

namespace {

std::string json_string_field(const nlohmann::json& obj, const char* field) {
  if (!obj.is_object()) return "";
  const auto it = obj.find(field);
  if (it == obj.end() || !it->is_string()) return "";
  return it->get<std::string>();
}

}  // namespace

void RuntimeNodeRegistry::register_service_spec(nlohmann::json spec, bool overwrite) {
  const std::string service_class = json_string_field(spec, "serviceClass");
  if (service_class.empty()) {
    throw std::invalid_argument("service spec requires serviceClass");
  }
  if (service_specs_.find(service_class) != service_specs_.end() && !overwrite) {
    throw RegistryError("service spec already registered: " + service_class);
  }
  service_specs_[service_class] = std::move(spec);
}

void RuntimeNodeRegistry::register_operator_spec(nlohmann::json spec, bool overwrite) {
  const std::string service_class = json_string_field(spec, "serviceClass");
  const std::string operator_class = json_string_field(spec, "operatorClass");
  if (service_class.empty() || operator_class.empty()) {
    throw std::invalid_argument("operator spec requires serviceClass and operatorClass");
  }
  auto& by_operator = operator_specs_[service_class];
  if (by_operator.find(operator_class) != by_operator.end() && !overwrite) {
    throw RegistryError("operator spec already registered: " + service_class + "/" + operator_class);
  }
  by_operator[operator_class] = std::move(spec);
}

void RuntimeNodeRegistry::register_service_factory(const std::string& service_class, ServiceNodeFactory factory,
                                                   bool overwrite) {
  if (service_class.empty()) {
    throw std::invalid_argument("service_class must be non-empty");
  }
  if (!factory) {
    throw std::invalid_argument("service factory must be callable");
  }
  if (service_factories_.find(service_class) != service_factories_.end() && !overwrite) {
    throw RegistryError("service factory already registered: " + service_class);
  }
  service_factories_[service_class] = std::move(factory);
}

void RuntimeNodeRegistry::register_operator_factory(const std::string& service_class,
                                                    const std::string& operator_class, OperatorNodeFactory factory,
                                                    bool overwrite) {
  if (service_class.empty() || operator_class.empty()) {
    throw std::invalid_argument("service_class and operator_class must be non-empty");
  }
  if (!factory) {
    throw std::invalid_argument("operator factory must be callable");
  }
  auto& by_operator = operator_factories_[service_class];
  if (by_operator.find(operator_class) != by_operator.end() && !overwrite) {
    throw RegistryError("operator factory already registered: " + service_class + "/" + operator_class);
  }
  by_operator[operator_class] = std::move(factory);
}

std::unique_ptr<RuntimeNode> RuntimeNodeRegistry::create_service_node(const std::string& service_class,
                                                                      const std::string& node_id,
                                                                      const generated::F8RuntimeNode& node,
                                                                      const nlohmann::json& initial_state) const {
  const auto factory_it = service_factories_.find(service_class);
  if (factory_it == service_factories_.end()) {
    if (!service_known(service_class)) {
      throw ServiceNotRegistered(service_class);
    }
    return std::make_unique<ServiceNode>(node_id);
  }
  auto created = factory_it->second(node_id, node, initial_state);
  if (!created) {
    throw RegistryError("service factory returned null: " + service_class);
  }
  return created;
}

std::unique_ptr<OperatorNode> RuntimeNodeRegistry::create_operator_node(const std::string& node_id,
                                                                        const generated::F8RuntimeNode& node,
                                                                        const nlohmann::json& initial_state) const {
  const std::string service_class = node.serviceClass;
  const std::string operator_class = node.operatorClass.value_or("");
  if (service_class.empty() || operator_class.empty()) {
    throw std::invalid_argument("runtime node requires serviceClass and operatorClass");
  }
  const auto svc_it = operator_factories_.find(service_class);
  if (svc_it == operator_factories_.end()) {
    if (!service_known(service_class)) {
      throw ServiceNotRegistered(service_class);
    }
    throw OperatorFactoryNotRegistered(service_class, operator_class);
  }
  const auto op_it = svc_it->second.find(operator_class);
  if (op_it == svc_it->second.end()) {
    throw OperatorFactoryNotRegistered(service_class, operator_class);
  }
  auto created = op_it->second(node_id, node, initial_state);
  if (!created) {
    throw RegistryError("operator factory returned null: " + service_class + "/" + operator_class);
  }
  return created;
}

nlohmann::json RuntimeNodeRegistry::describe(const std::string& service_class) const {
  const auto service_it = service_specs_.find(service_class);
  if (service_it == service_specs_.end()) {
    throw ServiceNotRegistered(service_class);
  }
  nlohmann::json operators = nlohmann::json::array();
  const auto ops_it = operator_specs_.find(service_class);
  if (ops_it != operator_specs_.end()) {
    std::vector<std::string> keys;
    keys.reserve(ops_it->second.size());
    for (const auto& item : ops_it->second) {
      keys.push_back(item.first);
    }
    std::sort(keys.begin(), keys.end());
    for (const std::string& key : keys) {
      operators.push_back(ops_it->second.at(key));
    }
  }
  return normalize_describe_with_builtin_state_fields(
      nlohmann::json{{"schemaVersion", "f8describe/1"}, {"service", service_it->second}, {"operators", operators}});
}

bool RuntimeNodeRegistry::service_known(const std::string& service_class) const {
  return service_factories_.find(service_class) != service_factories_.end() ||
         operator_factories_.find(service_class) != operator_factories_.end() ||
         service_specs_.find(service_class) != service_specs_.end() ||
         operator_specs_.find(service_class) != operator_specs_.end();
}

}  // namespace f8::cppsdk
