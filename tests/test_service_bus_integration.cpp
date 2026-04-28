#include <gtest/gtest.h>

#include <algorithm>
#include <chrono>
#include <cerrno>
#include <cctype>
#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <functional>
#include <iomanip>
#include <optional>
#include <sstream>
#include <thread>

#if !defined(_WIN32)
#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>
#endif

#include <nlohmann/json.hpp>

#include "f8cppsdk/data_bus.h"
#include "f8cppsdk/f8_naming.h"
#include "f8cppsdk/msg_codec.h"
#include "f8cppsdk/nats_client.h"
#include "f8cppsdk/service_bus.h"
#include "f8cppsdk/state_kv.h"

using json = nlohmann::json;

bool decode_payload(const std::vector<std::uint8_t>& bytes, json& out) {
  return f8::cppsdk::decode_json(bytes.data(), bytes.size(), out);
}

bool decode_payload(const std::optional<std::vector<std::uint8_t>>& bytes, json& out) {
  if (!bytes.has_value()) return false;
  return decode_payload(bytes.value(), out);
}

namespace {

#if !defined(_WIN32)

int pick_free_port() {
  const int fd = ::socket(AF_INET, SOCK_STREAM, 0);
  if (fd < 0) return 0;

  sockaddr_in addr{};
  addr.sin_family = AF_INET;
  addr.sin_addr.s_addr = htonl(INADDR_ANY);
  addr.sin_port = htons(0);

  if (::bind(fd, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) != 0) {
    ::close(fd);
    return 0;
  }

  socklen_t len = sizeof(addr);
  if (::getsockname(fd, reinterpret_cast<sockaddr*>(&addr), &len) != 0) {
    ::close(fd);
    return 0;
  }

  const int port = ntohs(addr.sin_port);
  ::close(fd);
  return port;
}

class NatsServerProcess {
 public:
  explicit NatsServerProcess(int port) : port_(port) {}
  ~NatsServerProcess() { stop(); }

  NatsServerProcess(const NatsServerProcess&) = delete;
  NatsServerProcess& operator=(const NatsServerProcess&) = delete;

  bool start() {
    stop();
    if (port_ <= 0) return false;
    store_dir_ = std::filesystem::temp_directory_path() / ("f8_nats_test_" + std::to_string(::getpid()) + "_" +
                                                          std::to_string(port_));
    std::error_code ec;
    std::filesystem::create_directories(store_dir_, ec);

    pid_ = ::fork();
    if (pid_ == 0) {
      const std::string port_s = std::to_string(port_);
      const std::string sd_s = store_dir_.string();
      ::execlp("nats-server", "nats-server", "-a", "127.0.0.1", "-p", port_s.c_str(), "-js", "-sd", sd_s.c_str(),
               nullptr);
      std::fprintf(stderr, "execlp(nats-server) failed: %s\n", std::strerror(errno));
      std::_Exit(127);
    }
    if (pid_ < 0) {
      pid_ = -1;
      return false;
    }
    return true;
  }

  void stop() {
    if (pid_ > 0) {
      ::kill(pid_, SIGTERM);
      int status = 0;
      (void)::waitpid(pid_, &status, 0);
      pid_ = -1;
    }
    if (!store_dir_.empty()) {
      std::error_code ec;
      std::filesystem::remove_all(store_dir_, ec);
      store_dir_.clear();
    }
  }

  int port() const { return port_; }

 private:
  int port_ = 0;
  pid_t pid_ = -1;
  std::filesystem::path store_dir_;
};

#endif

class RecordingDataNode final : public f8::cppsdk::DataReceivableNode {
 public:
  void on_data(const std::string& node_id, const std::string& port, const json& value, std::int64_t ts_ms,
               const json& meta) override {
    last_node_id = node_id;
    last_port = port;
    last_value = value;
    last_ts_ms = ts_ms;
    last_meta = meta;
    ++count;
  }

