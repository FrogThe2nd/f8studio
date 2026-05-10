#include "f8cppengine/cppengine_service.h"

#include <utility>

#include <spdlog/spdlog.h>

#include "f8cppengine/constants.h"
#include "f8cppengine/operators.h"

namespace f8::cppengine {

CppEngineService::CppEngineService(Config cfg) : cfg_(std::move(cfg)) {
  register_cppengine_specs(registry_);
}

CppEngineService::~CppEngineService() { stop(); }

bool CppEngineService::start() {
  if (running_.load(std::memory_order_acquire)) return true;

  f8::cppsdk::ServiceBus::Config bus_cfg;
  bus_cfg.service_id = cfg_.service_id;
  bus_cfg.service_class = kServiceClass;
  bus_cfg.service_name = "CppEngine";
  bus_cfg.data_delivery = f8::cppsdk::ServiceBus::DataDeliveryMode::kPull;
  bus_cfg.apply_runtime_backend(f8::cppsdk::normalize_runtime_backend_config(cfg_.runtime_backend));

  bus_ = std::make_unique<f8::cppsdk::ServiceBus>(bus_cfg);
  host_ = std::make_unique<f8::cppsdk::ServiceHost>(*bus_, registry_, kServiceClass);
  executor_ = std::make_unique<f8::cppsdk::ExecFlowExecutor>(*bus_);

  host_->start();
  bus_->add_lifecycle_node(this);
  bus_->add_rungraph_node(this);

  if (!bus_->start()) {
    executor_.reset();
    host_.reset();
    bus_.reset();
    return false;
  }

  running_.store(true, std::memory_order_release);
  stop_requested_.store(false, std::memory_order_release);
  spdlog::info("cppengine started serviceId={}", cfg_.service_id);
  return true;
}

void CppEngineService::stop() {
  stop_requested_.store(true, std::memory_order_release);
  if (!running_.exchange(false, std::memory_order_acq_rel)) return;

  if (executor_) {
    executor_->set_active(false);
    executor_->clear_nodes();
  }
  if (host_) {
    host_->stop();
  }
  if (bus_) {
    bus_->stop();
  }
  executor_.reset();
  host_.reset();
  bus_.reset();
}

bool CppEngineService::running() const {
  return running_.load(std::memory_order_acquire) && !stop_requested_.load(std::memory_order_acquire);
}

void CppEngineService::tick() {
  if (!running()) return;
  if (bus_) {
    (void)bus_->drain_main_thread();
    if (bus_->terminate_requested()) {
      stop_requested_.store(true, std::memory_order_release);
    }
  }
}

void CppEngineService::on_lifecycle(bool active, const nlohmann::json& meta) {
  (void)meta;
  if (executor_) {
    executor_->set_active(active);
  }
}

bool CppEngineService::on_set_rungraph(const nlohmann::json& graph_obj, const nlohmann::json& meta,
                                       std::string& error_code, std::string& error_message) {
  (void)meta;
  error_code.clear();
  error_message.clear();
  if (!host_ || !executor_) {
    error_code = "INTERNAL";
    error_message = "cppengine is not started";
    return false;
  }
  if (!host_->apply_rungraph(graph_obj, error_code, error_message)) {
    return false;
  }
  sync_exec_nodes();
  try {
    executor_->apply_rungraph(graph_obj);
  } catch (const std::exception& exc) {
    error_code = "INVALID_RUNGRAPH";
    error_message = exc.what();
    return false;
  }
  return true;
}

nlohmann::json CppEngineService::describe_json() const {
  return registry_.describe(kServiceClass);
}

void CppEngineService::sync_exec_nodes() {
  if (!host_ || !executor_) return;
  executor_->clear_nodes();
  for (f8::cppsdk::OperatorNode* node : host_->operator_nodes()) {
    if (node != nullptr && (!node->exec_in_ports().empty() || !node->exec_out_ports().empty())) {
      executor_->register_node(node);
    }
  }
}

}  // namespace f8::cppengine
