from __future__ import annotations

import logging

from qtpy import QtCore


def as_qbytearray(value: object) -> QtCore.QByteArray | None:
    if isinstance(value, QtCore.QByteArray):
        return value
    if isinstance(value, (bytes, bytearray)):
        return QtCore.QByteArray(bytes(value))
    return None


def read_layout_bytes(*, settings: QtCore.QSettings, group: str, key: str) -> QtCore.QByteArray | None:
    settings.beginGroup(str(group))
    try:
        raw = settings.value(str(key))
    finally:
        settings.endGroup()
    return as_qbytearray(raw)


def write_layout_bytes(*, settings: QtCore.QSettings, group: str, key: str, value: QtCore.QByteArray) -> None:
    settings.beginGroup(str(group))
    try:
        settings.setValue(str(key), value)
        settings.sync()
    finally:
        settings.endGroup()


def normalize_supported_log_level(level: int) -> int:
    normalized = int(level)
    if normalized <= logging.DEBUG:
        return logging.DEBUG
    if normalized <= logging.INFO:
        return logging.INFO
    if normalized <= logging.WARNING:
        return logging.WARNING
    if normalized <= logging.ERROR:
        return logging.ERROR
    return logging.CRITICAL


def log_level_name_for_value(*, level: int, choices: tuple[tuple[str, int], ...]) -> str:
    normalized_level = normalize_supported_log_level(level)
    for name, value in choices:
        if value == normalized_level:
            return str(name)
    return "WARNING"


def log_level_value_from_name(*, level_name: str, choices: tuple[tuple[str, int], ...]) -> int | None:
    normalized_name = str(level_name or "").strip().upper()
    for candidate_name, candidate_value in choices:
        if candidate_name == normalized_name:
            return candidate_value
    return None


def read_saved_log_level_name(*, settings: QtCore.QSettings, group: str, key: str) -> str:
    settings.beginGroup(str(group))
    try:
        raw = settings.value(str(key), "")
    finally:
        settings.endGroup()
    return str(raw or "").strip().upper()


def write_saved_log_level_name(*, settings: QtCore.QSettings, group: str, key: str, level_name: str) -> None:
    settings.beginGroup(str(group))
    try:
        settings.setValue(str(key), str(level_name or "").strip().upper())
        settings.sync()
    finally:
        settings.endGroup()