  int count = 0;
  std::string last_node_id;
  std::string last_port;
  json last_value;
  std::int64_t last_ts_ms = 0;
  json last_meta;
};

class RecordingStateNode final : public f8::cppsdk::StatefulNode {
 public:
  void on_state(const std::string& node_id, const std::string& field, const json& value, std::int64_t ts_ms,
                const json& meta) override {
    last_node_id = node_id;
    last_field = field;
    last_value = value;
    last_ts_ms = ts_ms;
    last_meta = meta;
    ++count;
  }

  int count = 0;
  std::string last_node_id;
  std::string last_field;
  json last_value;
  std::int64_t last_ts_ms = 0;
  json last_meta;
};

bool wait_until(const std::function<bool()>& pred, const std::function<void()>& pump, std::int64_t timeout_ms) {
  const auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(timeout_ms);
  while (std::chrono::steady_clock::now() < deadline) {
    pump();
    if (pred()) return true;
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
  }
  pump();
  return pred();
}

std::uint32_t fnv1a32(const std::string& text) {
  std::uint32_t value = 0x811C9DC5u;
  for (unsigned char ch : text) {
    value ^= static_cast<std::uint32_t>(ch);
    value *= 0x01000193u;
  }
  return value;
}

std::string command_key_for_name(const std::string& name) {
  std::string base;
  bool last_was_sep = false;
  for (unsigned char ch : name) {
    const char lower = static_cast<char>(std::tolower(ch));
    if ((lower >= 'a' && lower <= 'z') || (lower >= '0' && lower <= '9')) {
      base.push_back(lower);
      last_was_sep = false;
      continue;
    }
    if (!last_was_sep) {
      base.push_back('_');
      last_was_sep = true;
    }
  }
  while (!base.empty() && base.front() == '_') base.erase(base.begin());
  while (!base.empty() && base.back() == '_') base.pop_back();
  if (base.empty()) base = "command";
  std::ostringstream out;
  out << base << "_" << std::hex << std::nouppercase << std::setw(8) << std::setfill('0') << fnv1a32(name);
  return out.str();
}

std::string command_input_state_field(const std::string& name) {
  return "__cmd__." + command_key_for_name(name) + ".in";
}

std::string command_output_state_field(const std::string& name) {
  return "__cmd__." + command_key_for_name(name) + ".out";
}

class RecordingCommandNode final : public f8::cppsdk::CommandableNode {
 public:
  bool on_command(const std::string& call, const json& args, const json& meta, json& result, std::string& error_code,
                  std::string& error_message) override {
    last_call = call;
    last_args = args;
    last_meta = meta;
    ++count;
    error_code.clear();
    error_message.clear();
    result = json{
        {"call", call},
        {"args", args},
    };
    return true;
  }

  int count = 0;
  std::string last_call;
  json last_args = json::object();
  json last_meta = json::object();
};

class RejectingSetStateNode final : public f8::cppsdk::SetStateHandlerNode {
 public:
  bool on_set_state(const std::string& node_id, const std::string& field, const json& value, const json& meta,
                    std::string& error_code, std::string& error_message) override {
    last_node_id = node_id;
    last_field = field;
    last_value = value;
    last_meta = meta;
    ++count;
    error_code = "UNKNOWN_FIELD";
    error_message = "unknown state field";
    return false;
  }

  int count = 0;
  std::string last_node_id;
  std::string last_field;
  json last_value = json(nullptr);
  json last_meta = json::object();
};

}  // namespace

