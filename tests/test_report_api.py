"""report API(toc/content SSE/export) 테스트 — FakeReportClient, 네트워크 0회."""
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import config
from app.api import routes_report
from app.llm.base import LlmUnavailableError
from app.llm.t3q_client import T3qError
from tests.test_chat_api import parse_sse

CRITERIA = {
    "subject": "코로나19 재유행 대비계획",
    "backgroundInfo": {"disasterType": "감염병", "controlPhase": "대비"},
    "purposeOfDocument": {
        "goalOfBusiness": "재난안전계획서 작성",
        "role": "재난안전계획 수립 담당자",
        "targetAudiences": ["중앙정부"],
    },
}

TOC = {
    "title": "코로나19 재유행 대비계획서",
    "sections": [
        {"name": "1. 개요", "children": [
            {"name": "1.1. 목적", "children": []},
            {"name": "1.2. 배경", "children": []},
        ]},
    ],
}


class FakeReportClient:
    """T3qReportClient 대역 — 준비된 응답/스트림 항목을 반환한다."""

    def __init__(self, toc=None, content_items=None, error: Exception | None = None):
        self.toc = toc or TOC
        self.content_items = content_items or []
        self.error = error
        self.toc_calls: list[dict] = []
        self.content_calls: list[tuple[dict, list]] = []

    async def generate_toc(self, criteria):
        self.toc_calls.append(criteria)
        if self.error:
            raise self.error
        return self.toc

    async def generate_content(self, criteria, sections):
        self.content_calls.append((criteria, sections))
        for item in self.content_items:
            if isinstance(item, Exception):
                raise item
            yield item


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(config, "FILES_DIR", tmp_path / "files")
    app = FastAPI()
    app.include_router(routes_report.router, prefix="/api")
    client = TestClient(app)
    yield client, app


def _use(app: FastAPI, fake: FakeReportClient) -> None:
    app.dependency_overrides[routes_report.get_report_client] = lambda: fake


class TestToc:
    def test_toc_success(self, env):
        client, app = env
        fake = FakeReportClient()
        _use(app, fake)
        resp = client.post("/api/report/toc", json={"criteria": CRITERIA})
        assert resp.status_code == 200
        assert resp.json()["title"] == TOC["title"]
        assert fake.toc_calls == [CRITERIA]  # 기준정보가 그대로 전달된다

    def test_missing_required_fields_400(self, env):
        client, app = env
        _use(app, FakeReportClient())
        resp = client.post("/api/report/toc", json={"criteria": {"subject": "제목만"}})
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "backgroundInfo.disasterType" in detail
        assert "purposeOfDocument.targetAudiences" in detail

    def test_upstream_error_502(self, env):
        client, app = env
        _use(app, FakeReportClient(error=T3qError("형식 오류")))
        resp = client.post("/api/report/toc", json={"criteria": CRITERIA})
        assert resp.status_code == 502

    def test_unavailable_502(self, env):
        client, app = env
        _use(app, FakeReportClient(error=LlmUnavailableError("다운")))
        assert client.post("/api/report/toc", json={"criteria": CRITERIA}).status_code == 502


class TestContentStream:
    def test_sse_event_order_and_progress(self, env):
        client, app = env
        _use(app, FakeReportClient(content_items=[
            {"name": "1.1. 목적", "content": "본문 A", "references": []},
            {"name": "1.2. 배경", "content": "본문 B", "references": [
                {"id": "c", "fileId": "f", "fileName": "근거.pdf", "page": "1"}
            ]},
        ]))
        resp = client.post("/api/report/content", json={
            "criteria": CRITERIA, "sections": TOC["sections"],
        })
        assert resp.status_code == 200
        events = parse_sse(resp.text)
        assert [n for n, _ in events] == ["status", "section", "section", "done"]
        assert events[0][1]["total"] == 2  # 리프 2개
        first = events[1][1]
        assert (first["name"], first["seq"], first["total"]) == ("1.1. 목적", 1, 2)
        assert events[2][1]["references"][0]["fileName"] == "근거.pdf"
        assert events[3][1] == {"received": 2, "errors": 0, "total": 2}

    def test_section_error_relayed_and_stream_continues(self, env):
        client, app = env
        _use(app, FakeReportClient(content_items=[
            {"name": "1.1. 목적", "error": "생성 실패", "requestId": "r1"},
            {"name": "1.2. 배경", "content": "정상", "references": []},
        ]))
        resp = client.post("/api/report/content", json={
            "criteria": CRITERIA, "sections": TOC["sections"],
        })
        events = parse_sse(resp.text)
        assert [n for n, _ in events] == ["status", "section_error", "section", "done"]
        assert events[1][1]["error"] == "생성 실패"
        assert events[3][1]["errors"] == 1

    def test_aborted_stream_yields_error_after_partials(self, env):
        client, app = env
        _use(app, FakeReportClient(content_items=[
            {"name": "1.1. 목적", "content": "받은 것", "references": []},
            T3qError("완료 신호 없이 중단"),
        ]))
        resp = client.post("/api/report/content", json={
            "criteria": CRITERIA, "sections": TOC["sections"],
        })
        events = parse_sse(resp.text)
        assert [n for n, _ in events] == ["status", "section", "error"]
        assert events[2][1]["code"] == "t3q_error"
        assert events[2][1]["received"] == 1  # 받은 섹션 수 안내

    def test_empty_sections_400(self, env):
        client, app = env
        _use(app, FakeReportClient())
        resp = client.post("/api/report/content", json={"criteria": CRITERIA, "sections": []})
        assert resp.status_code == 400


