from __future__ import annotations

from f8pystudio.ai_assist.llm_bridge import AiLlmBridge


def test_strip_code_fence_basic() -> None:
    code = "```python\ndef foo():\n    pass\n```"
    assert AiLlmBridge._strip_code_fence(code) == "def foo():\n    pass"

def test_strip_code_fence_no_fences() -> None:
    code = "def foo():\n    pass"
    assert AiLlmBridge._strip_code_fence(code) == "def foo():\n    pass"

def test_strip_code_fence_with_think() -> None:
    code = "<think>\nThinking process here\n</think>\n```python\ndef foo():\n    pass\n```"
    assert AiLlmBridge._strip_code_fence(code) == "def foo():\n    pass"

def test_strip_code_fence_with_think_no_fences() -> None:
    code = "<think>\nThinking process here\n</think>\ndef foo():\n    pass"
    assert AiLlmBridge._strip_code_fence(code) == "def foo():\n    pass"

def test_strip_code_fence_with_think_inline() -> None:
    code = "<think>Hmm, let's fix this</think>\n```python\nprint('hello')\n```"
    assert AiLlmBridge._strip_code_fence(code) == "print('hello')"

def test_strip_code_fence_with_think_missing_start() -> None:
    code = "Hmm</think>\n```python\nprint('hello')\n```"
    assert AiLlmBridge._strip_code_fence(code) == "print('hello')"
