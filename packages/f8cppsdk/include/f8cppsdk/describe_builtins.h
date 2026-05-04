#pragma once

#include <nlohmann/json.hpp>

namespace f8::cppsdk {

// Normalize describe payload by force-applying SDK builtin state fields.
//
// Supported input shapes:
// - f8describe/1 payload: {"service": {...}, "operators": [...]}
// - f8service/1 payload:  {"serviceClass": "...", ...}
//
// Builtins:
// - service:  active(rw,bool,required=true,default=true,showOnNode=false,editPolicy=locked),
//             svcId(ro,string,required=true,showOnNode=false,editPolicy=locked),
//             monitor(dataOut,required=true,showOnNode=false)
// - operator: svcId(ro,string,required=true,showOnNode=false,editPolicy=locked),
//             operatorId(ro,string,required=true,showOnNode=false,editPolicy=locked)
nlohmann::json normalize_describe_with_builtin_state_fields(const nlohmann::json& payload);

}  // namespace f8::cppsdk
