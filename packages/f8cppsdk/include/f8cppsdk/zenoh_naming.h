#pragma once

#include <optional>
#include <string>

namespace f8::cppsdk {

std::string zenoh_data_key(const std::string& service_id, const std::string& node_id, const std::string& port_id);
std::string zenoh_endpoint_key(const std::string& service_id, const std::string& endpoint);
std::string zenoh_cmd_key(const std::string& service_id);
std::string zenoh_service_liveliness_key(const std::string& service_id);
std::string zenoh_studio_liveliness_key(const std::string& studio_service_id);
std::string zenoh_state_key(const std::string& service_id, const std::string& node_id, const std::string& field);
std::string zenoh_kv_key(const std::string& service_id, const std::string& key);
std::string zenoh_kv_pattern(const std::string& service_id, const std::string& key_pattern);
std::string kv_bucket_to_service_id(const std::string& bucket);
std::optional<std::string> zenoh_key_to_kv_key(const std::string& key);
std::string subject_to_zenoh_key(const std::string& subject);
std::string zenoh_key_to_subject(const std::string& key);

}  // namespace f8::cppsdk
