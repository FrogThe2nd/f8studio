#include "operator_common.h"

#include <algorithm>
#include <memory>
#include <mutex>
#include <optional>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#include <pybind11/embed.h>
#include <pybind11/functional.h>
#include <pybind11/stl.h>
#include <spdlog/spdlog.h>

#include "f8cppengine/constants.h"
#include "f8cppsdk/runtime_node_registry.h"
#include "f8cppsdk/runtime_node.h"

namespace f8::cppengine {

namespace py = pybind11;
using f8::cppsdk::ClosableNode;
using f8::cppsdk::ComputableNode;
using f8::cppsdk::OperatorNode;
using f8::cppsdk::RuntimeNodeRegistry;
using f8::cppsdk::generated::F8RuntimeNode;

namespace {

void ensure_python_interpreter() {
  static std::mutex mu;
  static py::scoped_interpreter* interpreter = nullptr;
  static PyThreadState* main_thread_state = nullptr;
  std::lock_guard<std::mutex> lock(mu);
  if (interpreter != nullptr) return;
  interpreter = new py::scoped_interpreter();
  {
    py::module_ sys = py::module_::import("sys");
    sys.attr("path").attr("insert")(0, "");
  }
  main_thread_state = PyEval_SaveThread();
  (void)main_thread_state;
}

py::object json_to_py(const json& value) {
  if (value.is_null()) return py::none();
  if (value.is_boolean()) return py::bool_(value.get<bool>());
  if (value.is_number_integer()) return py::int_(value.get<std::int64_t>());
  if (value.is_number_unsigned()) return py::int_(value.get<std::uint64_t>());
  if (value.is_number_float()) return py::float_(value.get<double>());
  if (value.is_string()) return py::str(value.get<std::string>());
  if (value.is_array()) {
    py::list out;
    for (const auto& item : value) {
      out.append(json_to_py(item));
    }
    return std::move(out);
  }
  if (value.is_object()) {
    py::dict out;
    for (auto it = value.begin(); it != value.end(); ++it) {
      out[py::str(it.key())] = json_to_py(it.value());
    }
    return std::move(out);
  }
  return py::none();
}

json py_to_json(const py::handle value) {
  if (value.is_none()) return nullptr;
  if (py::isinstance<py::bool_>(value)) return value.cast<bool>();
  if (py::isinstance<py::int_>(value)) return value.cast<std::int64_t>();
  if (py::isinstance<py::float_>(value)) return value.cast<double>();
  if (py::isinstance<py::str>(value)) return value.cast<std::string>();
  if (py::isinstance<py::dict>(value)) {
    json out = json::object();
    py::dict dict = py::reinterpret_borrow<py::dict>(value);
    for (const auto& item : dict) {
      out[py::str(item.first).cast<std::string>()] = py_to_json(item.second);
    }
    return out;
  }
  if (py::isinstance<py::list>(value) || py::isinstance<py::tuple>(value)) {
    json out = json::array();
    py::sequence seq = py::reinterpret_borrow<py::sequence>(value);
    for (const auto& item : seq) {
      out.push_back(py_to_json(item));
    }
    return out;
  }
  throw std::invalid_argument("python script returned an unsupported value type: " +
                              py::str(value.get_type()).cast<std::string>());
}

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

class CPythonScriptNode final : public OperatorNode, public ComputableNode, public ClosableNode {
 public:
  CPythonScriptNode(const std::string& node_id, const F8RuntimeNode& node, const json& initial_state)
      : OperatorNode(node_id, data_port_names(node.dataInPorts, {"msg"}), data_port_names(node.dataOutPorts, {"out"}),
                     state_names(node.stateFields, {"code"}), strings_or(node.execInPorts, {"exec"}),
                     strings_or(node.execOutPorts, {"exec"})) {
    code_ = initial_state.value("code", "");
    compile_and_start();
  }

  ~CPythonScriptNode() override { close(); }

  json validate_state(const std::string& field, const json& value, std::int64_t ts_ms, const json& meta) override {
    (void)ts_ms;
    (void)meta;
    if (field == "code") return value.is_string() ? value.get<std::string>() : "";
    return value;
  }

  void on_state(const std::string& field, const json& value, std::int64_t ts_ms, const json& meta) override {
    (void)meta;
    if (field == "code") {
      code_ = value.is_string() ? value.get<std::string>() : "";
      compile_and_start();
      return;
    }
    call_on_state(field, value, ts_ms);
  }

