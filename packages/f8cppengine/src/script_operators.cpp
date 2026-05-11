#include "operator_common.h"

#include <algorithm>
#include <cstdint>
#include <cstring>
#include <cstdlib>
#include <filesystem>
#include <functional>
#include <memory>
#include <mutex>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#ifdef _WIN32
#include <windows.h>
#else
#include <unistd.h>
#endif

#include <angelscript.h>
#include <pybind11/embed.h>
#include <pybind11/functional.h>
#include <pybind11/stl.h>
#include <sol/sol.hpp>
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

std::filesystem::path current_executable_path() {
#ifdef _WIN32
  std::vector<wchar_t> buffer(1024);
  while (true) {
    const DWORD length = GetModuleFileNameW(nullptr, buffer.data(), static_cast<DWORD>(buffer.size()));
    if (length == 0) {
      throw std::runtime_error("GetModuleFileNameW failed while resolving embedded CPython runtime path");
    }
    if (length < buffer.size() - 1) {
      return std::filesystem::path(std::wstring(buffer.data(), length));
    }
    buffer.resize(buffer.size() * 2);
  }
#else
  std::vector<char> buffer(1024);
  while (true) {
    const ssize_t length = readlink("/proc/self/exe", buffer.data(), buffer.size());
    if (length < 0) break;
    if (static_cast<std::size_t>(length) < buffer.size()) {
      return std::filesystem::path(std::string(buffer.data(), static_cast<std::size_t>(length)));
    }
    buffer.resize(buffer.size() * 2);
  }
  return std::filesystem::current_path();
#endif
}

std::optional<std::filesystem::path> deployed_python_home() {
  const auto exe_dir = current_executable_path().parent_path();
  const auto lib_dir = exe_dir / "Lib";
  if (std::filesystem::exists(lib_dir / "os.py") && std::filesystem::exists(lib_dir / "encodings")) {
    return exe_dir;
  }
  return std::nullopt;
}

void check_py_status(PyConfig& config, const PyStatus status, const std::string& context) {
  if (PyStatus_Exception(status) == 0) return;
  const std::string message = PyStatus_IsError(status) != 0 && status.err_msg != nullptr ? status.err_msg
                                                                                         : "unknown CPython error";
  PyConfig_Clear(&config);
  throw std::runtime_error(context + ": " + message);
}

void set_config_path(PyConfig& config, wchar_t** field, const std::filesystem::path& path,
                     const std::string& context) {
  check_py_status(config, PyConfig_SetString(&config, field, path.wstring().c_str()), context);
}

void append_config_path(PyConfig& config, const std::filesystem::path& path, const std::string& context) {
  check_py_status(config, PyWideStringList_Append(&config.module_search_paths, path.wstring().c_str()), context);
}

py::scoped_interpreter* create_python_interpreter() {
  const auto python_home = deployed_python_home();
  if (!python_home.has_value()) {
    return new py::scoped_interpreter(false);
  }

  PyConfig config;
  PyConfig_InitPythonConfig(&config);
  config.parse_argv = 0;
  config.install_signal_handlers = 0;
  config.pathconfig_warnings = 0;
  config.module_search_paths_set = 1;

  const auto exe_path = current_executable_path();
  set_config_path(config, &config.home, python_home.value(), "failed to set embedded CPython home");
  set_config_path(config, &config.program_name, exe_path, "failed to set embedded CPython program_name");
  set_config_path(config, &config.executable, exe_path, "failed to set embedded CPython executable");
  set_config_path(config, &config.base_executable, exe_path, "failed to set embedded CPython base_executable");
  append_config_path(config, python_home.value() / "Lib", "failed to add embedded CPython Lib path");
  append_config_path(config, python_home.value() / "DLLs", "failed to add embedded CPython DLLs path");
  if (std::filesystem::exists(python_home.value() / "Lib" / "site-packages")) {
    append_config_path(config, python_home.value() / "Lib" / "site-packages",
                       "failed to add embedded CPython site-packages path");
  }

  return new py::scoped_interpreter(&config, 0, nullptr, false);
}

