#include "f8cppengine/operators.h"

#include <atomic>
#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <cctype>
#include <functional>
#include <iomanip>
#include <iostream>
#include <mutex>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <unordered_map>
#include <utility>
#include <vector>

#include <nlohmann/json.hpp>
#include <mexce.h>

#include "f8cppengine/constants.h"
#include "f8cppsdk/generated/protocol_models.h"
#include "f8cppsdk/runtime_node.h"
#include "f8cppsdk/time_utils.h"

namespace f8::cppengine {

using json = nlohmann::json;
using f8::cppsdk::ComputableNode;
using f8::cppsdk::EntrypointContext;
using f8::cppsdk::EntrypointNode;
using f8::cppsdk::OperatorNode;
using f8::cppsdk::RuntimeNode;
using f8::cppsdk::RuntimeNodeRegistry;
using f8::cppsdk::generated::F8RuntimeNode;

namespace {

json any_schema() { return json{{"type", "any"}}; }
json number_schema(double default_value = 0.0) { return json{{"type", "number"}, {"default", default_value}}; }
json number_schema(double default_value, double minimum, double maximum = 0.0) {
  json out{{"type", "number"}, {"default", default_value}, {"minimum", minimum}};
  if (maximum > minimum) out["maximum"] = maximum;
  return out;
}
json integer_schema(std::int64_t default_value, std::int64_t minimum = 0, std::int64_t maximum = 0) {
  json out{{"type", "integer"}, {"default", default_value}, {"minimum", minimum}};
  if (maximum > minimum) out["maximum"] = maximum;
  return out;
}
json boolean_schema(bool default_value) { return json{{"type", "boolean"}, {"default", default_value}}; }
json string_schema(const std::string& default_value) { return json{{"type", "string"}, {"default", default_value}}; }
json string_enum_schema(const std::string& default_value, std::vector<std::string> values) {
  return json{{"type", "string"}, {"default", default_value}, {"enum", values}};
}
json array_schema(const json& items = any_schema()) { return json{{"type", "array"}, {"items", items}}; }

json data_port(const std::string& name, const std::string& description, const json& schema, bool required = false,
               bool show_on_node = true) {
  return json{{"name", name},
              {"description", description},
              {"valueSchema", schema},
              {"required", required},
              {"showOnNode", show_on_node}};
}

json state_field(const std::string& name, const std::string& label, const std::string& description, const json& schema,
                 const std::string& access = "rw", bool required = true, bool show_on_node = false,
                 const std::string& ui_control = "") {
  json out{{"name", name},
           {"label", label},
           {"description", description},
           {"valueSchema", schema},
           {"access", access},
           {"required", required},
           {"showOnNode", show_on_node}};
  if (!ui_control.empty()) out["uiControl"] = ui_control;
  return out;
}

json editable_collection_policy() {
  return json{{"canAdd", true}, {"canRemove", true}, {"canRename", true}};
}

json editable_script_policy() {
  return json{{"stateFields", editable_collection_policy()},
              {"dataInPorts", editable_collection_policy()},
              {"dataOutPorts", editable_collection_policy()},
              {"execInPorts", editable_collection_policy()},
              {"execOutPorts", editable_collection_policy()}};
}

std::vector<std::string> data_port_names(const std::optional<std::vector<f8::cppsdk::generated::F8DataPortSpec>>& ports,
                                         std::vector<std::string> fallback) {
  std::vector<std::string> out;
  for (const auto& port : ports.value_or(std::vector<f8::cppsdk::generated::F8DataPortSpec>{})) {
    out.push_back(port.name);
  }
  return out.empty() ? std::move(fallback) : out;
}

std::vector<std::string> state_names(const std::optional<std::vector<f8::cppsdk::generated::F8StateSpec>>& fields,
                                     std::vector<std::string> fallback) {
  std::vector<std::string> out;
  for (const auto& field : fields.value_or(std::vector<f8::cppsdk::generated::F8StateSpec>{})) {
    out.push_back(field.name);
  }
  return out.empty() ? std::move(fallback) : out;
}

std::vector<std::string> strings_or(const std::optional<std::vector<std::string>>& values,
                                    std::vector<std::string> fallback) {
  if (values.has_value() && !values->empty()) return values.value();
  return fallback;
}

double json_number_or(const json& value, double fallback) {
  if (value.is_number()) return value.get<double>();
  if (value.is_string()) {
    try {
      return std::stod(value.get<std::string>());
    } catch (const std::exception&) {
      return fallback;
    }
  }
  return fallback;
}

std::optional<double> json_number(const json& value) {
  if (value.is_number()) return value.get<double>();
  if (value.is_string()) {
    try {
      return std::stod(value.get<std::string>());
    } catch (const std::exception&) {
      return std::nullopt;
    }
  }
  return std::nullopt;
}

bool json_bool_or(const json& value, bool fallback) {
  if (value.is_boolean()) return value.get<bool>();
  if (value.is_number_integer()) return value.get<int>() != 0;
  if (value.is_string()) {
    std::string text = value.get<std::string>();
    std::transform(text.begin(), text.end(), text.begin(), [](unsigned char ch) { return static_cast<char>(std::tolower(ch)); });
    if (text == "1" || text == "true" || text == "yes" || text == "on") return true;
    if (text == "0" || text == "false" || text == "no" || text == "off") return false;
  }
  return fallback;
}

double clamp_double(double value, double lo, double hi) {
  return std::max(lo, std::min(hi, value));
}

int js_round(double value) {
  if (value >= 0.0) return static_cast<int>(std::floor(value + 0.5));
  return -static_cast<int>(std::floor(std::abs(value) + 0.5));
}

std::string json_to_printable(json value, bool strip) {
  std::string text;
  if (value.is_string()) {
    text = value.get<std::string>();
  } else {
    text = value.dump();
  }
  if (!strip) return text;
  text.erase(text.begin(), std::find_if(text.begin(), text.end(), [](unsigned char ch) { return !std::isspace(ch); }));
  text.erase(std::find_if(text.rbegin(), text.rend(), [](unsigned char ch) { return !std::isspace(ch); }).base(), text.end());
  return text;
}

std::vector<double> json_number_sequence(const json& value) {
  std::vector<double> out;
  if (const auto numeric = json_number(value); numeric.has_value()) {
    out.push_back(numeric.value());
    return out;
  }
  if (!value.is_array()) return {};
  for (const auto& item : value) {
    const auto numeric = json_number(item);
    if (!numeric.has_value()) return {};
    out.push_back(numeric.value());
  }
  return out;
}

json format_number_sequence(const std::vector<double>& values) {
  if (values.empty()) return nullptr;
  if (values.size() == 1) return values.front();
  return values;
}

double now_seconds() {
  return static_cast<double>(std::chrono::duration_cast<std::chrono::microseconds>(
                                 std::chrono::steady_clock::now().time_since_epoch())
                                 .count()) /
         1000000.0;
}

constexpr double kPi = 3.141592653589793238462643383279502884;
constexpr double kTwoPi = 2.0 * kPi;

std::string normalize_curve(const json& value) {
  std::string curve = value.is_string() ? value.get<std::string>() : "";
  std::transform(curve.begin(), curve.end(), curve.begin(), [](unsigned char ch) { return static_cast<char>(std::toupper(ch)); });
  static const std::vector<std::string> choices{"LINEAR", "SMOOTHSTEP", "SMOOTHERSTEP", "EASE_IN", "EASE_OUT",
                                                "EASE_IN_OUT"};
  for (const auto& choice : choices) {
    if (curve == choice) return curve;
  }
  return "LINEAR";
}

double apply_curve(const std::string& curve, double t) {
  if (curve == "SMOOTHSTEP") return t * t * (3.0 - 2.0 * t);
  if (curve == "SMOOTHERSTEP") return t * t * t * (t * (t * 6.0 - 15.0) + 10.0);
  if (curve == "EASE_IN") return t * t;
  if (curve == "EASE_OUT") return 1.0 - (1.0 - t) * (1.0 - t);
  if (curve == "EASE_IN_OUT") {
    if (t < 0.5) return 2.0 * t * t;
    return 1.0 - 2.0 * (1.0 - t) * (1.0 - t);
  }
  return t;
}

class CppEngineServiceNode final : public f8::cppsdk::ServiceNode {
 public:
  CppEngineServiceNode(const std::string& node_id, const F8RuntimeNode& node, const json& initial_state)
      : ServiceNode(node_id, data_port_names(node.dataInPorts, {}), data_port_names(node.dataOutPorts, {}),
                    state_names(node.stateFields, {"dataDelivery"})) {
    data_delivery_ = initial_state.value("dataDelivery", "buffered");
  }

  json validate_state(const std::string& field, const json& value, std::int64_t ts_ms, const json& meta) override {
    (void)ts_ms;
    (void)meta;
    if (field == "dataDelivery") {
      const std::string mode = value.is_string() ? value.get<std::string>() : "";
      if (mode != "buffered" && mode != "callback") {
        throw std::invalid_argument("dataDelivery must be 'buffered' or 'callback'");
      }
      return mode;
    }
    return value;
  }

  void on_state(const std::string& field, const json& value, std::int64_t ts_ms, const json& meta) override {
    (void)ts_ms;
    (void)meta;
    if (field == "dataDelivery" && value.is_string()) {
      data_delivery_ = value.get<std::string>();
    }
  }

 private:
  std::string data_delivery_ = "buffered";
};

class TickNode final : public OperatorNode, public EntrypointNode {
 public:
  TickNode(const std::string& node_id, const F8RuntimeNode& node, const json& initial_state)
      : OperatorNode(node_id, data_port_names(node.dataInPorts, {}), data_port_names(node.dataOutPorts, {"processingMs", "intervalMs", "latenessMs"}),
                     state_names(node.stateFields, {"tickMs", "hiResTimer"}), strings_or(node.execInPorts, {}),
                     strings_or(node.execOutPorts, {"exec"})) {
    tick_ms_ = coerce_tick_ms(initial_state.value("tickMs", 100));
    hi_res_timer_ = json_bool_or(initial_state.value("hiResTimer", true), true);
  }

  json validate_state(const std::string& field, const json& value, std::int64_t ts_ms, const json& meta) override {
    (void)ts_ms;
    (void)meta;
    if (field == "tickMs") return coerce_tick_ms(value);
    if (field == "hiResTimer") return json_bool_or(value, false);
    return value;
  }

  void on_state(const std::string& field, const json& value, std::int64_t ts_ms, const json& meta) override {
    (void)ts_ms;
    (void)meta;
    if (field == "tickMs") tick_ms_.store(coerce_tick_ms(value), std::memory_order_release);
    if (field == "hiResTimer") hi_res_timer_.store(json_bool_or(value, false), std::memory_order_release);
  }

  std::vector<std::string> on_exec(std::int64_t exec_id, const std::string& in_port) override {
    (void)exec_id;
    (void)in_port;
    return exec_out_ports();
  }

  void start_entrypoint(const EntrypointContext& ctx) override {
    stop_entrypoint();
    stop_requested_.store(false, std::memory_order_release);
    worker_ = std::thread([this, ctx]() {
      auto next_deadline = std::chrono::steady_clock::now();
      auto last_tick = std::optional<std::chrono::steady_clock::time_point>{};
      while (!stop_requested_.load(std::memory_order_acquire)) {
        const int period_ms = tick_ms_.load(std::memory_order_acquire);
        next_deadline += std::chrono::milliseconds(period_ms);
        std::unique_lock<std::mutex> lock(mu_);
        cv_.wait_until(lock, next_deadline, [&]() { return stop_requested_.load(std::memory_order_acquire); });
        if (stop_requested_.load(std::memory_order_acquire)) break;
        lock.unlock();

        const auto started = std::chrono::steady_clock::now();
        const std::int64_t exec_id = f8::cppsdk::now_ms();
        std::int64_t interval_ms = 0;
        if (last_tick.has_value()) {
          interval_ms = std::chrono::duration_cast<std::chrono::milliseconds>(started - last_tick.value()).count();
        }
        last_tick = started;
        const std::int64_t lateness_ms =
            std::max<std::int64_t>(0, std::chrono::duration_cast<std::chrono::milliseconds>(started - next_deadline).count());
        (void)emit("intervalMs", interval_ms);
        (void)emit("latenessMs", lateness_ms);
        for (const auto& port : exec_out_ports()) {
          ctx.emit_exec(port, exec_id);
        }
        const std::int64_t processing_ms =
            std::max<std::int64_t>(0, std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - started).count());
        (void)emit("processingMs", processing_ms);
        const auto now = std::chrono::steady_clock::now();
        if (next_deadline <= now) {
          const auto missed = std::chrono::duration_cast<std::chrono::milliseconds>(now - next_deadline).count() / period_ms + 1;
          next_deadline += std::chrono::milliseconds(missed * period_ms);
        }
      }
    });
  }

  void stop_entrypoint() override {
    stop_requested_.store(true, std::memory_order_release);
    cv_.notify_all();
    if (worker_.joinable()) worker_.join();
    stop_requested_.store(false, std::memory_order_release);
  }

 private:
  static int coerce_tick_ms(const json& value) {
    const auto numeric = json_number(value);
    if (!numeric.has_value()) throw std::invalid_argument("tickMs must be an integer");
    const int ms = static_cast<int>(*numeric);
    if (ms < 1) throw std::invalid_argument("tickMs must be >= 1");
    if (ms > 50000) throw std::invalid_argument("tickMs must be <= 50000");
    return ms;
  }

  std::atomic<int> tick_ms_{100};
  std::atomic<bool> hi_res_timer_{true};
  std::atomic<bool> stop_requested_{false};
  std::mutex mu_;
  std::condition_variable cv_;
  std::thread worker_;
};

class RangeMapNode final : public OperatorNode, public ComputableNode {
 public:
  RangeMapNode(const std::string& node_id, const F8RuntimeNode& node, const json& initial_state)
      : OperatorNode(node_id, data_port_names(node.dataInPorts, {"value"}), data_port_names(node.dataOutPorts, {"value"}),
                     state_names(node.stateFields, {"inMin", "inMax", "outMin", "outMax", "curve"}),
                     strings_or(node.execInPorts, {"exec"}), strings_or(node.execOutPorts, {"exec"})) {
    in_min_ = json_number_or(initial_state.value("inMin", 0.0), 0.0);
    in_max_ = json_number_or(initial_state.value("inMax", 1.0), 1.0);
    out_min_ = json_number_or(initial_state.value("outMin", 0.0), 0.0);
    out_max_ = json_number_or(initial_state.value("outMax", 1.0), 1.0);
    curve_ = normalize_curve(initial_state.value("curve", "LINEAR"));
  }

  void on_state(const std::string& field, const json& value, std::int64_t ts_ms, const json& meta) override {
    (void)ts_ms;
    (void)meta;
    if (field == "inMin") in_min_ = json_number_or(value, in_min_);
    if (field == "inMax") in_max_ = json_number_or(value, in_max_);
    if (field == "outMin") out_min_ = json_number_or(value, out_min_);
    if (field == "outMax") out_max_ = json_number_or(value, out_max_);
    if (field == "curve") curve_ = normalize_curve(value);
    dirty_ = true;
  }

  std::vector<std::string> on_exec(std::int64_t exec_id, const std::string& in_port) override {
    (void)in_port;
    const json value = compute_output("value", exec_id);
    if (!value.is_null()) {
      (void)emit("value", value);
    }
    return exec_out_ports();
  }

  void on_data(const std::string& port, const json& value, std::int64_t ts_ms, const json& meta) override {
    (void)value;
    (void)meta;
    if (port != "value") return;
    const json output = compute_output("value", ts_ms);
    if (!output.is_null()) {
      (void)emit("value", output, ts_ms);
    }
  }

