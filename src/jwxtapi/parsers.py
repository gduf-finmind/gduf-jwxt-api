from __future__ import annotations

import re
from collections.abc import Iterable
from copy import copy
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup, Tag
from bs4.element import Comment, NavigableString

from .exceptions import ParseError
from .models import (
    ClassroomEntry,
    ClassroomGrid,
    Grade,
    GradeDetail,
    GradeReport,
    Schedule,
    ScheduleEntry,
    StudentAward,
    StudentDisciplinaryRecord,
    StudentFamilyMember,
    StudentInfo,
    StudentStatusChange,
    StudentStudyWorkExperience,
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


def _is_schedule_container(node: Tag) -> bool:
    """按完整类名识别课程容器，兼容缺失、字符串和列表形式的 class 属性。

    BeautifulSoup 的属性类型包含 None；先收窄类型再比较，避免将字符串
    当作字符集合，也保证严格类型检查能验证这条解析路径。
    """
    if node.name != "div":
        return False
    classes = node.get("class")
    if classes is None:
        return False
    tokens = classes.split() if isinstance(classes, str) else classes
    return bool({"kbcontent", "kbcontent1"}.intersection(tokens))


def _schedule_fragments(cell: Tag) -> list[Tag]:
    """按容器和显式分隔线拆分课程，保留每段的字段归属。

    同一容器可包含单双周的多门课程；递归展开普通包装标签，font 作为整体
    保留，避免把老师、教室拆成课程名。嵌套课程容器只遍历一次。
    """
    soup = BeautifulSoup("", "html.parser")
    fragments: list[Tag] = []
    current = soup.new_tag("div")

    def flush() -> None:
        """结束当前非空课程片段，并为下一门课程创建独立容器。"""
        nonlocal current
        if _cell_text(current):
            fragments.append(current)
        current = soup.new_tag("div")

    def visit(node: Tag | NavigableString) -> None:
        """按文档顺序读取节点，仅在独立分隔文本或课程容器边界切段。"""
        if isinstance(node, Comment):
            return
        if isinstance(node, NavigableString):
            for part in str(node).splitlines(keepends=True):
                if re.fullmatch(r"\s*[-—－_─]{3,}\s*", part):
                    flush()
                else:
                    current.append(NavigableString(part))
            return
        if node.name == "hr":
            flush()
            return
        if node.name in {"font", "br"}:
            current.append(copy(node))
            return
        is_course = _is_schedule_container(node)
        if is_course:
            flush()
        for child in node.children:
            if isinstance(child, (Tag, NavigableString)):
                visit(child)
        if is_course:
            flush()

    # 同一逻辑编号的 -1/-2 分别为简略和详细视图；详细视图虽被 CSS 隐藏仍是完整数据。
    # get_attribute_list 始终返回字符串列表，避免 class 的可空联合类型参与成员判断。
    containers = cell.select("div.kbcontent, div.kbcontent1")
    detailed_ids = {str(node.get("id", ""))[:-2] for node in containers
                    if "kbcontent" in node.get_attribute_list("class") and str(node.get("id", "")).endswith("-2")}
    # 从最外层课程容器开始，内部容器由递归遍历处理，避免重复课程。
    for container in containers:
        identifier = str(container.get("id", ""))
        if ("kbcontent1" in container.get_attribute_list("class") and identifier.endswith("-1")
                and identifier[:-2] in detailed_ids):
            continue
        if any(
            parent is not cell and _is_schedule_container(parent)
            for parent in container.parents if isinstance(parent, Tag)
        ):
            continue
        visit(container)
    return fragments


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
    heading_occurrences: dict[str, int] = {}
    remarks: str | None = None
    for row in table.select("tr")[1:]:
        heading = row.find("th")
        cells = row.find_all("td", recursive=False)
        if heading and "备注" in _cell_text(heading):
            remarks = _cell_text(cells[0]) if cells else None
            continue
        if heading is None or len(cells) < 7:
            continue
        period_name = _cell_text(heading)
        period += 1
        heading_occurrences[period_name] = heading_occurrences.get(period_name, 0) + 1
        start_period, end_period = _schedule_period_span(
            period_name, heading_occurrences[period_name], period
        )
        for weekday, cell in enumerate(cells[:7], start=1):
            details = _schedule_fragments(cell)
            for detail in details:
                raw = _clean(detail.get_text(" ", strip=True))
                if not raw:
                    continue
                course_name = _direct_text_before_font(detail)
                fields: dict[str, str] = {}
                for font in detail.find_all("font"):
                    title = str(font.get("title"))
                    if title in {"老师", "教室", "周次(节次)"} and title in fields:
                        raise ParseError("个人课表课程字段重复，无法确定多门课程的分隔边界")
                    fields[title] = _clean(font.get_text(" ", strip=True))
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
                        period=start_period,
                        period_name=period_name,
                        period_code=f"{start_period:02d}{end_period:02d}",
                        start_period=start_period,
                        end_period=end_period,
                    )
                )
    return Schedule(selected_term, requested_week, tuple(entries), remarks)