void ensure_python_interpreter() {
  static std::mutex mu;
  static py::scoped_interpreter* interpreter = nullptr;
  static PyThreadState* main_thread_state = nullptr;
  std::lock_guard<std::mutex> lock(mu);
  if (interpreter != nullptr) return;
  interpreter = create_python_interpreter();
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

class LuaScriptNode;

struct LuaScriptContext {
  LuaScriptNode* node = nullptr;
  std::int64_t ctx_id = 0;
  std::string node_id;

  sol::object pull(const std::string& port);
  void emit(const std::string& port, sol::object value);
  void set_state(const std::string& field, sol::object value);
  void report_error(const std::string& code, const std::string& message, const std::string& severity,
                    const std::string& fingerprint);
  void clear_error(const std::string& fingerprint);
  void log(const std::string& message);
};

sol::object json_to_lua(sol::state_view lua, const json& value) {
  if (value.is_null()) return sol::make_object(lua, sol::nil);
  if (value.is_boolean()) return sol::make_object(lua, value.get<bool>());
  if (value.is_number_integer()) return sol::make_object(lua, value.get<std::int64_t>());
  if (value.is_number_unsigned()) return sol::make_object(lua, value.get<std::uint64_t>());
  if (value.is_number_float()) return sol::make_object(lua, value.get<double>());
  if (value.is_string()) return sol::make_object(lua, value.get<std::string>());
  if (value.is_array()) {
    sol::table out = lua.create_table();
    int index = 1;
    for (const auto& item : value) {
      out[index++] = json_to_lua(lua, item);
    }
    return sol::make_object(lua, out);
  }
  if (value.is_object()) {
    sol::table out = lua.create_table();
    for (auto it = value.begin(); it != value.end(); ++it) {
      out[it.key()] = json_to_lua(lua, it.value());
    }
    return sol::make_object(lua, out);
  }
  return sol::make_object(lua, sol::nil);
}

json lua_to_json(const sol::object& value) {
  switch (value.get_type()) {
    case sol::type::none:
    case sol::type::nil:
      return nullptr;
    case sol::type::boolean:
      return value.as<bool>();
    case sol::type::number:
      return value.as<double>();
    case sol::type::string:
      return value.as<std::string>();
    case sol::type::table: {
      sol::table table = value.as<sol::table>();
      bool array_like = true;
      std::size_t max_index = 0;
      std::size_t count = 0;
      for (const auto& item : table) {
        ++count;
        const sol::object key = item.first.as<sol::object>();
        if (key.get_type() != sol::type::number) {
          array_like = false;
          break;
        }
        const auto index = key.as<std::int64_t>();
        if (index <= 0) {
          array_like = false;
          break;
        }
        max_index = std::max(max_index, static_cast<std::size_t>(index));
      }
      if (array_like && max_index == count) {
        json out = json::array();
        for (std::size_t index = 1; index <= max_index; ++index) {
          out.push_back(lua_to_json(table[static_cast<int>(index)]));
        }
        return out;
      }
      json out = json::object();
      for (const auto& item : table) {
        const sol::object key = item.first.as<sol::object>();
        std::string key_s;
        if (key.get_type() == sol::type::string) {
          key_s = key.as<std::string>();
        } else if (key.get_type() == sol::type::number) {
          key_s = std::to_string(key.as<std::int64_t>());
        } else {
          throw std::invalid_argument("lua script returned a table with a non-string key");
        }
        out[key_s] = lua_to_json(item.second.as<sol::object>());
      }
      return out;
    }
    default:
      throw std::invalid_argument("lua script returned an unsupported value type");
  }
}

class LuaScriptNode final : public OperatorNode, public ComputableNode, public ClosableNode {
 public:
  LuaScriptNode(const std::string& node_id, const F8RuntimeNode& node, const json& initial_state)
      : OperatorNode(node_id, data_port_names(node.dataInPorts, {"msg"}), data_port_names(node.dataOutPorts, {"out"}),
                     state_names(node.stateFields, {"code"}), strings_or(node.execInPorts, {"exec"}),
                     strings_or(node.execOutPorts, {"exec"})) {
    code_ = initial_state.value("code", "");
    compile_and_start();
  }

  ~LuaScriptNode() override { close(); }

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
      if (!has_hook("on_msg")) return;
      sol::table inputs = lua_->create_table();
      inputs[port] = json_to_lua(*lua_, value);
      const sol::protected_function_result result = call_hook("on_msg", make_context(ts_ms), inputs);
      apply_result(checked_result("on_msg", result), ts_ms);
      clear_error("lua-script:" + node_id());
    } catch (const std::exception& exc) {
      report_lua_error("on_msg", exc, ts_ms);
    }
  }

  std::vector<std::string> on_exec(std::int64_t exec_id, const std::string& in_port) override {
    std::vector<std::string> out_ports = exec_out_ports();
    try {
      const sol::table inputs = pull_inputs(exec_id);
      sol::object result = sol::make_object(*lua_, sol::nil);
      if (has_hook("on_exec")) {
        result = checked_result("on_exec", call_hook("on_exec", make_context(exec_id), in_port, inputs));
      } else if (has_hook("on_msg")) {
        result = checked_result("on_msg", call_hook("on_msg", make_context(exec_id), inputs));
      }
      const auto selected_ports = apply_result(result, exec_id);
      if (selected_ports.has_value()) out_ports = selected_ports.value();
      clear_error("lua-script:" + node_id());
    } catch (const std::exception& exc) {
      report_lua_error("on_exec", exc, exec_id);
    }
    return out_ports;
  }

  json compute_output(const std::string& port, std::int64_t ctx_id) override {
    if (std::find(data_out_ports().begin(), data_out_ports().end(), port) == data_out_ports().end()) return nullptr;
    if (last_ctx_id_.has_value() && last_ctx_id_.value() == ctx_id && last_outputs_.contains(port)) {
      return last_outputs_[port];
    }
    const auto cached = object_value_or_null(last_outputs_, port);
    if (!cached.is_null() && !has_hook("on_pull")) {
      return cached;
    }
    try {
      const sol::table inputs = pull_inputs(ctx_id);
      sol::object result = sol::make_object(*lua_, sol::nil);
      if (has_hook("on_pull")) {
        result = checked_result("on_pull", call_hook("on_pull", make_context(ctx_id), port, inputs));
      } else if (has_hook("on_exec")) {
        result = checked_result("on_exec", call_hook("on_exec", make_context(ctx_id), std::string(), inputs));
      } else if (has_hook("on_msg")) {
        result = checked_result("on_msg", call_hook("on_msg", make_context(ctx_id), inputs));
      }
      last_outputs_ = extract_outputs(result);
      last_ctx_id_ = ctx_id;
      clear_error("lua-script:" + node_id());
      return object_value_or_null(last_outputs_, port);
    } catch (const std::exception& exc) {
      report_lua_error("compute", exc, ctx_id);
      return object_value_or_null(last_outputs_, port);
    }
  }

  void close() override {
    if (closed_) return;
    close_started_script();
    closed_ = true;
  }

  sol::object pull_lua(const std::string& port, std::int64_t ctx_id) {
    const auto value = ctx_id > 0 ? pull(port, ctx_id) : pull(port);
    return value.has_value() && lua_ ? json_to_lua(*lua_, value.value()) : sol::make_object(*lua_, sol::nil);
  }

  void emit_lua(const std::string& port, const sol::object& value, std::int64_t ctx_id) {
    (void)emit(port, lua_to_json(value), ctx_id);
  }

  void set_state_lua(const std::string& field, const sol::object& value) {
    (void)set_state(field, lua_to_json(value));
  }

  void report_error_lua(const std::string& code, const std::string& message, const std::string& severity,
                        const std::string& fingerprint) {
    report_error(code, message, severity, fingerprint.empty() ? "lua-script:" + node_id() : fingerprint);
  }

  void clear_error_lua(const std::string& fingerprint) {
    clear_error(fingerprint.empty() ? "lua-script:" + node_id() : fingerprint);
  }

  void log_lua(const std::string& message) {
    spdlog::info("[{}:lua_script] {}", node_id(), message);
  }

 private:
  void compile_and_start() {
    close_started_script();
    closed_ = false;
    lua_ = std::make_unique<sol::state>();
    lua_->open_libraries(sol::lib::base, sol::lib::math, sol::lib::table, sol::lib::string);
    try {
      lua_->new_usertype<LuaScriptContext>(
          "F8LuaScriptContext", "node_id", sol::readonly(&LuaScriptContext::node_id), "pull", &LuaScriptContext::pull,
          "emit", &LuaScriptContext::emit, "set_state", &LuaScriptContext::set_state, "report_error",
          &LuaScriptContext::report_error, "clear_error", &LuaScriptContext::clear_error, "log", &LuaScriptContext::log);
      const sol::protected_function_result load_result = lua_->safe_script(code_, sol::script_pass_on_error);
      if (!load_result.valid()) {
        sol::error err = load_result;
        throw std::runtime_error(err.what());
      }
      if (has_hook("on_start")) {
        (void)checked_result("on_start", call_hook("on_start", make_context(0)));
      }
      clear_error("lua-script:" + node_id());
    } catch (const std::exception& exc) {
      report_lua_error("compile", exc, 0);
    }
  }

  void close_started_script() {
    if (lua_ && !closed_) {
      try {
        if (has_hook("on_stop")) {
          (void)checked_result("on_stop", call_hook("on_stop", make_context(0)));
        }
      } catch (const std::exception& exc) {
        report_lua_error("on_stop", exc, 0);
      }
    }
    lua_.reset();
    last_outputs_ = json::object();
    last_ctx_id_.reset();
  }

  bool has_hook(const char* name) const {
    if (!lua_) return false;
    sol::object hook = (*lua_)[name];
    return hook.get_type() == sol::type::function;
  }

  template <typename... Args>
  sol::protected_function_result call_hook(const char* name, Args&&... args) {
    sol::protected_function hook = (*lua_)[name];
    return hook(std::forward<Args>(args)...);
  }

  sol::object checked_result(const std::string& stage, const sol::protected_function_result& result) {
    if (!result.valid()) {
      sol::error err = result;
      throw std::runtime_error(stage + ": " + err.what());
    }
    return result.get<sol::object>();
  }

  LuaScriptContext make_context(std::int64_t ctx_id) {
    return LuaScriptContext{this, ctx_id, node_id()};
  }

  sol::table pull_inputs(std::int64_t ctx_id) {
    sol::table inputs = lua_->create_table();
    for (const auto& port : data_in_ports()) {
      const auto value = pull(port, ctx_id);
      inputs[port] = value.has_value() ? json_to_lua(*lua_, value.value()) : sol::make_object(*lua_, sol::nil);
    }
    return inputs;
  }

  std::optional<std::vector<std::string>> apply_result(const sol::object& result, std::int64_t ctx_id) {
    const json value = lua_to_json(result);
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

  json extract_outputs(const sol::object& result) {
    const json value = lua_to_json(result);
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
      if (has_hook("on_state")) {
        (void)checked_result("on_state", call_hook("on_state", make_context(ts_ms), field, json_to_lua(*lua_, value), ts_ms));
      }
      clear_error("lua-script:" + node_id());
    } catch (const std::exception& exc) {
      report_lua_error("on_state", exc, ts_ms);
    }
  }

  void report_lua_error(const std::string& stage, const std::exception& exc, std::int64_t ts_ms) {
    report_error("LUA_SCRIPT_ERROR", stage + ": " + exc.what(), "error", "lua-script:" + node_id() + ":" + stage,
                 ts_ms);
  }

  std::string code_;
  std::unique_ptr<sol::state> lua_;
  json last_outputs_ = json::object();
  std::optional<std::int64_t> last_ctx_id_;
  bool closed_ = false;
};

