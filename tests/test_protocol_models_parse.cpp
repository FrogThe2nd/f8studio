#include <gtest/gtest.h>

#include <string>

#include <nlohmann/json.hpp>

#include "f8cppsdk/generated/protocol_models.h"

namespace {

using json = nlohmann::json;
using namespace f8::cppsdk::generated;

json monitor_snapshot_payload() {
  return json{
      {"schemaVersion", "f8monitor/1"},
      {"serviceId", "tracker"},
      {"serviceClass", "f8.cvkit.tracking"},
      {"nodeId", "tracker"},
      {"tsMs", 1000},
      {"alive", true},
      {"ready", true},
      {"active", true},
      {"uptimeMs", 100},
      {"cpu", json{{"processPercent", 0.0}, {"systemPercent", 0.0}}},
      {"memory", json{{"rssBytes", 0}, {"vmsBytes", 0}}},
      {"gpu", json{{"vendor", ""},
                   {"deviceIndex", nullptr},
                   {"utilPercent", nullptr},
                   {"memoryUsedBytes", nullptr},
                   {"memoryTotalBytes", nullptr},
                   {"available", false}}},
      {"frame", json{{"observed", 0},
                     {"processed", 0},
                     {"dropped", 0},
                     {"localOnlyEmits", 0},
                     {"routedCrossEmits", 0},
                     {"suppressedCrossPublishes", 0},
                     {"callbackDeliveries", 0},
                     {"bufferPullDeliveries", 0}}},
      {"timing", json{{"processMsAvg", 0.0},
                      {"processMsP95", 0.0},
                      {"waitMsAvg", 0.0},
                      {"waitMsP95", 0.0},
                      {"latencyMsAvg", nullptr},
                      {"latencyMsP95", nullptr}}},
      {"queue", json{{"depth", 0}}},
      {"error", json{{"countWindow", 0},
                     {"lastNodeId", ""},
                     {"lastCode", ""},
                     {"lastMessage", ""},
                     {"lastSeverity", "error"},
                     {"lastFingerprint", ""},
                     {"lastRepeatCount", 0},
                     {"lastTsMs", nullptr},
                     {"currentNodeId", ""},
                     {"currentCode", ""},
                     {"currentMessage", ""},
                     {"currentSeverity", ""},
                     {"currentTsMs", nullptr}}},
  };
}

TEST(ProtocolModelsParse, CommandInvoke_IgnoreExtra) {
  json j = json::object();
  j["reqId"] = "req-1";
  j["call"] = "pickRegion";
  j["args"] = json::object();
  j["args"]["x"] = 1;
  j["meta"] = json::object();
  j["meta"]["traceId"] = "t1";
  j["extra"] = 123;  // should be ignored

  F8CommandInvokeRequest req{};
  ParseError err{};
  EXPECT_TRUE(parse_F8CommandInvokeRequest(j, req, err)) << err.message;
  EXPECT_EQ(req.reqId, "req-1");
  EXPECT_EQ(req.call, "pickRegion");
  EXPECT_TRUE(req.args.is_object());
  EXPECT_TRUE(req.meta.is_object());
}

TEST(ProtocolModelsParse, SetActiveArgs_Parse) {
  json j = json::object();
  j["active"] = true;
  j["unexpected"] = "ok";

  F8SetActiveArgs req{};
  ParseError err{};
  EXPECT_TRUE(parse_F8SetActiveArgs(j, req, err)) << err.message;
  EXPECT_TRUE(req.active);
}

TEST(ProtocolModelsParse, SetStateArgs_RequiresValue) {
  {
    json j = json::object();
    j["nodeId"] = "svc.demo";
    j["field"] = "active";

    F8SetStateArgs req{};
    ParseError err{};
    EXPECT_FALSE(parse_F8SetStateArgs(j, req, err));
  }
  {
    json j = json::object();
    j["nodeId"] = "svc.demo";
    j["field"] = "active";
    j["value"] = nullptr;  // allowed
    j["extra"] = 1;

    F8SetStateArgs req{};
    ParseError err{};
    EXPECT_TRUE(parse_F8SetStateArgs(j, req, err)) << err.message;
    EXPECT_EQ(req.nodeId, "svc.demo");
    EXPECT_EQ(req.field, "active");
    EXPECT_TRUE(req.value.is_null());
  }
}

TEST(ProtocolModelsParse, SetRungraphArgs_Parse) {
  json graph = json::object();
  graph["graphId"] = "g1";
  graph["revision"] = "r1";

  json j = json::object();
  j["graph"] = graph;
  j["extra"] = "ignored";

  F8SetRungraphArgs req{};
  ParseError err{};
  EXPECT_TRUE(parse_F8SetRungraphArgs(j, req, err)) << err.message;
  EXPECT_EQ(req.graph.graphId, "g1");
  EXPECT_EQ(req.graph.revision, "r1");
}

TEST(ProtocolModelsParse, MonitorSnapshot_ServiceBusShapeParses) {
  F8MonitorSnapshot snapshot{};
  ParseError err{};
  EXPECT_TRUE(parse_F8MonitorSnapshot(monitor_snapshot_payload(), snapshot, err)) << err.message;
}

TEST(ProtocolModelsParse, MonitorSnapshot_NestedErrorsNameField) {
  json payload = monitor_snapshot_payload();
  payload["frame"].erase("localOnlyEmits");

  F8MonitorSnapshot snapshot{};
  ParseError err{};
  EXPECT_FALSE(parse_F8MonitorSnapshot(payload, snapshot, err));
  EXPECT_NE(err.message.find("frame:"), std::string::npos) << err.message;
}

}  // namespace