TEST(ServiceBusIntegration, DataRouteDeliversAndBuffers) {
#if defined(_WIN32)
  GTEST_SKIP() << "integration test requires nats-server + fork/exec";
#else
  const int port = pick_free_port();
  ASSERT_GT(port, 0);
  NatsServerProcess server(port);
  ASSERT_TRUE(server.start());

  const std::string url = "nats://127.0.0.1:" + std::to_string(port);

  f8::cppsdk::ServiceBus::Config cfg_b;
  cfg_b.service_id = "svcB";
  cfg_b.nats_url = url;
  cfg_b.kv_memory_storage = true;
  f8::cppsdk::ServiceBus bus_b(cfg_b);
  RecordingDataNode data_node;
  bus_b.add_data_node(&data_node);

  // Wait for the server before starting the bus.
  f8::cppsdk::NatsClient probe;
  ASSERT_TRUE(wait_until(
      [&]() { return probe.connect(url); }, [&]() {}, 2000))
      << "NATS server did not become reachable";
  probe.close();

  ASSERT_TRUE(bus_b.start());

  json graph;
  graph["graphId"] = "g1";
  graph["revision"] = "r1";
  graph["nodes"] = json::array({
      json{{"nodeId", "svcB"}, {"serviceId", "svcB"}, {"serviceClass", "demo"}, {"operatorClass", nullptr}},
      json{{"nodeId", "op2"}, {"serviceId", "svcB"}, {"serviceClass", "demo"}, {"operatorClass", "OpClass"}},
  });
  graph["edges"] = json::array({
      json{{"edgeId", "e1"},
           {"kind", "data"},
           {"fromServiceId", "svcA"},
           {"fromOperatorId", "op1"},
           {"fromPort", "out"},
           {"toServiceId", "svcB"},
           {"toOperatorId", "op2"},
           {"toPort", "in"},
           {"strategy", "latest"}},
  });

  std::string err_code;
  std::string err_msg;
  ASSERT_TRUE(bus_b.on_set_rungraph(graph, json::object(), err_code, err_msg)) << err_code << ": " << err_msg;

  f8::cppsdk::NatsClient pub;
  ASSERT_TRUE(pub.connect(url));
  ASSERT_TRUE(f8::cppsdk::publish_data(pub, "svcA", "op1", "out", json{{"x", 1}}, 0));

  ASSERT_TRUE(wait_until([&]() { return data_node.count > 0; }, [&]() { (void)bus_b.drain_main_thread(); }, 2000))
      << "did not receive cross-service data";

  EXPECT_EQ(data_node.last_node_id, "op2");
  EXPECT_EQ(data_node.last_port, "in");
  EXPECT_TRUE(data_node.last_value.is_object());
  EXPECT_EQ(data_node.last_value.value("x", 0), 1);

  auto pulled = bus_b.pull_data("op2", "in");
  ASSERT_TRUE(pulled.has_value());
  EXPECT_EQ(pulled->value("x", 0), 1);

  bus_b.stop();
  pub.close();
  server.stop();
#endif
}