sol::object LuaScriptContext::pull(const std::string& port) {
  return node != nullptr ? node->pull_lua(port, ctx_id) : sol::lua_nil;
}

void LuaScriptContext::emit(const std::string& port, sol::object value) {
  if (node != nullptr) node->emit_lua(port, value, ctx_id);
}

void LuaScriptContext::set_state(const std::string& field, sol::object value) {
  if (node != nullptr) node->set_state_lua(field, value);
}

void LuaScriptContext::report_error(const std::string& code, const std::string& message, const std::string& severity,
                                    const std::string& fingerprint) {
  if (node != nullptr) node->report_error_lua(code, message, severity, fingerprint);
}

void LuaScriptContext::clear_error(const std::string& fingerprint) {
  if (node != nullptr) node->clear_error_lua(fingerprint);
}

void LuaScriptContext::log(const std::string& message) {
  if (node != nullptr) node->log_lua(message);
}

std::string json_to_script_arg(const json& value) {
  if (value.is_string()) return value.get<std::string>();
  return value.dump();
}

json json_from_script_result(const std::string& value) {
  if (value.empty()) return nullptr;
  try {
    return json::parse(value);
  } catch (const json::parse_error&) {
    return value;
  }
}

std::string join_messages(const std::vector<std::string>& messages) {
  std::ostringstream out;
  for (const auto& message : messages) {
    if (out.tellp() > 0) out << "\n";
    out << message;
  }
  return out.str();
}

