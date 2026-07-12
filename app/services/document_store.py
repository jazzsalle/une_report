"""문서 저장소 (M5-2): 업로드·버전 관리·미리보기 캐시·내보내기.

M1(hwpx 코어)을 DB(documents/document_versions)·파일저장소와 연결한다.
파일 배치 — `{files_dir}/{document_id}/`:
  - original.hwpx : v0 (업로드 원본)
  - v{N}.hwpx     : N번째 편집본 (N >= 1)
  - v{N}.html     : 버전별 미리보기 HTML 캐시 (v0.html 포함)
  - v{N}_sections/: apply_edits가 남기는 편집 섹션 XML 스냅샷
DB에는 경로·메타만 기록한다 (DESIGN.md §7).

오류 규약 (라우터가 HTTP 상태로 매핑):
  - ValueError : 유효하지 않은 hwpx, 지원하지 않는 내보내기 형식 → 400
  - KeyError   : 문서 없음·소유자 아님·버전 없음 → 404
  - VersionConflictError : 편집 기준 버전 ≠ 현재 버전 (버전 핀 불일치)
"""
import json
import shutil
import sqlite3
import tempfile
import uuid
from pathlib import Path

from app import config
from app.core.hwpx import (
    TextNode,
    apply_edits,
    collect_placeholders,
    extract_hwpx,
    find_header_file,
    find_section_files,
    guide_char_pr_ids,
    hwpx_to_html,
    parse_section,
    validate_hwpx,
)
from app.db.database import now_iso

# TextNode.type → API 노드 type (M4 오케스트레이터·프론트가 소비하는 값)
_NODE_TYPE_MAP = {"body_text": "para", "table_cell": "cell"}

_EXPORT_FORMATS = ("hwpx", "docx")


class VersionConflictError(ValueError):
    """편집 기준 버전(base_version)이 문서의 현재 버전과 다름.

    전역 id는 파싱 순번이라 버전이 다르면 같은 id가 다른 노드를 가리킬 수
    있으므로, 클라이언트가 미리보기하던 버전과 현재 버전이 어긋나면 적용을
    거부한다. 클라이언트는 최신 미리보기로 갱신 후 재시도해야 한다.
    """

    def __init__(self, expected: int, current: int):
        super().__init__(
            f"문서가 다른 곳에서 수정되었습니다 (기준 v{expected}, 현재 v{current}). "
            "미리보기를 새로고침한 뒤 다시 시도해 주세요."
        )
        self.expected = expected
        self.current = current


