#pragma once

#include <functional>
#include <memory>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

#include <nlohmann/json.hpp>

#include "f8cppsdk/generated/protocol_models.h"
#include "f8cppsdk/runtime_node.h"

namespace f8::cppsdk {

class RegistryError : public std::runtime_error {
 public:
  explicit RegistryError(const std::string& message) : std::runtime_error(message) {}
};

class ServiceNotRegistered final : public RegistryError {
 public:
  explicit ServiceNotRegistered(const std::string& service_class)
      : RegistryError("service not registered: " + service_class), service_class(service_class) {}
  std::string service_class;
};

class OperatorFactoryNotRegistered final : public RegistryError {
 public:
  OperatorFactoryNotRegistered(const std::string& service_class, const std::string& operator_class)
      : RegistryError("operator runtime factory not registered for " + service_class + "/" + operator_class),
        service_class(service_class),
        operator_class(operator_class) {}
  std::string service_class;
  std::string operator_class;
};

using ServiceNodeFactory =
    std::function<std::unique_ptr<RuntimeNode>(const std::string&, const generated::F8RuntimeNode&, const nlohmann::json&)>;
using OperatorNodeFactory =
    std::function<std::unique_ptr<OperatorNode>(const std::string&, const generated::F8RuntimeNode&, const nlohmann::json&)>;

class RuntimeNodeRegistry final {
 public:
  void register_service_spec(nlohmann::json spec, bool overwrite = false);
  void register_operator_spec(nlohmann::json spec, bool overwrite = false);
  void register_service_factory(const std::string& service_class, ServiceNodeFactory factory, bool overwrite = false);
  void register_operator_factory(const std::string& service_class, const std::string& operator_class,
                                 OperatorNodeFactory factory, bool overwrite = false);

  std::unique_ptr<RuntimeNode> create_service_node(const std::string& service_class, const std::string& node_id,
                                                   const generated::F8RuntimeNode& node,
                                                   const nlohmann::json& initial_state) const;
  std::unique_ptr<OperatorNode> create_operator_node(const std::string& node_id, const generated::F8RuntimeNode& node,
                                                     const nlohmann::json& initial_state) const;

  nlohmann::json describe(const std::string& service_class) const;

 private:
  bool service_known(const std::string& service_class) const;

  std::unordered_map<std::string, ServiceNodeFactory> service_factories_;
  std::unordered_map<std::string, std::unordered_map<std::string, OperatorNodeFactory>> operator_factories_;
  std::unordered_map<std::string, nlohmann::json> service_specs_;
  std::unordered_map<std::string, std::unordered_map<std::string, nlohmann::json>> operator_specs_;
};

}  // namespace f8::cppsdk
