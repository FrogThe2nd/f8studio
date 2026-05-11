#include "operator_common.h"

#include <algorithm>
#include <cstdint>
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

class ScriptPlaceholderNode final : public OperatorNode, public ComputableNode {
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
    report_runtime_unavailable(ts_ms);
    (void)meta;
  }

  std::vector<std::string> on_exec(std::int64_t exec_id, const std::string& in_port) override {
    (void)exec_id;
    (void)in_port;
    report_runtime_unavailable();
    return exec_out_ports();
  }

  json compute_output(const std::string& port, std::int64_t ctx_id) override {
    if (std::find(data_out_ports().begin(), data_out_ports().end(), port) == data_out_ports().end()) return nullptr;
    report_runtime_unavailable(ctx_id);
    return nullptr;
  }

 private:
  std::string script_error_code() const { return lang_ == "Lua" ? "LUA_SCRIPT_UNAVAILABLE" : "ANGELSCRIPT_UNAVAILABLE"; }

  void report_runtime_unavailable(std::int64_t ts_ms = 0) {
    report_error(script_error_code(), lang_ + " runtime bridge is not linked in this build", "warning",
                 lang_ + "-script-placeholder:" + node_id(), ts_ms);
  }

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