  json compute_output(const std::string& port, std::int64_t ctx_id) override {
    if (port != "value") return nullptr;
    const auto raw = pull("value", ctx_id);
    if (!raw.has_value()) return last_output_.has_value() ? json(last_output_.value()) : json(nullptr);
    const auto numeric = json_number(raw.value());
    if (!numeric.has_value()) return last_output_.has_value() ? json(last_output_.value()) : json(nullptr);
    if (!dirty_ && last_ctx_id_.has_value() && last_ctx_id_.value() == ctx_id && last_input_.has_value() &&
        last_input_.value() == numeric.value()) {
      return last_output_.has_value() ? json(last_output_.value()) : json(nullptr);
    }
    double in_min = in_min_;
    double in_max = in_max_;
    double out_min = out_min_;
    double out_max = out_max_;
    if (in_min > in_max) std::swap(in_min, in_max);
    if (out_min > out_max) std::swap(out_min, out_max);
    double output = out_min;
    if ((in_max - in_min) != 0.0) {
      const double clipped = std::min(in_max, std::max(in_min, numeric.value()));
      const double t = apply_curve(curve_, (clipped - in_min) / (in_max - in_min));
      output = out_min + t * (out_max - out_min);
    }
    last_input_ = numeric.value();
    last_output_ = output;
    last_ctx_id_ = ctx_id;
    dirty_ = false;
    return output;
  }

 private:
  double in_min_ = 0.0;
  double in_max_ = 1.0;
  double out_min_ = 0.0;
  double out_max_ = 1.0;
  std::string curve_ = "LINEAR";
  std::optional<double> last_input_;
  std::optional<double> last_output_;
  std::optional<std::int64_t> last_ctx_id_;
  bool dirty_ = true;
};

class DataExprNode final : public OperatorNode, public ComputableNode {
 public:
  DataExprNode(const std::string& node_id, const F8RuntimeNode& node, const json& initial_state)
      : OperatorNode(node_id, data_port_names(node.dataInPorts, {"x"}), data_port_names(node.dataOutPorts, {"out"}),
                     state_names(node.stateFields, {"code"}), strings_or(node.execInPorts, {"exec"}),
                     strings_or(node.execOutPorts, {"exec"})) {
    code_ = initial_state.value("code", "x");
    rebuild_evaluator_bindings();
  }

  json validate_state(const std::string& field, const json& value, std::int64_t ts_ms, const json& meta) override {
    (void)ts_ms;
    (void)meta;
    if (field == "code") return value.is_string() ? value.get<std::string>() : "";
    return value;
  }

  void on_state(const std::string& field, const json& value, std::int64_t ts_ms, const json& meta) override {
    (void)ts_ms;
    (void)meta;
    if (field == "code") {
      code_ = value.is_string() ? value.get<std::string>() : "";
      evaluator_ready_ = false;
      dirty_ = true;
    }
  }

  std::vector<std::string> on_exec(std::int64_t exec_id, const std::string& in_port) override {
    (void)in_port;
    const std::string port = default_output_port();
    const json value = compute_output(port, exec_id);
    if (!value.is_null()) {
      (void)emit(port, value);
    }
    return exec_out_ports();
  }

  void on_data(const std::string& port, const json& value, std::int64_t ts_ms, const json& meta) override {
    (void)port;
    (void)value;
    (void)meta;
    const std::string output_port = default_output_port();
    const json output = compute_output(output_port, ts_ms);
    if (!output.is_null()) {
      (void)emit(output_port, output, ts_ms);
    }
  }

  json compute_output(const std::string& port, std::int64_t ctx_id) override {
    if (port.empty()) return nullptr;
    if (!dirty_ && last_ctx_id_.has_value() && last_ctx_id_.value() == ctx_id && last_outputs_.contains(port)) {
      return last_outputs_[port];
    }
    last_outputs_.clear();
    try {
      bind_inputs(ctx_id);
      compile_if_needed();
      last_outputs_[default_output_port()] = evaluator_.evaluate();
      clear_error("data_expr:" + node_id());
    } catch (const std::exception& exc) {
      report_error("DATA_EXPR_ERROR", exc.what(), "error", "data_expr:" + node_id());
      last_outputs_[default_output_port()] = nullptr;
    }
    last_ctx_id_ = ctx_id;
    dirty_ = false;
    return last_outputs_.value(port, nullptr);
  }

 private:
  static bool is_bindable_name(const std::string& name) {
    if (name.empty()) return false;
    const unsigned char first = static_cast<unsigned char>(name.front());
    if (!std::isalpha(first) && name.front() != '_') return false;
    for (const char ch : name) {
      const unsigned char uch = static_cast<unsigned char>(ch);
      if (!std::isalnum(uch) && ch != '_') return false;
    }
    return true;
  }

  std::string default_output_port() const {
    for (const auto& port : data_out_ports()) {
      if (port == "out") return port;
    }
    if (!data_out_ports().empty()) return data_out_ports().front();
    return "";
  }

  void rebuild_evaluator_bindings() {
    evaluator_ = mexce::evaluator();
    bound_values_.clear();
    for (const auto& input_port : data_in_ports()) {
      if (!is_bindable_name(input_port)) continue;
      auto inserted = bound_values_.emplace(input_port, 0.0);
      evaluator_.bind(inserted.first->second, input_port);
    }
    evaluator_ready_ = false;
    dirty_ = true;
  }

  void bind_inputs(std::int64_t ctx_id) {
    if (bound_values_.empty() && !data_in_ports().empty()) {
      rebuild_evaluator_bindings();
    }
    for (auto& item : bound_values_) {
      const auto raw = pull(item.first, ctx_id);
      if (!raw.has_value()) continue;
      const auto numeric = json_number(raw.value());
      if (numeric.has_value()) item.second = numeric.value();
    }
  }

  void compile_if_needed() {
    if (evaluator_ready_) return;
    evaluator_.set_expression(code_.empty() ? "0" : code_);
    evaluator_ready_ = true;
  }

  std::string code_ = "x";
  std::optional<std::int64_t> last_ctx_id_;
  json last_outputs_ = json::object();
  std::unordered_map<std::string, double> bound_values_;
  mexce::evaluator evaluator_;
  bool evaluator_ready_ = false;
  bool dirty_ = true;
};

class ExecSequenceNode final : public OperatorNode {
 public:
  ExecSequenceNode(const std::string& node_id, const F8RuntimeNode& node, const json& initial_state)
      : OperatorNode(node_id, data_port_names(node.dataInPorts, {}), data_port_names(node.dataOutPorts, {}),
                     state_names(node.stateFields, {}), strings_or(node.execInPorts, {"exec"}),
                     strings_or(node.execOutPorts, {"0", "1", "2"})) {
    (void)initial_state;
  }

  std::vector<std::string> on_exec(std::int64_t exec_id, const std::string& in_port) override {
    (void)exec_id;
    (void)in_port;
    return exec_out_ports();
  }
};

class PrintNode final : public OperatorNode {
 public:
  PrintNode(const std::string& node_id, const F8RuntimeNode& node, const json& initial_state)
      : OperatorNode(node_id, data_port_names(node.dataInPorts, {"value"}), data_port_names(node.dataOutPorts, {}),
                     state_names(node.stateFields, {"strip"}), strings_or(node.execInPorts, {"exec"}),
                     strings_or(node.execOutPorts, {})) {
    strip_ = json_bool_or(initial_state.value("strip", true), true);
  }

  void on_state(const std::string& field, const json& value, std::int64_t ts_ms, const json& meta) override {
    (void)ts_ms;
    (void)meta;
    if (field == "strip") strip_ = json_bool_or(value, strip_);
  }

  void on_data(const std::string& port, const json& value, std::int64_t ts_ms, const json& meta) override {
    (void)ts_ms;
    (void)meta;
    if (port == "value") {
      std::cout << "[" << node_id() << "] value=" << json_to_printable(value, strip_) << std::endl;
    }
  }

  std::vector<std::string> on_exec(std::int64_t exec_id, const std::string& in_port) override {
    (void)in_port;
    const auto value = pull("value", exec_id);
    std::cout << "[" << node_id() << "] exec=" << exec_id << " value="
              << json_to_printable(value.value_or(nullptr), strip_) << std::endl;
    return {};
  }

 private:
  bool strip_ = true;
};

class PhaseNode final : public OperatorNode, public ComputableNode {
 public:
  PhaseNode(const std::string& node_id, const F8RuntimeNode& node, const json& initial_state)
      : OperatorNode(node_id, data_port_names(node.dataInPorts, {"hz", "phase", "reset"}),
                     data_port_names(node.dataOutPorts, {"phase", "phaseTurns"}), state_names(node.stateFields, {"hz"}),
                     strings_or(node.execInPorts, {}), strings_or(node.execOutPorts, {})) {
    hz_ = json_number_or(initial_state.value("hz", 1.0), 1.0);
  }

  void on_lifecycle(bool active, const json& meta) override {
    (void)active;
    (void)meta;
    last_time_s_.reset();
  }

  void on_state(const std::string& field, const json& value, std::int64_t ts_ms, const json& meta) override {
    (void)ts_ms;
    (void)meta;
    if (field == "hz") hz_ = std::max(0.0, json_number_or(value, hz_));
  }

  void on_data(const std::string& port, const json& value, std::int64_t ts_ms, const json& meta) override {
    (void)port;
    (void)value;
    (void)meta;
    const json phase = compute_output("phase", ts_ms);
    const json turns = compute_output("phaseTurns", ts_ms);
    if (!phase.is_null()) (void)emit("phase", phase, ts_ms);
    if (!turns.is_null()) (void)emit("phaseTurns", turns, ts_ms);
  }

  json compute_output(const std::string& port, std::int64_t ctx_id) override {
    if (port != "phase" && port != "phaseTurns") return nullptr;
    if (last_ctx_id_.has_value() && last_ctx_id_.value() == ctx_id && cache_.contains(port)) return cache_[port];
    const auto in_hz = pull("hz", ctx_id);
    const auto in_phase = pull("phase", ctx_id);
    const auto in_reset = pull("reset", ctx_id);
    const double hz = std::max(0.0, in_hz.has_value() ? json_number_or(in_hz.value(), hz_) : hz_);
    if (in_reset.has_value() && json_bool_or(in_reset.value(), false)) turns_ = 0.0;
    if (in_phase.has_value()) {
      const auto phase = json_number(in_phase.value());
      if (phase.has_value()) turns_ = std::floor(turns_) + std::fmod(std::fmod(phase.value(), 1.0) + 1.0, 1.0);
    }
    const double now_s = now_seconds();
    double delta_s = 0.0;
    if (last_time_s_.has_value()) delta_s = std::max(0.0, now_s - last_time_s_.value());
    last_time_s_ = now_s;
    turns_ += hz * delta_s;
    cache_ = json{{"phase", std::fmod(std::fmod(turns_, 1.0) + 1.0, 1.0)}, {"phaseTurns", turns_}};
    last_ctx_id_ = ctx_id;
    return cache_.value(port, nullptr);
  }

 private:
  double hz_ = 1.0;
  double turns_ = 0.0;
  std::optional<double> last_time_s_;
  std::optional<std::int64_t> last_ctx_id_;
  json cache_ = json::object();
};

class CosineNode final : public OperatorNode, public ComputableNode {
 public:
  CosineNode(const std::string& node_id, const F8RuntimeNode& node, const json& initial_state)
      : OperatorNode(node_id, data_port_names(node.dataInPorts, {"phase", "amp", "dc", "phaseOffset"}),
                     data_port_names(node.dataOutPorts, {"value"}), state_names(node.stateFields, {"dc", "amp", "phaseOffset"}),
                     strings_or(node.execInPorts, {}), strings_or(node.execOutPorts, {})) {
    dc_ = json_number_or(initial_state.value("dc", 0.5), 0.5);
    amp_ = json_number_or(initial_state.value("amp", 0.5), 0.5);
    phase_offset_ = json_number_or(initial_state.value("phaseOffset", 0.0), 0.0);
  }

  void on_state(const std::string& field, const json& value, std::int64_t ts_ms, const json& meta) override {
    (void)ts_ms;
    (void)meta;
    if (field == "dc") dc_ = json_number_or(value, dc_);
    if (field == "amp") amp_ = json_number_or(value, amp_);
    if (field == "phaseOffset") phase_offset_ = json_number_or(value, phase_offset_);
  }

  void on_data(const std::string& port, const json& value, std::int64_t ts_ms, const json& meta) override {
    (void)port;
    (void)value;
    (void)meta;
    const json out = compute_output("value", ts_ms);
    if (!out.is_null()) (void)emit("value", out, ts_ms);
  }

  json compute_output(const std::string& port, std::int64_t ctx_id) override {
    if (port != "value") return nullptr;
    const double phase = json_number_or(pull("phase", ctx_id).value_or(0.0), 0.0);
    const double amp = json_number_or(pull("amp", ctx_id).value_or(amp_), amp_);
    const double dc = json_number_or(pull("dc", ctx_id).value_or(dc_), dc_);
    const double offset = json_number_or(pull("phaseOffset", ctx_id).value_or(phase_offset_), phase_offset_);
    return dc + amp * std::cos(2.0 * kPi * (std::fmod(std::fmod(phase, 1.0) + 1.0, 1.0) + offset));
  }

 private:
  double dc_ = 0.5;
  double amp_ = 0.5;
  double phase_offset_ = 0.0;
};

class TempestNode final : public OperatorNode, public ComputableNode {
 public:
  TempestNode(const std::string& node_id, const F8RuntimeNode& node, const json& initial_state)
      : OperatorNode(node_id, data_port_names(node.dataInPorts, {"phase", "amp", "phaseOffset", "eccentric", "dc"}),
                     data_port_names(node.dataOutPorts, {"out"}),
                     state_names(node.stateFields, {"dc", "amp", "phaseOffset", "eccentric"}),
                     strings_or(node.execInPorts, {}), strings_or(node.execOutPorts, {})) {
    dc_ = json_number_or(initial_state.value("dc", 0.5), 0.5);
    amp_ = json_number_or(initial_state.value("amp", 0.5), 0.5);
    phase_offset_ = json_number_or(initial_state.value("phaseOffset", 0.0), 0.0);
    eccentric_ = json_number_or(initial_state.value("eccentric", 0.0), 0.0);
  }

  void on_state(const std::string& field, const json& value, std::int64_t ts_ms, const json& meta) override {
    (void)ts_ms;
    (void)meta;
    if (field == "dc") dc_ = json_number_or(value, dc_);
    if (field == "amp") amp_ = json_number_or(value, amp_);
    if (field == "phaseOffset") phase_offset_ = json_number_or(value, phase_offset_);
    if (field == "eccentric") eccentric_ = json_number_or(value, eccentric_);
  }

  void on_data(const std::string& port, const json& value, std::int64_t ts_ms, const json& meta) override {
    (void)port;
    (void)value;
    (void)meta;
    const json out = compute_output("out", ts_ms);
    if (!out.is_null()) (void)emit("out", out, ts_ms);
  }

  json compute_output(const std::string& port, std::int64_t ctx_id) override {
    if (port != "out") return nullptr;
    const double phase = std::fmod(std::fmod(json_number_or(pull("phase", ctx_id).value_or(0.0), 0.0), 1.0) + 1.0, 1.0);
    const double amp = json_number_or(pull("amp", ctx_id).value_or(amp_), amp_);
    const double offset = json_number_or(pull("phaseOffset", ctx_id).value_or(phase_offset_), phase_offset_);
    const double eccentric = json_number_or(pull("eccentric", ctx_id).value_or(eccentric_), eccentric_);
    const double dc = json_number_or(pull("dc", ctx_id).value_or(dc_), dc_);
    const double theta = 2.0 * kPi * (phase + offset);
    return amp * std::cos(theta + eccentric * std::sin(theta)) + dc;
  }

