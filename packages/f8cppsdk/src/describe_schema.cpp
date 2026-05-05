#include "f8cppsdk/describe_schema.h"

#include <initializer_list>
#include <string>
#include <vector>

namespace f8::cppsdk::describe {

using json = nlohmann::json;

json schema_string() {
  return json{{"type", "string"}};
}

json schema_string_enum(std::initializer_list<const char*> items) {
  json s = schema_string();
  s["enum"] = json::array();
  for (const char* item : items) {
    if (item != nullptr && *item != '\0') {
      s["enum"].push_back(item);
    }
  }
  return s;
}

json schema_string_enum(const std::vector<std::string>& values, const std::string& default_value) {
  json s = schema_string();
  s["enum"] = json::array();
  for (const std::string& value : values) {
    if (!value.empty()) {
      s["enum"].push_back(value);
    }
  }
  if (!default_value.empty()) {
    s["default"] = default_value;
  }
  return s;
}

json schema_number() {
  return json{{"type", "number"}};
}

json schema_number(double default_value, double minimum, double maximum) {
  json s{{"type", "number"}};
  s["default"] = default_value;
  s["minimum"] = minimum;
  s["maximum"] = maximum;
  return s;
}

json schema_integer() {
  return json{{"type", "integer"}};
}

json schema_integer(std::int64_t default_value, std::int64_t minimum, std::int64_t maximum) {
  json s{{"type", "integer"}};
  s["default"] = default_value;
  s["minimum"] = minimum;
  s["maximum"] = maximum;
  return s;
}

json schema_boolean() {
  return json{{"type", "boolean"}};
}

json schema_object(const json& props, const json& required) {
  json obj;
  obj["type"] = "object";
  obj["properties"] = props;
  if (required.is_array()) {
    obj["required"] = required;
  }
  obj["additionalProperties"] = false;
  return obj;
}

json schema_array(const json& item_schema) {
  json arr;
  arr["type"] = "array";
  arr["items"] = item_schema;
  return arr;
}

json schema_video_frame() {
  json obj = schema_object(
      json{{"schemaVersion", schema_integer(1, 1, 1)},
           {"format", schema_string_enum({"bgra32", "bgr24", "flow2_f16", "scalar1_f32"})},
           {"width", schema_integer()},
           {"height", schema_integer()},
           {"pitch", schema_integer()},
           {"frameId", schema_integer()},
           {"tsMs", schema_integer()}},
      json::array({"schemaVersion", "format", "width", "height", "pitch", "frameId", "tsMs"}));
  obj["$comment"] = "f8.payloadKind=video_frame";
  return obj;
}

json schema_audio_chunk() {
  json obj = schema_object(
      json{{"schemaVersion", schema_integer(1, 1, 1)},
           {"format", schema_string_enum({"f32le"})},
           {"sampleRate", schema_integer()},
           {"channels", schema_integer()},
           {"frames", schema_integer()},
           {"bytesPerFrame", schema_integer()},
           {"seq", schema_integer()},
           {"frameIndex", schema_integer()},
           {"tsMs", schema_integer()}},
      json::array({"schemaVersion", "format", "sampleRate", "channels", "frames", "bytesPerFrame", "seq",
                   "frameIndex", "tsMs"}));
  obj["$comment"] = "f8.payloadKind=audio_chunk";
  return obj;
}

json state_field(std::string name, const json& value_schema, std::string access, std::string label,
                 std::string description, bool show_on_node, std::string ui_control, bool redact_on_publish) {
  json sf;
  sf["name"] = std::move(name);
  sf["valueSchema"] = value_schema;
  sf["access"] = std::move(access);
  sf["required"] = true;
  if (!label.empty()) {
    sf["label"] = std::move(label);
  }
  if (!description.empty()) {
    sf["description"] = std::move(description);
  }
  if (show_on_node) {
    sf["showOnNode"] = true;
  }
  if (!ui_control.empty()) {
    sf["uiControl"] = std::move(ui_control);
  }
  if (redact_on_publish) {
    sf["redactOnPublish"] = true;
  }
  return sf;
}

}  // namespace f8::cppsdk::describe
