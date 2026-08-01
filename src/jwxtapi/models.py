from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, cast


class DataModel:
    def to_dict(self) -> dict[str, Any]:
        return asdict(cast(Any, self))


@dataclass(frozen=True, slots=True)
class CaptchaImage(DataModel):
    content: bytes
    content_type: str


@dataclass(frozen=True, slots=True)
class LoginResult(DataModel):
    success: bool
    username: str


@dataclass(frozen=True, slots=True)
class ScheduleEntry(DataModel):
    course_name: str
    teacher: str | None
    classroom: str | None
    weeks_text: str
    weeks: tuple[int, ...]
    weekday: int
    period: int
    period_name: str


@dataclass(frozen=True, slots=True)
class Schedule(DataModel):
    term: str
    week: int | None
    items: tuple[ScheduleEntry, ...]
    remarks: str | None = None


@dataclass(frozen=True, slots=True)
class Option(DataModel):
    value: str
    label: str


@dataclass(frozen=True, slots=True)
class ClassroomEntry(DataModel):
    classroom: str
    weekday: int
    period: str
    course_name: str
    teacher: str | None
    weeks_text: str | None
    weeks: tuple[int, ...]
    class_name: str | None
    raw_text: str


@dataclass(frozen=True, slots=True)
class ClassroomSchedule(DataModel):
    term: str
    department: str
    campus: str
    building: str
    start_week: int | None
    end_week: int | None
    start_period: int | None
    end_period: int | None
    items: tuple[ClassroomEntry, ...]


@dataclass(frozen=True, slots=True)
class Grade(DataModel):
    index: int
    term: str
    course_code: str
    course_name: str
    score: str
    credit: str
    total_hours: str
    grade_point: str
    assessment_method: str
    course_attribute: str
    course_nature: str
    student_id: str | None
    teaching_task_id: str | None
    detail_total_score: str | None

    @property
    def has_detail(self) -> bool:
        return bool(self.student_id and self.teaching_task_id and self.detail_total_score is not None)


@dataclass(frozen=True, slots=True)
class GradeReport(DataModel):
    required_credits: str | None
    earned_credits: str | None
    remaining_credits: str | None
    major_gpa: str | None
    minor_gpa: str | None
    items: tuple[Grade, ...]


@dataclass(frozen=True, slots=True)
class GradeDetail(DataModel):
    final_score: str
    final_ratio: str
    midterm_score: str
    midterm_ratio: str
    regular_score: str
    regular_ratio: str
    total_score: str


@dataclass(frozen=True, slots=True)
class TrainingPlanCourse(DataModel):
    index: int
    term: str
    course_code: str
    course_name: str
    department: str
    credit: str
    total_hours: str
    assessment_method: str
    course_attribute: str
    is_exam: str


@dataclass(frozen=True, slots=True)
class TrainingPlan(DataModel):
    items: tuple[TrainingPlanCourse, ...]