class TestExport:
    SECTIONS_WITH_CONTENT = [
        {"name": "1. 개요", "content": "개요 본문", "references": [], "children": []},
    ]

    def test_export_hwpx(self, env, tmp_path):
        client, app = env
        resp = client.post("/api/report/export", json={
            "title": "대비계획서", "sections": self.SECTIONS_WITH_CONTENT, "format": "hwpx",
        })
        assert resp.status_code == 200
        out = tmp_path / "dl.hwpx"
        out.write_bytes(resp.content)
        from app.core.hwpx import validate_hwpx
        assert validate_hwpx(out).ok
        from urllib.parse import unquote
        disposition = unquote(resp.headers.get("content-disposition", ""))
        assert "대비계획서.hwpx" in disposition  # RFC 5987 percent 인코딩 해제 후 비교

    def test_export_docx(self, env, tmp_path):
        client, app = env
        resp = client.post("/api/report/export", json={
            "title": "대비계획서", "sections": self.SECTIONS_WITH_CONTENT, "format": "docx",
        })
        assert resp.status_code == 200
        out = tmp_path / "dl.docx"
        out.write_bytes(resp.content)
        from docx import Document
        texts = [p.text for p in Document(str(out)).paragraphs]
        assert "1. 개요" in texts and "개요 본문" in texts

    def test_bad_format_400(self, env):
        client, app = env
        resp = client.post("/api/report/export", json={
            "sections": self.SECTIONS_WITH_CONTENT, "format": "pdf",
        })
        assert resp.status_code == 400


class TestFlowE2E:
    def test_toc_to_content_to_export_flow(self, env, tmp_path):
        """목차 생성 → 본문 스트리밍 수신 → 결과를 트리에 배치 → hwpx 내보내기."""
        client, app = env
        fake = FakeReportClient(content_items=[
            {"name": "1.1. 목적", "content": "확산 방지와 신속 대응 체계 구축.", "references": []},
            {"name": "1.2. 배경", "content": "변이 확산으로 재유행 우려.\n\n| 구분 | 값 |\n| A | 1 |",
             "references": [{"id": "c", "fileId": "f", "fileName": "지침.pdf", "page": "2"}]},
        ])
        _use(app, fake)

        # 1) 목차
        toc = client.post("/api/report/toc", json={"criteria": CRITERIA}).json()
        sections = toc["sections"]

        # 2) 본문 스트리밍 → (프론트 역할) 리프 순서대로 트리에 배치
        events = parse_sse(client.post(
            "/api/report/content", json={"criteria": CRITERIA, "sections": sections},
        ).text)
        results = [d for n, d in events if n == "section"]
        assert len(results) == 2
        # 본문 생성 요청에 목차가 그대로 전달됐다
        assert fake.content_calls[0][1] == sections

        leaves = iter(results)
        def attach(nodes):
            for node in nodes:
                if node.get("children"):
                    attach(node["children"])
                else:
                    item = next(leaves)
                    node["content"] = item["content"]
                    node["references"] = item["references"]
        attach(sections)

        # 3) 내보내기 → 스트리밍으로 받은 내용이 문서에 들어 있다
        resp = client.post("/api/report/export", json={
            "title": toc["title"], "sections": sections, "format": "hwpx",
        })
        assert resp.status_code == 200
        out = tmp_path / "flow.hwpx"
        out.write_bytes(resp.content)
        from app.core.hwpx import extract_hwpx, find_section_files, parse_section, validate_hwpx
        assert validate_hwpx(out).ok
        extract_dir = tmp_path / "x"
        extract_hwpx(out, extract_dir)
        nodes, *_ = parse_section(find_section_files(extract_dir)[0])
        texts = [n.text for n in nodes]
        assert toc["title"] in texts
        assert "1.1. 목적" in texts
        assert "확산 방지와 신속 대응 체계 구축." in texts
        assert "지침.pdf" in " ".join(texts)  # 참조 표기


class TestTemplates:
    def test_list_templates(self, env):
        client, app = env
        resp = client.get("/api/report/templates")
        assert resp.status_code == 200
        items = {t["id"]: t for t in resp.json()["templates"]}
        assert "AI 행정문서 템플릿" in items
        assert items["AI 행정문서 템플릿"]["has_table"] is True

    def test_export_with_template_and_subtitle(self, env, tmp_path):
        client, app = env
        resp = client.post("/api/report/export", json={
            "title": "대비계획서",
            "subtitle": "서면 보고 / 2026. 7. 15.(수) / 담당자",
            "sections": [{"name": "1. 개요", "content": "○ 본문", "references": [], "children": []}],
            "format": "hwpx",
            "template": "AI 행정문서 템플릿",
        })
        assert resp.status_code == 200
        out = tmp_path / "tpl.hwpx"
        out.write_bytes(resp.content)
        from app.core.hwpx import extract_hwpx, find_section_files, parse_section, validate_hwpx
        assert validate_hwpx(out).ok
        extract_dir = tmp_path / "x"
        extract_hwpx(out, extract_dir)
        nodes, *_ = parse_section(find_section_files(extract_dir)[0])
        texts = [n.text for n in nodes]
        assert texts[0] == "대비계획서"
        assert texts[1].startswith("서면 보고 /")

    def test_export_unknown_template_400(self, env):
        client, app = env
        resp = client.post("/api/report/export", json={
            "sections": [{"name": "1.", "content": "x", "children": []}],
            "format": "hwpx", "template": "없는템플릿",
        })
        assert resp.status_code == 400