class AngelStringFactory final : public asIStringFactory {
 public:
  const void* GetStringConstant(const char* data, asUINT length) override {
    auto* value = new std::string(data, length);
    return value;
  }

  int ReleaseStringConstant(const void* str) override {
    delete static_cast<const std::string*>(str);
    return 0;
  }

  int GetRawStringData(const void* str, char* data, asUINT* length) const override {
    if (str == nullptr) return -1;
    const auto* value = static_cast<const std::string*>(str);
    if (length != nullptr) *length = static_cast<asUINT>(value->size());
    if (data != nullptr && !value->empty()) {
      std::memcpy(data, value->data(), value->size());
    }
    return 0;
  }
};

void construct_string(std::string* self) {
  new (self) std::string();
}

void copy_construct_string(const std::string& other, std::string* self) {
  new (self) std::string(other);
}

void destruct_string(std::string* self) {
  self->~basic_string();
}

std::string string_add(const std::string& lhs, const std::string& rhs) {
  return lhs + rhs;
}

std::string string_add_int(const std::string& lhs, std::int64_t rhs) {
  return lhs + std::to_string(rhs);
}

std::string string_add_double(const std::string& lhs, double rhs) {
  return lhs + std::to_string(rhs);
}

std::string json_compact(const json& value) {
  return value.dump();
}

json json_parse_or_null(const std::string& value) {
  if (value.empty()) return nullptr;
  try {
    return json::parse(value);
  } catch (const json::parse_error&) {
    return nullptr;
  }
}

std::string json_quote_string(const std::string& value) {
  return json(value).dump();
}

std::string json_get_path(const std::string& value_json, const std::string& path) {
  json value = json_parse_or_null(value_json);
  if (value.is_null()) return "null";
  if (path.empty()) return json_compact(value);

  std::size_t start = 0;
  while (start <= path.size()) {
    const std::size_t dot = path.find('.', start);
    const std::string key = path.substr(start, dot == std::string::npos ? std::string::npos : dot - start);
    if (key.empty()) return "null";
    if (value.is_object()) {
      const auto it = value.find(key);
      if (it == value.end()) return "null";
      value = *it;
    } else if (value.is_array()) {
      try {
        const std::size_t index = static_cast<std::size_t>(std::stoull(key));
        if (index >= value.size()) return "null";
        value = value[index];
      } catch (const std::invalid_argument&) {
        return "null";
      } catch (const std::out_of_range&) {
        return "null";
      }
    } else {
      return "null";
    }
    if (dot == std::string::npos) break;
    start = dot + 1;
  }
  return json_compact(value);
}

