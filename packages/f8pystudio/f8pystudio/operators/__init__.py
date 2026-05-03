from f8pysdk.registry import Registry, RuntimeNodeRegistry, create_runtime_node_registry, shared_runtime_node_registry

from .viz_text import VizTextRuntimeNode, register_operator as register_viz_text
from .viz_track import VizTrackRuntimeNode, register_operator as register_viz_track
from .viz_wave import VizWaveRuntimeNode, register_operator as register_viz_wave
from .viz_video import VizVideoRuntimeNode, register_operator as register_viz_video
from .viz_audio import VizAudioRuntimeNode, register_operator as register_viz_audio
from .viz_three_d import VizThreeDRuntimeNode, register_operator as register_viz_three_d
from .control_panel import ControlPanelRuntimeNode, register_operator as register_control_panel
from .backdrop import BackdropRuntimeNode, register_operator as register_backdrop
from .data_expr import DataExprRuntimeNode, register_operator as register_data_expr
from .note import NoteRuntimeNode, register_operator as register_note
from .patch_hub import PatchHubRuntimeNode, register_operator as register_patch_hub
from .state_expr import StateExprRuntimeNode, register_operator as register_state_expr
from .value_stepper import ValueStepperRuntimeNode, register_operator as register_value_stepper

__all__ = [
    "VizTextRuntimeNode",
    "VizTrackRuntimeNode",
    "VizWaveRuntimeNode",
    "VizVideoRuntimeNode",
    "VizAudioRuntimeNode",
    "VizThreeDRuntimeNode",
    "ControlPanelRuntimeNode",
    "BackdropRuntimeNode",
    "DataExprRuntimeNode",
    "NoteRuntimeNode",
    "PatchHubRuntimeNode",
    "StateExprRuntimeNode",
    "ValueStepperRuntimeNode",
    "create_operator_registry",
    "register_operator",
    "shared_operator_registry",
]


def register_operator(registry: Registry) -> Registry:
    """
    Register all Studio in-process operators.
    """
    reg = register_viz_text(registry)
    reg = register_viz_wave(reg)
    reg = register_viz_track(reg)
    reg = register_viz_video(reg)
    reg = register_viz_audio(reg)
    reg = register_viz_three_d(reg)
    reg = register_control_panel(reg)
    reg = register_backdrop(reg)
    reg = register_data_expr(reg)
    reg = register_note(reg)
    reg = register_patch_hub(reg)
    reg = register_state_expr(reg)
    reg = register_value_stepper(reg)
    return reg


def create_operator_registry() -> RuntimeNodeRegistry:
    runtime_registry = create_runtime_node_registry()
    register_operator(Registry.wrap(runtime_registry))
    return runtime_registry


def shared_operator_registry() -> RuntimeNodeRegistry:
    runtime_registry = shared_runtime_node_registry()
    register_operator(Registry.wrap(runtime_registry))
    return runtime_registry
