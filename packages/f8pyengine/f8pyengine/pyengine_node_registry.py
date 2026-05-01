from __future__ import annotations

from typing import Any

from f8pysdk.specs import (
    F8ServiceSpec,
    F8ServiceSchemaVersion,
    F8StateAccess,
    F8StateSpec,
    string_schema,
)
from f8pysdk.specs import F8RuntimeNode
from f8pysdk.nodes import RuntimeNode
from f8pysdk.registry import create_runtime_node_registry, RuntimeNodeRegistry, shared_runtime_node_registry

from .constants import SERVICE_CLASS
from .operators.serial_out import register_operator as register_serial_out_operator
from .operators.udp_in import register_operator as register_udp_in_operator
from .operators.udp_out import register_operator as register_udp_out_operator
from .operators.skeleton_decoder import register_operator as register_skeleton_decoder_operator
from .operators.exec_sequence import register_operator as register_exec_sequence_operator
from .operators.signal import register_operator as register_signal_operator
from .operators.print import register_operator as register_print_operator
from .operators.program_wave import register_operator as register_program_wave_operator
from .operators.tick import register_operator as register_tick_operator
from .operators.envelope import register_operator as register_envelope_operator
from .operators.smooth_filter import register_operator as register_smooth_filter_operator
from .operators.range_map import register_operator as register_range_map_operator
from .operators.rate_limiter import register_operator as register_rate_limiter_operator
from .operators.tcode import register_operator as register_tcode_operator
from .operators.python_script import register_operator as register_python_script_operator
from .operators.data_expr import register_operator as register_data_expr_operator
from .operators.lovense_mock_server import register_operator as register_lovense_mock_server_operator
from .operators.lovense_out import register_operator as register_lovense_out_operator
from .operators.buttplug_out import register_operator as register_buttplug_out_operator
from .operators.silence_detector import register_operator as register_silence_detector_operator
from .operators.switch_mixer import register_operator as register_switch_mixer_operator
from .operators.sequence_player import register_operator as register_sequence_player_operator
from .operators.playback_sync import register_operator as register_playback_sync_operator
from .operators.handy_out import register_operator as register_handy_out_operator
from .operators.state_trigger import register_operator as register_state_trigger_operator
from .operators.state_expr import register_operator as register_state_expr_operator
from .operators.bone_filter import register_operator as register_bone_filter_operator
from .operators.quat_to_euler import register_operator as register_quat_to_euler_operator
from .operators.vmc_decoder import register_operator as register_vmc_decoder_operator
from .operators.bone_selector import register_operator as register_bone_selector_operator
from .operators.wave_expr import register_operator as register_wave_expr_operator
from .operators.wave_pattern import register_operator as register_wave_pattern_operator
from .operators.wave_funscript import register_operator as register_wave_funscript_operator
from .operators.detrend import register_operator as register_detrend_operator
from .operators.lowpass_filter import register_operator as register_lowpass_filter_operator
from .operators.highpass_filter import register_operator as register_highpass_filter_operator
from .operators.bandpass_filter import register_operator as register_bandpass_filter_operator
from .operators.periodicity_detector import register_operator as register_periodicity_detector_operator
from .operators.recorder import register_operator as register_recorder_operator
from .operators.replayer import register_operator as register_replayer_operator
from .pyengine_service_node import PyEngineServiceNode


def register_pyengine_specs(registry: RuntimeNodeRegistry) -> RuntimeNodeRegistry:
    """
    Register f8.pyengine service + operator specs and runtime factories.

    Specs live next to their runtime implementations (see `operators/*.py`).
    """
    registry.register_service_spec(
        F8ServiceSpec(
            schemaVersion=F8ServiceSchemaVersion.f8service_1,
            serviceClass=SERVICE_CLASS,
            paletteCategory="svc",
            version="0.0.1",
            label="PyEngine",
            description="Python-based execution engine for Feel8 operators.",
            tags=["engine", "python", "py"],
            rendererClass="default_container",
            stateFields=[
                F8StateSpec(
                    name="dataDelivery",
                    label="Data Delivery",
                    description="How data inputs are delivered to nodes: buffered inputs only, or callback plus buffered inputs.",
                    valueSchema=string_schema(default="buffered", enum=["buffered", "callback"]),
                    access=F8StateAccess.rw,
                    required=True,
                    showOnNode=True,
                ),
            ],
        ),
        overwrite=True,
    )

    def _service_factory(node_id: str, node: F8RuntimeNode, initial_state: dict[str, Any]) -> RuntimeNode:
        return PyEngineServiceNode(node_id=node_id, node=node, initial_state=initial_state)

    registry.register_service_factory(SERVICE_CLASS, _service_factory, overwrite=True)

    register_tick_operator(registry)
    register_exec_sequence_operator(registry)
    register_signal_operator(registry)
    register_print_operator(registry)
    register_program_wave_operator(registry)
    register_envelope_operator(registry)
    register_smooth_filter_operator(registry)
    register_range_map_operator(registry)
    register_rate_limiter_operator(registry)
    register_serial_out_operator(registry)
    register_udp_in_operator(registry)
    register_skeleton_decoder_operator(registry)
    register_udp_out_operator(registry)
    register_tcode_operator(registry)
    register_python_script_operator(registry)
    register_data_expr_operator(registry)
    register_lovense_out_operator(registry)
    register_buttplug_out_operator(registry)
    register_lovense_mock_server_operator(registry)
    register_sequence_player_operator(registry)
    register_silence_detector_operator(registry)
    register_switch_mixer_operator(registry)
    register_playback_sync_operator(registry)
    register_handy_out_operator(registry)
    register_state_trigger_operator(registry)
    register_state_expr_operator(registry)
    register_bone_filter_operator(registry)
    register_quat_to_euler_operator(registry)
    register_vmc_decoder_operator(registry)
    register_bone_selector_operator(registry)
    register_wave_expr_operator(registry)
    register_wave_pattern_operator(registry)
    register_wave_funscript_operator(registry)
    register_detrend_operator(registry)
    register_lowpass_filter_operator(registry)
    register_highpass_filter_operator(registry)
    register_bandpass_filter_operator(registry)
    register_periodicity_detector_operator(registry)
    register_recorder_operator(registry)
    register_replayer_operator(registry)
    return registry


def create_pyengine_registry() -> RuntimeNodeRegistry:
    return register_pyengine_specs(create_runtime_node_registry())


def shared_pyengine_registry() -> RuntimeNodeRegistry:
    return register_pyengine_specs(shared_runtime_node_registry())