  void on_data(const std::string& port, const json& value, std::int64_t ts_ms, const json& meta) override {
    (void)meta;
    try {
      py::gil_scoped_acquire gil;
      if (!has_hook("onMsg")) return;
      py::dict inputs;
      inputs[py::str(port)] = json_to_py(value);
      py::object result = call_hook("onMsg", py_context(), inputs);
      apply_result(result, ts_ms);
      clear_error("cpython-script:" + node_id());
    } catch (const std::exception& exc) {
      report_python_error("onMsg", exc, ts_ms);
    }
  }

  std::vector<std::string> on_exec(std::int64_t exec_id, const std::string& in_port) override {
    std::vector<std::string> out_ports = exec_out_ports();
    try {
      py::gil_scoped_acquire gil;
      const py::dict inputs = pull_inputs(exec_id);
      py::object result = py::none();
      if (has_hook("onExec")) {
        result = call_hook("onExec", py_context(), py::str(in_port), inputs);
      } else if (has_hook("onMsg")) {
        result = call_hook("onMsg", py_context(), inputs);
      }
      const auto selected_ports = apply_result(result, exec_id);
      if (selected_ports.has_value()) out_ports = selected_ports.value();
      clear_error("cpython-script:" + node_id());
    } catch (const std::exception& exc) {
      report_python_error("onExec", exc, exec_id);
    }
    return out_ports;
  }

  json compute_output(const std::string& port, std::int64_t ctx_id) override {
    if (std::find(data_out_ports().begin(), data_out_ports().end(), port) == data_out_ports().end()) return nullptr;
    if (last_ctx_id_.has_value() && last_ctx_id_.value() == ctx_id && last_outputs_.contains(port)) {
      return last_outputs_[port];
    }
    try {
      py::gil_scoped_acquire gil;
      const py::dict inputs = pull_inputs(ctx_id);
      py::object result = py::none();
      if (has_hook("onPull")) {
        result = call_hook("onPull", py_context(), py::str(port), inputs);
      } else if (has_hook("onExec")) {
        result = call_hook("onExec", py_context(), py::str(""), inputs);
      } else if (has_hook("onMsg")) {
        result = call_hook("onMsg", py_context(), inputs);
      }
      const auto outputs = extract_outputs(result);
      last_outputs_ = outputs;
      last_ctx_id_ = ctx_id;
      clear_error("cpython-script:" + node_id());
      return object_value_or_null(last_outputs_, port);
    } catch (const std::exception& exc) {
      report_python_error("compute", exc, ctx_id);
      return object_value_or_null(last_outputs_, port);
    }
  }

  void close() override {
    if (closed_) return;
    closed_ = true;
    close_started_script();
  }

  py::object pull_py(const std::string& port) {
    const auto value = current_ctx_id_.has_value() ? pull(port, current_ctx_id_.value()) : pull(port);
    return value.has_value() ? json_to_py(value.value()) : py::none();
  }

  void emit_py(const std::string& port, const py::object& value) {
    (void)emit(port, py_to_json(value));
  }

  void set_state_py(const std::string& field, const py::object& value) {
    (void)set_state(field, py_to_json(value));
  }

  void report_error_py(const std::string& code, const std::string& message, const std::string& severity,
                       const std::string& fingerprint) {
    report_error(code, message, severity, fingerprint.empty() ? "cpython-script:" + node_id() : fingerprint);
  }

  void clear_error_py(const std::string& fingerprint) {
    clear_error(fingerprint.empty() ? "cpython-script:" + node_id() : fingerprint);
  }

 private:
  void compile_and_start() {
    close_started_script();
    closed_ = false;
    try {
      ensure_python_interpreter();
      py::gil_scoped_acquire gil;
      globals_ = py::dict();
      globals_["__builtins__"] = py::module_::import("builtins");
      py::exec(code_, globals_);
      if (has_hook("onStart")) {
        call_hook("onStart", py_context());
      }
      clear_error("cpython-script:" + node_id());
    } catch (const std::exception& exc) {
      report_python_error("compile", exc, 0);
    }
  }

  void close_started_script() {
    if (globals_.ptr() != nullptr && !globals_.is_none()) {
      py::gil_scoped_acquire gil;
      try {
        if (has_hook("onStop")) {
          call_hook("onStop", py_context());
        }
      } catch (const std::exception& exc) {
        report_python_error("onStop", exc, 0);
      }
      globals_ = py::object();
    }
    last_outputs_ = json::object();
    last_ctx_id_.reset();
    current_ctx_id_.reset();
  }

