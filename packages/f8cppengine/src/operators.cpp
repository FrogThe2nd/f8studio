#include "f8cppengine/operators.h"

#include <atomic>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <cctype>
#include <functional>
#include <mutex>
#include <optional>
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

  json compute_output(const std::string& port, std::int64_t ctx_id) override {
    if (port != "value") return nullptr;
    const auto raw = pull("value");
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

  json compute_output(const std::string& port, std::int64_t ctx_id) override {
    if (port.empty()) return nullptr;
    if (!dirty_ && last_ctx_id_.has_value() && last_ctx_id_.value() == ctx_id && last_outputs_.contains(port)) {
      return last_outputs_[port];
    }
    last_outputs_.clear();
    try {
      bind_inputs();
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

  void bind_inputs() {
    if (bound_values_.empty() && !data_in_ports().empty()) {
      rebuild_evaluator_bindings();
    }
    for (auto& item : bound_values_) {
      const auto raw = pull(item.first);
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
              {"execInPorts", json::array({"exec"})},
              {"execOutPorts", json::array({"exec"})},
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
              {"execInPorts", json::array({"exec"})},
              {"execOutPorts", json::array({"exec"})},
              {"dataInPorts", json::array({data_port("x", "Input value for the expression.", any_schema(), false, true)})},
              {"dataOutPorts", json::array({data_port("out", "Expression result.", any_schema(), false, true)})},
              {"editPolicy", json{{"dataInPorts", editable_collection_policy()}, {"dataOutPorts", editable_collection_policy()}}},
              {"stateFields", json::array({state_field("code", "Expr", "Scalar expression. Reference numeric input port names directly.",
                                                       string_schema("x"), "rw", true, true, "wrapline[cpp]")})}};
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
