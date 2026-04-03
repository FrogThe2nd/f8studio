from __future__ import annotations

from typing import TypeVar

T = TypeVar("T")


def validate_as(model_type: type[T], value: object, *_args: object, **_kwargs: object) -> T: ...


def dump_json(value: object, *_args: object, **_kwargs: object) -> object: ...


def copy_model(value: T, *_args: object, **kwargs: object) -> T: ...