def _schedule_period_span(name: str, occurrence: int, fallback_row: int) -> tuple[int, int]:
    """将重复的大节标题映射为真实小节范围，兼容清远校区课表结构。"""
    mapping = {
        ("第一大节", 1): (1, 2),
        ("第二大节", 1): (3, 4),
        ("第二大节", 2): (5, 5),
        ("第三大节", 1): (6, 7),
        ("第三大节", 2): (8, 8),
        ("第四大节", 1): (9, 10),
        ("第五大节", 1): (11, 12),
    }
    return mapping.get((name.replace(" ", ""), occurrence), (fallback_row, fallback_row))


def _classroom_grid_parts(html: str) -> tuple[list[str], list[ClassroomEntry]]:
    soup = _soup(html)
    table = soup.select_one("#kbtable")
    if table is None:
        raise ParseError("教室课表响应中缺少 #kbtable")
    rows = table.select("tr")
    if len(rows) < 2:
        raise ParseError("教室课表缺少星期或节次表头")

    weekday_cells = rows[0].find_all(["td", "th"], recursive=False)[1:]
    period_cells = rows[1].find_all(["td", "th"], recursive=False)[1:]
    if (
        len(weekday_cells) != 7
        or any(not _cell_text(cell) for cell in weekday_cells)
        or not period_cells
        or any(not _cell_text(cell) for cell in period_cells)
    ):
        raise ParseError("教室课表缺少星期或节次表头")
    all_periods = [_cell_text(cell) for cell in period_cells]
    if len(all_periods) % 7 != 0:
        raise ParseError("教室课表节次列数量无效")
    periods_per_day = len(all_periods) // 7
    period_names = all_periods[:periods_per_day]
    classrooms: list[str] = []
    entries: list[ClassroomEntry] = []
    for row in rows[2:]:
        cells = row.find_all(["td", "th"], recursive=False)
        if not cells:
            continue
        classroom = _cell_text(cells[0])
        classrooms.append(classroom)
        slot = 0
        for cell in cells[1:]:
            try:
                colspan = int(str(cell.get("colspan", "1")))
            except ValueError as exc:
                raise ParseError("教室课表单元格 colspan 无效") from exc
            if (
                colspan < 1
                or slot >= len(all_periods)
                or slot + colspan > len(all_periods)
            ):
                raise ParseError("教室课表单元格超出节次网格")
            weekday = slot // periods_per_day + 1
            period = period_names[slot % periods_per_day]
            period_span = _period_code_range(period) or (0, 0)
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
                        start_period=period_span[0],
                        end_period=period_span[1],
                    )
                )
            slot += colspan
    return classrooms, entries


def _period_code_range(value: str) -> tuple[int, int] | None:
    """解析 0102、1-2、5、0505 等教务节次编码。"""
    text = _clean(value).replace("－", "-").replace("至", "-")
    match = re.search(r"(\d+)\s*[-~]\s*(\d+)", text)
    if match:
        start, end = int(match.group(1)), int(match.group(2))
        return (start, end) if start > 0 and end >= start else None
    digits = "".join(char for char in text if char.isdigit())
    if len(digits) >= 4 and len(digits) % 2 == 0:
        middle = len(digits) // 2
        start, end = int(digits[:middle]), int(digits[middle:])
        return (start, end) if start > 0 and end >= start else None
    if digits:
        value_int = int(digits)
        return (value_int, value_int) if value_int > 0 else None
    return None


def parse_classroom_entries(html: str) -> tuple[ClassroomEntry, ...]:
    """解析教室课表中的课程占用条目，保留原有返回结构。"""
    _, entries = _classroom_grid_parts(html)
    return tuple(entries)


def parse_classroom_grid(html: str) -> ClassroomGrid:
    """解析教室课表中的全量教室清单和课程占用条目。"""
    classrooms, entries = _classroom_grid_parts(html)
    return ClassroomGrid(tuple(classrooms), tuple(entries))