 private:
  double dc_ = 0.5;
  double amp_ = 0.5;
  double phase_offset_ = 0.0;
  double eccentric_ = 0.0;
};

class EmaFilter {
 public:
  explicit EmaFilter(double alpha) : alpha_(alpha) {}
  double update(double value, double alpha) {
    alpha_ = alpha;
    if (!value_.has_value()) value_ = value;
    else value_ = (1.0 - alpha_) * value_.value() + alpha_ * value;
    return value_.value();
  }
  void reset() { value_.reset(); }

 private:
  double alpha_ = 0.4;
  std::optional<double> value_;
};

class DemaFilter {
 public:
  double update(double value, double alpha) {
    const double ema1 = first_.update(value, alpha);
    const double ema2 = second_.update(ema1, alpha);
    return 2.0 * ema1 - ema2;
  }
  void reset() {
    first_.reset();
    second_.reset();
  }

 private:
  EmaFilter first_{0.4};
  EmaFilter second_{0.4};
};

class OneEuroFilter {
 public:
  double min_cutoff = 1.5;
  double beta = 0.0;
  double derivative_cutoff = 1.0;
  double default_frequency = 90.0;

  double update(double value, double timestamp) {
    double dt = 1.0 / std::max(1e-6, default_frequency);
    if (last_time_.has_value()) dt = std::max(1e-6, timestamp - last_time_.value());
    last_time_ = timestamp;
    if (!estimate_.has_value()) {
      estimate_ = value;
      derivative_estimate_ = 0.0;
      return value;
    }
    const double derivative = (value - estimate_.value()) / dt;
    derivative_estimate_ = derivative_filter_.update(derivative, alpha(derivative_cutoff, dt));
    const double cutoff = min_cutoff + beta * std::abs(derivative_estimate_);
    estimate_ = value_filter_.update(value, alpha(cutoff, dt));
    return estimate_.value();
  }

 private:
  static double alpha(double cutoff, double dt) {
    const double tau = 1.0 / (2.0 * kPi * std::max(1e-9, cutoff));
    return 1.0 / (1.0 + tau / dt);
  }

  std::optional<double> estimate_;
  double derivative_estimate_ = 0.0;
  std::optional<double> last_time_;
  EmaFilter value_filter_{1.0};
  EmaFilter derivative_filter_{1.0};
};

class SmoothFilterNode final : public OperatorNode, public ComputableNode {
 public:
  SmoothFilterNode(const std::string& node_id, const F8RuntimeNode& node, const json& initial_state)
      : OperatorNode(node_id, data_port_names(node.dataInPorts, {"value"}), data_port_names(node.dataOutPorts, {"value"}),
                     state_names(node.stateFields,
                                 {"filter_type", "ema_alpha", "dema_alpha", "one_euro_min_cutoff", "one_euro_beta",
                                  "one_euro_derivative_cutoff", "one_euro_default_freq"}),
                     strings_or(node.execInPorts, {}), strings_or(node.execOutPorts, {})) {
    filter_type_ = normalize_filter_type(initial_state.value("filter_type", "EMA"));
    ema_alpha_ = clamp_double(json_number_or(initial_state.value("ema_alpha", 0.4), 0.4), 0.0, 1.0);
    dema_alpha_ = clamp_double(json_number_or(initial_state.value("dema_alpha", 0.4), 0.4), 0.0, 1.0);
    one_euro_min_cutoff_ = std::max(1e-6, json_number_or(initial_state.value("one_euro_min_cutoff", 1.5), 1.5));
    one_euro_beta_ = std::max(0.0, json_number_or(initial_state.value("one_euro_beta", 0.0), 0.0));
    one_euro_derivative_cutoff_ =
        std::max(1e-6, json_number_or(initial_state.value("one_euro_derivative_cutoff", 1.0), 1.0));
    one_euro_default_freq_ = std::max(1e-3, json_number_or(initial_state.value("one_euro_default_freq", 90.0), 90.0));
  }

  void on_state(const std::string& field, const json& value, std::int64_t ts_ms, const json& meta) override {
    (void)ts_ms;
    (void)meta;
    if (field == "filter_type") filter_type_ = normalize_filter_type(value);
    if (field == "ema_alpha") ema_alpha_ = clamp_double(json_number_or(value, ema_alpha_), 0.0, 1.0);
    if (field == "dema_alpha") dema_alpha_ = clamp_double(json_number_or(value, dema_alpha_), 0.0, 1.0);
    if (field == "one_euro_min_cutoff") one_euro_min_cutoff_ = std::max(1e-6, json_number_or(value, one_euro_min_cutoff_));
    if (field == "one_euro_beta") one_euro_beta_ = std::max(0.0, json_number_or(value, one_euro_beta_));
    if (field == "one_euro_derivative_cutoff") one_euro_derivative_cutoff_ = std::max(1e-6, json_number_or(value, one_euro_derivative_cutoff_));
    if (field == "one_euro_default_freq") one_euro_default_freq_ = std::max(1e-3, json_number_or(value, one_euro_default_freq_));
    reset_bank();
  }

  void on_data(const std::string& port, const json& value, std::int64_t ts_ms, const json& meta) override {
    (void)port;
    (void)value;
    (void)meta;
    const json out = compute_output("value", ts_ms);
    if (!out.is_null()) (void)emit("value", out, ts_ms);
  }

  json compute_output(const std::string& port, std::int64_t ctx_id) override {
    if (port != "value") return nullptr;
    const auto raw = pull("value", ctx_id);
    if (!raw.has_value()) return format_number_sequence(last_output_);
    const auto sample = json_number_sequence(raw.value());
    if (sample.empty()) return format_number_sequence(last_output_);
    if (!dirty_ && last_ctx_id_.has_value() && last_ctx_id_.value() == ctx_id && sample == last_input_) {
      return format_number_sequence(last_output_);
    }
    ensure_dimension(sample.size());
    std::vector<double> output;
    output.reserve(sample.size());
    const double t = now_seconds();
    if (filter_type_ == "NONE") {
      output = sample;
    } else if (filter_type_ == "EMA") {
      for (std::size_t i = 0; i < sample.size(); ++i) output.push_back(emas_[i].update(sample[i], ema_alpha_));
    } else if (filter_type_ == "DEMA") {
      for (std::size_t i = 0; i < sample.size(); ++i) output.push_back(demas_[i].update(sample[i], dema_alpha_));
    } else {
      for (std::size_t i = 0; i < sample.size(); ++i) output.push_back(one_euros_[i].update(sample[i], t));
    }
    last_input_ = sample;
    last_output_ = output;
    last_ctx_id_ = ctx_id;
    dirty_ = false;
    return format_number_sequence(last_output_);
  }

 private:
  static std::string normalize_filter_type(const json& value) {
    std::string text = value.is_string() ? value.get<std::string>() : "NONE";
    std::transform(text.begin(), text.end(), text.begin(), [](unsigned char ch) { return static_cast<char>(std::toupper(ch)); });
    if (text == "EMA" || text == "DEMA" || text == "ONEEURO") return text;
    return "NONE";
  }

  void reset_bank() {
    emas_.clear();
    demas_.clear();
    one_euros_.clear();
    dirty_ = true;
  }

  void ensure_dimension(std::size_t dimension) {
    if (filter_type_ == "EMA" && emas_.size() != dimension) emas_.assign(dimension, EmaFilter(ema_alpha_));
    if (filter_type_ == "DEMA" && demas_.size() != dimension) demas_.assign(dimension, DemaFilter());
    if (filter_type_ == "ONEEURO" && one_euros_.size() != dimension) {
      one_euros_.assign(dimension, OneEuroFilter());
      for (auto& filter : one_euros_) {
        filter.min_cutoff = one_euro_min_cutoff_;
        filter.beta = one_euro_beta_;
        filter.derivative_cutoff = one_euro_derivative_cutoff_;
        filter.default_frequency = one_euro_default_freq_;
      }
    }
  }

  std::string filter_type_ = "EMA";
  double ema_alpha_ = 0.4;
  double dema_alpha_ = 0.4;
  double one_euro_min_cutoff_ = 1.5;
  double one_euro_beta_ = 0.0;
  double one_euro_derivative_cutoff_ = 1.0;
  double one_euro_default_freq_ = 90.0;
  std::vector<EmaFilter> emas_;
  std::vector<DemaFilter> demas_;
  std::vector<OneEuroFilter> one_euros_;
  std::vector<double> last_input_;
  std::vector<double> last_output_;
  std::optional<std::int64_t> last_ctx_id_;
  bool dirty_ = true;
};

class DetrendNode final : public OperatorNode, public ComputableNode {
 public:
  DetrendNode(const std::string& node_id, const F8RuntimeNode& node, const json& initial_state)
      : OperatorNode(node_id, data_port_names(node.dataInPorts, {"value"}), data_port_names(node.dataOutPorts, {"value"}),
                     state_names(node.stateFields, {"mode", "alpha", "reset_on_state_change"}),
                     strings_or(node.execInPorts, {}), strings_or(node.execOutPorts, {})) {
    mode_ = normalize_mode(initial_state.value("mode", "CONSTANT"));
    alpha_ = clamp_double(json_number_or(initial_state.value("alpha", 0.05), 0.05), 0.0, 1.0);
    reset_on_state_change_ = json_bool_or(initial_state.value("reset_on_state_change", true), true);
  }

  void on_state(const std::string& field, const json& value, std::int64_t ts_ms, const json& meta) override {
    (void)ts_ms;
    (void)meta;
    if (field == "mode") mode_ = normalize_mode(value);
    if (field == "alpha") alpha_ = clamp_double(json_number_or(value, alpha_), 0.0, 1.0);
    if (field == "reset_on_state_change") reset_on_state_change_ = json_bool_or(value, reset_on_state_change_);
    if (reset_on_state_change_) trackers_.clear();
    dirty_ = true;
  }

  void on_data(const std::string& port, const json& value, std::int64_t ts_ms, const json& meta) override {
    (void)port;
    (void)value;
    (void)meta;
    const json out = compute_output("value", ts_ms);
    if (!out.is_null()) (void)emit("value", out, ts_ms);
  }

  json compute_output(const std::string& port, std::int64_t ctx_id) override {
    if (port != "value") return nullptr;
    const auto raw = pull("value", ctx_id);
    if (!raw.has_value()) return format_number_sequence(last_output_);
    const auto sample = json_number_sequence(raw.value());
    if (sample.empty()) return format_number_sequence(last_output_);
    if (!dirty_ && last_ctx_id_.has_value() && last_ctx_id_.value() == ctx_id && sample == last_input_) {
      return format_number_sequence(last_output_);
    }
    if (trackers_.size() != sample.size()) trackers_.assign(sample.size(), TrendTracker{});
    std::vector<double> out;
    out.reserve(sample.size());
    for (std::size_t i = 0; i < sample.size(); ++i) {
      out.push_back(mode_ == "LINEAR" ? trackers_[i].update_linear(sample[i], alpha_) : trackers_[i].update_constant(sample[i], alpha_));
    }
    last_input_ = sample;
    last_output_ = out;
    last_ctx_id_ = ctx_id;
    dirty_ = false;
    return format_number_sequence(last_output_);
  }

 private:
  struct TrendTracker {
    std::optional<double> level;
    double slope = 0.0;
    double update_constant(double value, double alpha) {
      if (!level.has_value()) level = value;
      else level = (1.0 - alpha) * level.value() + alpha * value;
      return value - level.value();
    }
    double update_linear(double value, double alpha) {
      if (!level.has_value()) {
        level = value;
        slope = 0.0;
        return 0.0;
      }
      const double predicted = level.value() + slope;
      const double residual = value - predicted;
      level = predicted + alpha * residual;
      slope += alpha * alpha * residual;
      return value - level.value();
    }
  };

  static std::string normalize_mode(const json& value) {
    std::string text = value.is_string() ? value.get<std::string>() : "CONSTANT";
    std::transform(text.begin(), text.end(), text.begin(), [](unsigned char ch) { return static_cast<char>(std::toupper(ch)); });
    return text == "LINEAR" ? "LINEAR" : "CONSTANT";
  }

  std::string mode_ = "CONSTANT";
  double alpha_ = 0.05;
  bool reset_on_state_change_ = true;
  std::vector<TrendTracker> trackers_;
  std::vector<double> last_input_;
  std::vector<double> last_output_;
  std::optional<std::int64_t> last_ctx_id_;
  bool dirty_ = true;
};

class RateLimiterNode final : public OperatorNode, public ComputableNode {
 public:
  RateLimiterNode(const std::string& node_id, const F8RuntimeNode& node, const json& initial_state)
      : OperatorNode(node_id, data_port_names(node.dataInPorts, {"value"}), data_port_names(node.dataOutPorts, {"value"}),
                     state_names(node.stateFields, {"inMin", "inMax", "maxRateUp", "maxRateDown", "maxAccel"}),
                     strings_or(node.execInPorts, {}), strings_or(node.execOutPorts, {})) {
    in_min_ = json_number_or(initial_state.value("inMin", 0.0), 0.0);
    in_max_ = json_number_or(initial_state.value("inMax", 1.0), 1.0);
    max_rate_up_ = std::max(0.0, json_number_or(initial_state.value("maxRateUp", 2.0), 2.0));
    max_rate_down_ = std::max(0.0, json_number_or(initial_state.value("maxRateDown", 2.0), 2.0));
    max_accel_ = std::max(0.0, json_number_or(initial_state.value("maxAccel", 0.0), 0.0));
  }

  void on_lifecycle(bool active, const json& meta) override {
    (void)active;
    (void)meta;
    last_time_s_.reset();
  }

  void on_state(const std::string& field, const json& value, std::int64_t ts_ms, const json& meta) override {
    (void)ts_ms;
    (void)meta;
    if (field == "inMin") in_min_ = json_number_or(value, in_min_);
    if (field == "inMax") in_max_ = json_number_or(value, in_max_);
    if (field == "maxRateUp") max_rate_up_ = std::max(0.0, json_number_or(value, max_rate_up_));
    if (field == "maxRateDown") max_rate_down_ = std::max(0.0, json_number_or(value, max_rate_down_));
    if (field == "maxAccel") max_accel_ = std::max(0.0, json_number_or(value, max_accel_));
    dirty_ = true;
  }

  void on_data(const std::string& port, const json& value, std::int64_t ts_ms, const json& meta) override {
    (void)port;
    (void)value;
    (void)meta;
    const json out = compute_output("value", ts_ms);
    if (!out.is_null()) (void)emit("value", out, ts_ms);
  }

  json compute_output(const std::string& port, std::int64_t ctx_id) override {
    if (port != "value") return nullptr;
    const auto raw = pull("value", ctx_id);
    if (!raw.has_value()) return last_out_.has_value() ? json(last_out_.value()) : json(nullptr);
    const auto numeric = json_number(raw.value());
    if (!numeric.has_value()) return last_out_.has_value() ? json(last_out_.value()) : json(nullptr);
    if (!dirty_ && last_ctx_id_.has_value() && last_ctx_id_.value() == ctx_id && last_in_.has_value() &&
        last_in_.value() == numeric.value()) {
      return last_out_.has_value() ? json(last_out_.value()) : json(nullptr);
    }
    const double out = step(numeric.value());
    last_in_ = numeric.value();
    last_out_ = out;
    last_ctx_id_ = ctx_id;
    dirty_ = false;
    return out;
  }

