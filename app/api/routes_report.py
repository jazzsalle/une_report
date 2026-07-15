"""재난안전계획서 생성 API (T3Q 전환 — docs/t3q_upgrade_design.md §3).

- POST /api/report/toc      기준정보 → 목차 (API-RPT-001 프록시)
- POST /api/report/content  기준정보+목차 → SSE로 목차별 본문 중계 (API-RPT-002)
  이벤트: `status`(시작·진행) → `section`*(리프별 결과, 도착 즉시)
          / `section_error`(해당 섹션만 실패) → `done`, 치명 오류는 `error`
- POST /api/report/export   내용이 채워진 목차 트리 → hwpx/docx 파일

문서 반영은 프론트가 section 이벤트를 누적해 트리에 배치하고,
export 시 완성 트리를 그대로 보낸다 (서버 무저장 — 설계 §5).
"""
import json
import uuid
from typing import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from app import config
from app.llm.base import LlmError, LlmTimeoutError, LlmUnavailableError
from app.llm.t3q_client import T3qError, T3qReportClient
from app.services.report_builder import (
    build_report_docx,
    build_report_hwpx,
    flatten_leaf_names,
)
from app.services.report_template import TemplateError, list_templates, load_template_styles

router = APIRouter(prefix="/report", tags=["report"])

# 기준정보 필수 경로 (API-RPT-001/002 명세의 필수 항목)
_REQUIRED_PATHS = [
    ("subject",),
    ("backgroundInfo", "disasterType"),
    ("backgroundInfo", "controlPhase"),
    ("purposeOfDocument", "goalOfBusiness"),
    ("purposeOfDocument", "role"),
    ("purposeOfDocument", "targetAudiences"),
]


class TocRequest(BaseModel):
    criteria: dict


class ContentRequest(BaseModel):
    criteria: dict
    sections: list[dict]


class ExportRequest(BaseModel):
    title: str = ""
    subtitle: str = ""       # 부제 줄 ("서면 보고 / 보고일시 / 역할") — 템플릿 사용 시 표기
    sections: list[dict]
    format: str = "hwpx"     # hwpx | docx
    template: str = ""       # 표준 템플릿 id (templates/*.hwpx stem) — 빈 값이면 기본 조립


def get_report_client() -> T3qReportClient:
    """T3Q 클라이언트 의존성. 테스트는 dependency_overrides로 교체한다."""
    return T3qReportClient()


def _validate_criteria(criteria: dict) -> None:
    """필수 기준정보 누락을 한국어 메시지의 400으로 안내한다."""
    missing: list[str] = []
    for path in _REQUIRED_PATHS:
        cur: object = criteria
        for key in path:
            cur = cur.get(key) if isinstance(cur, dict) else None
        if cur is None or (isinstance(cur, str) and not cur.strip()) or cur == []:
            missing.append(".".join(path))
    if missing:
        raise HTTPException(
            status_code=400, detail=f"필수 기준정보가 없습니다: {', '.join(missing)}"
        )


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/toc")
async def generate_toc(
    body: TocRequest, client: T3qReportClient = Depends(get_report_client)
):
    """기준정보 → {title, sections} 목차. 재요청은 같은 API 재호출."""
    _validate_criteria(body.criteria)
    try:
        return await client.generate_toc(body.criteria)
    except LlmTimeoutError as e:
        raise HTTPException(status_code=504, detail=str(e))
    except (LlmUnavailableError, T3qError, LlmError) as e:
        raise HTTPException(status_code=502, detail=str(e))


async def _content_events(
    body: ContentRequest, client: T3qReportClient
) -> AsyncIterator[str]:
    """RPT-002 스트림을 SSE로 중계한다 (모든 오류는 error 이벤트 — SSE 시작 후
    HTTP 상태를 바꿀 수 없으므로 200 + error)."""
    leaves = flatten_leaf_names(body.sections)
    total = len(leaves)
    yield _sse("status", {"detail": f"본문 생성을 시작합니다 (목차 {total}개)", "total": total})
    received = 0
    errors = 0
    try:
        async for item in client.generate_content(body.criteria, body.sections):
            if "error" in item:
                errors += 1
                yield _sse("section_error", {
                    "name": item.get("name") or "",
                    "error": item["error"],
                    "requestId": item.get("requestId"),
                })
                continue
            received += 1
            yield _sse("section", {
                "name": item["name"],
                "content": item["content"],
                "references": item["references"],
                "seq": received,
                "total": total,
            })
        yield _sse("done", {"received": received, "errors": errors, "total": total})
    except LlmTimeoutError as e:
        yield _sse("error", {"code": "t3q_timeout", "message": str(e)})
    except (LlmUnavailableError, T3qError, LlmError) as e:
        # T3qError("[DONE] 없이 중단")도 여기 — 받은 섹션은 이미 중계됨
        yield _sse("error", {"code": "t3q_error", "message": str(e), "received": received})


@router.post("/content")
async def generate_content(
    body: ContentRequest, client: T3qReportClient = Depends(get_report_client)
):
    """기준정보+목차 → SSE 본문 스트리밍 (목차별 도착 즉시 중계)."""
    _validate_criteria(body.criteria)
    if not body.sections:
        raise HTTPException(status_code=400, detail="목차(sections)가 비어 있습니다")
    return StreamingResponse(
        _content_events(body, client),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/templates")
async def get_templates():
    """표준 템플릿 목록 (templates/*.hwpx) — UI 선택지."""
    return {"templates": list_templates()}


@router.post("/export")
async def export_report(body: ExportRequest):
    """내용이 채워진 목차 트리를 hwpx/docx 파일로 조립해 반환한다.

    template 지정 시 hwpx는 템플릿 서식 조립, docx는 템플릿 스타일 근사 적용.
    """
    if body.format not in ("hwpx", "docx"):
        raise HTTPException(status_code=400, detail=f"지원하지 않는 형식: {body.format}")
    if not body.sections:
        raise HTTPException(status_code=400, detail="목차(sections)가 비어 있습니다")

    safe_title = "".join(
        ch for ch in (body.title or "재난안전계획서") if ch not in '\\/:*?"<>|'
    ).strip() or "재난안전계획서"
    out_dir = config.FILES_DIR / "reports" / uuid.uuid4().hex
    out_path = out_dir / f"{safe_title}.{body.format}"
    try:
        if body.format == "hwpx":
            build_report_hwpx(
                body.title, body.sections, out_path,
                subtitle=body.subtitle, template_id=body.template or None,
            )
            media = "application/octet-stream"
        else:
            styles = load_template_styles(body.template) if body.template else None
            build_report_docx(
                body.title, body.sections, out_path,
                subtitle=body.subtitle, template_styles=styles,
            )
            media = (
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            )
    except TemplateError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return FileResponse(out_path, media_type=media, filename=out_path.name)