std::string json_get_string_path(const std::string& value_json, const std::string& path, const std::string& fallback) {
  const json value = json_parse_or_null(json_get_path(value_json, path));
  if (value.is_string()) return value.get<std::string>();
  if (value.is_number_integer()) return std::to_string(value.get<std::int64_t>());
  if (value.is_number_unsigned()) return std::to_string(value.get<std::uint64_t>());
  if (value.is_number_float()) return std::to_string(value.get<double>());
  if (value.is_boolean()) return value.get<bool>() ? "true" : "false";
  return fallback;
}

double json_get_number_path(const std::string& value_json, const std::string& path, double fallback) {
  const json value = json_parse_or_null(json_get_path(value_json, path));
  return value.is_number() ? value.get<double>() : fallback;
}

bool json_get_bool_path(const std::string& value_json, const std::string& path, bool fallback) {
  const json value = json_parse_or_null(json_get_path(value_json, path));
  return value.is_boolean() ? value.get<bool>() : fallback;
}

std::string json_object_field(const std::string& key, const std::string& value_json) {
  json value = json_parse_or_null(value_json);
  return json_compact(json::object({{key, value}}));
}

std::string json_output_result(const std::string& port, const std::string& value_json) {
  json value = json_parse_or_null(value_json);
  return json_compact(json{{"outputs", json::object({{port, value}})}});
}

std::string json_output_exec_result(const std::string& port, const std::string& value_json, const std::string& exec_port) {
  json value = json_parse_or_null(value_json);
  return json_compact(json{{"outputs", json::object({{port, value}})}, {"exec", json::array({exec_port})}});
}

class AngelScriptNode final : public OperatorNode, public ComputableNode, public ClosableNode {
 public:
  AngelScriptNode(const std::string& node_id, const F8RuntimeNode& node, const json& initial_state)
      : OperatorNode(node_id, data_port_names(node.dataInPorts, {"msg"}), data_port_names(node.dataOutPorts, {"out"}),
                     state_names(node.stateFields, {"code"}), strings_or(node.execInPorts, {"exec"}),
                     strings_or(node.execOutPorts, {"exec"})) {
    code_ = initial_state.value("code", "");
    compile_and_start();
  }