 private:
  double step(double x) {
    double lo = in_min_;
    double hi = in_max_;
    if (lo > hi) std::swap(lo, hi);
    const double x_clip = clamp_double(x, lo, hi);
    const double now_s = now_seconds();
    if (!y_.has_value()) {
      y_ = x_clip;
      v_ = 0.0;
      last_time_s_ = now_s;
      return y_.value();
    }
    if (!last_time_s_.has_value()) {
      last_time_s_ = now_s;
      return y_.value();
    }
    const double dt = std::max(1e-6, now_s - last_time_s_.value());
    last_time_s_ = now_s;
    const double err = x_clip - y_.value();
    double desired_v = err / dt;
    if (desired_v > 0.0) desired_v = std::min(desired_v, max_rate_up_);
    else desired_v = std::max(desired_v, -max_rate_down_);
    if (max_accel_ > 0.0) {
      const double max_dv = max_accel_ * dt;
      v_ += clamp_double(desired_v - v_, -max_dv, max_dv);
    } else {
      v_ = desired_v;
    }
    double y_new = y_.value() + v_ * dt;
    if (err != 0.0 && (x_clip - y_new) * err < 0.0) {
      y_new = x_clip;
      v_ = 0.0;
    }
    y_ = clamp_double(y_new, lo, hi);
    return y_.value();
  }

  double in_min_ = 0.0;
  double in_max_ = 1.0;
  double max_rate_up_ = 2.0;
  double max_rate_down_ = 2.0;
  double max_accel_ = 0.0;
  std::optional<double> y_;
  double v_ = 0.0;
  std::optional<double> last_time_s_;
  std::optional<double> last_out_;
  std::optional<double> last_in_;
  std::optional<std::int64_t> last_ctx_id_;
  bool dirty_ = true;
};

class TCodeNode final : public OperatorNode, public ComputableNode {
 public:
  TCodeNode(const std::string& node_id, const F8RuntimeNode& node, const json& initial_state)
      : OperatorNode(node_id, data_port_names(node.dataInPorts, default_axes()), data_port_names(node.dataOutPorts, {"tcode"}),
                     state_names(node.stateFields, {"intervalMs"}), strings_or(node.execInPorts, {}),
                     strings_or(node.execOutPorts, {})) {
    interval_ms_ = std::max(1, js_round(json_number_or(initial_state.value("intervalMs", 20), 20)));
  }

  json validate_state(const std::string& field, const json& value, std::int64_t ts_ms, const json& meta) override {
    (void)ts_ms;
    (void)meta;
    if (field != "intervalMs") return value;
    const auto numeric = json_number(value);
    if (!numeric.has_value()) throw std::invalid_argument("intervalMs must be a number");
    const int interval = std::max(1, js_round(numeric.value()));
    if (interval > 50000) throw std::invalid_argument("intervalMs must be <= 50000");
    return interval;
  }

  void on_state(const std::string& field, const json& value, std::int64_t ts_ms, const json& meta) override {
    if (field == "intervalMs") interval_ms_ = validate_state(field, value, ts_ms, meta).get<int>();
  }

  void on_data(const std::string& port, const json& value, std::int64_t ts_ms, const json& meta) override {
    (void)port;
    (void)value;
    (void)meta;
    const json out = compute_output("tcode", ts_ms);
    if (!out.is_null()) (void)emit("tcode", out, ts_ms);
  }

  json compute_output(const std::string& port, std::int64_t ctx_id) override {
    if (port != "tcode") return nullptr;
    const int interval = std::max(1, js_round(json_number_or(pull("intervalMs", ctx_id).value_or(interval_ms_), interval_ms_)));
    std::vector<std::string> commands;
    for (const auto& axis : axes()) {
      const auto raw = pull(axis, ctx_id);
      if (!raw.has_value()) continue;
      const auto numeric = json_number(raw.value());
      if (!numeric.has_value()) continue;
      const int payload = js_round(clamp_double(numeric.value(), 0.0, 1.0) * 9999.0);
      std::ostringstream part;
      part << axis << std::setw(4) << std::setfill('0') << payload << "I" << std::setw(3) << std::setfill('0') << interval;
      commands.push_back(part.str());
    }
    if (commands.empty()) return "";
    std::ostringstream out;
    for (std::size_t i = 0; i < commands.size(); ++i) {
      if (i > 0) out << ' ';
      out << commands[i];
    }
    out << '\n';
    return out.str();
  }

 private:
  static const std::array<std::string, 10>& axes() {
    static const std::array<std::string, 10> kAxes{"L0", "L1", "L2", "R0", "R1", "R2", "V0", "V1", "A0", "A1"};
    return kAxes;
  }
  static std::vector<std::string> default_axes() {
    std::vector<std::string> out(axes().begin(), axes().end());
    out.push_back("intervalMs");
    return out;
  }

  int interval_ms_ = 20;
};

class QuatToEulerNode final : public OperatorNode, public ComputableNode {
 public:
  QuatToEulerNode(const std::string& node_id, const F8RuntimeNode& node, const json& initial_state)
      : OperatorNode(node_id, data_port_names(node.dataInPorts, {"quat"}), data_port_names(node.dataOutPorts, {"euler"}),
                     state_names(node.stateFields, {"order", "degrees"}), strings_or(node.execInPorts, {}),
                     strings_or(node.execOutPorts, {})) {
    order_ = normalize_order(initial_state.value("order", "ZYX"));
    degrees_ = json_bool_or(initial_state.value("degrees", true), true);
  }

  void on_state(const std::string& field, const json& value, std::int64_t ts_ms, const json& meta) override {
    (void)ts_ms;
    (void)meta;
    if (field == "order") order_ = normalize_order(value);
    if (field == "degrees") degrees_ = json_bool_or(value, degrees_);
    dirty_ = true;
  }

  void on_data(const std::string& port, const json& value, std::int64_t ts_ms, const json& meta) override {
    (void)port;
    (void)value;
    (void)meta;
    const json out = compute_output("euler", ts_ms);
    if (!out.is_null()) (void)emit("euler", out, ts_ms);
  }

  json compute_output(const std::string& port, std::int64_t ctx_id) override {
    if (port != "euler") return nullptr;
    const auto raw = pull("quat", ctx_id);
    if (!raw.has_value() || !raw->is_array() || raw->size() != 4) return last_output_;
    std::array<double, 4> q{};
    double norm = 0.0;
    for (std::size_t i = 0; i < 4; ++i) {
      const auto n = json_number(raw.value()[i]);
      if (!n.has_value()) return last_output_;
      q[i] = n.value();
      norm += q[i] * q[i];
    }
    norm = std::sqrt(norm);
    if (norm <= 1e-9) return last_output_;
    for (double& v : q) v /= norm;
    if (!dirty_ && last_ctx_id_.has_value() && last_ctx_id_.value() == ctx_id && last_quat_ == q) return last_output_;
    const auto e = quat_to_euler(q);
    const double f = degrees_ ? 180.0 / kPi : 1.0;
    last_quat_ = q;
    last_output_ = json::array({e[0] * f, e[1] * f, e[2] * f});
    last_ctx_id_ = ctx_id;
    dirty_ = false;
    return last_output_;
  }

 private:
  static double clamp_unit(double v) { return clamp_double(v, -1.0, 1.0); }
  static std::string normalize_order(const json& value) {
    std::string text = value.is_string() ? value.get<std::string>() : "ZYX";
    std::transform(text.begin(), text.end(), text.begin(), [](unsigned char ch) { return static_cast<char>(std::toupper(ch)); });
    static const std::vector<std::string> orders{"XYZ", "XZY", "YXZ", "YZX", "ZXY", "ZYX"};
    for (const auto& order : orders) {
      if (text == order) return text;
    }
    return "ZYX";
  }

  std::array<double, 3> quat_to_euler(const std::array<double, 4>& q) const {
    const double w = q[0], x = q[1], y = q[2], z = q[3];
    const double m11 = 1.0 - 2.0 * (y * y + z * z);
    const double m12 = 2.0 * (x * y - z * w);
    const double m13 = 2.0 * (x * z + y * w);
    const double m21 = 2.0 * (x * y + z * w);
    const double m22 = 1.0 - 2.0 * (x * x + z * z);
    const double m23 = 2.0 * (y * z - x * w);
    const double m31 = 2.0 * (x * z - y * w);
    const double m32 = 2.0 * (y * z + x * w);
    const double m33 = 1.0 - 2.0 * (x * x + y * y);
    double ex = 0.0, ey = 0.0, ez = 0.0;
    if (order_ == "XYZ") {
      ey = std::asin(clamp_unit(m13));
      if (std::abs(m13) < 0.9999999) {
        ex = std::atan2(-m23, m33);
        ez = std::atan2(-m12, m11);
      } else {
        ex = std::atan2(m32, m22);
      }
    } else if (order_ == "XZY") {
      ez = std::asin(-clamp_unit(m12));
      if (std::abs(m12) < 0.9999999) {
        ex = std::atan2(m32, m22);
        ey = std::atan2(m13, m11);
      } else {
        ex = std::atan2(-m23, m33);
      }
    } else if (order_ == "YXZ") {
      ex = std::asin(-clamp_unit(m23));
      if (std::abs(m23) < 0.9999999) {
        ey = std::atan2(m13, m33);
        ez = std::atan2(m21, m22);
      } else {
        ey = std::atan2(-m31, m11);
      }
    } else if (order_ == "YZX") {
      ez = std::asin(clamp_unit(m21));
      if (std::abs(m21) < 0.9999999) {
        ex = std::atan2(-m23, m22);
        ey = std::atan2(-m31, m11);
      } else {
        ey = std::atan2(m13, m33);
      }
    } else if (order_ == "ZXY") {
      ex = std::asin(clamp_unit(m32));
      if (std::abs(m32) < 0.9999999) {
        ey = std::atan2(-m31, m33);
        ez = std::atan2(-m12, m22);
      } else {
        ez = std::atan2(m21, m11);
      }
    } else {
      ey = std::asin(-clamp_unit(m31));
      if (std::abs(m31) < 0.9999999) {
        ex = std::atan2(m32, m33);
        ez = std::atan2(m21, m11);
      } else {
        ez = std::atan2(-m12, m22);
      }
    }
    return {ex, ey, ez};
  }

  std::string order_ = "ZYX";
  bool degrees_ = true;
  std::array<double, 4> last_quat_{};
  json last_output_ = nullptr;
  std::optional<std::int64_t> last_ctx_id_;
  bool dirty_ = true;
};

class SilenceDetectorNode final : public OperatorNode {
 public:
  SilenceDetectorNode(const std::string& node_id, const F8RuntimeNode& node, const json& initial_state)
      : OperatorNode(node_id, data_port_names(node.dataInPorts, {"value"}), data_port_names(node.dataOutPorts, {}),
                     state_names(node.stateFields, {"silenceMs", "deltaThreshold", "isSilent"}),
                     strings_or(node.execInPorts, {"exec"}), strings_or(node.execOutPorts, {"exec"})) {
    silence_ms_ = std::max(0.0, json_number_or(initial_state.value("silenceMs", 500), 500));
    delta_threshold_ = std::max(0.0, json_number_or(initial_state.value("deltaThreshold", 0.001), 0.001));
  }

  json validate_state(const std::string& field, const json& value, std::int64_t ts_ms, const json& meta) override {
    (void)ts_ms;
    (void)meta;
    if (field == "silenceMs") return static_cast<int>(std::max(0.0, json_number_or(value, 0.0)));
    if (field == "deltaThreshold") return std::max(0.0, json_number_or(value, 0.0));
    return value;
  }

  void on_state(const std::string& field, const json& value, std::int64_t ts_ms, const json& meta) override {
    if (field == "silenceMs") silence_ms_ = validate_state(field, value, ts_ms, meta).get<double>();
    if (field == "deltaThreshold") delta_threshold_ = validate_state(field, value, ts_ms, meta).get<double>();
  }

  std::vector<std::string> on_exec(std::int64_t exec_id, const std::string& in_port) override {
    (void)in_port;
    sample_and_publish(exec_id);
    return exec_out_ports();
  }

 private:
  void sample_and_publish(std::int64_t ctx_id) {
    const auto raw = pull("value", ctx_id);
    const auto value = raw.has_value() ? json_number(raw.value()) : std::nullopt;
    const double now_s = now_seconds();
    if (value.has_value()) {
      if (!last_value_.has_value() || std::abs(value.value() - last_value_.value()) > delta_threshold_) {
        last_active_s_ = now_s;
      }
      last_value_ = value.value();
    }
    if (!last_active_s_.has_value()) last_active_s_ = now_s;
    const bool next_silent = silence_ms_ > 0.0 && ((now_s - last_active_s_.value()) * 1000.0 >= silence_ms_);
    if (next_silent != is_silent_) {
      is_silent_ = next_silent;
      (void)set_state("isSilent", is_silent_);
    }
  }

  double silence_ms_ = 500.0;
  double delta_threshold_ = 0.001;
  std::optional<double> last_value_;
  std::optional<double> last_active_s_;
  bool is_silent_ = false;
};

class SwitchMixerNode final : public OperatorNode, public ComputableNode {
 public:
  SwitchMixerNode(const std::string& node_id, const F8RuntimeNode& node, const json& initial_state)
      : OperatorNode(node_id, data_port_names(node.dataInPorts, {"ch1", "ch2"}), data_port_names(node.dataOutPorts, {"out", "alpha"}),
                     state_names(node.stateFields, {"currentChannel", "resolvedChannel", "fadeMs"}),
                     strings_or(node.execInPorts, {"exec"}), strings_or(node.execOutPorts, {"exec"})) {
    current_channel_ = initial_state.value("currentChannel", "ch1");
    fade_ms_ = std::max(0.0, json_number_or(initial_state.value("fadeMs", 200), 200));
    fade_tau_s_ = fade_ms_ > 0.0 ? fade_ms_ / 3000.0 : 0.0;
  }

  json validate_state(const std::string& field, const json& value, std::int64_t ts_ms, const json& meta) override {
    (void)ts_ms;
    (void)meta;
    if (field == "fadeMs") return static_cast<int>(std::max(0.0, json_number_or(value, 0.0)));
    if (field == "currentChannel") return value.is_string() ? value.get<std::string>() : value.dump();
    return value;
  }

  void on_state(const std::string& field, const json& value, std::int64_t ts_ms, const json& meta) override {
    if (field == "currentChannel") current_channel_ = validate_state(field, value, ts_ms, meta).get<std::string>();
    if (field == "fadeMs") {
      fade_ms_ = validate_state(field, value, ts_ms, meta).get<int>();
      fade_tau_s_ = fade_ms_ > 0.0 ? fade_ms_ / 3000.0 : 0.0;
    }
  }

  std::vector<std::string> on_exec(std::int64_t exec_id, const std::string& in_port) override {
    (void)in_port;
    const json out = compute_output("out", exec_id);
    const json alpha = compute_output("alpha", exec_id);
    if (!out.is_null()) (void)emit("out", out);
    if (!alpha.is_null()) (void)emit("alpha", alpha);
    return exec_out_ports();
  }

  void on_data(const std::string& port, const json& value, std::int64_t ts_ms, const json& meta) override {
    (void)meta;
    const auto numeric = json_number(value);
    if (numeric.has_value()) last_values_[port] = numeric.value();
    const json out = compute_output("out", ts_ms);
    const json alpha = compute_output("alpha", ts_ms);
    if (!out.is_null()) (void)emit("out", out, ts_ms);
    if (!alpha.is_null()) (void)emit("alpha", alpha, ts_ms);
  }