TEST(ServiceBusIntegration, SetStateEndpointWritesKVAndEnforcesAccess) {
#if defined(_WIN32)
  GTEST_SKIP() << "integration test requires nats-server + fork/exec";
#else
  const int port = pick_free_port();
  ASSERT_GT(port, 0);
  NatsServerProcess server(port);
  ASSERT_TRUE(server.start());

  const std::string url = "nats://127.0.0.1:" + std::to_string(port);

  f8::cppsdk::ServiceBus::Config cfg;
  cfg.service_id = "svcB";
  cfg.nats_url = url;
  cfg.kv_memory_storage = true;
  f8::cppsdk::ServiceBus bus(cfg);
  RecordingStateNode state_node;
  bus.add_stateful_node(&state_node);

  f8::cppsdk::NatsClient probe;
  ASSERT_TRUE(wait_until(
      [&]() { return probe.connect(url); }, [&]() {}, 2000))
      << "NATS server did not become reachable";
  probe.close();

  ASSERT_TRUE(bus.start());

  // Ready payload should match pysdk schema.
  auto ready_raw = bus.kv().get(f8::cppsdk::kv_key_ready());
  ASSERT_TRUE(ready_raw.has_value());
  json ready = json::object();
  ASSERT_TRUE(f8::cppsdk::decode_json(ready_raw->data(), ready_raw->size(), ready));
  ASSERT_TRUE(ready.is_object());
  EXPECT_EQ(ready.value("serviceId", ""), "svcB");
  EXPECT_TRUE(ready.value("ready", false));
  EXPECT_EQ(ready.value("reason", ""), "start");

  json graph;
  graph["graphId"] = "g1";
  graph["revision"] = "r1";
  graph["nodes"] = json::array({
      json{
          {"nodeId", "svcB"},
          {"serviceId", "svcB"},
          {"serviceClass", "demo"},
          {"operatorClass", nullptr},
          {"stateFields",
           json::array({
               json{{"name", "foo"}, {"access", "rw"}, {"valueSchema", json::object()}},
               json{{"name", "bar"}, {"access", "ro"}, {"valueSchema", json::object()}},
           })},
      },
  });
  graph["edges"] = json::array();

  std::string err_code;
  std::string err_msg;
  ASSERT_TRUE(bus.on_set_rungraph(graph, json::object(), err_code, err_msg)) << err_code << ": " << err_msg;

  f8::cppsdk::NatsClient caller;
  ASSERT_TRUE(caller.connect(url));

  const auto set_state_subject = f8::cppsdk::svc_endpoint_subject("svcB", "set_state");

  json req_ok;
  req_ok["reqId"] = "r1";
  req_ok["args"] = json{{"nodeId", "svcB"}, {"field", "foo"}, {"value", 123}};
  req_ok["meta"] = json{{"traceId", "t1"}};

  auto resp_bytes = caller.request(set_state_subject, f8::cppsdk::encode_json(req_ok), 1000);
  ASSERT_TRUE(resp_bytes.has_value());
  json resp = json::object();
  ASSERT_TRUE(decode_payload(resp_bytes, resp));
  ASSERT_TRUE(resp.is_object());
  EXPECT_TRUE(resp.value("ok", false));

  ASSERT_TRUE(wait_until([&]() { return state_node.count > 0; }, [&]() { (void)bus.drain_main_thread(); }, 2000));
  EXPECT_EQ(state_node.last_node_id, "svcB");
  EXPECT_EQ(state_node.last_field, "foo");
  EXPECT_EQ(state_node.last_value, 123);

  auto kv_raw = bus.kv().get(f8::cppsdk::kv_key_node_state("svcB", "foo"));
  ASSERT_TRUE(kv_raw.has_value());
  json kv_payload = json::object();
  ASSERT_TRUE(f8::cppsdk::decode_json(kv_raw->data(), kv_raw->size(), kv_payload));
  ASSERT_TRUE(kv_payload.is_object());
  EXPECT_EQ(kv_payload.value("actor", ""), "svcB");
  EXPECT_EQ(kv_payload.value("origin", ""), "external");
  EXPECT_EQ(kv_payload.value("source", ""), "endpoint");
  EXPECT_EQ(kv_payload["value"], 123);

  // Read-only field should be rejected.
  json req_ro;
  req_ro["reqId"] = "r2";
  req_ro["args"] = json{{"nodeId", "svcB"}, {"field", "bar"}, {"value", 1}};
  req_ro["meta"] = json::object();
  auto resp_ro_bytes = caller.request(set_state_subject, f8::cppsdk::encode_json(req_ro), 1000);
  ASSERT_TRUE(resp_ro_bytes.has_value());
  json resp_ro = json::object();
  ASSERT_TRUE(decode_payload(resp_ro_bytes, resp_ro));
  ASSERT_TRUE(resp_ro.is_object());
  EXPECT_FALSE(resp_ro.value("ok", true));
  ASSERT_TRUE(resp_ro.contains("error") && resp_ro["error"].is_object());
  EXPECT_EQ(resp_ro["error"].value("code", ""), "FORBIDDEN");

  bus.stop();
  caller.close();
  server.stop();
#endif
}

