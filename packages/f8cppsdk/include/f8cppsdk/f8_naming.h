#pragma once

#include <string>

namespace f8::cppsdk {

// Ensure a string is safe to use as a single runtime path token.
std::string ensure_token(std::string value, const char* label);

std::string rungraph_key(const std::string& service_id);
std::string rungraph_deploy_status_key(const std::string& service_id);
std::string rungraph_deploy_request_status_key(const std::string& service_id, const std::string& req_id);
std::string ready_key(const std::string& service_id);
std::string state_path_node_field(const std::string& node_id, const std::string& field);

std::string data_key(const std::string& from_service_id, const std::string& from_node_id,
                     const std::string& port_id);
std::string cmd_channel_key(const std::string& service_id);
std::string svc_endpoint_key(const std::string& service_id, const std::string& endpoint);

}  // namespace f8::cppsdk