  json compute_output(const std::string& port, std::int64_t ctx_id) override {
    if (port != "out" && port != "alpha") return nullptr;
    if (last_ctx_id_.has_value() && last_ctx_id_.value() == ctx_id && cache_.contains(port)) return cache_[port];
    step(ctx_id);
    last_ctx_id_ = ctx_id;
    return cache_.value(port, nullptr);
  }

 private:
  std::string resolve_channel() const {
    if (!current_channel_.empty() &&
        std::find(data_in_ports().begin(), data_in_ports().end(), current_channel_) != data_in_ports().end()) {
      return current_channel_;
    }
    if (!resolved_channel_.empty() &&
        std::find(data_in_ports().begin(), data_in_ports().end(), resolved_channel_) != data_in_ports().end()) {
      return resolved_channel_;
    }
    return data_in_ports().empty() ? "" : data_in_ports().front();
  }

  void step(std::int64_t ctx_id) {
    for (const auto& port : data_in_ports()) {
      const auto raw = pull(port, ctx_id);
      if (!raw.has_value()) continue;
      const auto numeric = json_number(raw.value());
      if (numeric.has_value()) last_values_[port] = numeric.value();
    }
    const double now_s = now_seconds();
    const double dt = last_time_s_.has_value() ? std::max(0.0, now_s - last_time_s_.value()) : 0.0;
    last_time_s_ = now_s;
    const std::string desired = resolve_channel();
    if (desired != resolved_channel_) {
      const bool first = resolved_channel_.empty() && !cache_.contains("out");
      transition_from_ = cache_.value("out", last_values_.count(resolved_channel_) ? last_values_[resolved_channel_] : 0.0);
      resolved_channel_ = desired;
      alpha_ = first ? 1.0 : 0.0;
      if (first && last_values_.count(desired)) transition_from_ = last_values_[desired];
      (void)set_state("resolvedChannel", resolved_channel_);
    }
    const double target = last_values_.count(resolved_channel_) ? last_values_[resolved_channel_] : transition_from_;
    if (fade_tau_s_ <= 0.0) alpha_ = 1.0;
    else alpha_ = clamp_double(alpha_ + (1.0 - std::exp(-dt / fade_tau_s_)) * (1.0 - alpha_), 0.0, 1.0);
    const double out = (1.0 - alpha_) * transition_from_ + alpha_ * target;
    if (alpha_ >= 0.999999) transition_from_ = target;
    cache_ = json{{"out", out}, {"alpha", alpha_}};
  }

  std::string current_channel_ = "ch1";
  std::string resolved_channel_;
  double fade_ms_ = 200.0;
  double fade_tau_s_ = 200.0 / 3000.0;
  std::unordered_map<std::string, double> last_values_;
  double transition_from_ = 0.0;
  double alpha_ = 1.0;
  std::optional<double> last_time_s_;
  std::optional<std::int64_t> last_ctx_id_;
  json cache_ = json::object();
};

class UnsupportedOperatorNode final : public OperatorNode {
 public:
  UnsupportedOperatorNode(const std::string& node_id, const F8RuntimeNode& node, const json& initial_state)
      : OperatorNode(node_id, data_port_names(node.dataInPorts, {}), data_port_names(node.dataOutPorts, {}),
                     state_names(node.stateFields, {}), strings_or(node.execInPorts, {}),
                     strings_or(node.execOutPorts, {})),
        operator_class_(node.operatorClass.value_or("unknown")) {
    (void)initial_state;
  }

  void on_data(const std::string& port, const json& value, std::int64_t ts_ms, const json& meta) override {
    (void)value;
    (void)meta;
    report_error("CPP_OPERATOR_UNIMPLEMENTED",
                 operator_class_ + " is described by f8.cppengine but its native runtime is not implemented yet",
                 "warning", "cpp-unimplemented:" + operator_class_ + ":" + node_id(), ts_ms);
    (void)port;
  }

  std::vector<std::string> on_exec(std::int64_t exec_id, const std::string& in_port) override {
    (void)exec_id;
    (void)in_port;
    report_error("CPP_OPERATOR_UNIMPLEMENTED",
                 operator_class_ + " is described by f8.cppengine but its native runtime is not implemented yet",
                 "warning", "cpp-unimplemented:" + operator_class_ + ":" + node_id());
    return {};
  }

 private:
  std::string operator_class_;
};

class ScriptPlaceholderNode : public OperatorNode {
 public:
  ScriptPlaceholderNode(const std::string& node_id, const F8RuntimeNode& node, const json& initial_state, std::string lang)
      : OperatorNode(node_id, data_port_names(node.dataInPorts, {"msg"}), data_port_names(node.dataOutPorts, {"out"}),
                     state_names(node.stateFields, {"code"}), strings_or(node.execInPorts, {"exec"}),
                     strings_or(node.execOutPorts, {"exec"})),
        lang_(std::move(lang)) {
    code_ = initial_state.value("code", "");
  }

  json validate_state(const std::string& field, const json& value, std::int64_t ts_ms, const json& meta) override {
    (void)ts_ms;
    (void)meta;
    if (field == "code") return value.is_string() ? value.get<std::string>() : "";
    return value;
  }

  void on_state(const std::string& field, const json& value, std::int64_t ts_ms, const json& meta) override {
    (void)ts_ms;
    (void)meta;
    if (field == "code") code_ = value.is_string() ? value.get<std::string>() : "";
  }

  void on_data(const std::string& port, const json& value, std::int64_t ts_ms, const json& meta) override {
    (void)port;
    (void)value;
    report_error(script_error_code(), lang_ + " runtime bridge is not linked in this build", "warning",
                 lang_ + "-script-placeholder:" + node_id(), ts_ms);
    (void)meta;
  }

  std::vector<std::string> on_exec(std::int64_t exec_id, const std::string& in_port) override {
    (void)exec_id;
    (void)in_port;
    report_error(script_error_code(), lang_ + " runtime bridge is not linked in this build", "warning",
                 lang_ + "-script-placeholder:" + node_id());
    return exec_out_ports();
  }

 private:
  std::string script_error_code() const { return lang_ == "Lua" ? "LUA_SCRIPT_UNAVAILABLE" : "ANGELSCRIPT_UNAVAILABLE"; }

