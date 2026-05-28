from __future__ import annotations

# State writes cross runtime-node callbacks and transport/watch boundaries.
# These named tuples keep boundary catches grep-able while preserving log-once behavior.
STATE_LOCAL_DELIVERY_ERRORS = (Exception,)
STATE_NODE_CALLBACK_ERRORS = (Exception,)
STATE_ROUTE_PROPAGATION_ERRORS = (Exception,)
STATE_TRANSPORT_ERRORS = (Exception,)
STATE_VALUE_COERCION_ERRORS = (TypeError, ValueError)
STATE_WATCH_LIFECYCLE_ERRORS = (Exception,)
