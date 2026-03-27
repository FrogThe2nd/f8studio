from .viz_text import VizTextRuntimeNode, register_operator as register_viz_text
from .viz_track import VizTrackRuntimeNode, register_operator as register_viz_track
from .viz_wave import VizWaveRuntimeNode, register_operator as register_viz_wave
from .viz_video import VizVideoRuntimeNode, register_operator as register_viz_video
from .viz_audio import VizAudioRuntimeNode, register_operator as register_viz_audio
from .viz_three_d import VizThreeDRuntimeNode, register_operator as register_viz_three_d
from .control_panel import ControlPanelRuntimeNode, register_operator as register_control_panel
from .note import NoteRuntimeNode, register_operator as register_note
from .value_stepper import ValueStepperRuntimeNode, register_operator as register_value_stepper

__all__ = [
    "VizTextRuntimeNode",
    "VizTrackRuntimeNode",
    "VizWaveRuntimeNode",
    "VizVideoRuntimeNode",
    "VizAudioRuntimeNode",
    "VizThreeDRuntimeNode",
    "ControlPanelRuntimeNode",
    "NoteRuntimeNode",
    "ValueStepperRuntimeNode",
    "register_operator",
]


def register_operator(registry=None):
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
    reg = register_note(reg)
    reg = register_value_stepper(reg)
    return reg
