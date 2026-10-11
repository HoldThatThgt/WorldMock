"""@brief 真实 JSONL 行读取的字节边界、严格模型与定位行为。"""

from pathlib import Path

import pytest
from pydantic import Field, StrictInt

from worldmock.base_models import DeepFrozenModel
from worldmock.catalog.errors import RegistrationError
from worldmock.catalog.loader import parse_catalog_lines, read_catalog_rows
from worldmock.catalog.models import MapEdge


class CatalogRow(DeepFrozenModel):
    """@brief 通过生产严格基类检验 JSONL 读取，不假定未决目录字段。"""

    content_id: str = Field(strict=True)
    duration_seconds: StrictInt = Field(ge=60)


VALID_ROW = b'{"content_id":"sample","duration_seconds":60}'


@pytest.mark.parametrize("content", [VALID_ROW, VALID_ROW + b"\n"])
def test_jsonl_accepts_optional_final_lf(content):
    """@brief 最后一行是否带 LF 不改变对象，实际字节不被改写。"""
    rows = parse_catalog_lines(content, "content-catalog.jsonl", CatalogRow)
    assert rows == (CatalogRow(content_id="sample", duration_seconds=60),)


def test_jsonl_zero_bytes_means_zero_rows():
    """@brief 空回调文件不产生伪造的空白行。"""
    assert parse_catalog_lines(b"", "callbacks.jsonl", CatalogRow) == ()


def test_jsonl_preserves_row_order_and_accepts_key_order_and_spaces():
    """@brief JSON 键序与行内空白不参与对象语法限制，文件行序保持原样。"""
    content = VALID_ROW + b'\n { "duration_seconds": 120, "content_id": "second" } \n'
    rows = parse_catalog_lines(content, "content-catalog.jsonl", CatalogRow)
    assert [(row.content_id, row.duration_seconds) for row in rows] == [("sample", 60), ("second", 120)]


@pytest.mark.parametrize("line", [
    b"", b" ", b"\t", b"null", b"true", b"1", b'"value"', b"[]", b"{} {}",
    b"{", b'[{"content_id":"sample","duration_seconds":60}]', b"\xff",
    b"\xef\xbb\xbf" + VALID_ROW, b'{"content_id":"sample","duration_seconds":NaN}',
    b'{"content_id":"sample","duration_seconds":Infinity}',
    b'{"content_id":"sample","duration_seconds":-Infinity}',
])
def test_jsonl_rejects_non_object_rows_with_exact_location(line, caplog):
    """@brief 所有行解析错误均给出行号和统一登记码，并记录英文故障。"""
    content = VALID_ROW + b"\n" + line + b"\n"
    with pytest.raises(RegistrationError) as captured:
        parse_catalog_lines(content, "content-catalog.jsonl", CatalogRow)
    assert captured.value.code == "ENVIRONMENT_LINE_INVALID"
    assert captured.value.detail.startswith("content-catalog.jsonl:2")
    assert "ENVIRONMENT_LINE_INVALID" in caplog.text


def test_jsonl_rejects_multiline_object_on_its_starting_line():
    """@brief 跨行对象不能借逐行读取拼成有效对象。"""
    with pytest.raises(RegistrationError) as captured:
        parse_catalog_lines(b'{\n"content_id":"sample","duration_seconds":60}\n',
                            "content-catalog.jsonl", CatalogRow)
    assert captured.value.code == "ENVIRONMENT_LINE_INVALID"
    assert captured.value.detail.startswith("content-catalog.jsonl:1")


@pytest.mark.parametrize("content,line_number", [
    (VALID_ROW + b"\r\n", 1), (VALID_ROW + b"\n" + VALID_ROW + b"\r", 2),
    (b'{"content_id":"sam\rple","duration_seconds":60}', 1),
])
def test_jsonl_rejects_every_carriage_return(content, line_number):
    """@brief 任意 CR 使用种类错误，详情同时保留路径、行号与固定英文原因。"""
    with pytest.raises(RegistrationError) as captured:
        parse_catalog_lines(content, "content-catalog.jsonl", CatalogRow)
    assert captured.value.code == "ENVIRONMENT_KIND_INVALID"
    assert captured.value.detail == (
        f"content-catalog.jsonl:{line_number} Catalog JSON must use LF line endings."
    )


@pytest.mark.parametrize("line,code", [
    (b'{"content_id":"sample","duration_seconds":true}', "ENVIRONMENT_NUMBER_INVALID"),
    (b'{"content_id":"sample","duration_seconds":59}', "ENVIRONMENT_NUMBER_INVALID"),
    (b'{"content_id":"sample","duration_seconds":"60"}', "ENVIRONMENT_NUMBER_INVALID"),
    (b'{"content_id":"sample"}', "ENVIRONMENT_NUMBER_INVALID"),
    (b'{"content_id":1,"duration_seconds":60}', "ENVIRONMENT_KIND_INVALID"),
    (b'{"content_id":"sample","duration_seconds":59,"retired":true}', "ENVIRONMENT_KIND_INVALID"),
    (b'{"contents":[]}', "ENVIRONMENT_KIND_INVALID"),
])
def test_jsonl_preserves_field_error_priority_and_line_location(line, code):
    """@brief 行模型使用现有严格校验码，多余字段优先于数值错误且不接受旧根包装。"""
    with pytest.raises(RegistrationError) as captured:
        parse_catalog_lines(VALID_ROW + b"\n" + line, "content-catalog.jsonl", CatalogRow)
    assert captured.value.code == code
    assert captured.value.detail.startswith("content-catalog.jsonl:2")