def _student_value(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = _clean(value)
    if not cleaned or "隐私不作为接口显示" in cleaned:
        return None
    return cleaned


def _student_label(value: str) -> str:
    return re.sub(r"[\s：:]", "", value)


def _student_labeled_values(table: Tag) -> dict[str, str | None]:
    values: dict[str, str | None] = {}
    for row in table.find_all("tr"):
        # The later sections reuse labels such as “姓名”; only the first
        # personal-information block belongs in this flat mapping.
        if _student_label("学习及工作简历") in _student_label(row.get_text(" ", strip=True)):
            break
        cells = row.find_all(["td", "th"], recursive=False)
        texts = [_clean(cell.get_text(" ", strip=True)) for cell in cells]
        for index, text in enumerate(texts):
            if not text:
                continue
            if "：" in text or ":" in text:
                label, value = re.split(r"[：:]", text, maxsplit=1)
                if _student_label(label):
                    values[_student_label(label)] = _student_value(value)
                continue
            if index % 2:
                continue
            label = _student_label(text)
            if index + 1 < len(texts):
                values[label] = _student_value(texts[index + 1])
    return values


def _student_section_rows(table: Tag, heading: str, header_count: int) -> list[list[str | None]]:
    rows = table.find_all("tr")
    start = -1
    for index, row in enumerate(rows):
        if _student_label(heading) in _student_label(row.get_text(" ", strip=True)):
            start = index
            break
    if start < 0:
        return []
    header_index = -1
    for index in range(start + 1, len(rows)):
        cells = rows[index].find_all(["td", "th"], recursive=False)
        if len(cells) == header_count:
            header_index = index
            break
    if header_index < 0:
        return []
    result: list[list[str | None]] = []
    for row in rows[header_index + 1 :]:
        text = _clean(row.get_text(" ", strip=True))
        compact_text = _student_label(text)
        if any(_student_label(marker) in compact_text for marker in ("学习及工作简历", "家庭成员情况", "学籍变动情况", "奖励情况", "处分情况")):
            break
        cells = row.find_all("td", recursive=False)
        if len(cells) != header_count:
            continue
        values = [_student_value(cell.get_text(" ", strip=True)) for cell in cells]
        if any(value is not None for value in values):
            result.append(values)
    return result


def parse_student_info(html: str) -> StudentInfo:
    soup = _soup(html)
    table = soup.select_one("#xjkpTable")
    if table is None:
        raise ParseError("学生信息响应中缺少 #xjkpTable")
    values = _student_labeled_values(table)
    if not any(values.get(label) is not None for label in ("院系", "学号", "姓名")):
        raise ParseError("学生信息表缺少基本学籍字段")

    def get(*labels: str) -> str | None:
        for label in labels:
            value = values.get(_student_label(label))
            if value is not None:
                return value
        return None

    experiences = tuple(
        StudentStudyWorkExperience(*row)
        for row in _student_section_rows(table, "学习及工作简历", 3)
    )
    family_members = tuple(
        StudentFamilyMember(*row)
        for row in _student_section_rows(table, "家庭成员情况", 4)
    )
    status_changes = tuple(
        StudentStatusChange(*row)
        for row in _student_section_rows(table, "学籍变动情况", 6)
    )
    awards = tuple(
        StudentAward(*row) for row in _student_section_rows(table, "奖励情况", 4)
    )
    disciplinary_records = tuple(
        StudentDisciplinaryRecord(*row)
        for row in _student_section_rows(table, "处分情况", 4)
    )
    return StudentInfo(
        department=get("院系"),
        major=get("专业"),
        education_duration=get("学制"),
        class_name=get("班级"),
        student_id=get("学号"),
        name=get("姓名"),
        gender=get("性别"),
        name_pinyin=get("姓名拼音"),
        birth_date=get("出生日期"),
        marital_status=get("婚否"),
        major_direction=get("专业方向"),
        political_status=get("政治面貌"),
        native_place=get("籍贯"),
        party_membership_date=get("入党团时间"),
        ethnicity=get("民族"),
        study_form=get("学习形式"),
        education_level=get("学习层次"),
        foreign_language=get("外语种类"),
        railway_station=get("下车火车站"),
        postal_code=get("邮政编码"),
        enrollment_date=get("入学日期"),
        graduation_date=get("毕业日期"),
        study_work_experiences=experiences,
        family_members=family_members,
        status_changes=status_changes,
        awards=awards,
        disciplinary_records=disciplinary_records,
    )


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
