"""package.py 테스트: extract→repack 왕복 보존, validate_hwpx 합격/불합격."""
import zipfile
from pathlib import Path

from app.core.hwpx import extract_hwpx, find_section_files, repack_hwpx, validate_hwpx


def _zip_entries(path: Path) -> list[zipfile.ZipInfo]:
    with zipfile.ZipFile(path) as zf:
        return zf.infolist()


class TestExtractRepackRoundtrip:
    def test_roundtrip_preserves_entries_and_order(self, report_table_hwpx, tmp_path):
        """해제→재압축 후 엔트리 목록·순서·압축 방식이 원본과 동일해야 한다."""
        extract_dir = tmp_path / "x"
        out = tmp_path / "out.hwpx"
        compress_info, file_order = extract_hwpx(report_table_hwpx, extract_dir)
        repack_hwpx(extract_dir, out, compress_info, file_order)

        orig = _zip_entries(report_table_hwpx)
        new = _zip_entries(out)
        assert [i.filename for i in new] == [i.filename for i in orig]
        assert [i.compress_type for i in new] == [i.compress_type for i in orig]
        # 내용도 바이트 단위로 동일 (편집 없는 왕복)
        with zipfile.ZipFile(report_table_hwpx) as z1, zipfile.ZipFile(out) as z2:
            for name in z1.namelist():
                assert z1.read(name) == z2.read(name), name

    def test_mimetype_first_and_stored(self, demo_form_hwpx, tmp_path):
        """재압축 결과에서 mimetype이 첫 엔트리이자 무압축(STORED)이어야 한다."""
        extract_dir = tmp_path / "x"
        out = tmp_path / "out.hwpx"
        compress_info, file_order = extract_hwpx(demo_form_hwpx, extract_dir)
        repack_hwpx(extract_dir, out, compress_info, file_order)

        entries = _zip_entries(out)
        assert entries[0].filename == "mimetype"
        assert entries[0].compress_type == zipfile.ZIP_STORED

    def test_repack_without_original_info(self, report_table_hwpx, tmp_path):
        """compress_info/file_order 없이 재압축해도 mimetype 규칙과 검증을 만족해야 한다."""
        extract_dir = tmp_path / "x"
        out = tmp_path / "out.hwpx"
        extract_hwpx(report_table_hwpx, extract_dir)
        repack_hwpx(extract_dir, out)  # 메타 정보 미전달

        entries = _zip_entries(out)
        assert entries[0].filename == "mimetype"
        assert entries[0].compress_type == zipfile.ZIP_STORED
        assert validate_hwpx(out).ok

    def test_roundtrip_passes_validation(self, skeleton_hwpx, tmp_path):
        extract_dir = tmp_path / "x"
        out = tmp_path / "out.hwpx"
        compress_info, file_order = extract_hwpx(skeleton_hwpx, extract_dir)
        repack_hwpx(extract_dir, out, compress_info, file_order)
        result = validate_hwpx(out)
        assert result.ok, result.errors


class TestValidateHwpx:
    def test_valid_files_pass(self, skeleton_hwpx, report_table_hwpx, demo_form_hwpx):
        """실측 대상 3종(골격·표 보고서·builder 생성물) 모두 구조 검증 통과."""
        for path in (skeleton_hwpx, report_table_hwpx, demo_form_hwpx):
            result = validate_hwpx(path)
            assert result.ok, (path.name, result.errors)
            # 필수 검사 항목이 실제로 수행됐는지
            assert "mimetype" in result.checked
            assert "Contents/section*.xml" in result.checked
            assert any(c.startswith("xml_parse:") for c in result.checked)

    def test_rejects_empty_zip(self, tmp_path):
        """엔트리가 하나도 없는 zip은 필수 엔트리 누락으로 불합격."""
        bad = tmp_path / "empty.hwpx"
        with zipfile.ZipFile(bad, "w"):
            pass
        result = validate_hwpx(bad)
        assert not result.ok
        assert result.errors  # mimetype·section·header 누락 에러들

    def test_rejects_non_zip_file(self, tmp_path):
        """zip이 아닌 파일은 열기 단계에서 불합격."""
        bad = tmp_path / "not_a_zip.hwpx"
        bad.write_text("this is not a zip archive", encoding="utf-8")
        result = validate_hwpx(bad)
        assert not result.ok
        assert any("zip" in e for e in result.errors)

    def test_rejects_zip_missing_sections(self, tmp_path):
        """mimetype만 있고 Contents가 없는 zip은 불합격."""
        bad = tmp_path / "no_sections.hwpx"
        with zipfile.ZipFile(bad, "w") as zf:
            zf.writestr("mimetype", "application/hwp+zip")
        result = validate_hwpx(bad)
        assert not result.ok
        assert any("section" in e for e in result.errors)


class TestFindSectionFiles:
    def test_numeric_sort_order(self, tmp_path):
        """section10이 section2보다 뒤에 오는 숫자 정렬이어야 한다(사전순 금지)."""
        contents = tmp_path / "Contents"
        contents.mkdir()
        for name in ("section10.xml", "section0.xml", "section2.xml", "header.xml"):
            (contents / name).write_text("<x/>", encoding="utf-8")
        names = [p.name for p in find_section_files(tmp_path)]
        assert names == ["section0.xml", "section2.xml", "section10.xml"]

    def test_missing_contents_dir(self, tmp_path):
        assert find_section_files(tmp_path) == []
