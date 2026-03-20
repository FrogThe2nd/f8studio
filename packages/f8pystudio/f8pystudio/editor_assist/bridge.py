from __future__ import annotations

import concurrent.futures
import json
import logging
import os
import threading
import time
from typing import Any

from qtpy import QtCore

from .lsp_client import LspClientError, PythonLspClient
from .workspace import EditorAssistContext, EditorWorkspaceSession

logger = logging.getLogger(__name__)


def _float_env(name: str, default: float, *, minimum: float) -> float:
    raw = str(os.environ.get(name) or "").strip()
    if not raw:
        return max(minimum, float(default))
    try:
        value = float(raw)
    except ValueError:
        return max(minimum, float(default))
    return max(minimum, value)


class PythonEditorAssistBridge(QtCore.QObject):
    completion_ready = QtCore.Signal(str, str)
    completionReady = QtCore.Signal(str, str)
    completion_item_resolved = QtCore.Signal(str, str)
    completionItemResolved = QtCore.Signal(str, str)
    hover_ready = QtCore.Signal(str, str)
    hoverReady = QtCore.Signal(str, str)
    signature_help_ready = QtCore.Signal(str, str)
    signatureHelpReady = QtCore.Signal(str, str)
    diagnostics_ready = QtCore.Signal(object)
    diagnosticsReady = QtCore.Signal(object)

    def __init__(
        self,
        *,
        code: str,
        language: str,
        context: EditorAssistContext | None = None,
        parent: QtCore.QObject | None = None,
    ) -> None:
        super().__init__(parent)
        lang = str(language or "").strip().lower() or "python"
        self._language = lang
        self._context = context or EditorAssistContext(language="python")
        self._line_offset = 0
        self._version = 1
        self._last_user_code = str(code or "")
        self._last_error_sig = ""
        self._last_error_ts = 0.0
        self._state_lock = threading.Lock()
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="f8-pylsp-bridge")
        self._completion_future: concurrent.futures.Future[list[dict[str, Any]]] | None = None
        self._completion_resolve_future: concurrent.futures.Future[dict[str, Any] | None] | None = None
        self._hover_future: concurrent.futures.Future[dict[str, Any] | None] | None = None
        self._signature_future: concurrent.futures.Future[dict[str, Any] | None] | None = None
        self._completion_generation = 0
        self._completion_resolve_generation = 0
        self._hover_generation = 0
        self._signature_generation = 0
        self._completion_resolve_seq = 0
        self._completion_resolve_items: dict[str, dict[str, Any]] = {}
        self._is_shutting_down = False
        self._completion_timeout_s = _float_env("F8_PY_LSP_COMPLETION_TIMEOUT_S", 4.0, minimum=0.3)
        self._completion_resolve_timeout_s = _float_env("F8_PY_LSP_COMPLETION_RESOLVE_TIMEOUT_S", 2.5, minimum=0.3)
        self._hover_timeout_s = _float_env("F8_PY_LSP_HOVER_TIMEOUT_S", 3.0, minimum=0.3)
        self._signature_timeout_s = _float_env("F8_PY_LSP_SIGNATURE_TIMEOUT_S", 3.0, minimum=0.3)
        self._workspace, self._client, self._line_offset = self._build_runtime_context(
            context=self._context,
            code=self._last_user_code,
            version=int(self._version),
        )
        self._log_context_ready(prefix="started")

    def _build_runtime_context(
        self,
        *,
        context: EditorAssistContext,
        code: str,
        version: int,
    ) -> tuple[EditorWorkspaceSession, PythonLspClient, int]:
        workspace = EditorWorkspaceSession(language=self._language, context=context)
        client = PythonLspClient(
            workspace_root=workspace.root_path,
            diagnostics_callback=self._on_publish_diagnostics,
            request_timeout_s=2.5,
        )
        try:
            client.start()
            snapshot = workspace.build_document_snapshot(user_code=str(code or ""))
            line_offset = int(snapshot.line_offset)
            client.open_document(
                uri=workspace.document_uri,
                language_id="python",
                text=snapshot.text,
                version=int(version),
            )
        except Exception:
            try:
                client.shutdown()
            except Exception:
                logger.exception("python lsp bridge failed to shutdown client after init error")
            workspace.close()
            raise
        return workspace, client, line_offset

    def _log_context_ready(self, *, prefix: str) -> None:
        logger.info(
            "python lsp bridge %s: uri=%s lineOffset=%d completionTimeout=%.1fs completionResolveTimeout=%.1fs hoverTimeout=%.1fs signatureTimeout=%.1fs supportFiles=%s dynamicInputs=%s dynamicStates=%s",
            str(prefix or "ready"),
            self._workspace.document_uri,
            self._line_offset,
            self._completion_timeout_s,
            self._completion_resolve_timeout_s,
            self._hover_timeout_s,
            self._signature_timeout_s,
            [name for name, _ in self._context.support_files],
            self._context.dynamic_inputs_binding is not None,
            self._context.dynamic_states_binding is not None,
        )
        error_message = str(self._context.error_message or "").strip()
        if error_message:
            logger.warning("python lsp bridge context warning: %s", error_message)

    def _detach_inflight(self) -> tuple[
        concurrent.futures.Future[list[dict[str, Any]]] | None,
        concurrent.futures.Future[dict[str, Any] | None] | None,
        concurrent.futures.Future[dict[str, Any] | None] | None,
        concurrent.futures.Future[dict[str, Any] | None] | None,
    ]:
        with self._state_lock:
            completion_future = self._completion_future
            completion_resolve_future = self._completion_resolve_future
            hover_future = self._hover_future
            signature_future = self._signature_future
            self._completion_future = None
            self._completion_resolve_future = None
            self._hover_future = None
            self._signature_future = None
            self._completion_generation += 1
            self._completion_resolve_generation += 1
            self._hover_generation += 1
            self._signature_generation += 1
            self._completion_resolve_items.clear()
        return completion_future, completion_resolve_future, hover_future, signature_future

    @staticmethod
    def _cancel_future(future: concurrent.futures.Future[Any] | None) -> None:
        if future is None:
            return
        future.cancel()

    def reload_context(self, context: EditorAssistContext | None) -> bool:
        if self._is_shutting_down:
            return False
        next_context = context or EditorAssistContext(language="python")
        started_at = time.perf_counter()
        with self._state_lock:
            code_snapshot = str(self._last_user_code or "")
            next_version = int(self._version) + 1
        try:
            new_workspace, new_client, new_line_offset = self._build_runtime_context(
                context=next_context,
                code=code_snapshot,
                version=next_version,
            )
        except Exception as exc:
            self._log_bridge_error("reloadContext", exc)
            return False

        completion_future, completion_resolve_future, hover_future, signature_future = self._detach_inflight()
        with self._state_lock:
            old_workspace = self._workspace
            old_client = self._client
            self._workspace = new_workspace
            self._client = new_client
            self._context = next_context
            self._version = next_version
            self._line_offset = int(new_line_offset)
        self._cancel_future(completion_future)
        self._cancel_future(completion_resolve_future)
        self._cancel_future(hover_future)
        self._cancel_future(signature_future)
        try:
            old_client.shutdown()
        except Exception:
            logger.exception("python lsp bridge reload failed to shutdown old client")
        old_workspace.close()
        elapsed_ms = int((time.perf_counter() - started_at) * 1000.0)
        self._log_context_ready(prefix=f"reloaded elapsedMs={elapsed_ms}")
        return True

    def shutdown(self) -> None:
        self._is_shutting_down = True
        completion_future, completion_resolve_future, hover_future, signature_future = self._detach_inflight()
        self._cancel_future(completion_future)
        self._cancel_future(completion_resolve_future)
        self._cancel_future(hover_future)
        self._cancel_future(signature_future)
        self._executor.shutdown(wait=False, cancel_futures=True)
        self._client.shutdown()
        self._workspace.close()

    @QtCore.Slot(str)
    def sync_document(self, code: str) -> None:
        text = str(code or "")
        try:
            with self._state_lock:
                if text == self._last_user_code:
                    return
                self._last_user_code = text
                self._version += 1
                snapshot = self._workspace.build_document_snapshot(user_code=self._last_user_code)
                self._line_offset = int(snapshot.line_offset)
                self._client.change_document(
                    uri=self._workspace.document_uri,
                    text=snapshot.text,
                    version=self._version,
                )
        except Exception as exc:
            self._log_bridge_error("didChange", exc)

    @QtCore.Slot(str)
    def syncDocument(self, code: str) -> None:
        self.sync_document(code)

    @QtCore.Slot(str, str, int, int)
    def request_completions(self, request_id: str, code: str, line: int, column: int) -> None:
        if self._is_shutting_down:
            return
        request_id_txt = str(request_id or "")
        started_at = time.perf_counter()
        with self._state_lock:
            self._completion_generation += 1
            generation = int(self._completion_generation)
            previous = self._completion_future
            if previous is not None and not previous.done():
                previous.cancel()
            future = self._executor.submit(
                self._completion_items,
                code=str(code or ""),
                line=int(line),
                column=int(column),
                request_id=request_id_txt,
            )
            self._completion_future = future

        def _on_done(done_future: concurrent.futures.Future[list[dict[str, Any]]]) -> None:
            if done_future.cancelled() or self._is_shutting_down:
                return
            try:
                items = done_future.result()
            except Exception as exc:  # boundary: worker thread callback
                self._log_bridge_error("completion", exc)
                items = []
            with self._state_lock:
                if generation != self._completion_generation:
                    return
            elapsed_ms = int((time.perf_counter() - started_at) * 1000.0)
            logger.debug("python lsp completion response: id=%s items=%d elapsedMs=%d", request_id_txt, len(items), elapsed_ms)
            payload = json.dumps(items, ensure_ascii=False)
            self.completion_ready.emit(request_id_txt, payload)
            self.completionReady.emit(request_id_txt, payload)

        future.add_done_callback(_on_done)

    @QtCore.Slot(str, str, int, int)
    def requestCompletions(self, request_id: str, code: str, line: int, column: int) -> None:
        self.request_completions(request_id, code, line, column)

    @QtCore.Slot(str, str)
    def request_completion_item_resolve(self, request_id: str, resolve_key: str) -> None:
        if self._is_shutting_down:
            return
        request_id_txt = str(request_id or "")
        resolve_key_txt = str(resolve_key or "").strip()
        if not resolve_key_txt:
            payload_empty = json.dumps(None, ensure_ascii=False)
            self.completion_item_resolved.emit(request_id_txt, payload_empty)
            self.completionItemResolved.emit(request_id_txt, payload_empty)
            return
        started_at = time.perf_counter()
        with self._state_lock:
            self._completion_resolve_generation += 1
            generation = int(self._completion_resolve_generation)
            previous = self._completion_resolve_future
            if previous is not None and not previous.done():
                previous.cancel()
            future = self._executor.submit(self._completion_item_resolve_payload, resolve_key_txt)
            self._completion_resolve_future = future

        def _on_done(done_future: concurrent.futures.Future[dict[str, Any] | None]) -> None:
            if done_future.cancelled() or self._is_shutting_down:
                return
            try:
                payload = done_future.result()
            except Exception as exc:  # boundary: worker thread callback
                self._log_bridge_error("completionResolve", exc)
                payload = None
            with self._state_lock:
                if generation != self._completion_resolve_generation:
                    return
            elapsed_ms = int((time.perf_counter() - started_at) * 1000.0)
            has_payload = payload is not None
            logger.debug(
                "python lsp completion resolve response: id=%s hasPayload=%s elapsedMs=%d",
                request_id_txt,
                has_payload,
                elapsed_ms,
            )
            payload_json = json.dumps(payload, ensure_ascii=False)
            self.completion_item_resolved.emit(request_id_txt, payload_json)
            self.completionItemResolved.emit(request_id_txt, payload_json)

        future.add_done_callback(_on_done)

    @QtCore.Slot(str, str)
    def requestCompletionItemResolve(self, request_id: str, resolve_key: str) -> None:
        self.request_completion_item_resolve(request_id, resolve_key)

    @QtCore.Slot(str, str, int, int)
    def request_hover(self, request_id: str, code: str, line: int, column: int) -> None:
        if self._is_shutting_down:
            return
        request_id_txt = str(request_id or "")
        with self._state_lock:
            self._hover_generation += 1
            generation = int(self._hover_generation)
            previous = self._hover_future
            if previous is not None and not previous.done():
                previous.cancel()
            future = self._executor.submit(
                self._hover_payload,
                code=str(code or ""),
                line=int(line),
                column=int(column),
            )
            self._hover_future = future

        def _on_done(done_future: concurrent.futures.Future[dict[str, Any] | None]) -> None:
            if done_future.cancelled() or self._is_shutting_down:
                return
            try:
                payload = done_future.result()
            except Exception as exc:  # boundary: worker thread callback
                self._log_bridge_error("hover", exc)
                payload = None
            with self._state_lock:
                if generation != self._hover_generation:
                    return
            payload_json = json.dumps(payload, ensure_ascii=False)
            self.hover_ready.emit(request_id_txt, payload_json)
            self.hoverReady.emit(request_id_txt, payload_json)

        future.add_done_callback(_on_done)

    @QtCore.Slot(str, str, int, int)
    def requestHover(self, request_id: str, code: str, line: int, column: int) -> None:
        self.request_hover(request_id, code, line, column)

    @QtCore.Slot(str, str, int, int)
    def request_signature_help(self, request_id: str, code: str, line: int, column: int) -> None:
        if self._is_shutting_down:
            return
        request_id_txt = str(request_id or "")
        started_at = time.perf_counter()
        with self._state_lock:
            self._signature_generation += 1
            generation = int(self._signature_generation)
            previous = self._signature_future
            if previous is not None and not previous.done():
                previous.cancel()
            future = self._executor.submit(
                self._signature_payload,
                code=str(code or ""),
                line=int(line),
                column=int(column),
            )
            self._signature_future = future

        def _on_done(done_future: concurrent.futures.Future[dict[str, Any] | None]) -> None:
            if done_future.cancelled() or self._is_shutting_down:
                return
            try:
                payload = done_future.result()
            except Exception as exc:  # boundary: worker thread callback
                self._log_bridge_error("signatureHelp", exc)
                payload = None
            with self._state_lock:
                if generation != self._signature_generation:
                    return
            elapsed_ms = int((time.perf_counter() - started_at) * 1000.0)
            has_payload = payload is not None
            logger.debug("python lsp signatureHelp response: id=%s hasPayload=%s elapsedMs=%d", request_id_txt, has_payload, elapsed_ms)
            payload_json = json.dumps(payload, ensure_ascii=False)
            self.signature_help_ready.emit(request_id_txt, payload_json)
            self.signatureHelpReady.emit(request_id_txt, payload_json)

        future.add_done_callback(_on_done)

    @QtCore.Slot(str, str, int, int)
    def requestSignatureHelp(self, request_id: str, code: str, line: int, column: int) -> None:
        self.request_signature_help(request_id, code, line, column)

    def _on_publish_diagnostics(self, uri: str, diagnostics: list[dict[str, Any]]) -> None:
        if str(uri) != self._workspace.document_uri:
            return
        markers: list[dict[str, Any]] = []
        offset = int(self._line_offset)
        for item in diagnostics:
            range_obj = item.get("range")
            if not isinstance(range_obj, dict):
                continue
            start_obj = range_obj.get("start")
            end_obj = range_obj.get("end")
            if not isinstance(start_obj, dict) or not isinstance(end_obj, dict):
                continue

            start_line_lsp = int(start_obj.get("line") or 0)
            end_line_lsp = int(end_obj.get("line") or start_line_lsp)
            if end_line_lsp < offset:
                continue

            start_line = max(1, start_line_lsp - offset + 1)
            end_line = max(start_line, end_line_lsp - offset + 1)
            start_col = max(1, int(start_obj.get("character") or 0) + 1)
            end_col = max(start_col, int(end_obj.get("character") or 0) + 1)

            severity = self._normalize_severity(item.get("severity"))
            message = str(item.get("message") or "")
            source = str(item.get("source") or "")
            code = item.get("code")
            code_txt = ""
            if code is not None:
                code_txt = str(code)
            marker: dict[str, Any] = {
                "startLineNumber": start_line,
                "startColumn": start_col,
                "endLineNumber": end_line,
                "endColumn": end_col,
                "severity": severity,
                "message": message,
                "source": source,
            }
            if code_txt:
                marker["code"] = code_txt
            markers.append(marker)
        self.diagnostics_ready.emit(markers)
        self.diagnosticsReady.emit(markers)

    @staticmethod
    def _normalize_severity(value: Any) -> int:
        try:
            sev = int(value)
        except (TypeError, ValueError):
            sev = 3
        if sev < 1 or sev > 8:
            return 3
        return sev

    def _lsp_line(self, editor_line_1based: int) -> int:
        with self._state_lock:
            offset = int(self._line_offset)
        return max(0, int(editor_line_1based) - 1 + offset)

    def inline_completion_items(self, *, line: int, column: int, request_id: str) -> list[dict[str, Any]]:
        return self._completion_items(code="", line=int(line), column=int(column), request_id=str(request_id or ""))

    def _completion_items(self, *, code: str, line: int, column: int, request_id: str) -> list[dict[str, Any]]:
        self.sync_document(str(code or ""))
        logger.debug("python lsp completion request: id=%s line=%s col=%s", request_id, line, column)
        try:
            result = self._client.request_completion(
                uri=self._workspace.document_uri,
                line=self._lsp_line(line),
                character=max(0, int(column)),
                timeout_s=self._completion_timeout_s,
            )
            self._clear_completion_resolve_items()
            return self._normalize_completion_result(result)
        except LspClientError as exc:
            if "timeout" not in str(exc).lower():
                self._log_bridge_error("completion", exc)
                return []
            try:
                result_retry = self._client.request_completion(
                    uri=self._workspace.document_uri,
                    line=self._lsp_line(line),
                    character=max(0, int(column)),
                    timeout_s=(self._completion_timeout_s * 1.5),
                )
                self._clear_completion_resolve_items()
                return self._normalize_completion_result(result_retry)
            except Exception as retry_exc:
                self._log_bridge_error("completion", retry_exc)
                return []
        except Exception as exc:
            self._log_bridge_error("completion", exc)
            return []

    def _clear_completion_resolve_items(self) -> None:
        with self._state_lock:
            self._completion_resolve_items.clear()
            self._completion_resolve_seq = 0

    def _register_completion_resolve_item(self, item: dict[str, Any]) -> str:
        with self._state_lock:
            self._completion_resolve_seq += 1
            key = f"c{self._completion_resolve_seq}"
            self._completion_resolve_items[key] = dict(item)
            if len(self._completion_resolve_items) > 500:
                sorted_keys = sorted(
                    self._completion_resolve_items.keys(),
                    key=lambda entry_key: int(str(entry_key)[1:]) if str(entry_key).startswith("c") else 0,
                )
                trim_count = len(self._completion_resolve_items) - 500
                for stale_key in sorted_keys[:trim_count]:
                    self._completion_resolve_items.pop(stale_key, None)
            return key

    def _completion_item_resolve_payload(self, resolve_key: str) -> dict[str, Any] | None:
        with self._state_lock:
            item = self._completion_resolve_items.get(str(resolve_key or ""))
            if item is None:
                return None
            request_item = dict(item)
        logger.debug("python lsp completion resolve request: key=%s", resolve_key)
        try:
            result = self._client.request_completion_item_resolve(
                item=request_item,
                timeout_s=self._completion_resolve_timeout_s,
            )
            return self._normalize_resolved_completion_item(result)
        except Exception as exc:
            self._log_bridge_error("completionResolve", exc)
            return None

    def _hover_payload(self, *, code: str, line: int, column: int) -> dict[str, Any] | None:
        self.sync_document(str(code or ""))
        try:
            result = self._client.request_hover(
                uri=self._workspace.document_uri,
                line=self._lsp_line(line),
                character=max(0, int(column)),
                timeout_s=self._hover_timeout_s,
            )
            return self._normalize_hover_result(result)
        except Exception as exc:
            self._log_bridge_error("hover", exc)
            return None

    def _signature_payload(self, *, code: str, line: int, column: int) -> dict[str, Any] | None:
        self.sync_document(str(code or ""))
        logger.debug("python lsp signatureHelp request: line=%s col=%s", line, column)
        try:
            result = self._client.request_signature_help(
                uri=self._workspace.document_uri,
                line=self._lsp_line(line),
                character=max(0, int(column)),
                timeout_s=self._signature_timeout_s,
            )
            return self._normalize_signature_result(result)
        except Exception as exc:
            self._log_bridge_error("signatureHelp", exc)
            return None

    @staticmethod
    def _completion_items_raw(result: Any) -> list[dict[str, Any]]:
        if isinstance(result, list):
            return [item for item in result if isinstance(item, dict)]
        if isinstance(result, dict):
            items = result.get("items")
            if isinstance(items, list):
                return [item for item in items if isinstance(item, dict)]
        return []

    def _normalize_completion_result(self, result: Any) -> list[dict[str, Any]]:
        items_raw = self._completion_items_raw(result)
        out: list[dict[str, Any]] = []
        for item in items_raw[:300]:
            label = str(item.get("label") or "").strip()
            if not label:
                continue
            insert_text = self._completion_insert_text(item, fallback=label)
            kind = self._normalize_completion_kind(item.get("kind"))
            detail = str(item.get("detail") or "")
            documentation = self._completion_documentation(item.get("documentation"))
            resolve_key = self._register_completion_resolve_item(item)

            entry: dict[str, Any] = {
                "label": label,
                "insertText": insert_text,
                "kind": kind,
                "detail": detail,
                "resolveKey": resolve_key,
            }
            sort_text = item.get("sortText")
            if isinstance(sort_text, str) and sort_text:
                entry["sortText"] = sort_text
            filter_text = item.get("filterText")
            if isinstance(filter_text, str) and filter_text:
                entry["filterText"] = filter_text
            if documentation:
                entry["documentation"] = documentation
            out.append(entry)
        return out

    @classmethod
    def _normalize_resolved_completion_item(cls, result: Any) -> dict[str, Any] | None:
        if not isinstance(result, dict):
            return None
        label = str(result.get("label") or "").strip()
        detail = str(result.get("detail") or "")
        documentation = cls._completion_documentation(result.get("documentation"))
        insert_text = cls._completion_insert_text(result, fallback=label)
        payload: dict[str, Any] = {}
        if label:
            payload["label"] = label
        if detail:
            payload["detail"] = detail
        if documentation:
            payload["documentation"] = documentation
        if insert_text:
            payload["insertText"] = insert_text
        return payload if payload else None

    @staticmethod
    def _completion_insert_text(item: dict[str, Any], *, fallback: str) -> str:
        insert_text = item.get("insertText")
        if isinstance(insert_text, str) and insert_text:
            return insert_text
        text_edit = item.get("textEdit")
        if isinstance(text_edit, dict):
            new_text = text_edit.get("newText")
            if isinstance(new_text, str) and new_text:
                return new_text
        return fallback

    @staticmethod
    def _completion_documentation(value: Any) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            content = value.get("value")
            if isinstance(content, str):
                return content
        return ""

    @staticmethod
    def _normalize_completion_kind(value: Any) -> int:
        try:
            kind = int(value)
        except (TypeError, ValueError):
            return 1
        return kind if 1 <= kind <= 25 else 1

    @staticmethod
    def _normalize_hover_result(result: Any) -> dict[str, Any] | None:
        if not isinstance(result, dict):
            return None
        contents = result.get("contents")
        monaco_contents = PythonEditorAssistBridge._hover_contents(contents)
        if not monaco_contents:
            return None
        return {"contents": monaco_contents}

    @staticmethod
    def _normalize_signature_result(result: Any) -> dict[str, Any] | None:
        if not isinstance(result, dict):
            return None
        signatures_raw = result.get("signatures")
        if not isinstance(signatures_raw, list):
            return None
        signatures_out: list[dict[str, Any]] = []
        for signature_item in signatures_raw[:25]:
            if not isinstance(signature_item, dict):
                continue
            label = str(signature_item.get("label") or "").strip()
            if not label:
                continue
            signature_out: dict[str, Any] = {"label": label, "parameters": []}
            doc = PythonEditorAssistBridge._completion_documentation(signature_item.get("documentation"))
            if doc:
                signature_out["documentation"] = doc
            parameters_raw = signature_item.get("parameters")
            parameters_out: list[dict[str, Any]] = []
            if isinstance(parameters_raw, list):
                for parameter_item in parameters_raw[:25]:
                    if not isinstance(parameter_item, dict):
                        continue
                    label_value = parameter_item.get("label")
                    parameter_out: dict[str, Any] = {}
                    if isinstance(label_value, str):
                        parameter_label = label_value.strip()
                        if not parameter_label:
                            continue
                        parameter_out["label"] = parameter_label
                    elif (
                        isinstance(label_value, list)
                        and len(label_value) == 2
                        and isinstance(label_value[0], int)
                        and isinstance(label_value[1], int)
                    ):
                        start = max(0, int(label_value[0]))
                        end = max(start, int(label_value[1]))
                        parameter_out["label"] = [start, end]
                    else:
                        continue
                    parameter_doc = PythonEditorAssistBridge._completion_documentation(parameter_item.get("documentation"))
                    if parameter_doc:
                        parameter_out["documentation"] = parameter_doc
                    parameters_out.append(parameter_out)
            signature_out["parameters"] = parameters_out
            signatures_out.append(signature_out)
        if not signatures_out:
            return None

        active_signature = 0
        active_parameter = 0
        try:
            active_signature = int(result.get("activeSignature") or 0)
        except (TypeError, ValueError):
            active_signature = 0
        try:
            active_parameter = int(result.get("activeParameter") or 0)
        except (TypeError, ValueError):
            active_parameter = 0

        active_signature = max(0, min(active_signature, len(signatures_out) - 1))
        selected_params = signatures_out[active_signature].get("parameters")
        if isinstance(selected_params, list) and selected_params:
            active_parameter = max(0, min(active_parameter, len(selected_params) - 1))
        else:
            active_parameter = 0

        return {
            "signatures": signatures_out,
            "activeSignature": active_signature,
            "activeParameter": active_parameter,
        }

    @staticmethod
    def _hover_contents(contents: Any) -> list[dict[str, str]]:
        if isinstance(contents, str):
            return [{"value": contents}]
        if isinstance(contents, dict):
            value = contents.get("value")
            if isinstance(value, str):
                return [{"value": value}]
            language = contents.get("language")
            text = contents.get("value")
            if isinstance(language, str) and isinstance(text, str):
                return [{"value": f"```{language}\n{text}\n```"}]
            return []
        if isinstance(contents, list):
            out: list[dict[str, str]] = []
            for item in contents:
                out.extend(PythonEditorAssistBridge._hover_contents(item))
            return out
        return []

    def _log_bridge_error(self, stage: str, exc: BaseException) -> None:
        now = time.time()
        sig = f"{stage}:{type(exc).__name__}:{exc}"
        if sig == self._last_error_sig and (now - self._last_error_ts) < 2.0:
            return
        self._last_error_sig = sig
        self._last_error_ts = now
        if isinstance(exc, LspClientError):
            logger.warning("python lsp bridge %s failed: %s", stage, exc)
            return
        logger.exception("python lsp bridge %s failed", stage)