  bool has_hook(const char* name) const {
    if (globals_.ptr() == nullptr) return false;
    if (globals_.is_none()) return false;
    if (!py::isinstance<py::dict>(globals_) || !globals_.contains(name)) return false;
    py::object hook = globals_[name];
    return PyCallable_Check(hook.ptr()) != 0;
  }

  py::object call_hook(const char* name) {
    return globals_[name]();
  }

  template <typename... Args>
  py::object call_hook(const char* name, Args&&... args) {
    return globals_[name](std::forward<Args>(args)...);
  }

  py::object py_context() {
    py::object ctx = py::eval("type('F8CPythonScriptContext', (), {})")();
    ctx.attr("node_id") = node_id();
    ctx.attr("pull") = py::cpp_function([this](const std::string& port) { return pull_py(port); });
    ctx.attr("emit") = py::cpp_function([this](const std::string& port, const py::object& value) {
      emit_py(port, value);
    });
    ctx.attr("set_state") = py::cpp_function([this](const std::string& field, const py::object& value) {
      set_state_py(field, value);
    });
    ctx.attr("report_error") =
        py::cpp_function([this](const std::string& code, const std::string& message, const std::string& severity,
                                const std::string& fingerprint) {
          report_error_py(code, message, severity, fingerprint);
        });
    ctx.attr("clear_error") = py::cpp_function([this](const std::string& fingerprint) {
      clear_error_py(fingerprint);
    });
    ctx.attr("log") = py::cpp_function([this](const std::string& message) {
      spdlog::info("[{}:cpython_script] {}", node_id(), message);
    });
    return ctx;
  }

  py::dict pull_inputs(std::int64_t ctx_id) {
    current_ctx_id_ = ctx_id;
    py::dict inputs;
    for (const auto& port : data_in_ports()) {
      const auto value = pull(port, ctx_id);
      inputs[py::str(port)] = value.has_value() ? json_to_py(value.value()) : py::none();
    }
    return inputs;
  }

  std::optional<std::vector<std::string>> apply_result(const py::object& result, std::int64_t ctx_id) {
    const json value = py_to_json(result);
    std::optional<std::vector<std::string>> out_ports;
    if (value.is_object()) {
      const auto outputs_it = value.find("outputs");
      last_outputs_ = outputs_it != value.end() && outputs_it->is_object() ? *outputs_it : json::object();
      last_ctx_id_ = ctx_id;
      if (outputs_it != value.end() && outputs_it->is_object()) {
        for (auto it = outputs_it->begin(); it != outputs_it->end(); ++it) {
          (void)emit(it.key(), it.value(), ctx_id);
        }
      }
      const auto exec_it = value.find("exec");
      if (exec_it != value.end() && exec_it->is_array()) {
        out_ports = std::vector<std::string>{};
        for (const auto& item : *exec_it) {
          if (item.is_string()) out_ports->push_back(item.get<std::string>());
        }
      }
      return out_ports;
    }
    if (!value.is_null() && std::find(data_out_ports().begin(), data_out_ports().end(), "out") != data_out_ports().end()) {
      last_outputs_ = json{{"out", value}};
      last_ctx_id_ = ctx_id;
      (void)emit("out", value, ctx_id);
    }
    return std::nullopt;
  }

  json extract_outputs(const py::object& result) {
    const json value = py_to_json(result);
    if (value.is_object()) {
      const auto outputs_it = value.find("outputs");
      if (outputs_it != value.end() && outputs_it->is_object()) return *outputs_it;
    }
    if (!value.is_null() && std::find(data_out_ports().begin(), data_out_ports().end(), "out") != data_out_ports().end()) {
      return json{{"out", value}};
    }
    return json::object();
  }

  void call_on_state(const std::string& field, const json& value, std::int64_t ts_ms) {
    try {
      py::gil_scoped_acquire gil;
      if (has_hook("onState")) {
        call_hook("onState", py_context(), py::str(field), json_to_py(value), py::int_(ts_ms));
      }
      clear_error("cpython-script:" + node_id());
    } catch (const std::exception& exc) {
      report_python_error("onState", exc, ts_ms);
    }
  }