TEST(ServiceBusIntegration, CrossServiceStateEdgeMirrorsRemoteKV) {
#if defined(_WIN32)
  GTEST_SKIP() << "integration test requires nats-server + fork/exec";
#else
  const int port = pick_free_port();
  ASSERT_GT(port, 0);
  NatsServerProcess server(port);
  ASSERT_TRUE(server.start());

  const std::string url = "nats://127.0.0.1:" + std::to_string(port);

  f8::cppsdk::ServiceBus::Config cfg_a;
  cfg_a.service_id = "svcA";
  cfg_a.nats_url = url;
  cfg_a.kv_memory_storage = true;
  f8::cppsdk::ServiceBus bus_a(cfg_a);

  f8::cppsdk::ServiceBus::Config cfg_b;
  cfg_b.service_id = "svcB";
  cfg_b.nats_url = url;
  cfg_b.kv_memory_storage = true;
  f8::cppsdk::ServiceBus bus_b(cfg_b);

  RecordingStateNode state_node;
  bus_b.add_stateful_node(&state_node);

  // Wait for NATS.
  f8::cppsdk::NatsClient probe;
  ASSERT_TRUE(wait_until(
      [&]() { return probe.connect(url); }, [&]() {}, 2000))
      << "NATS server did not become reachable";
  probe.close();

  ASSERT_TRUE(bus_a.start());
  ASSERT_TRUE(bus_b.start());

  // Publish a remote state value BEFORE binding is applied to exercise initial sync.
  ASSERT_TRUE(f8::cppsdk::kv_set_node_state(bus_a.kv(), "svcA", "op1", "out", "v1", "runtime", json::object(), 1, "runtime"));

  json graph;
  graph["graphId"] = "g1";
  graph["revision"] = "r1";
  graph["nodes"] = json::array({
      json{{"nodeId", "svcA"}, {"serviceId", "svcA"}, {"serviceClass", "demo"}, {"operatorClass", nullptr}},
      json{{"nodeId", "op1"}, {"serviceId", "svcA"}, {"serviceClass", "demo"}, {"operatorClass", "OpA"}},
      json{{"nodeId", "svcB"}, {"serviceId", "svcB"}, {"serviceClass", "demo"}, {"operatorClass", nullptr}},
      json{{"nodeId", "op2"},
           {"serviceId", "svcB"},
           {"serviceClass", "demo"},
           {"operatorClass", "OpB"},
           {"stateFields", json::array({json{{"name", "input"}, {"valueSchema", json{{"type", "string"}}}, {"access", "rw"}}})}},
  });
  graph["edges"] = json::array({
      json{{"edgeId", "e1"},
           {"kind", "state"},
           {"fromServiceId", "svcA"},
           {"fromOperatorId", "op1"},
           {"fromPort", "out"},
           {"toServiceId", "svcB"},
           {"toOperatorId", "op2"},
           {"toPort", "input"},
           {"strategy", "latest"}},
  });
  // Simulate Studio UI default clobbering: downstream stateValues sets input to empty.
  // Cross-state binding should still win (remote KV), even if rungraph meta.ts is newer.
  graph["nodes"][3]["stateValues"] = json::object({{"input", ""}});

  std::string err_code;
  std::string err_msg;
  ASSERT_TRUE(bus_b.on_set_rungraph(graph, json::object(), err_code, err_msg)) << err_code << ": " << err_msg;

  ASSERT_TRUE(wait_until([&]() { return state_node.count > 0; }, [&]() { (void)bus_b.drain_main_thread(); }, 2000))
      << "did not receive cross-service state via remote KV watch";

  EXPECT_EQ(state_node.last_node_id, "op2");
  EXPECT_EQ(state_node.last_field, "input");
  EXPECT_TRUE(state_node.last_value.is_string());
  EXPECT_EQ(state_node.last_value.get<std::string>(), "v1");

  bus_a.stop();
  bus_b.stop();
  server.stop();
#endif
}