  std::string lang_;
  std::string code_;
};

json service_spec() {
  return json{{"specKind", "service"},
              {"schemaVersion", "f8service/1"},
              {"serviceClass", kServiceClass},
              {"paletteCategory", "svc"},
              {"version", "0.0.1"},
              {"label", "CppEngine"},
              {"description", "C++ execution engine for high-frequency Feel8 operator graphs."},
              {"tags", json::array({"engine", "cpp", "native"})},
              {"rendererClass", "default_container"},
              {"stateFields",
               json::array({state_field("dataDelivery", "Data Delivery",
                                         "How data inputs are delivered to local nodes.", string_enum_schema("buffered", {"buffered", "callback"}),
                                         "rw", true, true)})}};
}

json tick_spec() {
  return json{{"specKind", "operator"},
              {"schemaVersion", "f8operator/1"},
              {"serviceClass", kServiceClass},
              {"paletteCategory", std::string(kServiceClass) + ".execution"},
              {"operatorClass", "f8.tick"},
              {"version", "0.0.1"},
              {"label", "Tick"},
              {"description", "Source operator that generates periodic exec ticks."},
              {"tags", json::array({"execution", "timer", "start", "clock", "entrypoint"})},
              {"stateFields",
               json::array({state_field("tickMs", "Tick (ms)", "Interval in milliseconds for emitting exec ticks.",
                                         integer_schema(100, 1, 50000), "rw", true, true),
                            state_field("hiResTimer", "High-res Timer (Windows)",
                                        "Request high-resolution timer behavior where supported.", boolean_schema(true), "rw", true, false)})},
              {"execOutPorts", json::array({"exec"})},
              {"dataOutPorts",
               json::array({data_port("processingMs", "Per-tick processing time in milliseconds.", integer_schema(0), false, false),
                            data_port("intervalMs", "Actual interval between tick starts in milliseconds.", integer_schema(0), false, false),
                            data_port("latenessMs", "How late this tick started relative to its scheduled deadline.", integer_schema(0), false, false)})}};
}

json range_map_spec() {
  return json{{"specKind", "operator"},
              {"schemaVersion", "f8operator/1"},
              {"serviceClass", kServiceClass},
              {"paletteCategory", std::string(kServiceClass) + ".signal"},
              {"operatorClass", "f8.range_map"},
              {"version", "0.0.1"},
              {"label", "Range Map"},
              {"description", "Clip input to [inMin,inMax] then remap to [outMin,outMax] with a curve."},
              {"tags", json::array({"map", "range", "normalize", "curve", "transform"})},
              {"dataInPorts", json::array({data_port("value", "Input value.", number_schema(), false, true)})},
              {"dataOutPorts", json::array({data_port("value", "Mapped output.", number_schema(), false, true)})},
              {"stateFields",
               json::array({state_field("inMin", "Input Min", "Input range minimum.", number_schema(0.0)),
                            state_field("inMax", "Input Max", "Input range maximum.", number_schema(1.0)),
                            state_field("outMin", "Output Min", "Output range minimum.", number_schema(0.0), "rw", true, true),
                            state_field("outMax", "Output Max", "Output range maximum.", number_schema(1.0), "rw", true, true),
                            state_field("curve", "Curve", "Mapping curve.",
                                        string_enum_schema("LINEAR", {"LINEAR", "SMOOTHSTEP", "SMOOTHERSTEP", "EASE_IN", "EASE_OUT", "EASE_IN_OUT"}),
                                        "rw", true, true)})}};
}

json data_expr_spec() {
  return json{{"specKind", "operator"},
              {"schemaVersion", "f8operator/1"},
              {"serviceClass", kServiceClass},
              {"paletteCategory", std::string(kServiceClass) + ".expr"},
              {"operatorClass", "f8.data_expr"},
              {"version", "0.0.1"},
              {"label", "Data Expr"},
              {"description", "Evaluate a C++ scalar expression using numeric input values. Python-only syntax and numpy are unsupported."},
              {"tags", json::array({"expr", "math", "logic", "transform", "lightweight"})},
              {"dataInPorts", json::array({data_port("x", "Input value for the expression.", any_schema(), false, true)})},
              {"dataOutPorts", json::array({data_port("out", "Expression result.", any_schema(), false, true)})},
              {"editPolicy", json{{"dataInPorts", editable_collection_policy()}, {"dataOutPorts", editable_collection_policy()}}},
              {"stateFields", json::array({state_field("code", "Expr", "Scalar expression. Reference numeric input port names directly.",
                                                      string_schema("x"), "rw", true, true, "wrapline[cpp]")})}};
}

json exec_sequence_spec() {
  return json{{"specKind", "operator"},
              {"schemaVersion", "f8operator/1"},
              {"serviceClass", kServiceClass},
              {"paletteCategory", std::string(kServiceClass) + ".execution"},
              {"operatorClass", "f8.exec_sequence"},
              {"version", "0.0.1"},
              {"label", "Sequence"},
              {"description", "Exec flow splitter: triggers its exec outputs in order."},
              {"tags", json::array({"execution", "flow", "sequence", "branch"})},
              {"execInPorts", json::array({"exec"})},
              {"execOutPorts", json::array({"0", "1", "2"})},
              {"editPolicy", json{{"execOutPorts", editable_collection_policy()}}}};
}

json print_spec() {
  return json{{"specKind", "operator"},
              {"schemaVersion", "f8operator/1"},
              {"serviceClass", kServiceClass},
              {"paletteCategory", std::string(kServiceClass) + ".debug"},
              {"operatorClass", "f8.print"},
              {"version", "0.0.1"},
              {"label", "Print"},
              {"description", "Exec/data-driven printer for debugging graph values."},
              {"tags", json::array({"debug", "console", "print"})},
              {"execInPorts", json::array({"exec"})},
              {"dataInPorts", json::array({data_port("value", "value to print", any_schema(), false, true)})},
              {"stateFields", json::array({state_field("strip", "Strip",
                                                       "If true, strip whitespace/newlines from string values before printing.",
                                                       boolean_schema(true), "rw", true, false)})}};
}

json phase_spec() {
  return json{{"specKind", "operator"},
              {"schemaVersion", "f8operator/1"},
              {"serviceClass", kServiceClass},
              {"paletteCategory", std::string(kServiceClass) + ".signal"},
              {"operatorClass", "f8.phase"},
              {"version", "0.0.1"},
              {"label", "Phase"},
              {"description", "Phase accumulator. Outputs normalized phase (0..1) and unwrapped phase turns."},
              {"tags", json::array({"signal", "phase", "waveform", "generator", "oscillator"})},
              {"dataInPorts",
               json::array({data_port("hz", "Frequency override (Hz).", number_schema(), false, true),
                            data_port("phase", "Absolute phase override (0..1).", number_schema(), false, true),
                            data_port("reset", "If true, reset phase to 0.", boolean_schema(false), false, true)})},
              {"dataOutPorts",
               json::array({data_port("phase", "Normalized phase (0..1).", number_schema(), false, true),
                            data_port("phaseTurns", "Unwrapped phase turns (cycles).", number_schema(), false, true)})},
              {"stateFields", json::array({state_field("hz", "Hz", "Frequency in Hz.", number_schema(1.0, 0.0, 100.0),
                                                       "rw", true, true)})}};
}

json cosine_spec() {
  return json{{"specKind", "operator"},
              {"schemaVersion", "f8operator/1"},
              {"serviceClass", kServiceClass},
              {"paletteCategory", std::string(kServiceClass) + ".signal"},
              {"operatorClass", "f8.cosine"},
              {"version", "0.0.1"},
              {"label", "Cosine"},
              {"description", "Cosine phase transform. Provide phase (0..1) from an upstream phase driver."},
              {"tags", json::array({"signal", "cos", "waveform", "generator", "oscillator"})},
              {"dataInPorts",
               json::array({data_port("phase", "Phase input (0..1).", number_schema(), true, true),
                            data_port("amp", "Amplitude override.", number_schema(0.5), false, false),
                            data_port("dc", "DC offset override.", number_schema(0.5), false, false),
                            data_port("phaseOffset", "Phase offset override (0..1).", number_schema(), false, false)})},
              {"dataOutPorts", json::array({data_port("value", "cosine output", number_schema(), false, true)})},
              {"stateFields",
               json::array({state_field("dc", "DC", "Default DC offset.", number_schema(0.5)),
                            state_field("amp", "Amp", "Amplitude.", number_schema(0.5)),
                            state_field("phaseOffset", "Phase Offset", "Normalized phase offset (0.0 to 1.0).",
                                        number_schema(0.0, 0.0, 1.0))})}};
}

json tempest_spec() {
  return json{{"specKind", "operator"},
              {"schemaVersion", "f8operator/1"},
              {"serviceClass", kServiceClass},
              {"paletteCategory", std::string(kServiceClass) + ".signal"},
              {"operatorClass", "f8.tempest"},
              {"version", "0.0.1"},
              {"label", "Tempest"},
              {"description", "Tempest phase transform (phase-modulated cosine)."},
              {"tags", json::array({"signal", "waveform", "generator", "oscillator", "tempest"})},
              {"dataInPorts",
               json::array({data_port("phase", "Phase input (0..1).", number_schema(), true, true),
                            data_port("amp", "Amplitude override.", number_schema(), false, false),
                            data_port("phaseOffset", "Phase offset override (0..1).", number_schema(), false, false),
                            data_port("eccentric", "Eccentricity override", number_schema(), false, false),
                            data_port("dc", "DC offset override.", number_schema(), false, false)})},
              {"dataOutPorts", json::array({data_port("out", "tempest output", number_schema(), false, true)})},
              {"stateFields",
               json::array({state_field("dc", "DC", "Default DC offset.", number_schema(0.5)),
                            state_field("amp", "Amp", "Default amplitude.", number_schema(0.5)),
                            state_field("phaseOffset", "Phase Offset", "Fraction of a full cycle added to the phase.",
                                        number_schema(0.0, 0.0, 1.0)),
                            state_field("eccentric", "Eccentric", "Controls curvature of the inner sine.", number_schema(0.0))})}};
}

json smooth_filter_spec() {
  return json{{"specKind", "operator"},
              {"schemaVersion", "f8operator/1"},
              {"serviceClass", kServiceClass},
              {"paletteCategory", std::string(kServiceClass) + ".signal"},
              {"operatorClass", "f8.smooth_filter"},
              {"version", "0.0.1"},
              {"label", "Smooth Filter"},
              {"description", "Smooths scalar or vector inputs with EMA/DEMA/One Euro filtering."},
              {"tags", json::array({"filter", "smoothing", "one_euro", "signal"})},
              {"dataInPorts", json::array({data_port("value", "Value to filter.", any_schema(), false, true)})},
              {"dataOutPorts", json::array({data_port("value", "Filtered output.", any_schema(), false, true)})},
              {"stateFields",
               json::array({state_field("filter_type", "Filter", "Filter type.",
                                        string_enum_schema("EMA", {"NONE", "EMA", "DEMA", "ONEEURO"}), "rw", true, true),
                            state_field("ema_alpha", "EMA Alpha", "EMA smoothing factor (0..1).", number_schema(0.4, 0.0, 1.0),
                                        "rw", true, true, "slider"),
                            state_field("dema_alpha", "DEMA Alpha", "DEMA smoothing factor (0..1).", number_schema(0.4, 0.0, 1.0),
                                        "rw", true, false, "slider"),
                            state_field("one_euro_min_cutoff", "One Euro Min Cutoff", "Minimum cutoff frequency.",
                                        number_schema(1.5, 0.01, 10.0), "rw", true, false, "slider"),
                            state_field("one_euro_beta", "One Euro Beta", "Speed coefficient for dynamic cutoff.",
                                        number_schema(0.0, 0.0, 5.0), "rw", true, false, "slider"),
                            state_field("one_euro_derivative_cutoff", "One Euro Derivative Cutoff",
                                        "Cutoff frequency for the derivative filter.", number_schema(1.0, 0.01, 10.0),
                                        "rw", true, false, "slider"),
                            state_field("one_euro_default_freq", "One Euro Default Freq", "Default sampling frequency (Hz).",
                                        number_schema(90.0, 1.0, 240.0), "rw", true, false, "slider")})}};
}

json detrend_spec() {
  return json{{"specKind", "operator"},
              {"schemaVersion", "f8operator/1"},
              {"serviceClass", kServiceClass},
              {"paletteCategory", std::string(kServiceClass) + ".signal"},
              {"operatorClass", "f8.detrend"},
              {"version", "0.0.1"},
              {"label", "Detrend"},
              {"description", "Removes slow baseline or linear trend from scalar or vector inputs."},
              {"tags", json::array({"signal", "detrend", "filter"})},
              {"dataInPorts", json::array({data_port("value", "Value to detrend.", any_schema(), false, true)})},
              {"dataOutPorts", json::array({data_port("value", "Detrended output.", any_schema(), false, true)})},
              {"stateFields",
               json::array({state_field("mode", "Mode", "Detrend mode.",
                                        string_enum_schema("CONSTANT", {"CONSTANT", "LINEAR"}), "rw", true, true),
                            state_field("alpha", "Alpha", "Trend tracking smoothing factor.", number_schema(0.05, 0.0, 1.0),
                                        "rw", true, true, "slider"),
                            state_field("reset_on_state_change", "Reset On State Change",
                                        "Reset tracker history when parameters change.", boolean_schema(true), "rw", true, false)})}};
}

json rate_limiter_spec() {
  return json{{"specKind", "operator"},
              {"schemaVersion", "f8operator/1"},
              {"serviceClass", kServiceClass},
              {"paletteCategory", std::string(kServiceClass) + ".signal"},
              {"operatorClass", "f8.rate_limiter"},
              {"version", "0.0.1"},
              {"label", "Rate Limiter"},
              {"description", "Limits the rate of change and optionally acceleration of an input signal."},
              {"tags", json::array({"signal", "limit", "rate", "slew", "smoothing", "transform"})},
              {"dataInPorts", json::array({data_port("value", "Input value.", number_schema(), false, true)})},
              {"dataOutPorts", json::array({data_port("value", "Rate-limited output.", number_schema(), false, true)})},
              {"stateFields",
               json::array({state_field("inMin", "Input Min", "Input/output clamp minimum.", number_schema(0.0)),
                            state_field("inMax", "Input Max", "Input/output clamp maximum.", number_schema(1.0)),
                            state_field("maxRateUp", "Max Rate Up", "Maximum rising rate (units/sec).",
                                        number_schema(2.0, 0.0), "rw", true, true),
                            state_field("maxRateDown", "Max Rate Down", "Maximum falling rate (units/sec).",
                                        number_schema(2.0, 0.0), "rw", true, true),
                            state_field("maxAccel", "Max Accel", "Maximum acceleration. 0 disables acceleration limiting.",
                                        number_schema(0.0, 0.0))})}};
}

json tcode_spec() {
  json ins = json::array();
  const std::array<std::string, 10> axes{"L0", "L1", "L2", "R0", "R1", "R2", "V0", "V1", "A0", "A1"};
  for (std::size_t i = 0; i < axes.size(); ++i) {
    ins.push_back(data_port(axes[i], "Axis " + axes[i] + " (0..1).", number_schema(), false, i == 0));
  }
  ins.push_back(data_port("intervalMs", "Optional interval override in milliseconds.", number_schema(20.0, 1.0, 50000.0), false, false));
  return json{{"specKind", "operator"},
              {"schemaVersion", "f8operator/1"},
              {"serviceClass", kServiceClass},
              {"paletteCategory", std::string(kServiceClass) + ".output"},
              {"operatorClass", "f8.tcode"},
              {"version", "0.0.1"},
              {"label", "TCode"},
              {"description", "Generates TCode v0.3 command strings from normalized axis values."},
              {"tags", json::array({"transform", "tcode", "osr", "command", "string"})},
              {"dataInPorts", ins},
              {"dataOutPorts", json::array({data_port("tcode", "TCode v0.3 command string", string_schema(""), false, true)})},
              {"stateFields", json::array({state_field("intervalMs", "Interval (ms)",
                                                       "Default interval appended as I### when intervalMs input is not provided.",
                                                       number_schema(20.0, 1.0, 50000.0), "rw", true, true)})}};
}

json quat_to_euler_spec() {
  return json{{"specKind", "operator"},
              {"schemaVersion", "f8operator/1"},
              {"serviceClass", kServiceClass},
              {"paletteCategory", std::string(kServiceClass) + ".motion"},
              {"operatorClass", "f8.quat_to_euler"},
              {"version", "0.0.1"},
              {"label", "Quat To Euler"},
              {"description", "Converts quaternion [w,x,y,z] to Euler angles with configurable order."},
              {"tags", json::array({"math", "rotation", "quaternion", "euler", "transform"})},
              {"dataInPorts", json::array({data_port("quat", "Input quaternion [w,x,y,z].", array_schema(number_schema()), false, true)})},
              {"dataOutPorts", json::array({data_port("euler", "Euler angles [x,y,z] in selected order.", array_schema(number_schema()), false, true)})},
              {"stateFields",
               json::array({state_field("order", "Order", "Euler rotation order.",
                                        string_enum_schema("ZYX", {"XYZ", "XZY", "YXZ", "YZX", "ZXY", "ZYX"}), "rw", true, true),
                            state_field("degrees", "Degrees", "Output in degrees when true, radians when false.",
                                        boolean_schema(true), "rw", true, true)})}};
}

json silence_detector_spec() {
  return json{{"specKind", "operator"},
              {"schemaVersion", "f8operator/1"},
              {"serviceClass", kServiceClass},
              {"paletteCategory", std::string(kServiceClass) + ".analysis"},
              {"operatorClass", "f8.silence_detector"},
              {"version", "0.0.1"},
              {"label", "Silence Detector"},
              {"description", "Detect whether a signal has stayed nearly unchanged for long enough to be considered silent."},
              {"tags", json::array({"analysis", "silence", "activity", "state", "gate"})},
              {"execInPorts", json::array({"exec"})},
              {"execOutPorts", json::array({"exec"})},
              {"dataInPorts", json::array({data_port("value", "Signal to analyze", number_schema(), false, true)})},
              {"dataOutPorts", json::array()},
              {"stateFields",
               json::array({state_field("silenceMs", "Silence (ms)",
                                        "If the input changes less than deltaThreshold for this long, mark it silent.",
                                        integer_schema(500, 0, 60000), "rw", true, true),
                            state_field("deltaThreshold", "Delta Threshold",
                                        "Absolute change threshold to treat the input as active.", number_schema(0.001, 0.0),
                                        "rw", true, true),
                            state_field("isSilent", "Is Silent",
                                        "Readonly sparse state output indicating whether the signal is currently silent.",
                                        boolean_schema(false), "ro", true, true)})}};
}

json switch_mixer_spec() {
  return json{{"specKind", "operator"},
              {"schemaVersion", "f8operator/1"},
              {"serviceClass", kServiceClass},
              {"paletteCategory", std::string(kServiceClass) + ".motion"},
              {"operatorClass", "f8.switch_mixer"},
              {"version", "0.0.3"},
              {"label", "Switch Mixer"},
              {"description", "Switch between user-defined input channels with an optional smooth crossfade."},
              {"tags", json::array({"mix", "switch", "channel", "track", "crossfade"})},
              {"execInPorts", json::array({"exec"})},
              {"execOutPorts", json::array({"exec"})},
              {"dataInPorts",
               json::array({data_port("ch1", "Input channel 1", number_schema(), false, true),
                            data_port("ch2", "Input channel 2", number_schema(), false, true)})},
              {"dataOutPorts",
               json::array({data_port("out", "Mixed output", number_schema(), false, true),
                            data_port("alpha", "Transition progress (0..1)", number_schema(), false, true)})},
              {"editPolicy", json{{"dataInPorts", editable_collection_policy()}}},
              {"stateFields",
               json::array({state_field("currentChannel", "Current Channel",
                                        "Name of the selected input channel/track to play.", string_schema("ch1"), "rw", true, true),
                            state_field("resolvedChannel", "Resolved Channel",
                                        "Readonly currently resolved input channel after validation/fallback.", string_schema(""), "ro", true, true),
                            state_field("fadeMs", "Fade (ms)", "Transition duration in milliseconds.",
                                        integer_schema(200, 0, 60000), "rw", true, true)})}};
}

json simple_operator_spec(const std::string& operator_class, const std::string& label, const std::string& category,
                          const std::vector<json>& data_in, const std::vector<json>& data_out,
                          const std::vector<json>& states, const std::vector<std::string>& exec_in = {},
                          const std::vector<std::string>& exec_out = {}, const json& edit_policy = nullptr) {
  json spec{{"specKind", "operator"},
            {"schemaVersion", "f8operator/1"},
            {"serviceClass", kServiceClass},
            {"paletteCategory", std::string(kServiceClass) + "." + category},
            {"operatorClass", operator_class},
            {"version", "0.0.1"},
            {"label", label},
            {"description", label + " operator described for C++ engine graphs. Native runtime is pending."},
            {"tags", json::array({"cpp", "pending"})}};
  if (!exec_in.empty()) spec["execInPorts"] = exec_in;
  if (!exec_out.empty()) spec["execOutPorts"] = exec_out;
  if (!data_in.empty()) spec["dataInPorts"] = data_in;
  if (!data_out.empty()) spec["dataOutPorts"] = data_out;
  if (!states.empty()) spec["stateFields"] = states;
  if (!edit_policy.is_null()) spec["editPolicy"] = edit_policy;
  return spec;
}

std::vector<json> remaining_operator_specs() {
  std::vector<json> specs;
  specs.push_back(simple_operator_spec(
      "f8.bandpass_filter", "Bandpass Filter", "signal",
      {data_port("value", "Value to filter.", any_schema())}, {data_port("value", "Filtered output.", any_schema())},
      {state_field("sampleIntervalMs", "Sample Interval (ms)", "Sampling interval in milliseconds.", number_schema(1000.0 / 120.0, 0.001, 50000.0), "rw", true, true),
       state_field("low_cutoff", "Low Cutoff", "Lower band edge in Hz.", number_schema(1.0, 0.001, 5000.0), "rw", true, true),
       state_field("high_cutoff", "High Cutoff", "Upper band edge in Hz.", number_schema(8.0, 0.001, 5000.0), "rw", true, true),
       state_field("order", "Order", "Butterworth filter order.", number_schema(2.0, 1.0, 12.0)),
       state_field("reset_on_state_change", "Reset On State Change", "Reset filter history when parameters change.", boolean_schema(true))}));
  specs.push_back(simple_operator_spec(
      "f8.highpass_filter", "Highpass Filter", "signal",
      {data_port("value", "Value to filter.", any_schema())}, {data_port("value", "Filtered output.", any_schema())},
      {state_field("sampleIntervalMs", "Sample Interval (ms)", "Sampling interval in milliseconds.", number_schema(1000.0 / 120.0, 0.001, 50000.0), "rw", true, true),
       state_field("cutoff", "Cutoff", "High-pass cutoff frequency in Hz.", number_schema(1.0, 0.001, 5000.0), "rw", true, true),
       state_field("order", "Order", "Butterworth filter order.", number_schema(2.0, 1.0, 12.0)),
       state_field("reset_on_state_change", "Reset On State Change", "Reset filter history when parameters change.", boolean_schema(true))}));
  specs.push_back(simple_operator_spec(
      "f8.lowpass_filter", "Lowpass Filter", "signal",
      {data_port("value", "Value to filter.", any_schema())}, {data_port("value", "Filtered output.", any_schema())},
      {state_field("sampleIntervalMs", "Sample Interval (ms)", "Sampling interval in milliseconds.", number_schema(1000.0 / 120.0, 0.001, 50000.0), "rw", true, true),
       state_field("cutoff", "Cutoff", "Low-pass cutoff frequency in Hz.", number_schema(8.0, 0.001, 5000.0), "rw", true, true),
       state_field("order", "Order", "Butterworth filter order.", number_schema(2.0, 1.0, 12.0)),
       state_field("reset_on_state_change", "Reset On State Change", "Reset filter history when parameters change.", boolean_schema(true))}));
  specs.push_back(simple_operator_spec(
      "f8.envelope", "Envelope", "signal", {data_port("value", "Value input.", any_schema())},
      {data_port("lower", "Lower envelope.", any_schema()), data_port("upper", "Upper envelope.", any_schema()),
       data_port("normalized", "Normalized output.", any_schema())},
      {state_field("method", "Method", "Envelope method.", string_enum_schema("EMA", {"EMA", "SMA"}), "rw", true, true),
       state_field("rise_alpha", "Rise Alpha", "Rise alpha.", number_schema(0.2, 0.0, 1.0), "rw", true, true),
       state_field("fall_alpha", "Fall Alpha", "Fall alpha.", number_schema(0.05, 0.0, 1.0), "rw", true, true),
       state_field("min_span", "Min Span", "Minimum envelope span.", number_schema(0.001, 0.0)),
       state_field("sma_window", "SMA Window", "Simple moving average window.", integer_schema(30, 1, 10000)),
       state_field("margin", "Margin", "Envelope margin.", number_schema(0.0, 0.0)),
       state_field("jumpEnabled", "Jump Enabled", "Enable jump reseed.", boolean_schema(true)),
       state_field("jumpSpanMult", "Jump Span Mult", "Jump span multiplier.", number_schema(4.0, 0.0)),
       state_field("jumpConsecutiveFrames", "Jump Consecutive Frames", "Frames before jump reseed.", integer_schema(3, 1)),
       state_field("jumpReseedFrames", "Jump Reseed Frames", "Frames used for reseed.", integer_schema(6, 1))}));
  specs.push_back(simple_operator_spec(
      "f8.periodicity_detector", "Periodicity Detector", "analysis",
      {data_port("value", "Scalar signal input.", number_schema())},
      {data_port("confidence", "Periodicity confidence.", number_schema()), data_port("rms", "RMS level.", number_schema()),
       data_port("periodicEnergy", "Periodic energy.", number_schema()), data_port("periodMs", "Period in milliseconds.", number_schema()),
       data_port("period_hz", "Period in Hz.", number_schema()), data_port("is_periodic", "Whether periodic.", boolean_schema(false))},
      {state_field("window", "Window", "Analysis window.", integer_schema(240, 1)),
       state_field("min_lag", "Min Lag", "Minimum lag.", integer_schema(4, 1)),
       state_field("max_lag", "Max Lag", "Maximum lag.", integer_schema(120, 1)),
       state_field("peak_prominence", "Peak Prominence", "Peak prominence.", number_schema(0.1, 0.0)),
       state_field("min_peaks", "Min Peaks", "Minimum peaks.", integer_schema(1, 1)),
       state_field("smoothing_alpha", "Smoothing Alpha", "Smoothing alpha.", number_schema(0.3, 0.0, 1.0)),
       state_field("noise_floor", "Noise Floor", "Noise floor.", number_schema(0.001, 0.0)),
       state_field("threshold", "Threshold", "Periodicity threshold.", number_schema(0.5, 0.0, 1.0)),
       state_field("rms_window", "RMS Window", "RMS window.", integer_schema(60, 1)),
       state_field("sampleIntervalMs", "Sample Interval (ms)", "Sampling interval.", number_schema(1000.0 / 120.0, 0.001, 50000.0)),
       state_field("reset_on_missing", "Reset On Missing", "Reset when input is missing.", boolean_schema(true))}));
  specs.push_back(simple_operator_spec(
      "f8.bone_filter", "Bone Filter", "motion", {data_port("bone", "Bone input.", any_schema())},
      {data_port("filtered", "Filtered bone.", any_schema()), data_port("relative", "Relative bone.", any_schema())},
      {state_field("filter_type", "Filter", "Filter type.", string_schema("EMA"), "rw", true, true),
       state_field("ema_alpha", "EMA Alpha", "EMA smoothing factor.", number_schema(0.4, 0.0, 1.0), "rw", true, true),
       state_field("dema_alpha", "DEMA Alpha", "DEMA smoothing factor.", number_schema(0.4, 0.0, 1.0)),
       state_field("one_euro_min_cutoff", "One Euro Min Cutoff", "Minimum cutoff.", number_schema(1.5, 0.01, 10.0)),
       state_field("one_euro_beta", "One Euro Beta", "Speed coefficient.", number_schema(0.0, 0.0, 5.0)),
       state_field("one_euro_derivative_cutoff", "One Euro Derivative Cutoff", "Derivative cutoff.", number_schema(1.0, 0.01, 10.0)),
       state_field("one_euro_default_freq", "One Euro Default Freq", "Default frequency.", number_schema(90.0, 1.0, 240.0)),
       state_field("jumpEnabled", "Jump Enabled", "Enable jump rejection.", boolean_schema(true)),
       state_field("jumpPosThreshold", "Jump Pos Threshold", "Position jump threshold.", number_schema(1.0, 0.0)),
       state_field("jumpRotDegThreshold", "Jump Rot Deg Threshold", "Rotation jump threshold.", number_schema(45.0, 0.0)),
       state_field("jumpConsecutiveFrames", "Jump Consecutive Frames", "Consecutive frames.", integer_schema(2, 1)),
       state_field("jumpCooldownFrames", "Jump Cooldown Frames", "Cooldown frames.", integer_schema(10, 0))}));
  specs.push_back(simple_operator_spec(
      "f8.bone_selector", "Bone Selector", "motion", {data_port("skeleton", "Skeleton input.", any_schema())},
      {data_port("bone", "Selected bone.", any_schema())},
      {state_field("target", "Target", "Target bone name.", string_schema(""), "rw", true, true),
       state_field("availableBones", "Available Bones", "Available bone names.", array_schema(string_schema("")), "ro", true, false)}));
  specs.push_back(simple_operator_spec(
      "f8.playback_sync", "Playback Sync", "playback", {data_port("playback", "Playback snapshot.", any_schema())},
      {data_port("position", "Position.", number_schema()), data_port("rawPosition", "Raw position.", number_schema()),
       data_port("duration", "Duration.", number_schema()), data_port("playing", "Playing.", boolean_schema(false)),
       data_port("videoId", "Video id.", string_schema("")), data_port("ageMs", "Age in ms.", number_schema()),
       data_port("stale", "Stale.", boolean_schema(false))},
      {state_field("maxExtrapolateMs", "Max Extrapolate (ms)", "Maximum extrapolation time.", integer_schema(500, 0)),
       state_field("playbackRate", "Playback Rate", "Playback rate.", number_schema(1.0, 0.0)),
       state_field("clampToDuration", "Clamp To Duration", "Clamp position to duration.", boolean_schema(true))}));
  specs.push_back(simple_operator_spec(
      "f8.program_wave", "Program Wave", "wave", {}, {data_port("phaseTurns", "Phase turns.", number_schema()),
                                                        data_port("phase", "Phase.", number_schema()),
                                                        data_port("active", "Active.", boolean_schema(false)),
                                                        data_port("done", "Done.", boolean_schema(false)),
                                                        data_port("elapsedSec", "Elapsed seconds.", number_schema())},
      {state_field("program", "Program", "Program definition.", any_schema(), "rw", true, true)}));
  specs.push_back(simple_operator_spec(
      "f8.sequence_player", "Sequence Player", "wave", {}, {data_port("value", "Value.", any_schema()),
                                                             data_port("index", "Index.", integer_schema(0)),
                                                             data_port("active", "Active.", boolean_schema(false)),
                                                             data_port("done", "Done.", boolean_schema(false)),
                                                             data_port("elapsedSec", "Elapsed seconds.", number_schema())},
      {state_field("sequence", "Sequence", "Sequence definition.", any_schema(), "rw", true, true)}));
  specs.push_back(simple_operator_spec(
      "f8.wave_expr", "Wave Expr", "wave", {data_port("t", "Time/phase input.", number_schema())},
      {data_port("value", "Value.", number_schema())},
      {state_field("template", "Template", "Expression template.", string_schema("sin"), "rw", true, true),
       state_field("maxT", "Max T", "Maximum t.", number_schema(1.0, 0.0), "rw", true, true),
       state_field("minValue", "Min Value", "Minimum output.", number_schema(0.0)),
       state_field("maxValue", "Max Value", "Maximum output.", number_schema(1.0)),
       state_field("express", "Express", "Expression text.", string_schema(""), "rw", true, true),
       state_field("preview", "Preview", "Preview points.", any_schema(), "ro", true, false)},
      {}, {}, json{{"stateFields", editable_collection_policy()}}));
  specs.push_back(simple_operator_spec(
      "f8.wave_pattern", "Wave Pattern", "wave", {data_port("t", "Time/phase input.", number_schema())},
      {data_port("value", "Value.", number_schema())},
      {state_field("points", "Points", "Pattern points.", any_schema(), "rw", true, true),
       state_field("maxT", "Max T", "Maximum t.", number_schema(1.0, 0.0), "rw", true, true),
       state_field("minValue", "Min Value", "Minimum output.", number_schema(0.0)),
       state_field("maxValue", "Max Value", "Maximum output.", number_schema(1.0)),
       state_field("interp", "Interp", "Interpolation mode.", string_schema("linear")),
       state_field("preview", "Preview", "Preview points.", any_schema(), "ro", true, false)},
      {}, {}, json{{"stateFields", editable_collection_policy()}}));
  specs.push_back(simple_operator_spec(
      "f8.wave_funscript", "Wave Funscript", "wave", {data_port("t", "Time/phase input.", number_schema())},
      {data_port("value", "Value.", number_schema())},
      {state_field("funscriptPath", "Funscript Path", "Funscript path.", string_schema(""), "rw", true, true),
       state_field("allAxes", "All Axes", "All axes.", any_schema(), "ro", true, false),
       state_field("selectedAxis", "Selected Axis", "Selected axis.", string_schema("")),
       state_field("points", "Points", "Loaded points.", any_schema(), "ro", true, false),
       state_field("maxT", "Max T", "Maximum t.", number_schema(1.0, 0.0)),
       state_field("interp", "Interp", "Interpolation mode.", string_schema("linear")),
       state_field("heatmap", "Heatmap", "Heatmap preview.", any_schema(), "ro", true, false)},
      {}, {}, json{{"stateFields", editable_collection_policy()}}));
  specs.push_back(simple_operator_spec(
      "f8.skeleton_decoder", "Skeleton Decoder", "motion", {data_port("packet", "Skeleton packet.", any_schema())},
      {data_port("skeletons", "Skeletons.", any_schema()), data_port("selectedSkeleton", "Selected skeleton.", any_schema())},
      {state_field("cleanupAfterMs", "Cleanup After (ms)", "Cleanup timeout.", integer_schema(1000, 0)),
       state_field("selectedKey", "Selected Key", "Selected skeleton key.", string_schema(""), "rw", true, true),
       state_field("availableKeys", "Available Keys", "Available skeleton keys.", array_schema(string_schema("")), "ro", true, false)},
      {"packet"}, {"packet"}));
  specs.push_back(simple_operator_spec(
      "f8.vmc_decoder", "VMC Decoder", "motion", {data_port("packet", "VMC packet.", any_schema())},
      {data_port("skeletons", "Skeletons.", any_schema()), data_port("selectedSkeleton", "Selected skeleton.", any_schema())},
      {state_field("cleanupAfterMs", "Cleanup After (ms)", "Cleanup timeout.", integer_schema(1000, 0)),
       state_field("selectedKey", "Selected Key", "Selected skeleton key.", string_schema(""), "rw", true, true),
       state_field("availableKeys", "Available Keys", "Available skeleton keys.", array_schema(string_schema("")), "ro", true, false)},
      {"packet"}, {"packet"}));
  specs.push_back(simple_operator_spec(
      "f8.state_trigger", "State Trigger", "state", {}, {},
      {state_field("value", "Value", "Watched state value.", any_schema(), "rw", true, true),
       state_field("enabled", "Enabled", "Enable trigger.", boolean_schema(true), "rw", true, true),
       state_field("fireOnStart", "Fire On Start", "Fire on activation.", boolean_schema(false))},
      {}, {"changed"}));
  specs.push_back(simple_operator_spec(
      "f8.state_expr", "State Expr", "state", {}, {},
      {state_field("allowNumpy", "Allow Numpy", "Python-only compatibility flag; ignored by C++.", boolean_schema(false)),
       state_field("code", "Code", "Expression code.", string_schema("out = 0"), "rw", true, true, "wrapline[cpp]"),
       state_field("out", "Out", "Expression output.", any_schema(), "rw", true, true)},
      {}, {}, json{{"stateFields", editable_collection_policy()}}));
  specs.push_back(simple_operator_spec(
      "f8.recorder", "Recorder", "io", {}, {},
      {state_field("path", "Path", "Recording path.", string_schema(""), "rw", true, true),
       state_field("enabled", "Enabled", "Enable recording.", boolean_schema(true), "rw", true, true),
       state_field("append", "Append", "Append to existing file.", boolean_schema(false)),
       state_field("recording", "Recording", "Recording state.", boolean_schema(false), "ro", true, true),
       state_field("sessionStartTsMs", "Session Start", "Session start timestamp.", integer_schema(0, 0), "ro")},
      {"record"}));
  specs.push_back(simple_operator_spec(
      "f8.replayer", "Replayer", "io", {}, {data_port("positionMs", "Replay position in ms.", number_schema())},
      {state_field("path", "Path", "Replay path.", string_schema(""), "rw", true, true),
       state_field("loop", "Loop", "Loop replay.", boolean_schema(false)),
       state_field("timeMode", "Time Mode", "Replay time mode.", string_schema("wall")),
       state_field("playing", "Playing", "Playing.", boolean_schema(false), "ro", true, true),
       state_field("durationMs", "Duration (ms)", "Duration.", integer_schema(0, 0), "ro"),
       state_field("loaded", "Loaded", "Loaded.", boolean_schema(false), "ro")},
      {"play", "pause", "stop"}, {"sample", "started", "stopped", "looped", "done"}));
  specs.push_back(simple_operator_spec(
      "f8.serial_out", "Serial Out", "output", {data_port("value", "Value to send.", any_schema())},
      {data_port("isOpen", "Whether port is open.", boolean_schema(false)), data_port("writtenBytes", "Written bytes.", integer_schema(0, 0)),
       data_port("error", "Error.", string_schema(""))},
      {state_field("enabled", "Enabled", "Enable serial output.", boolean_schema(true), "rw", true, true),
       state_field("port", "Port", "Serial port.", string_schema(""), "rw", true, true),
       state_field("baudrate", "Baudrate", "Baud rate.", integer_schema(115200, 1), "rw", true, true)},
      {"exec"}));
  specs.push_back(simple_operator_spec(
      "f8.udp_in", "UDP In", "io", {}, {data_port("text", "Text payload.", string_schema("")),
                                         data_port("raw", "Raw payload.", any_schema()),
                                         data_port("json", "JSON payload.", any_schema()),
                                         data_port("packet", "Packet.", any_schema())},
      {state_field("bindAddress", "Bind Address", "Bind address.", string_schema("127.0.0.1")),
       state_field("allowNonLoopbackBind", "Allow Non-loopback Bind", "Allow non-loopback bind.", boolean_schema(false)),
       state_field("port", "Port", "UDP port.", integer_schema(9000, 0, 65535)),
       state_field("maxQueue", "Max Queue", "Maximum queue length.", integer_schema(1024, 1)),
       state_field("reuseAddress", "Reuse Address", "Reuse address.", boolean_schema(true)),
       state_field("listening", "Listening", "Listening.", boolean_schema(false), "ro", true, true)},
      {}, {"packet"}));
  specs.push_back(simple_operator_spec(
      "f8.udp_out", "UDP Out", "output", {data_port("value", "Value to send.", any_schema())},
      {data_port("isOpen", "Socket open.", boolean_schema(false)), data_port("sentBytes", "Sent bytes.", integer_schema(0, 0)),
       data_port("error", "Error.", string_schema(""))},
      {state_field("enabled", "Enabled", "Enable UDP output.", boolean_schema(true), "rw", true, true),
       state_field("host", "Host", "Destination host.", string_schema("127.0.0.1"), "rw", true, true),
       state_field("port", "Port", "Destination port.", integer_schema(9000, 0, 65535), "rw", true, true),
       state_field("appendNewline", "Append Newline", "Append newline.", boolean_schema(false)),
       state_field("forceText", "Force Text", "Force text encoding.", boolean_schema(false))},
      {"exec"}));
  specs.push_back(simple_operator_spec(
      "f8.handy_out", "Handy Out", "output",
      {data_port("value", "Position value.", number_schema()), data_port("durationMs", "Duration override.", number_schema()),
       data_port("immediateResponse", "Immediate response override.", boolean_schema(false)),
       data_port("stopOnTarget", "Stop on target override.", boolean_schema(false))},
      {data_port("sentPosition", "Sent position.", number_schema()), data_port("httpStatus", "HTTP status.", integer_schema(0, 0)),
       data_port("result", "Result.", any_schema()), data_port("error", "Error.", string_schema(""))},
      {state_field("enabled", "Enabled", "Enable output.", boolean_schema(true), "rw", true, true),
       state_field("connectionKey", "Connection Key", "Connection key.", string_schema(""), "rw", true, true),
       state_field("baseUrl", "Base URL", "Base URL.", string_schema("https://www.handyfeeling.com")),
       state_field("ensureHdspMode", "Ensure HDSP Mode", "Ensure HDSP mode.", boolean_schema(true)),
       state_field("invert", "Invert", "Invert position.", boolean_schema(false)),
       state_field("minPercent", "Min Percent", "Minimum percent.", number_schema(0.0, 0.0, 100.0)),
       state_field("maxPercent", "Max Percent", "Maximum percent.", number_schema(100.0, 0.0, 100.0)),
       state_field("defaultDurationMs", "Default Duration (ms)", "Default duration.", integer_schema(100, 1)),
       state_field("requestTimeoutMs", "Request Timeout (ms)", "Request timeout.", integer_schema(2000, 1)),
       state_field("minSendIntervalMs", "Min Send Interval (ms)", "Minimum send interval.", integer_schema(20, 0)),
       state_field("immediateResponse", "Immediate Response", "Immediate response.", boolean_schema(false)),
       state_field("stopOnTarget", "Stop On Target", "Stop on target.", boolean_schema(false))},
      {"exec"}));
  specs.push_back(simple_operator_spec(
      "f8.lovense_out", "Lovense Out", "output", {data_port("position", "Position command.", number_schema())}, {},
      {state_field("enabled", "Enabled", "Enable output.", boolean_schema(true)),
       state_field("commandUrl", "Command URL", "Command URL.", string_schema("")),
       state_field("platformName", "Platform Name", "Platform name.", string_schema("Feel8")),
       state_field("requestTimeoutMs", "Request Timeout (ms)", "Request timeout.", integer_schema(2000, 1)),
       state_field("verifyTls", "Verify TLS", "Verify TLS.", boolean_schema(true)),
       state_field("minSendIntervalMs", "Min Send Interval (ms)", "Minimum send interval.", integer_schema(20, 0)),
       state_field("vibrate", "Vibrate", "Vibrate value.", number_schema(0.0)),
       state_field("rotate", "Rotate", "Rotate value.", number_schema(0.0)),
       state_field("pump", "Pump", "Pump value.", number_schema(0.0)),
       state_field("thrusting", "Thrusting", "Thrusting value.", number_schema(0.0)),
       state_field("fingering", "Fingering", "Fingering value.", number_schema(0.0)),
       state_field("suction", "Suction", "Suction value.", number_schema(0.0)),
       state_field("depth", "Depth", "Depth value.", number_schema(0.0)),
       state_field("oscillate", "Oscillate", "Oscillate value.", number_schema(0.0)),
       state_field("all", "All", "All functions.", number_schema(0.0)),
       state_field("strokeMin", "Stroke Min", "Stroke min.", number_schema(0.0)),
       state_field("strokeMax", "Stroke Max", "Stroke max.", number_schema(1.0)),
       state_field("stop", "Stop", "Stop command.", boolean_schema(false)),
       state_field("timeSec", "Time Sec", "Command time.", number_schema(0.0)),
       state_field("loopRunningSec", "Loop Running Sec", "Loop running seconds.", number_schema(0.0)),
       state_field("loopPauseSec", "Loop Pause Sec", "Loop pause seconds.", number_schema(0.0)),
       state_field("stopPrevious", "Stop Previous", "Stop previous.", boolean_schema(false)),
       state_field("toy", "Toy", "Toy id.", string_schema("")),
       state_field("defaultToy", "Default Toy", "Default toy.", string_schema("")),
       state_field("availableToys", "Available Toys", "Available toys.", array_schema(string_schema("")), "ro")},
      {"sendPositionCmd", "sendFunctionCmd"}));
  specs.push_back(simple_operator_spec(
      "f8.buttplug_out", "Buttplug Out", "output", {data_port("position", "Position command.", number_schema())}, {},
      {state_field("enabled", "Enabled", "Enable output.", boolean_schema(true)),
       state_field("wsUrl", "WebSocket URL", "WebSocket URL.", string_schema("ws://127.0.0.1:12345")),
       state_field("autoConnect", "Auto Connect", "Auto connect.", boolean_schema(true)),
       state_field("autoScanOnConnect", "Auto Scan On Connect", "Auto scan.", boolean_schema(true)),
       state_field("scanDurationMs", "Scan Duration (ms)", "Scan duration.", integer_schema(5000, 0)),
       state_field("reconnectIntervalMs", "Reconnect Interval (ms)", "Reconnect interval.", integer_schema(2000, 0)),
       state_field("selectedDevice", "Selected Device", "Selected device.", string_schema("")),
       state_field("rescan", "Rescan", "Rescan.", boolean_schema(false)),
       state_field("vibrateFeatureIndex", "Vibrate Feature Index", "Feature index.", integer_schema(0, 0)),
       state_field("rotateFeatureIndex", "Rotate Feature Index", "Feature index.", integer_schema(0, 0)),
       state_field("oscillateFeatureIndex", "Oscillate Feature Index", "Feature index.", integer_schema(0, 0)),
       state_field("positionFeatureIndex", "Position Feature Index", "Feature index.", integer_schema(0, 0)),
       state_field("defaultPositionDurationMs", "Default Position Duration (ms)", "Default duration.", integer_schema(100, 1)),
       state_field("vibrate", "Vibrate", "Vibrate.", number_schema(0.0)),
       state_field("rotate", "Rotate", "Rotate.", number_schema(0.0)),
       state_field("oscillate", "Oscillate", "Oscillate.", number_schema(0.0)),
       state_field("stop", "Stop", "Stop.", boolean_schema(false)),
       state_field("stopOnDeactivate", "Stop On Deactivate", "Stop on deactivate.", boolean_schema(true)),
       state_field("connected", "Connected", "Connected.", boolean_schema(false), "ro"),
       state_field("scanning", "Scanning", "Scanning.", boolean_schema(false), "ro"),
       state_field("availableDevices", "Available Devices", "Available devices.", array_schema(string_schema("")), "ro"),
       state_field("deviceInfos", "Device Infos", "Device infos.", any_schema(), "ro"),
       state_field("selectedDeviceInfo", "Selected Device Info", "Selected device info.", any_schema(), "ro")},
      {"sendPositionCmd", "sendFunctionCmd"}));
  specs.push_back(simple_operator_spec(
      "f8.lovense_mock_server", "Lovense Mock Server", "io", {}, {data_port("event", "Event.", any_schema())},
      {state_field("bindAddress", "Bind Address", "Bind address.", string_schema("127.0.0.1")),
       state_field("allowNonLoopbackBind", "Allow Non-loopback Bind", "Allow non-loopback bind.", boolean_schema(false)),
       state_field("port", "Port", "Port.", integer_schema(30010, 0, 65535)),
       state_field("printEnabled", "Print Enabled", "Print events.", boolean_schema(true)),
       state_field("eventIncludePayload", "Event Include Payload", "Include payload.", boolean_schema(true)),
       state_field("eventIncludeRequest", "Event Include Request", "Include request.", boolean_schema(false)),
       state_field("listening", "Listening", "Listening.", boolean_schema(false), "ro")},
      {}, {"event"}));
  return specs;
}

json script_spec(const std::string& operator_class, const std::string& label, const std::string& lang) {
  const std::string lower = lang == "Lua" ? "lua" : "angelscript";
  return json{{"specKind", "operator"},
              {"schemaVersion", "f8operator/1"},
              {"serviceClass", kServiceClass},
              {"paletteCategory", std::string(kServiceClass) + ".script"},
              {"operatorClass", operator_class},
              {"version", "0.0.1"},
              {"label", label},
              {"description", lang + " script node for C++ engine graphs. The V1 bridge reports a clear runtime error when the interpreter is not linked."},
              {"tags", json::array({"script", lower, "programmable"})},
              {"execInPorts", json::array({"exec"})},
              {"execOutPorts", json::array({"exec"})},
              {"dataInPorts", json::array({data_port("msg", "Message input.", any_schema(), false, true)})},
              {"dataOutPorts", json::array({data_port("out", "Script output.", any_schema(), false, true)})},
              {"editPolicy", editable_script_policy()},
              {"stateFields", json::array({state_field("code", "Code", lang + " source code.", string_schema(""), "rw", true, false,
                                                       "code[" + lower + "]")})}};
}

}  // namespace

