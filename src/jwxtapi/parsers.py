from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup, Tag

from .exceptions import ParseError
from .models import (
    ClassroomEntry,
    Grade,
    GradeDetail,
    GradeReport,
    Schedule,
    ScheduleEntry,
    TrainingPlan,
    TrainingPlanCourse,
)


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()


def _cell_text(cell: Tag) -> str:
    return _clean(cell.get_text(" ", strip=True))


def parse_weeks(value: str) -> tuple[int, ...]:
    text = value.replace("（", "(").replace("）", ")")
    text = re.sub(r"[第周()\s]", "", text)
    parity: int | None = None
    if "单" in text:
        parity = 1
    elif "双" in text:
        parity = 0
    text = text.replace("单", "").replace("双", "")
    weeks: set[int] = set()
    for part in re.split(r"[,，、;；]", text):
        if not part:
            continue
        match = re.fullmatch(r"(\d+)(?:[-—~至](\d+))?", part)
        if not match:
            continue
        start = int(match.group(1))
        end = int(match.group(2) or start)
        if start > end:
            continue
        for week in range(start, end + 1):
            if parity is None or week % 2 == parity:
                weeks.add(week)
    return tuple(sorted(weeks))


def _direct_text_before_font(container: Tag) -> str:
    parts: list[str] = []
    for child in container.children:
        if isinstance(child, Tag) and child.name == "font":
            break
        if isinstance(child, Tag):
            text = child.get_text(" ", strip=True)
        else:
            text = str(child)
        cleaned = _clean(text)
        if cleaned:
            parts.append(cleaned)
    return " ".join(parts)


def parse_schedule(html: str, requested_term: str, requested_week: int | None) -> Schedule:
    soup = _soup(html)
    table = soup.select_one("#kbtable")
    if table is None:
        raise ParseError("个人课表响应中缺少 #kbtable")
    term_select = soup.select_one("#xnxq01id")
    selected_term = requested_term
    if term_select:
        selected = term_select.select_one("option[selected]")
        if selected and selected.get("value"):
            selected_term = str(selected["value"])

    entries: list[ScheduleEntry] = []
    period = 0
    remarks: str | None = None
    for row in table.select("tr")[1:]:
        heading = row.find("th")
        cells = row.find_all("td", recursive=False)
        if heading and "备注" in _cell_text(heading):
            remarks = _cell_text(cells[0]) if cells else None
            continue
        if heading is None or len(cells) < 7:
            continue
        period += 1
        period_name = _cell_text(heading)
        for weekday, cell in enumerate(cells[:7], start=1):
            details = cell.select("div.kbcontent") or cell.select("div.kbcontent1")
            for detail in details:
                raw = _clean(detail.get_text(" ", strip=True))
                if not raw:
                    continue
                course_name = _direct_text_before_font(detail)
                fields = {
                    str(font.get("title")): _clean(font.get_text(" ", strip=True))
                    for font in detail.find_all("font")
                }
                if not course_name:
                    lines = [_clean(part) for part in detail.get_text("\n", strip=True).splitlines()]
                    course_name = next((part for part in lines if part), "")
                if not course_name:
                    continue
                weeks_text = fields.get("周次(节次)", "")
                entries.append(
                    ScheduleEntry(
                        course_name=course_name,
                        teacher=fields.get("老师"),
                        classroom=fields.get("教室"),
                        weeks_text=weeks_text,
                        weeks=parse_weeks(weeks_text),
                        weekday=weekday,
                        period=period,
                        period_name=period_name,
                    )
                )
    return Schedule(selected_term, requested_week, tuple(entries), remarks)


def parse_classroom_entries(html: str) -> tuple[ClassroomEntry, ...]:
    soup = _soup(html)
    table = soup.select_one("#kbtable")
    if table is None:
        raise ParseError("教室课表响应中缺少 #kbtable")
    rows = table.select("tr")
    if len(rows) < 2:
        raise ParseError("教室课表缺少星期或节次表头")
    period_cells = rows[1].find_all(["td", "th"], recursive=False)[1:]
    all_periods = [_cell_text(cell) for cell in period_cells]
    if not all_periods or len(all_periods) % 7 != 0:
        raise ParseError("教室课表节次列数量无效")
    periods_per_day = len(all_periods) // 7
    period_names = all_periods[:periods_per_day]
    entries: list[ClassroomEntry] = []
    for row in rows[2:]:
        cells = row.find_all("td", recursive=False)
        if not cells:
            continue
        classroom = _cell_text(cells[0])
        slot = 0
        for cell in cells[1:]:
            colspan = int(str(cell.get("colspan", "1")))
            weekday = slot // periods_per_day + 1
            period = period_names[slot % periods_per_day]
            for content in cell.select("div.kbcontent1, div.kbcontent"):
                lines = [
                    _clean(line)
                    for line in content.get_text("\n", strip=True).splitlines()
                    if _clean(line)
                ]
                if not lines:
                    continue
                weeks_index = next((index for index, line in enumerate(lines) if "周" in line), -1)
                weeks_text = lines[weeks_index] if weeks_index >= 0 else None
                teacher = lines[1] if len(lines) > 1 and weeks_index != 1 else None
                class_name = " ".join(lines[weeks_index + 1 :]) or None if weeks_index >= 0 else None
                raw_text = "\n".join(lines)
                entries.append(
                    ClassroomEntry(
                        classroom=classroom,
                        weekday=weekday,
                        period=period,
                        course_name=lines[0],
                        teacher=teacher,
                        weeks_text=weeks_text,
                        weeks=parse_weeks(weeks_text or ""),
                        class_name=class_name,
                        raw_text=raw_text,
                    )
                )
            slot += colspan
    return tuple(entries)