TEST(ServiceBusIntegration, CommandInputStateDispatchesAndFansOutOutput) {
#if defined(_WIN32)
  GTEST_SKIP() << "integration test requires nats-server + fork/exec";
#else
  const int port = pick_free_port();
  ASSERT_GT(port, 0);
  NatsServerProcess server(port);
  ASSERT_TRUE(server.start());

  const std::string url = "nats://127.0.0.1:" + std::to_string(port);

  f8::cppsdk::ServiceBus::Config cfg;
  cfg.service_id = "svcB";
  cfg.nats_url = url;
  cfg.kv_memory_storage = true;
  f8::cppsdk::ServiceBus bus(cfg);
  RecordingCommandNode command_node;
  RecordingStateNode state_node;
  bus.add_command_node(
      &command_node,
      json{{"service",
            {{"commands",
              json::array({
                  json{{"name", "run"}, {"params", json::array({json{{"name", "a"}}, json{{"name", "b"}}})}},
              })}}}});
  bus.add_stateful_node(&state_node);

  f8::cppsdk::NatsClient probe;
  ASSERT_TRUE(wait_until(
      [&]() { return probe.connect(url); }, [&]() {}, 2000))
      << "NATS server did not become reachable";
  probe.close();

  ASSERT_TRUE(bus.start());

  const std::string input_field = command_input_state_field("run");
  const std::string output_field = command_output_state_field("run");

  json graph;
  graph["graphId"] = "g1";
  graph["revision"] = "r1";
  graph["nodes"] = json::array({
      json{{"nodeId", "svcB"},
           {"serviceId", "svcB"},
           {"serviceClass", "demo"},
           {"operatorClass", nullptr},
           {"stateFields",
            json::array({
                json{{"name", input_field}, {"access", "wo"}, {"valueSchema", json::object()}},
                json{{"name", output_field}, {"access", "ro"}, {"valueSchema", json::object()}},
                json{{"name", "result"}, {"access", "rw"}, {"valueSchema", json::object()}},
            })}},
  });
  graph["edges"] = json::array({
      json{{"edgeId", "e1"},
           {"kind", "state"},
           {"fromServiceId", "svcB"},
           {"fromOperatorId", "svcB"},
           {"fromPort", output_field},
           {"toServiceId", "svcB"},
           {"toOperatorId", "svcB"},
           {"toPort", "result"},
           {"strategy", "latest"}},
  });

  std::string err_code;
  std::string err_msg;
  ASSERT_TRUE(bus.on_set_rungraph(graph, json::object(), err_code, err_msg)) << err_code << ": " << err_msg;

  ASSERT_TRUE(bus.on_set_state("svcB", input_field, json::array({1, 2, 3}), json::object(), err_code, err_msg))
      << err_code << ": " << err_msg;

  ASSERT_TRUE(wait_until([&]() { return command_node.count > 0; }, [&]() { (void)bus.drain_main_thread(); }, 2000))
      << "command input did not dispatch";
  ASSERT_TRUE(wait_until([&]() { return state_node.count > 0; }, [&]() { (void)bus.drain_main_thread(); }, 2000))
      << "command output did not fan out";

  EXPECT_EQ(command_node.last_call, "run");
  ASSERT_TRUE(command_node.last_args.is_object());
  EXPECT_EQ(command_node.last_args.value("a", 0), 1);
  EXPECT_EQ(command_node.last_args.value("b", 0), 2);
  EXPECT_FALSE(command_node.last_args.contains("c"));

  EXPECT_EQ(state_node.count, 1);
  EXPECT_EQ(state_node.last_node_id, "svcB");
  EXPECT_EQ(state_node.last_field, "result");
  ASSERT_TRUE(state_node.last_value.is_object());
  EXPECT_EQ(state_node.last_value["call"], "run");
  ASSERT_TRUE(state_node.last_value["args"].is_object());
  EXPECT_EQ(state_node.last_value["args"].value("a", 0), 1);
  EXPECT_EQ(state_node.last_value["args"].value("b", 0), 2);

  auto output_raw = bus.kv().get(f8::cppsdk::kv_key_node_state("svcB", output_field));
  ASSERT_TRUE(output_raw.has_value());
  json output_payload = json::object();
  ASSERT_TRUE(f8::cppsdk::decode_json(output_raw->data(), output_raw->size(), output_payload));
  ASSERT_TRUE(output_payload.is_object());
  EXPECT_EQ(output_payload["value"]["call"], "run");

  bus.stop();
  server.stop();
#endif
}