  void report_python_error(const std::string& stage, const std::exception& exc, std::int64_t ts_ms) {
    report_error("CPYTHON_SCRIPT_ERROR", stage + ": " + exc.what(), "error",
                 "cpython-script:" + node_id() + ":" + stage, ts_ms);
  }

  std::string code_;
  py::object globals_;
  json last_outputs_ = json::object();
  std::optional<std::int64_t> last_ctx_id_;
  std::optional<std::int64_t> current_ctx_id_;
  bool closed_ = false;
};

std::string lua_script_template() {
  return R"F8(-- f8.lua_script starter for cppengine graphs.
-- Target runtime: LuaJIT via the C++ engine script bridge.
-- Current V1 builds may report LUA_SCRIPT_UNAVAILABLE until the LuaJIT bridge is linked.
--
-- Hooks: define any subset.
--   on_start(ctx)
--   on_state(ctx, field, value, ts_ms)
--   on_msg(ctx, inputs)
--   on_exec(ctx, exec_in, inputs)
--   on_stop(ctx)
--
-- Context API planned for cppengine script runtimes:
--   ctx.node_id
--   ctx:pull(port)                         -- fresh pull from a data input
--   ctx:emit(port, value)                  -- emit a data output immediately
--   ctx:set_state(field, value)            -- update an explicit state field
--   ctx:report_error(code, message, severity, fingerprint)
--   ctx:clear_error(fingerprint)
--   ctx:log(message)
--
-- Return protocol:
--   on_msg may return { outputs = { out = value } } or a plain value for output 'out'.
--   on_exec returns { exec = { "exec" }, outputs = { out = value } }.
--   Values must be JSON-compatible: nil, boolean, number, string, array tables, object tables.

local state = {
  count = 0,
}

local function input_value(inputs, name)
  if inputs == nil then
    return nil
  end
  return inputs[name]
end

function on_start(ctx)
  ctx:log("lua_script started")
end

function on_msg(ctx, inputs)
  return {
    outputs = {
      out = input_value(inputs, "msg"),
    },
  }
end

function on_exec(ctx, exec_in, inputs)
  state.count = state.count + 1

  local msg = input_value(inputs, "msg")
  if msg == nil then
    msg = ctx:pull("msg")
  end

  return {
    outputs = {
      out = {
        value = msg,
        count = state.count,
        exec_in = exec_in,
        node = ctx.node_id,
      },
    },
    exec = { "exec" },
  }
end

function on_state(ctx, field, value, ts_ms)
  ctx:log("state " .. tostring(field) .. "=" .. tostring(value))
end

function on_stop(ctx)
  ctx:log("lua_script stopped")
end
)F8";
}

std::string angelscript_template() {
  return R"F8(// f8.angelscript starter for cppengine graphs.
// Target runtime: AngelScript module embedded in the C++ engine script bridge.
// Current V1 builds may report ANGELSCRIPT_UNAVAILABLE until the AngelScript bridge is linked.
//
// Hooks: define any subset.
//   void on_start(F8Context@ ctx)
//   void on_state(F8Context@ ctx, const string &in field, F8Value@ value, int64 ts_ms)
//   F8Result@ on_msg(F8Context@ ctx, F8Map@ inputs)
//   F8Result@ on_exec(F8Context@ ctx, const string &in exec_in, F8Map@ inputs)
//   void on_stop(F8Context@ ctx)
//
// Context API planned for cppengine script runtimes:
//   ctx.node_id()
//   ctx.pull("msg")
//   ctx.emit("out", value)
//   ctx.set_state("field", value)
//   ctx.report_error("CODE", "message", "error", "fingerprint")
//   ctx.clear_error("fingerprint")
//   ctx.log("message")
//
// Result protocol:
//   F8Result@ r = F8Result();
//   r.outputs["out"] = value;
//   r.exec.insertLast("exec");
//   return r;
// Values must be JSON-compatible scalar, array, or object values exposed by the runtime API.

int count = 0;

F8Value@ input_value(F8Map@ inputs, const string &in name) {
  if (inputs is null) {
    return null;
  }
  return inputs[name];
}

void on_start(F8Context@ ctx) {
  ctx.log("angelscript started");
}

F8Result@ on_msg(F8Context@ ctx, F8Map@ inputs) {
  F8Result@ result = F8Result();
  result.outputs["out"] = input_value(inputs, "msg");
  return result;
}

F8Result@ on_exec(F8Context@ ctx, const string &in exec_in, F8Map@ inputs) {
  count += 1;

  F8Value@ msg = input_value(inputs, "msg");
  if (msg is null) {
    @msg = ctx.pull("msg");
  }

  F8Map@ out = F8Map();
  out["value"] = msg;
  out["count"] = count;
  out["exec_in"] = exec_in;
  out["node"] = ctx.node_id();

  F8Result@ result = F8Result();
  result.outputs["out"] = out;
  result.exec.insertLast("exec");
  return result;
}

void on_state(F8Context@ ctx, const string &in field, F8Value@ value, int64 ts_ms) {
  ctx.log("state " + field);
}

void on_stop(F8Context@ ctx) {
  ctx.log("angelscript stopped");
}
)F8";
}