def test_jsonl_read_uses_real_safe_file_boundary(tmp_path):
    """@brief 文件读取真实消费目录字节，不改写输入，且拒绝越界路径。"""
    directory = tmp_path / "catalog"
    directory.mkdir()
    source = directory / "content-catalog.jsonl"
    source.write_bytes(VALID_ROW + b"\n")
    assert read_catalog_rows(directory, source.name, CatalogRow)[0].content_id == "sample"
    assert source.read_bytes() == VALID_ROW + b"\n"
    outside = tmp_path / "outside.jsonl"
    outside.write_bytes(VALID_ROW)
    with pytest.raises(RegistrationError, match="ENVIRONMENT_IDENTITY_MISMATCH"):
        read_catalog_rows(directory, "../outside.jsonl", CatalogRow)


def test_jsonl_read_rejects_symbolic_file(tmp_path, monkeypatch):
    """@brief Windows 无需链接权限即可核对生产安全边界的符号文件拒绝。"""
    source = tmp_path / "content-catalog.jsonl"
    source.write_bytes(VALID_ROW)
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == source or original(path))
    with pytest.raises(RegistrationError, match="ENVIRONMENT_IDENTITY_MISMATCH"):
        read_catalog_rows(tmp_path, source.name, CatalogRow)


def test_jsonl_read_rejects_missing_file(tmp_path):
    """@brief 缺失目录文件使用文件边界错误。"""
    with pytest.raises(RegistrationError, match="ENVIRONMENT_IDENTITY_MISMATCH"):
        read_catalog_rows(tmp_path, "content-catalog.jsonl", CatalogRow)


def test_map_rows_parse_into_the_strict_edge_model_in_file_order():
    """@brief 声明边只有三个字段，键序与行内空白不影响解析出的值，零字节就是零行。"""
    content = b'{"a":"cafe","b":"office","length_m":4500}\n { "length_m": 12600, "b": "office", "a": "home" } \n'
    rows = parse_catalog_lines(content, "map.jsonl", MapEdge)
    assert [(row.a, row.b, row.length_m) for row in rows] == [("cafe", "office", 4500), ("home", "office", 12600)]
    assert parse_catalog_lines(b"", "map.jsonl", MapEdge) == ()


@pytest.mark.parametrize("line,code", [
    (b'{"a":"home","b":"office","length_m":5,"note":"x"}', "ENVIRONMENT_KIND_INVALID"),
    (b'{"a":"home","b":"office","length_m":0,"note":"x"}', "ENVIRONMENT_KIND_INVALID"),
    (b'{"a":1,"b":"office","length_m":5}', "ENVIRONMENT_KIND_INVALID"),
    (b'{"a":"home","b":" ","length_m":5}', "ENVIRONMENT_KIND_INVALID"),
    (b'{"a":"home","length_m":5}', "ENVIRONMENT_KIND_INVALID"),
    (b'{"a":"home","b":"office","length_m":0}', "ENVIRONMENT_NUMBER_INVALID"),
    (b'{"a":"home","b":"office","length_m":-3}', "ENVIRONMENT_NUMBER_INVALID"),
    (b'{"a":"home","b":"office","length_m":true}', "ENVIRONMENT_NUMBER_INVALID"),
    (b'{"a":"home","b":"office","length_m":1.5}', "ENVIRONMENT_NUMBER_INVALID"),
    (b'{"a":"home","b":"office","length_m":12600.0}', "ENVIRONMENT_NUMBER_INVALID"),
    (b'{"a":"home","b":"office","length_m":"5"}', "ENVIRONMENT_NUMBER_INVALID"),
    (b'{"a":"home","b":"office","length_m":null}', "ENVIRONMENT_NUMBER_INVALID"),
    (b'{"a":"home","b":"office"}', "ENVIRONMENT_NUMBER_INVALID"),
    (b'["home","office",5]', "ENVIRONMENT_LINE_INVALID"),
    (b'{"a":"home","b":"office","length_m":5} {}', "ENVIRONMENT_LINE_INVALID"),
    (b"", "ENVIRONMENT_LINE_INVALID"),
])
def test_map_row_errors_carry_the_fixed_code_file_and_line(line, code):
    """@brief 未知键、坏端点和坏长度各用规定的登记码，详情以 map.jsonl 和行号开头。"""
    content = b'{"a":"cafe","b":"home","length_m":900}\n' + line + b"\n"
    with pytest.raises(RegistrationError) as captured:
        parse_catalog_lines(content, "map.jsonl", MapEdge)
    assert captured.value.code == code
    assert captured.value.detail.startswith("map.jsonl:2")


def test_map_row_accepts_one_metre_and_distances_beyond_the_random_edge_range():
    """@brief 声明边的下限是 1 米，不设 2000 米上限。"""
    content = b'{"a":"a","b":"b","length_m":1}\n{"a":"a","b":"c","length_m":62208}\n'
    assert [row.length_m for row in parse_catalog_lines(content, "map.jsonl", MapEdge)] == [1, 62208]
