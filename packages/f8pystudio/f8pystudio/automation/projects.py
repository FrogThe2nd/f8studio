from __future__ import annotations

from typing import Any, Protocol

from f8pystudio.assets.common import JsonObject
from f8pystudio.assets.projects.project_models import F8ProjectRecord, F8ProjectSummary
from f8pystudio.assets.projects.project_storage import ProjectStorageService


class AutomationProjectGraph(Protocol):
    def clear_session(self) -> None: ...

    def load_session_payload(self, payload: JsonObject) -> None: ...

    def serialize_session(self) -> JsonObject: ...


def project_list_payload() -> dict[str, Any]:
    service = ProjectStorageService()
    current_project_id = service.current_project_id()
    autosave_project_id = service.autosave_project_id()
    return {
        "currentProjectId": current_project_id,
        "autosaveProjectId": autosave_project_id,
        "projects": [_project_summary_to_dict(project) for project in service.list_projects()],
    }


def project_new_payload(graph: AutomationProjectGraph, *, clear_current_project: bool = True) -> dict[str, Any]:
    graph.clear_session()
    service = ProjectStorageService()
    if bool(clear_current_project):
        service.set_current_project_id("")
    return {
        "cleared": True,
        "currentProjectId": service.current_project_id(),
    }


def project_save_payload(
    graph: AutomationProjectGraph,
    *,
    name: str = "",
    description: str = "",
    tags: list[str] | None = None,
    project_id: str = "",
    overwrite_project_id: str = "",
) -> dict[str, Any]:
    service = ProjectStorageService()
    normalized_project_id = str(project_id or "").strip() or str(overwrite_project_id or "").strip()
    if normalized_project_id:
        base = service.project(normalized_project_id)
        if base is None:
            raise FileNotFoundError(f"Project not found: {normalized_project_id}")
        saved = service.save_project(
            content=graph.serialize_session(),
            project_id=base.projectId,
            name=str(name or base.name),
            description=str(description if description else base.description),
            tags=_normalized_tags(tags, fallback=list(base.tags)),
            set_current=True,
        )
    else:
        current = service.project(service.current_project_id())
        if current is not None and not str(name or "").strip() and description == "" and tags is None:
            saved = service.save_project(
                content=graph.serialize_session(),
                project_id=current.projectId,
                name=current.name,
                description=current.description,
                tags=list(current.tags),
                set_current=True,
            )
        else:
            resolved_project_id = _resolve_project_id_for_name_overwrite(service, name=name)
            base = service.project(resolved_project_id) if resolved_project_id else None
            if base is not None:
                saved = service.save_project(
                    content=graph.serialize_session(),
                    project_id=base.projectId,
                    name=str(name or base.name),
                    description=str(description if description else base.description),
                    tags=_normalized_tags(tags, fallback=list(base.tags)),
                    set_current=True,
                )
            else:
                saved = service.save_project(
                    content=graph.serialize_session(),
                    name=str(name or "").strip() or "Untitled Project",
                    description=str(description or ""),
                    tags=_normalized_tags(tags, fallback=[]),
                    set_current=True,
                )
    return {"project": _project_record_to_dict(saved), "currentProjectId": service.current_project_id()}


def project_load_payload(graph: AutomationProjectGraph, *, project_id: str) -> dict[str, Any]:
    normalized_project_id = str(project_id or "").strip()
    if not normalized_project_id:
        raise ValueError("project_id is required")
    service = ProjectStorageService()
    project = service.project(normalized_project_id)
    if project is None:
        raise FileNotFoundError(f"Project not found: {normalized_project_id}")
    graph.load_session_payload(project.content)
    service.set_current_project_id(project.projectId)
    return {"project": _project_record_to_dict(project), "currentProjectId": service.current_project_id()}


def _normalized_tags(tags: list[str] | None, *, fallback: list[str]) -> list[str]:
    if tags is None:
        return [str(tag).strip() for tag in fallback if str(tag).strip()]
    return [str(tag).strip() for tag in list(tags) if str(tag).strip()]


def _resolve_project_id_for_name_overwrite(service: ProjectStorageService, *, name: str) -> str:
    normalized_name = str(name or "").strip()
    if not normalized_name:
        return ""
    matching_projects = service.list_projects_by_name(normalized_name)
    if len(matching_projects) == 1:
        return str(matching_projects[0].projectId)
    if len(matching_projects) > 1:
        raise ValueError(
            f"Multiple projects named '{normalized_name}' already exist; pass project_id to choose which one to overwrite."
        )
    return ""


def _project_summary_to_dict(project: F8ProjectSummary) -> dict[str, Any]:
    return {
        "projectId": project.projectId,
        "name": project.name,
        "description": project.description,
        "tags": list(project.tags),
        "latestVersionNumber": project.latestVersionNumber,
        "createdAt": project.createdAt,
        "updatedAt": project.updatedAt,
    }


def _project_record_to_dict(project: F8ProjectRecord) -> dict[str, Any]:
    return {
        "projectId": project.projectId,
        "name": project.name,
        "description": project.description,
        "tags": list(project.tags),
        "createdAt": project.createdAt,
        "updatedAt": project.updatedAt,
        "nodeCount": _project_node_count(project.content),
    }


def _project_node_count(content: JsonObject) -> int:
    layout = content.get("layout")
    if not isinstance(layout, dict):
        return 0
    nodes = layout.get("nodes")
    if not isinstance(nodes, dict):
        return 0
    return len(nodes)