  ~AngelScriptNode() override { close(); }

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
    try {
      const std::string value_json = value.dump();
      const std::string result = call_string_hook("on_state_json", {field, value_json, std::to_string(ts_ms)});
      (void)result;
      clear_error("angelscript:" + node_id());
    } catch (const std::exception& exc) {
      report_angelscript_error("on_state_json", exc, ts_ms);
    }
  }

  void on_data(const std::string& port, const json& value, std::int64_t ts_ms, const json& meta) override {
    (void)meta;
    try {
      if (!has_function("string on_msg_json(const string &in, const string &in)")) return;
      const std::string result = call_string_hook("on_msg_json", {port, value.dump()});
      apply_result(json_from_script_result(result), ts_ms);
      clear_error("angelscript:" + node_id());
    } catch (const std::exception& exc) {
      report_angelscript_error("on_msg_json", exc, ts_ms);
    }
  }

  std::vector<std::string> on_exec(std::int64_t exec_id, const std::string& in_port) override {
    std::vector<std::string> out_ports = exec_out_ports();
    try {
      const std::string inputs_json = pull_inputs(exec_id).dump();
      const std::string result = call_string_hook("on_exec_json", {in_port, inputs_json});
      const auto selected_ports = apply_result(json_from_script_result(result), exec_id);
      if (selected_ports.has_value()) out_ports = selected_ports.value();
      clear_error("angelscript:" + node_id());
    } catch (const std::exception& exc) {
      report_angelscript_error("on_exec_json", exc, exec_id);
    }
    return out_ports;
  }

  json compute_output(const std::string& port, std::int64_t ctx_id) override {
    if (std::find(data_out_ports().begin(), data_out_ports().end(), port) == data_out_ports().end()) return nullptr;
    if (last_ctx_id_.has_value() && last_ctx_id_.value() == ctx_id && last_outputs_.contains(port)) {
      return last_outputs_[port];
    }
    const auto cached = object_value_or_null(last_outputs_, port);
    if (!cached.is_null() && !has_function("string on_pull_json(const string &in, const string &in)")) {
      return cached;
    }
    try {
      std::string result;
      if (has_function("string on_pull_json(const string &in, const string &in)")) {
        result = call_string_hook("on_pull_json", {port, pull_inputs(ctx_id).dump()});
      } else if (has_function("string on_exec_json(const string &in, const string &in)")) {
        result = call_string_hook("on_exec_json", {"", pull_inputs(ctx_id).dump()});
      } else if (has_function("string on_msg_json(const string &in, const string &in)")) {
        result = call_string_hook("on_msg_json", {"", pull_inputs(ctx_id).dump()});
      }
      last_outputs_ = extract_outputs(json_from_script_result(result));
      last_ctx_id_ = ctx_id;
      clear_error("angelscript:" + node_id());
      return object_value_or_null(last_outputs_, port);
    } catch (const std::exception& exc) {
      report_angelscript_error("compute", exc, ctx_id);
      return object_value_or_null(last_outputs_, port);
    }
  }

  void close() override {
    if (closed_) return;
    close_started_script();
    closed_ = true;
  }

 private:
  void compile_and_start() {
    close_started_script();
    closed_ = false;
    compile_messages_.clear();
    engine_ = asCreateScriptEngine();
    if (engine_ == nullptr) {
      report_error("ANGELSCRIPT_ERROR", "compile: failed to create AngelScript engine", "error",
                   "angelscript:" + node_id() + ":compile");
      return;
    }
    try {
      register_string_type();
      const int msg_rc = engine_->SetMessageCallback(asFUNCTION(AngelScriptNode::message_callback), this, asCALL_CDECL_OBJLAST);
      if (msg_rc < 0) {
        throw std::runtime_error("failed to register compiler message callback");
      }
      module_ = engine_->GetModule(("f8_" + node_id()).c_str(), asGM_ALWAYS_CREATE);
      if (module_ == nullptr) {
        throw std::runtime_error("failed to create script module");
      }
      const int add_rc = module_->AddScriptSection("code", code_.c_str(), code_.size());
      const int build_rc = add_rc >= 0 ? module_->Build() : add_rc;
      if (build_rc < 0) {
        const std::string messages = join_messages(compile_messages_);
        throw std::runtime_error(messages.empty() ? "script build failed" : messages);
      }
      if (has_function("void on_start()")) {
        (void)call_void_hook("on_start");
      }
      clear_error("angelscript:" + node_id());
    } catch (const std::exception& exc) {
      report_angelscript_error("compile", exc, 0);
    }
  }

  void close_started_script() {
    if (engine_ != nullptr && !closed_) {
      try {
        if (has_function("void on_stop()")) {
          (void)call_void_hook("on_stop");
        }
      } catch (const std::exception& exc) {
        report_angelscript_error("on_stop", exc, 0);
      }
    }
    module_ = nullptr;
    if (engine_ != nullptr) {
      engine_->ShutDownAndRelease();
      engine_ = nullptr;
    }
    last_outputs_ = json::object();
    last_ctx_id_.reset();
  }

  void register_string_type() {
    int rc = engine_->RegisterObjectType("string", static_cast<int>(sizeof(std::string)),
                                         asOBJ_VALUE | asOBJ_APP_CLASS_CDAK);
    if (rc < 0) throw std::runtime_error("failed to register AngelScript string type");
    rc = engine_->RegisterStringFactory("string", &string_factory_);
    if (rc < 0) throw std::runtime_error("failed to register AngelScript string factory");
    rc = engine_->RegisterObjectBehaviour("string", asBEHAVE_CONSTRUCT, "void f()", asFUNCTION(construct_string),
                                          asCALL_CDECL_OBJLAST);
    if (rc < 0) throw std::runtime_error("failed to register AngelScript string constructor");
    rc = engine_->RegisterObjectBehaviour("string", asBEHAVE_CONSTRUCT, "void f(const string &in)",
                                          asFUNCTION(copy_construct_string), asCALL_CDECL_OBJLAST);
    if (rc < 0) throw std::runtime_error("failed to register AngelScript string copy constructor");
    rc = engine_->RegisterObjectBehaviour("string", asBEHAVE_DESTRUCT, "void f()", asFUNCTION(destruct_string),
                                          asCALL_CDECL_OBJLAST);
    if (rc < 0) throw std::runtime_error("failed to register AngelScript string destructor");
    rc = engine_->RegisterObjectMethod("string", "string &opAssign(const string &in)", asMETHODPR(std::string, operator=,
                                                                                                  (const std::string&),
                                                                                                  std::string&),
                                       asCALL_THISCALL);
    if (rc < 0) throw std::runtime_error("failed to register AngelScript string assignment");
    rc = engine_->RegisterObjectMethod("string", "string opAdd(const string &in) const", asFUNCTION(string_add),
                                       asCALL_CDECL_OBJFIRST);
    if (rc < 0) throw std::runtime_error("failed to register AngelScript string concat");
    rc = engine_->RegisterObjectMethod("string", "string opAdd(int64) const", asFUNCTION(string_add_int),
                                       asCALL_CDECL_OBJFIRST);
    if (rc < 0) throw std::runtime_error("failed to register AngelScript string/int concat");
    rc = engine_->RegisterObjectMethod("string", "string opAdd(double) const", asFUNCTION(string_add_double),
                                       asCALL_CDECL_OBJFIRST);
    if (rc < 0) throw std::runtime_error("failed to register AngelScript string/double concat");

    rc = engine_->RegisterGlobalFunction("string json_quote(const string &in)", asFUNCTION(json_quote_string),
                                         asCALL_CDECL);
    if (rc < 0) throw std::runtime_error("failed to register AngelScript json_quote");
    rc = engine_->RegisterGlobalFunction("string json_get(const string &in, const string &in)", asFUNCTION(json_get_path),
                                         asCALL_CDECL);
    if (rc < 0) throw std::runtime_error("failed to register AngelScript json_get");
    rc = engine_->RegisterGlobalFunction("string json_get_string(const string &in, const string &in, const string &in)",
                                         asFUNCTION(json_get_string_path), asCALL_CDECL);
    if (rc < 0) throw std::runtime_error("failed to register AngelScript json_get_string");
    rc = engine_->RegisterGlobalFunction("double json_get_number(const string &in, const string &in, double)",
                                         asFUNCTION(json_get_number_path), asCALL_CDECL);
    if (rc < 0) throw std::runtime_error("failed to register AngelScript json_get_number");
    rc = engine_->RegisterGlobalFunction("bool json_get_bool(const string &in, const string &in, bool)",
                                         asFUNCTION(json_get_bool_path), asCALL_CDECL);
    if (rc < 0) throw std::runtime_error("failed to register AngelScript json_get_bool");
    rc = engine_->RegisterGlobalFunction("string json_field(const string &in, const string &in)",
                                         asFUNCTION(json_object_field), asCALL_CDECL);
    if (rc < 0) throw std::runtime_error("failed to register AngelScript json_field");
    rc = engine_->RegisterGlobalFunction("string json_output(const string &in, const string &in)",
                                         asFUNCTION(json_output_result), asCALL_CDECL);
    if (rc < 0) throw std::runtime_error("failed to register AngelScript json_output");
    rc = engine_->RegisterGlobalFunction("string json_output_exec(const string &in, const string &in, const string &in)",
                                         asFUNCTION(json_output_exec_result), asCALL_CDECL);
    if (rc < 0) throw std::runtime_error("failed to register AngelScript json_output_exec");
  }

  static void message_callback(const asSMessageInfo* msg, AngelScriptNode* self) {
    if (msg == nullptr || self == nullptr) return;
    const char* type = msg->type == asMSGTYPE_ERROR ? "error" : msg->type == asMSGTYPE_WARNING ? "warning" : "info";
    std::ostringstream out;
    out << (msg->section != nullptr ? msg->section : "script") << ":" << msg->row << ":" << msg->col << " " << type
        << ": " << (msg->message != nullptr ? msg->message : "");
    self->compile_messages_.push_back(out.str());
  }

  bool has_function(const char* decl) const {
    return module_ != nullptr && module_->GetFunctionByDecl(decl) != nullptr;
  }

  std::string call_string_hook(const char* name, const std::vector<std::string>& args) {
    if (module_ == nullptr) return "";
    std::ostringstream decl;
    decl << "string " << name << "(";
    for (std::size_t index = 0; index < args.size(); ++index) {
      if (index > 0) decl << ", ";
      decl << "const string &in";
    }
    decl << ")";
    asIScriptFunction* func = module_->GetFunctionByDecl(decl.str().c_str());
    if (func == nullptr) return "";
    asIScriptContext* ctx = engine_->CreateContext();
    if (ctx == nullptr) throw std::runtime_error("failed to create AngelScript context");
    const int prepare_rc = ctx->Prepare(func);
    if (prepare_rc < 0) {
      ctx->Release();
      throw std::runtime_error("failed to prepare AngelScript function " + std::string(name));
    }
    for (asUINT index = 0; index < args.size(); ++index) {
      const int arg_rc = ctx->SetArgObject(index, const_cast<std::string*>(&args[index]));
      if (arg_rc < 0) {
        ctx->Release();
        throw std::runtime_error("failed to pass AngelScript argument " + std::to_string(index));
      }
    }
    const int exec_rc = ctx->Execute();
    if (exec_rc != asEXECUTION_FINISHED) {
      const std::string message = execution_error(ctx, exec_rc);
      ctx->Release();
      throw std::runtime_error(message);
    }
    std::string result;
    if (void* result_ptr = ctx->GetReturnObject(); result_ptr != nullptr) {
      result = *static_cast<std::string*>(result_ptr);
    }
    ctx->Release();
    return result;
  }

  void call_void_hook(const char* name) {
    if (module_ == nullptr) return;
    std::string decl = "void " + std::string(name) + "()";
    asIScriptFunction* func = module_->GetFunctionByDecl(decl.c_str());
    if (func == nullptr) return;
    asIScriptContext* ctx = engine_->CreateContext();
    if (ctx == nullptr) throw std::runtime_error("failed to create AngelScript context");
    const int prepare_rc = ctx->Prepare(func);
    if (prepare_rc < 0) {
      ctx->Release();
      throw std::runtime_error("failed to prepare AngelScript function " + std::string(name));
    }
    const int exec_rc = ctx->Execute();
    if (exec_rc != asEXECUTION_FINISHED) {
      const std::string message = execution_error(ctx, exec_rc);
      ctx->Release();
      throw std::runtime_error(message);
    }
    ctx->Release();
  }

  std::string execution_error(asIScriptContext* ctx, int exec_rc) const {
    if (ctx != nullptr && exec_rc == asEXECUTION_EXCEPTION) {
      int column = 0;
      const char* section = nullptr;
      const int line = ctx->GetExceptionLineNumber(&column, &section);
      std::ostringstream out;
      out << "exception";
      const char* exception = ctx->GetExceptionString();
      if (exception != nullptr) out << ": " << exception;
      out << " at " << (section != nullptr ? section : "script") << ":" << line << ":" << column;
      return out.str();
    }
    return "execution failed with AngelScript status " + std::to_string(exec_rc);
  }

  json pull_inputs(std::int64_t ctx_id) {
    json inputs = json::object();
    for (const auto& port : data_in_ports()) {
      const auto value = pull(port, ctx_id);
      inputs[port] = value.value_or(nullptr);
    }
    return inputs;
  }

  std::optional<std::vector<std::string>> apply_result(const json& value, std::int64_t ctx_id) {
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

  json extract_outputs(const json& value) {
    if (value.is_object()) {
      const auto outputs_it = value.find("outputs");
      if (outputs_it != value.end() && outputs_it->is_object()) return *outputs_it;
    }
    if (!value.is_null() && std::find(data_out_ports().begin(), data_out_ports().end(), "out") != data_out_ports().end()) {
      return json{{"out", value}};
    }
    return json::object();
  }

  void report_angelscript_error(const std::string& stage, const std::exception& exc, std::int64_t ts_ms) {
    report_error("ANGELSCRIPT_ERROR", stage + ": " + exc.what(), "error", "angelscript:" + node_id() + ":" + stage,
                 ts_ms);
  }

  std::string code_;
  AngelStringFactory string_factory_;
  asIScriptEngine* engine_ = nullptr;
  asIScriptModule* module_ = nullptr;
  std::vector<std::string> compile_messages_;
  json last_outputs_ = json::object();
  std::optional<std::int64_t> last_ctx_id_;
  bool closed_ = false;
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
--
-- Hooks: define any subset.
--   on_start(ctx)
--   on_state(ctx, field, value, ts_ms)
--   on_msg(ctx, inputs)
--   on_exec(ctx, exec_in, inputs)
--   on_pull(ctx, port, inputs)
--   on_stop(ctx)
--
-- Context API:
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
  local msg = input_value(inputs, "msg")
  if msg == nil then
    msg = ctx:pull("msg")
  end

  return {
    outputs = {
      out = msg,
    },
    exec = { "exec" },
  }
end

function on_pull(ctx, port, inputs)
  return {
    outputs = {
      out = input_value(inputs, "msg"),
    },
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
//
// The bridge passes JSON strings at hook boundaries and registers explicit JSON
// helper functions so scripts can read values and build valid return payloads.
//
// Hooks: define any subset.
//   void on_start()
//   string on_msg_json(const string &in port, const string &in value_json)
//   string on_exec_json(const string &in exec_in, const string &in inputs_json)
//   string on_pull_json(const string &in port, const string &in inputs_json)
//   string on_state_json(const string &in field, const string &in value_json, const string &in ts_ms)
//   void on_stop()
//
// Result protocol:
//   Return a JSON object like {"outputs":{"out":123},"exec":["exec"]}.
//   Return a plain JSON scalar/string to write output "out".
//
// JSON helpers:
//   json_get(json, "msg.value")                         -> JSON string or "null"
//   json_get_string(json, "msg.name", "fallback")       -> string
//   json_get_number(json, "msg.phase", 0.0)             -> double
//   json_get_bool(json, "msg.active", false)            -> bool
//   json_quote(text)                                    -> JSON quoted string
//   json_field("key", value_json)                       -> {"key":value}
//   json_output("out", value_json)                      -> {"outputs":{"out":value}}
//   json_output_exec("out", value_json, "exec")         -> {"outputs":{"out":value},"exec":["exec"]}
//
// Notes:
//   inputs_json is a JSON object keyed by input port name.
//   For quick pass-through, json_output("out", value_json) is valid.

int count = 0;

void on_start() {
}

string on_msg_json(const string &in port, const string &in value_json) {
  return json_output("out", value_json);
}

string on_exec_json(const string &in exec_in, const string &in inputs_json) {
  count += 1;

  string msg = json_get(inputs_json, "msg");
  return json_output_exec("out", msg, "exec");
}

string on_pull_json(const string &in port, const string &in inputs_json) {
  return json_output("out", json_get(inputs_json, "msg"));
}

string on_state_json(const string &in field, const string &in value_json, const string &in ts_ms) {
  return "";
}

void on_stop() {
}
)F8";
}

std::string script_template_for_language(const std::string& lang) {
  if (lang == "Lua") return lua_script_template();
  return angelscript_template();
}

std::string script_description_for_language(const std::string& lang) {
  if (lang == "Lua") {
    return "LuaJIT script node for C++ engine graphs. The default code documents the hook/context contract and starts from a Python-like pass-through scaffold.";
  }
  return "AngelScript node for C++ engine graphs. V1 uses the AngelScript core runtime plus explicit JSON helper functions for hook inputs and outputs.";
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
                                       return std::make_unique<LuaScriptNode>(node_id, node, initial_state);
                                     },
                                     true);

  registry.register_operator_spec(script_spec("f8.angelscript", "AngelScript", "AngelScript"), true);
  registry.register_operator_factory(kServiceClass, "f8.angelscript",
                                     [](const std::string& node_id, const F8RuntimeNode& node, const json& initial_state) {
                                       return std::make_unique<AngelScriptNode>(node_id, node, initial_state);
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
