from pathlib import Path

from jwxtapi.parsers import (
    parse_classroom_entries,
    parse_grade_detail,
    parse_grades,
    parse_schedule,
    parse_training_plan,
)


SOURCE = Path(__file__).resolve().parents[1] / "实际请求返回数据.md"


def _response_block(header: str) -> str:
    text = SOURCE.read_text(encoding="utf-8")
    marker = f"### {header}"
    start = text.index(marker)
    fence = text.index("```", start) + 3
    end = text.index("```", fence)
    return text[fence:end]


def test_all_real_response_samples_are_parseable() -> None:
    schedule = parse_schedule(_response_block("/jsxsd/xskb/xskb_list.do"), "2026-2027-1", 1)
    classrooms = parse_classroom_entries(_response_block("/jsxsd/kbcx/kbxx_classroom_ifr"))
    grades = parse_grades(_response_block("/jsxsd/kscj/cjcx_list"))
    detail = parse_grade_detail(_response_block("/jsxsd/kscj/pscj_list.do"))
    plan = parse_training_plan(_response_block("/jsxsd/pyfa/pyfa_query"))

    assert len(schedule.items) == 12
    assert schedule.items[0].teacher == "叶东东"
    assert len(classrooms) == 13
    assert classrooms[0].classroom == "清远北区7-101（创院）"
    assert len(grades.items) == 48
    assert grades.required_credits == "225"
    assert grades.items[0].teaching_task_id == "202420251008816"
    assert detail.total_score == "76"
    assert len(plan.items) == 80