TEST(ServiceBusIntegration, HiddenCommandInputBypassesRejectingSetStateHandler) {
#if defined(_WIN32)
  GTEST_SKIP() << "integration test requires nats-server + fork/exec";
#else
  const int port = pick_free_port();
  ASSERT_GT(port, 0);
  NatsServerProcess server(port);
  ASSERT_TRUE(server.start());

  const std::string url = "nats://127.0.0.1:" + std::to_string(port);

  f8::cppsdk::ServiceBus::Config cfg;
  cfg.service_id = "svcB";
  cfg.nats_url = url;
  cfg.kv_memory_storage = true;
  f8::cppsdk::ServiceBus bus(cfg);
  RecordingCommandNode command_node;
  RecordingStateNode state_node;
  RejectingSetStateNode rejecting_state_node;
  bus.add_set_state_node(&rejecting_state_node);
  bus.add_command_node(
      &command_node,
      json{{"service",
            {{"commands",
              json::array({
                  json{{"name", "run"}, {"params", json::array({json{{"name", "a"}}, json{{"name", "b"}}})}},
              })}}}});
  bus.add_stateful_node(&state_node);

  f8::cppsdk::NatsClient probe;
  ASSERT_TRUE(wait_until(
      [&]() { return probe.connect(url); }, [&]() {}, 2000))
      << "NATS server did not become reachable";
  probe.close();

  ASSERT_TRUE(bus.start());

  const std::string input_field = command_input_state_field("run");
  const std::string output_field = command_output_state_field("run");

  json graph;
  graph["graphId"] = "g1";
  graph["revision"] = "r1";
  graph["nodes"] = json::array({
      json{{"nodeId", "svcB"},
           {"serviceId", "svcB"},
           {"serviceClass", "demo"},
           {"operatorClass", nullptr},
           {"stateFields",
            json::array({
                json{{"name", input_field}, {"access", "wo"}, {"valueSchema", json::object()}},
                json{{"name", output_field}, {"access", "ro"}, {"valueSchema", json::object()}},
                json{{"name", "result"}, {"access", "rw"}, {"valueSchema", json::object()}},
            })}}},
  });
  graph["edges"] = json::array({
      json{{"edgeId", "e1"},
           {"kind", "state"},
           {"fromServiceId", "svcB"},
           {"fromOperatorId", "svcB"},
           {"fromPort", output_field},
           {"toServiceId", "svcB"},
           {"toOperatorId", "svcB"},
           {"toPort", "result"},
           {"strategy", "latest"}}});

  std::string err_code;
  std::string err_msg;
  ASSERT_TRUE(bus.on_set_rungraph(graph, json::object(), err_code, err_msg)) << err_code << ": " << err_msg;

  ASSERT_TRUE(bus.on_set_state("svcB", input_field, json::array({7, 8}), json::object(), err_code, err_msg))
      << err_code << ": " << err_msg;

  ASSERT_TRUE(wait_until([&]() { return command_node.count > 0; }, [&]() { (void)bus.drain_main_thread(); }, 2000))
      << "hidden command input did not dispatch";
  ASSERT_TRUE(wait_until([&]() { return state_node.count > 0; }, [&]() { (void)bus.drain_main_thread(); }, 2000))
      << "hidden command output did not fan out";

  EXPECT_EQ(rejecting_state_node.count, 0);
  EXPECT_EQ(command_node.last_call, "run");
  ASSERT_TRUE(command_node.last_args.is_object());
  EXPECT_EQ(command_node.last_args.value("a", 0), 7);
  EXPECT_EQ(command_node.last_args.value("b", 0), 8);

  ASSERT_TRUE(state_node.last_value.is_object());
  EXPECT_EQ(state_node.last_field, "result");
  EXPECT_EQ(state_node.last_value["call"], "run");
  ASSERT_TRUE(state_node.last_value["args"].is_object());
  EXPECT_EQ(state_node.last_value["args"].value("a", 0), 7);
  EXPECT_EQ(state_node.last_value["args"].value("b", 0), 8);

  auto output_raw = bus.kv().get(f8::cppsdk::kv_key_node_state("svcB", output_field));
  ASSERT_TRUE(output_raw.has_value());
  json output_payload = json::object();
  ASSERT_TRUE(f8::cppsdk::decode_json(output_raw->data(), output_raw->size(), output_payload));
  ASSERT_TRUE(output_payload.is_object());
  EXPECT_EQ(output_payload["value"]["call"], "run");

  bus.stop();
  server.stop();
#endif
}
