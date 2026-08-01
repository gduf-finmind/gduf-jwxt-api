from __future__ import annotations

import base64
import json
import threading
import time
from collections.abc import Mapping
from typing import Any

import httpx
from bs4 import BeautifulSoup

from .exceptions import (
    AuthenticationError,
    CaptchaError,
    ParseError,
    RequestError,
    SessionExpiredError,
    ValidationError,
)
from .models import (
    CaptchaImage,
    ClassroomSchedule,
    Grade,
    GradeDetail,
    GradeReport,
    LoginResult,
    Option,
    Schedule,
    TrainingPlan,
)
from .parsers import (
    parse_classroom_entries,
    parse_grade_detail,
    parse_grades,
    parse_schedule,
    parse_training_plan,
)


class JwxtClient:
    """广东金融学院综合教务系统同步客户端。"""

    def __init__(
        self,
        base_url: str = "https://jwxt.gduf.edu.cn",
        timeout: float = 15.0,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if timeout <= 0:
            raise ValidationError("timeout 必须大于 0")
        self._lock = threading.RLock()
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            follow_redirects=False,
            transport=transport,
            headers={
                "User-Agent": "jwxtapi/0.1 (+https://jwxt.gduf.edu.cn)",
                "Accept-Language": "zh-CN,zh;q=0.9",
            },
        )
        self._logged_in = False
        self._captcha_ready = False
        self._closed = False
        self._grade_objects: dict[int, Grade] = {}

    def __enter__(self) -> JwxtClient:
        self._ensure_open()
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()

    @property
    def is_logged_in(self) -> bool:
        with self._lock:
            return self._logged_in

    def get_captcha(self) -> CaptchaImage:
        with self._lock:
            self._ensure_open()
            self._clear_session()
            self._request("GET", "/jsxsd/", follow_redirects=True)
            response = self._request(
                "GET",
                "/jsxsd/verifycode.servlet",
                params={"t": str(time.time_ns())},
            )
            content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if not response.content or not content_type.startswith("image/"):
                raise CaptchaError("验证码接口没有返回图片")
            self._captcha_ready = True
            return CaptchaImage(response.content, content_type)

    def login(self, username: str, password: str, captcha: str) -> LoginResult:
        with self._lock:
            self._ensure_open()
            if not username:
                raise ValidationError("username 不能为空")
            if not password:
                raise ValidationError("password 不能为空")
            if not captcha:
                raise ValidationError("captcha 不能为空")
            if not self._captcha_ready:
                raise CaptchaError("请先调用 get_captcha() 获取与当前会话绑定的验证码")
            encoded = self._encode_credentials(username, password)
            try:
                response = self._request(
                    "POST",
                    "/jsxsd/xk/LoginToXk",
                    data={"encoded": encoded, "RANDOMCODE": captcha},
                )
                home = self._request("GET", "/jsxsd/framework/xsMain.jsp", follow_redirects=True)
            finally:
                self._captcha_ready = False
            if self._is_login_page(home.text):
                self._logged_in = False
                message = self._login_error_message(response.text) or self._login_error_message(home.text)
                if message and "验证码" in message:
                    raise CaptchaError(message)
                raise AuthenticationError(message or "登录失败：账号、密码或验证码不正确")
            if not self._is_student_home(home.text):
                raise ParseError("登录验证响应不是学生主页，也不是登录页")
            self._logged_in = True
            self._grade_objects.clear()
            return LoginResult(True, username)

    def keep_alive(self) -> bool:
        with self._lock:
            self._require_login()
            response = self._request("GET", "/jsxsd/framework/blankPage.jsp", follow_redirects=True)
            self._raise_if_session_expired(response)
            return True

    def logout(self) -> None:
        with self._lock:
            self._ensure_open()
            try:
                if self._logged_in:
                    self._request(
                        "GET",
                        "/jsxsd/xk/LoginToXk",
                        params={"method": "exit"},
                        follow_redirects=True,
                    )
            finally:
                self._clear_session()

    def get_schedule(self, term: str, week: int | None = None) -> Schedule:
        with self._lock:
            self._require_login()
            self._validate_term(term)
            if week is not None and not 1 <= week <= 30:
                raise ValidationError("week 必须在 1 到 30 之间")
            base_form: dict[str, str | list[str]] = {
                "cj0701id": "",
                "zc": "" if week is None else str(week),
                "demo": "",
                "xnxq01id": term,
                "sfFD": "1",
            }
            initial = self._request("POST", "/jsxsd/xskb/xskb_list.do", data=base_form, follow_redirects=True)
            self._raise_if_session_expired(initial)
            soup = BeautifulSoup(initial.text, "html.parser")
            table = soup.select_one("#kbtable")
            if table is None:
                raise ParseError("个人课表响应中缺少 #kbtable")
            for name in ("jx0415zbdiv_1", "jx0415zbdiv_2"):
                base_form[name] = [
                    str(element.get("value", ""))
                    for element in table.select(f'input[name="{name}"]')
                ]
            response = self._request(
                "POST", "/jsxsd/xskb/xskb_list.do", data=base_form, follow_redirects=True
            )
            self._raise_if_session_expired(response)
            return parse_schedule(response.text, term, week)

    def get_buildings(self, campus: str) -> list[Option]:
        with self._lock:
            self._require_login()
            if not campus:
                raise ValidationError("campus 不能为空")
            response = self._request(
                "POST", "/jsxsd/kbcx/getJxlByAjax", data={"xqid": campus}, follow_redirects=True
            )
            self._raise_if_session_expired(response)
            try:
                payload: Any = response.json()
            except (ValueError, json.JSONDecodeError) as exc:
                raise ParseError("教学楼接口没有返回有效 JSON") from exc
            if not isinstance(payload, list):
                raise ParseError("教学楼接口返回值不是数组")
            result: list[Option] = []
            for item in payload:
                if not isinstance(item, dict) or "dm" not in item or "dmmc" not in item:
                    raise ParseError("教学楼接口返回项缺少 dm 或 dmmc")
                result.append(Option(str(item["dm"]), str(item["dmmc"])))
            return result

    def get_classroom_schedule(
        self,
        term: str,
        department: str = "",
        campus: str = "",
        building: str = "",
        start_week: int | None = None,
        end_week: int | None = None,
        start_period: int | None = None,
        end_period: int | None = None,
    ) -> ClassroomSchedule:
        with self._lock:
            self._require_login()
            self._validate_term(term)
            self._validate_range("周次", start_week, end_week, 30)
            max_period = self._get_max_period(term) if start_period is not None or end_period is not None else 99
            self._validate_range("节次", start_period, end_period, max_period)
            response = self._request(
                "POST",
                "/jsxsd/kbcx/kbxx_classroom_ifr",
                data={
                    "xnxqh": term,
                    "skyx": department,
                    "xqid": campus,
                    "jzwid": building,
                    "zc1": "" if start_week is None else str(start_week),
                    "zc2": "" if end_week is None else str(end_week),
                    "jc1": "" if start_period is None else f"{start_period:02d}",
                    "jc2": "" if end_period is None else f"{end_period:02d}",
                },
                follow_redirects=True,
            )
            self._raise_if_session_expired(response)
            return ClassroomSchedule(
                term=term,
                department=department,
                campus=campus,
                building=building,
                start_week=start_week,
                end_week=end_week,
                start_period=start_period,
                end_period=end_period,
                items=parse_classroom_entries(response.text),
            )

    def get_grades(self, extra_form: Mapping[str, str] | None = None) -> GradeReport:
        with self._lock:
            self._require_login()
            response = self._request(
                "POST",
                "/jsxsd/kscj/cjcx_list",
                data=dict(extra_form or {}),
                follow_redirects=True,
            )
            self._raise_if_session_expired(response)
            report = parse_grades(response.text)
            self._grade_objects = {id(grade): grade for grade in report.items}
            return report

    def get_grade_detail(self, grade: Grade) -> GradeDetail:
        with self._lock:
            self._require_login()
            if not isinstance(grade, Grade):
                raise ValidationError("grade 必须是 get_grades() 返回的 Grade 对象")
            if not grade.has_detail:
                raise ValidationError("该成绩没有可用的明细链接")
            key = (grade.student_id or "", grade.teaching_task_id or "", grade.detail_total_score or "")
            if self._grade_objects.get(id(grade)) is not grade:
                raise ValidationError("grade 不是当前客户端最近一次 get_grades() 返回的成绩")
            response = self._request(
                "GET",
                "/jsxsd/kscj/pscj_list.do",
                params={"xs0101id": key[0], "jx0404id": key[1], "zcj": key[2]},
                follow_redirects=True,
            )
            self._raise_if_session_expired(response)
            return parse_grade_detail(response.text)

    def get_training_plan(self, extra_form: Mapping[str, str] | None = None) -> TrainingPlan:
        with self._lock:
            self._require_login()
            response = self._request(
                "POST",
                "/jsxsd/pyfa/pyfa_query",
                data=dict(extra_form or {}),
                follow_redirects=True,
            )
            self._raise_if_session_expired(response)
            return parse_training_plan(response.text)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._clear_session()
            self._client.close()
            self._closed = True

    def _get_max_period(self, term: str) -> int:
        response = self._request(
            "GET", "/jsxsd/kbxx/initJc", params={"xnxq": term}, follow_redirects=True
        )
        self._raise_if_session_expired(response)
        try:
            payload = response.json()
            major_periods = float(payload["mtdjs"])
        except (ValueError, TypeError, KeyError) as exc:
            raise ParseError("节次初始化接口缺少有效的 mtdjs") from exc
        maximum = int(major_periods * 2)
        if maximum <= 0:
            raise ParseError("节次初始化接口返回的最大节次无效")
        return maximum

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        self._ensure_open()
        try:
            response = self._client.request(method, path, **kwargs)
            if response.is_error:
                response.raise_for_status()
            return response
        except httpx.TimeoutException as exc:
            raise RequestError(f"请求超时：{method} {path}") from exc
        except httpx.HTTPStatusError as exc:
            raise RequestError(f"远端返回 HTTP {exc.response.status_code}：{method} {path}") from exc
        except httpx.HTTPError as exc:
            raise RequestError(f"请求失败：{method} {path}: {exc}") from exc

    def _raise_if_session_expired(self, response: httpx.Response) -> None:
        if self._is_login_page(response.text):
            self._clear_session()
            raise SessionExpiredError("会话已经过期，请重新获取验证码并登录")

    def _require_login(self) -> None:
        self._ensure_open()
        if not self._logged_in:
            raise SessionExpiredError("客户端尚未登录")

    def _ensure_open(self) -> None:
        if self._closed:
            raise RequestError("客户端已经关闭")

    def _clear_session(self) -> None:
        self._logged_in = False
        self._captcha_ready = False
        self._grade_objects.clear()
        self._client.cookies.clear()

    @staticmethod
    def _encode_credentials(username: str, password: str) -> str:
        account = base64.b64encode(username.encode("utf-8")).decode("ascii")
        secret = base64.b64encode(password.encode("utf-8")).decode("ascii")
        return f"{account}%%%{secret}"

    @staticmethod
    def _is_login_page(html: str) -> bool:
        return ("id=\"userAccount\"" in html and "RANDOMCODE" in html) or (
            "用户登录" in html and "LoginToXk" in html
        )

    @staticmethod
    def _is_student_home(html: str) -> bool:
        return "学生个人中心" in html or ("xsMain.jsp" in html and "退出" in html)

    @staticmethod
    def _login_error_message(html: str) -> str | None:
        soup = BeautifulSoup(html, "html.parser")
        for font in soup.find_all("font", attrs={"color": "red"}):
            text = " ".join(font.get_text(" ", strip=True).split())
            if text and "严禁处理" not in text:
                return text
        return None

    @staticmethod
    def _validate_term(term: str) -> None:
        import re

        if not re.fullmatch(r"\d{4}-\d{4}-[12]", term):
            raise ValidationError("term 格式必须为 YYYY-YYYY-1 或 YYYY-YYYY-2")
        first_year, second_year, _ = term.split("-")
        if int(second_year) != int(first_year) + 1:
            raise ValidationError("term 中第二个年份必须比第一个年份大 1")

    @staticmethod
    def _validate_range(label: str, start: int | None, end: int | None, maximum: int) -> None:
        for name, value in (("开始", start), ("结束", end)):
            if value is not None and not 1 <= value <= maximum:
                raise ValidationError(f"{label}{name}值必须在 1 到 {maximum} 之间")
        if start is not None and end is not None and start > end:
            raise ValidationError(f"{label}开始值不能大于结束值")
