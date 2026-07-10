"""LLM 배치 처리용 노드 청크 분할 (M1-7).

한 번의 LLM 호출에 넣을 노드 수를 제한하되, 표는 문맥이 잘리면 채움
품질이 급락하므로 **같은 table_idx의 셀들을 절대 다른 청크로 나누지
않는다**. 표 하나가 max_nodes보다 크면 그 표만으로 한 청크가 된다.
헤딩-표 병합 같은 휴리스틱은 이 모듈 범위 밖이다.
"""
from .models import TextNode


def _atomic_groups(nodes: list[TextNode]) -> list[list[TextNode]]:
    """노드를 분할 불가 단위로 묶는다: 본문 문단 1개 또는 한 표의 셀 전체.

    parse_section 순번 규칙상 같은 표(중첩 표 포함)의 셀들은 같은
    table_idx로 연속 배치되므로, 연속 구간만 묶으면 표 전체가 한 그룹이 된다.
    """
    groups: list[list[TextNode]] = []
    for node in nodes:
        is_cell = node.type == "table_cell" and node.table_idx >= 0
        if (
            is_cell
            and groups
            and groups[-1][-1].type == "table_cell"
            and groups[-1][-1].table_idx == node.table_idx
        ):
            groups[-1].append(node)
        else:
            groups.append([node])
    return groups


def chunk_nodes(nodes: list[TextNode], max_nodes: int = 30) -> list[list[TextNode]]:
    """노드 목록을 max_nodes 이하의 청크들로 나눈다(표는 원자 단위).

    - 그룹(본문 1개 | 표 셀 전체)을 순서대로 채우다가, 추가하면
      max_nodes를 넘는 시점에 현재 청크를 닫는다.
    - 단일 표 그룹이 max_nodes보다 크면 그 표만으로 한 청크가 된다.
    """
    if max_nodes <= 0:
        raise ValueError(f"max_nodes는 1 이상이어야 함: {max_nodes}")
    if not nodes:
        return []

    chunks: list[list[TextNode]] = []
    current: list[TextNode] = []
    for group in _atomic_groups(nodes):
        if current and len(current) + len(group) > max_nodes:
            chunks.append(current)
            current = []
        current.extend(group)
        if len(current) >= max_nodes:
            chunks.append(current)
            current = []
    if current:
        chunks.append(current)
    return chunks