std::string script_template_for_language(const std::string& lang) {
  if (lang == "Lua") return lua_script_template();
  return angelscript_template();
}

std::string script_description_for_language(const std::string& lang) {
  if (lang == "Lua") {
    return "LuaJIT script node for C++ engine graphs. The default code documents the planned hook/context contract and starts from a pass-through exec scaffold. Current V1 builds report a clear runtime error until the LuaJIT bridge is linked.";
  }
  return "AngelScript node for C++ engine graphs. The default code documents the planned strongly typed hook/context contract and starts from a pass-through exec scaffold. Current V1 builds report a clear runtime error until the AngelScript bridge is linked.";
}

json script_code_state_field(const std::string& lang, const std::string& lower) {
  json field = state_field("code", "Code", lang + " source code and starter hook scaffold.",
                           string_schema(script_template_for_language(lang)), "rw", true, false, "code[" + lower + "]");
  field["editorAssist"] = json{{"version", 1}, {"language", lower}};
  return field;
}

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

json script_spec(const std::string& operator_class, const std::string& label, const std::string& lang) {
  const std::string lower = lang == "Lua" ? "lua" : "angelscript";
  return json{{"specKind", "operator"},
              {"schemaVersion", "f8operator/1"},
              {"serviceClass", kServiceClass},
              {"paletteCategory", std::string(kServiceClass) + ".script"},
              {"operatorClass", operator_class},
              {"version", "0.0.1"},
              {"label", label},
              {"description", script_description_for_language(lang)},
              {"tags", json::array({"script", lower, "programmable"})},
              {"execInPorts", json::array({"exec"})},
              {"execOutPorts", json::array({"exec"})},
              {"dataInPorts", json::array({data_port("msg", "Message input.", any_schema(), false, true)})},
              {"dataOutPorts", json::array({data_port("out", "Script output.", any_schema(), false, true)})},
              {"editPolicy", editable_script_policy()},
              {"stateFields", json::array({script_code_state_field(lang, lower)})}};
}

json cpython_script_spec() {
  return json{{"specKind", "operator"},
              {"schemaVersion", "f8operator/1"},
              {"serviceClass", kServiceClass},
              {"paletteCategory", std::string(kServiceClass) + ".script"},
              {"operatorClass", "f8.cpython_script"},
              {"version", "0.0.1"},
              {"label", "CPython Script"},
              {"description",
               "Embedded CPython script node for C++ engine migration/debug flows. V1 supports synchronous hooks and JSON-compatible values only; use f8.pyengine/f8.python_script for full Python Script compatibility."},
              {"tags", json::array({"script", "python", "cpython", "migration"})},
              {"execInPorts", json::array({"exec"})},
              {"execOutPorts", json::array({"exec"})},
              {"dataInPorts", json::array({data_port("msg", "Message input.", any_schema(), false, true)})},
              {"dataOutPorts", json::array({data_port("out", "Script output.", any_schema(), false, true)})},
              {"editPolicy", editable_script_policy()},
              {"stateFields",
               json::array({state_field("code", "Code", "Python source code.", string_schema(
                                           "def onExec(ctx, exec_in, inputs):\n"
                                           "    return {'exec': ['exec'], 'outputs': {'out': inputs.get('msg')}}\n"),
                                       "rw", true, false, "code[python]")})}};
}



}  // namespace

void register_script_operator_specs(RuntimeNodeRegistry& registry) {
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

  registry.register_operator_spec(cpython_script_spec(), true);
  registry.register_operator_factory(kServiceClass, "f8.cpython_script",
                                     [](const std::string& node_id, const F8RuntimeNode& node, const json& initial_state) {
                                       return std::make_unique<CPythonScriptNode>(node_id, node, initial_state);
                                     },
                                     true);
}

}  // namespace f8::cppengine