class DocumentStore:
    """documents/document_versions 테이블과 로컬 파일저장소를 다루는 저장소 계층."""

    def __init__(self, db: sqlite3.Connection, files_dir: Path | None = None):
        self.db = db
        self.files_dir = Path(files_dir) if files_dir is not None else config.FILES_DIR

    # ------------------------------------------------------------------
    # 내부 헬퍼
    # ------------------------------------------------------------------

    def _doc_dir(self, document_id: str) -> Path:
        return self.files_dir / document_id

    def _get_doc(self, user_id: int, document_id: str) -> sqlite3.Row:
        """소유 문서 행을 반환한다. 없거나 소유자가 아니면 KeyError(존재 여부 비노출)."""
        row = self.db.execute(
            "SELECT * FROM documents WHERE id = ? AND user_id = ?",
            (document_id, user_id),
        ).fetchone()
        if row is None:
            raise KeyError(f"문서를 찾을 수 없음: {document_id}")
        return row

    def _version_hwpx(self, document_id: str, version: int) -> Path:
        """버전 N의 hwpx 파일 경로 (v0 = original.hwpx)."""
        name = "original.hwpx" if version == 0 else f"v{version}.hwpx"
        return self._doc_dir(document_id) / name

    def _version_html(self, document_id: str, version: int) -> Path:
        return self._doc_dir(document_id) / f"v{version}.html"

    def _sections_with_offsets(self, hwpx_path: Path) -> list[tuple[int, list[TextNode]]]:
        """hwpx를 임시 해제해 섹션별 (전역 id offset, 노드 목록)을 반환한다.

        전역 id = 섹션 로컬 id + 이전 섹션들의 노드 수 합 (M1 순번 규칙).
        header.xml의 가이드 charPr(파란 기울임체)를 해석해 각 노드의
        guide_text도 채운다 (B1 — 실양식 작성 가이드 인지).
        """
        if not hwpx_path.is_file():
            raise KeyError(f"hwpx 파일이 없음: {hwpx_path.name}")
        out: list[tuple[int, list[TextNode]]] = []
        with tempfile.TemporaryDirectory(prefix="docstore_", ignore_cleanup_errors=True) as tmp:
            extract_dir = Path(tmp) / "hwpx"
            extract_hwpx(hwpx_path, extract_dir)
            header = find_header_file(extract_dir)
            guide_ids = guide_char_pr_ids(header) if header else set()
            offset = 0
            for sf in find_section_files(extract_dir):
                nodes, _tree, _parent_map, _t_ns = parse_section(sf, guide_char_ids=guide_ids)
                out.append((offset, nodes))
                offset += len(nodes)
        return out

    # ------------------------------------------------------------------
    # 공개 API (T4가 소비하는 고정 계약)
    # ------------------------------------------------------------------

    def create_document(self, user_id: int, filename: str, data: bytes) -> dict:
        """hwpx 바이트를 저장·검증·HTML 캐시하고 documents/v0 행을 만든다.

        유효하지 않은 hwpx면 파일을 남기지 않고 ValueError.
        """
        document_id = uuid.uuid4().hex
        doc_dir = self._doc_dir(document_id)
        doc_dir.mkdir(parents=True, exist_ok=True)
        hwpx_path = doc_dir / "original.hwpx"
        try:
            hwpx_path.write_bytes(data)
            validation = validate_hwpx(hwpx_path)
            if not validation.ok:
                raise ValueError("유효하지 않은 hwpx: " + "; ".join(validation.errors))
            result = hwpx_to_html(hwpx_path)
        except ValueError:
            shutil.rmtree(doc_dir, ignore_errors=True)
            raise
        except Exception as e:  # zip은 통과했지만 변환 불가 등 — 업로드 오류로 취급
            shutil.rmtree(doc_dir, ignore_errors=True)
            raise ValueError(f"hwpx 처리 실패: {e}") from e

        html_path = self._version_html(document_id, 0)
        html_path.write_text(result.html, encoding="utf-8")

        title = Path(filename).stem or "문서"
        now = now_iso()
        self.db.execute(
            "INSERT INTO documents(id, user_id, title, source_type, original_path,"
            " current_version, page_count, created_at, updated_at)"
            " VALUES(?,?,?,?,?,0,?,?,?)",
            (document_id, user_id, title, "upload", str(hwpx_path), result.page_count, now, now),
        )
        self.db.execute(
            "INSERT INTO document_versions(document_id, version, html_path, edit_summary, created_at)"
            " VALUES(?,0,?,?,?)",
            (document_id, str(html_path), "업로드 원본", now),
        )
        self.db.commit()
        return {"document_id": document_id, "title": title, "pages": result.page_count, "version": 0}

    def get_preview(self, user_id: int, document_id: str, version: int | None = None) -> dict:
        """버전의 미리보기 HTML을 반환한다(캐시 우선, 없으면 재생성·재캐시)."""
        doc = self._get_doc(user_id, document_id)
        if version is None:
            version = doc["current_version"]
        row = self.db.execute(
            "SELECT html_path FROM document_versions WHERE document_id = ? AND version = ?",
            (document_id, version),
        ).fetchone()
        if row is None:
            raise KeyError(f"버전이 없음: {document_id} v{version}")

        page_count = doc["page_count"]
        cache = Path(row["html_path"]) if row["html_path"] else self._version_html(document_id, version)
        if cache.is_file():
            html = cache.read_text(encoding="utf-8")
        else:
            hwpx_path = self._version_hwpx(document_id, version)
            if not hwpx_path.is_file():
                raise KeyError(f"버전 파일이 없음: {document_id} v{version}")
            result = hwpx_to_html(hwpx_path)
            html = result.html
            page_count = result.page_count
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(html, encoding="utf-8")
        return {"html": html, "page_count": page_count, "version": version}

    def get_nodes(self, user_id: int, document_id: str) -> list[dict]:
        """현재 버전의 편집 대상 노드 목록 (전역 id, LLM 컨텍스트·직접 편집용).

        표 셀에는 table_idx/row/col/row_span/col_span을 포함해(B2) 프롬프트가
        표를 그리드로 직렬화할 수 있게 한다.
        """
        doc = self._get_doc(user_id, document_id)
        src = self._version_hwpx(document_id, doc["current_version"])
        result: list[dict] = []
        for offset, nodes in self._sections_with_offsets(src):
            for n in nodes:
                item = {
                    "id": offset + n.id,
                    "text": n.text,
                    "type": _NODE_TYPE_MAP.get(n.type, n.type),
                }
                if n.type == "table_cell":
                    item.update({
                        "table_idx": n.table_idx,
                        "row": n.row,
                        "col": n.col,
                        "row_span": n.cell_row_span,
                        "col_span": n.cell_col_span,
                    })
                result.append(item)
        return result

    # get_placeholders의 kind=guide token 길이 상한 (가이드 문구가 긴 경우 요약)
    _GUIDE_TOKEN_MAX = 120

    def get_placeholders(self, user_id: int, document_id: str) -> list[dict]:
        """현재 버전에 남아 있는 채움 대상 표식 목록 (미채움 검증 겸용).

        - kind="pattern": regex placeholder ([기관명], YYYY 등)
        - kind="guide"  : 파란 기울임체 작성 가이드 문구 (B1). 문구 자체가
          작성 지시이므로 token으로 그대로 노출한다. 채움 후 재호출하면
          가이드 잔존 검사("가이드 삭제 후 제출" 요건)를 겸한다.
        """
        doc = self._get_doc(user_id, document_id)
        src = self._version_hwpx(document_id, doc["current_version"])
        out: list[dict] = []
        for offset, nodes in self._sections_with_offsets(src):
            for h in collect_placeholders(nodes, id_offset=offset):
                out.append({"id": h.id, "token": h.text, "kind": "pattern"})
            for n in nodes:
                if n.guide_text:
                    token = n.guide_text[:self._GUIDE_TOKEN_MAX]
                    out.append({"id": offset + n.id, "token": token, "kind": "guide"})
        return out

    def get_current_version(self, user_id: int, document_id: str) -> int:
        """문서의 현재 버전 번호 (버전 핀 사전 검사용)."""
        return self._get_doc(user_id, document_id)["current_version"]

    def apply_document_edits(
        self,
        user_id: int,
        document_id: str,
        edits: list[dict],
        summary: str | None = None,
        message_id: int | None = None,
        expected_version: int | None = None,
    ) -> dict:
        """edits를 현재 버전에 적용해 새 버전(hwpx+HTML 캐시+DB 행)을 만든다.

        expected_version(버전 핀)이 주어지면 적용 직전 현재 버전과 비교해
        다르면 VersionConflictError를 던진다 (edits의 id가 그 버전의 노드
        순번을 기준으로 만들어졌기 때문).
        """
        doc = self._get_doc(user_id, document_id)
        current = doc["current_version"]
        if expected_version is not None and expected_version != current:
            raise VersionConflictError(expected_version, current)
        src = self._version_hwpx(document_id, current)
        if not src.is_file():
            raise KeyError(f"버전 파일이 없음: {document_id} v{current}")

        new_version = current + 1
        out = self._version_hwpx(document_id, new_version)
        edit_result = apply_edits(src, edits, out)

        html_result = hwpx_to_html(out)
        html_path = self._version_html(document_id, new_version)
        html_path.write_text(html_result.html, encoding="utf-8")

        # 섹션 XML 스냅샷 디렉터리 (apply_edits가 "v{N}_sections/"로 남김. 무변경이면 None)
        xml_path = (
            str(Path(edit_result.section_snapshots[0]).parent)
            if edit_result.section_snapshots else None
        )
        now = now_iso()
        self.db.execute(
            "INSERT INTO document_versions(document_id, version, xml_path, html_path,"
            " edit_summary, edits_json, message_id, created_at) VALUES(?,?,?,?,?,?,?,?)",
            (document_id, new_version, xml_path, str(html_path), summary,
             json.dumps(edits, ensure_ascii=False), message_id, now),
        )
        self.db.execute(
            "UPDATE documents SET current_version = ?, page_count = ?, updated_at = ? WHERE id = ?",
            (new_version, html_result.page_count, now, document_id),
        )
        self.db.commit()
        return {
            "version": new_version,
            "html": html_result.html,
            "applied_ids": edit_result.applied_ids,
            "skipped_ids": edit_result.skipped_ids,
        }

    def list_versions(self, user_id: int, document_id: str) -> list[dict]:
        """버전 이력 (DESIGN §6 GET /documents/{id}/versions)."""
        self._get_doc(user_id, document_id)
        rows = self.db.execute(
            "SELECT version, edit_summary AS summary, created_at FROM document_versions"
            " WHERE document_id = ? ORDER BY version",
            (document_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def export_file(
        self, user_id: int, document_id: str, fmt: str = "hwpx", version: int | None = None,
    ) -> tuple[Path, str]:
        """버전 파일을 내보낸다. 반환: (디스크 경로, 다운로드 파일명)."""
        if fmt not in _EXPORT_FORMATS:
            raise ValueError(f"지원하지 않는 형식: {fmt} (지원: {', '.join(_EXPORT_FORMATS)})")
        doc = self._get_doc(user_id, document_id)
        if version is None:
            version = doc["current_version"]
        src = self._version_hwpx(document_id, version)
        if not src.is_file():
            raise KeyError(f"버전 파일이 없음: {document_id} v{version}")

        title = doc["title"] or "document"
        suffix = "" if version == doc["current_version"] else f"_v{version}"
        if fmt == "hwpx":
            return src, f"{title}{suffix}.hwpx"

        # docx: M2-1 단순 재구성 (텍스트·표 격자만 보존)
        from app.core.docx.exporter import hwpx_to_docx

        out = self._doc_dir(document_id) / f"v{version}.docx"
        hwpx_to_docx(src, out)
        return out, f"{title}{suffix}.docx"