def _data_rows(html: str, expected_headers: Iterable[str]) -> tuple[BeautifulSoup, list[Tag]]:
    soup = _soup(html)
    table = soup.select_one("#dataList")
    if table is None:
        raise ParseError("响应中缺少 #dataList")
    headers = [_cell_text(header) for header in table.find_all("th")]
    missing = [header for header in expected_headers if header not in headers]
    if missing:
        raise ParseError(f"数据表缺少列：{', '.join(missing)}")
    return soup, [row for row in table.find_all("tr") if row.find_all("td", recursive=False)]


def parse_grades(html: str) -> GradeReport:
    soup, rows = _data_rows(html, ["开课学期", "课程编号", "课程名称", "成绩", "学分"])
    items: list[Grade] = []
    for row in rows:
        cells = row.find_all("td", recursive=False)
        if len(cells) < 11:
            raise ParseError("成绩表数据行列数不足 11")
        values = [_cell_text(cell) for cell in cells]
        link = cells[4].find("a", href=True)
        params: dict[str, list[str]] = {}
        if link:
            href = str(link["href"])
            url_match = re.search(r"['\"]([^'\"]*pscj_list\.do\?[^'\"]+)", href)
            if url_match:
                params = parse_qs(urlparse(url_match.group(1)).query, keep_blank_values=True)
        try:
            index = int(values[0])
        except ValueError as exc:
            raise ParseError(f"成绩序号不是整数：{values[0]}") from exc
        items.append(
            Grade(
                index=index,
                term=values[1],
                course_code=values[2],
                course_name=values[3],
                score=values[4],
                credit=values[5],
                total_hours=values[6],
                grade_point=values[7],
                assessment_method=values[8],
                course_attribute=values[9],
                course_nature=values[10],
                student_id=params.get("xs0101id", [None])[0],
                teaching_task_id=params.get("jx0404id", [None])[0],
                detail_total_score=params.get("zcj", [None])[0],
            )
        )
    page_text = _clean(soup.get_text(" ", strip=True))
    summary = re.search(
        r"一共需要修读\s*([^\s，,]+)\s*学分.*?已修读\s*([^\s，,]+)\s*学分.*?"
        r"还需修读\s*([^\s，,]+)\s*学分.*?主修课程平均学分绩点\s*([^\s，,]+).*?"
        r"辅修课程平均学分绩点\s*([^\s，,]+)",
        page_text,
    )
    if summary:
        summary_values: tuple[str | None, str | None, str | None, str | None, str | None] = (
            summary.group(1),
            summary.group(2),
            summary.group(3),
            summary.group(4),
            summary.group(5),
        )
    else:
        summary_values = (None, None, None, None, None)
    return GradeReport(
        required_credits=summary_values[0],
        earned_credits=summary_values[1],
        remaining_credits=summary_values[2],
        major_gpa=summary_values[3],
        minor_gpa=summary_values[4],
        items=tuple(items),
    )


def parse_grade_detail(html: str) -> GradeDetail:
    _, rows = _data_rows(
        html,
        ["期末成绩", "期末成绩比例", "期中成绩", "期中成绩比例", "平时成绩", "平时成绩比例", "总成绩"],
    )
    if not rows:
        raise ParseError("单科成绩明细没有数据行")
    values = [_cell_text(cell) for cell in rows[0].find_all("td", recursive=False)]
    if len(values) < 8:
        raise ParseError("单科成绩明细数据行列数不足 8")
    return GradeDetail(*values[1:8])


def parse_training_plan(html: str) -> TrainingPlan:
    _, rows = _data_rows(html, ["开课学期", "课程编号", "课程名称", "开课单位", "是否考试"])
    items: list[TrainingPlanCourse] = []
    for row in rows:
        values = [_cell_text(cell) for cell in row.find_all("td", recursive=False)]
        if len(values) < 10:
            raise ParseError("培养方案数据行列数不足 10")
        try:
            index = int(values[0])
        except ValueError as exc:
            raise ParseError(f"培养方案序号不是整数：{values[0]}") from exc
        items.append(TrainingPlanCourse(index, *values[1:10]))
    return TrainingPlan(tuple(items))