void register_cppengine_specs(RuntimeNodeRegistry& registry) {
  registry.register_service_spec(service_spec(), true);
  registry.register_service_factory(kServiceClass,
                                    [](const std::string& node_id, const F8RuntimeNode& node, const json& initial_state) {
                                      return std::make_unique<CppEngineServiceNode>(node_id, node, initial_state);
                                    },
                                    true);

  registry.register_operator_spec(tick_spec(), true);
  registry.register_operator_factory(kServiceClass, "f8.tick",
                                     [](const std::string& node_id, const F8RuntimeNode& node, const json& initial_state) {
                                       return std::make_unique<TickNode>(node_id, node, initial_state);
                                     },
                                     true);

  registry.register_operator_spec(range_map_spec(), true);
  registry.register_operator_factory(kServiceClass, "f8.range_map",
                                     [](const std::string& node_id, const F8RuntimeNode& node, const json& initial_state) {
                                       return std::make_unique<RangeMapNode>(node_id, node, initial_state);
                                     },
                                     true);

  registry.register_operator_spec(data_expr_spec(), true);
  registry.register_operator_factory(kServiceClass, "f8.data_expr",
                                     [](const std::string& node_id, const F8RuntimeNode& node, const json& initial_state) {
                                       return std::make_unique<DataExprNode>(node_id, node, initial_state);
                                     },
                                     true);

  registry.register_operator_spec(exec_sequence_spec(), true);
  registry.register_operator_factory(kServiceClass, "f8.exec_sequence",
                                     [](const std::string& node_id, const F8RuntimeNode& node, const json& initial_state) {
                                       return std::make_unique<ExecSequenceNode>(node_id, node, initial_state);
                                     },
                                     true);

  registry.register_operator_spec(print_spec(), true);
  registry.register_operator_factory(kServiceClass, "f8.print",
                                     [](const std::string& node_id, const F8RuntimeNode& node, const json& initial_state) {
                                       return std::make_unique<PrintNode>(node_id, node, initial_state);
                                     },
                                     true);

  registry.register_operator_spec(phase_spec(), true);
  registry.register_operator_factory(kServiceClass, "f8.phase",
                                     [](const std::string& node_id, const F8RuntimeNode& node, const json& initial_state) {
                                       return std::make_unique<PhaseNode>(node_id, node, initial_state);
                                     },
                                     true);

  registry.register_operator_spec(cosine_spec(), true);
  registry.register_operator_factory(kServiceClass, "f8.cosine",
                                     [](const std::string& node_id, const F8RuntimeNode& node, const json& initial_state) {
                                       return std::make_unique<CosineNode>(node_id, node, initial_state);
                                     },
                                     true);

  registry.register_operator_spec(tempest_spec(), true);
  registry.register_operator_factory(kServiceClass, "f8.tempest",
                                     [](const std::string& node_id, const F8RuntimeNode& node, const json& initial_state) {
                                       return std::make_unique<TempestNode>(node_id, node, initial_state);
                                     },
                                     true);

  registry.register_operator_spec(smooth_filter_spec(), true);
  registry.register_operator_factory(kServiceClass, "f8.smooth_filter",
                                     [](const std::string& node_id, const F8RuntimeNode& node, const json& initial_state) {
                                       return std::make_unique<SmoothFilterNode>(node_id, node, initial_state);
                                     },
                                     true);

  registry.register_operator_spec(detrend_spec(), true);
  registry.register_operator_factory(kServiceClass, "f8.detrend",
                                     [](const std::string& node_id, const F8RuntimeNode& node, const json& initial_state) {
                                       return std::make_unique<DetrendNode>(node_id, node, initial_state);
                                     },
                                     true);

  registry.register_operator_spec(rate_limiter_spec(), true);
  registry.register_operator_factory(kServiceClass, "f8.rate_limiter",
                                     [](const std::string& node_id, const F8RuntimeNode& node, const json& initial_state) {
                                       return std::make_unique<RateLimiterNode>(node_id, node, initial_state);
                                     },
                                     true);

  registry.register_operator_spec(tcode_spec(), true);
  registry.register_operator_factory(kServiceClass, "f8.tcode",
                                     [](const std::string& node_id, const F8RuntimeNode& node, const json& initial_state) {
                                       return std::make_unique<TCodeNode>(node_id, node, initial_state);
                                     },
                                     true);

  registry.register_operator_spec(quat_to_euler_spec(), true);
  registry.register_operator_factory(kServiceClass, "f8.quat_to_euler",
                                     [](const std::string& node_id, const F8RuntimeNode& node, const json& initial_state) {
                                       return std::make_unique<QuatToEulerNode>(node_id, node, initial_state);
                                     },
                                     true);

  registry.register_operator_spec(silence_detector_spec(), true);
  registry.register_operator_factory(kServiceClass, "f8.silence_detector",
                                     [](const std::string& node_id, const F8RuntimeNode& node, const json& initial_state) {
                                       return std::make_unique<SilenceDetectorNode>(node_id, node, initial_state);
                                     },
                                     true);

  registry.register_operator_spec(switch_mixer_spec(), true);
  registry.register_operator_factory(kServiceClass, "f8.switch_mixer",
                                     [](const std::string& node_id, const F8RuntimeNode& node, const json& initial_state) {
                                       return std::make_unique<SwitchMixerNode>(node_id, node, initial_state);
                                     },
                                     true);

  for (const auto& spec : remaining_operator_specs()) {
    const std::string operator_class = spec.value("operatorClass", "");
    if (operator_class.empty()) continue;
    registry.register_operator_spec(spec, true);
    registry.register_operator_factory(kServiceClass, operator_class,
                                       [](const std::string& node_id, const F8RuntimeNode& node, const json& initial_state) {
                                         return std::make_unique<UnsupportedOperatorNode>(node_id, node, initial_state);
                                       },
                                       true);
  }

  registry.register_operator_spec(script_spec("f8.lua_script", "Lua Script", "Lua"), true);
  registry.register_operator_factory(kServiceClass, "f8.lua_script",
                                     [](const std::string& node_id, const F8RuntimeNode& node, const json& initial_state) {
                                       return std::make_unique<ScriptPlaceholderNode>(node_id, node, initial_state, "Lua");
                                     },
                                     true);

  registry.register_operator_spec(script_spec("f8.angelscript", "AngelScript", "AngelScript"), true);
  registry.register_operator_factory(kServiceClass, "f8.angelscript",
                                     [](const std::string& node_id, const F8RuntimeNode& node, const json& initial_state) {
                                       return std::make_unique<ScriptPlaceholderNode>(node_id, node, initial_state, "AngelScript");
                                     },
                                     true);
}

}  // namespace f8::cppengine
