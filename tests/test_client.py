from __future__ import annotations

import base64
import threading
from urllib.parse import parse_qs

import httpx
import pytest

from jwxtapi import (
    AuthenticationError,
    CaptchaError,
    ClassroomGrid,
    JwxtClient,
    ParseError,
    RequestError,
    SessionExpiredError,
    ValidationError,
)


LOGIN = '<form action="/jsxsd/xk/LoginToXk"><input id="userAccount"><input name="RANDOMCODE">用户登录</form>'
HOME = "<html><title>学生个人中心</title></html>"
BLANK = "<html></html>"
CLASSROOM_GRID_HTML = """
<table id="kbtable">
<tr><th></th><th>星期一</th><th>星期二</th><th>星期三</th><th>星期四</th><th>星期五</th><th>星期六</th><th>星期日</th></tr>
<tr><td>教室/节次</td><td>0102</td><td>0102</td><td>0102</td><td>0102</td><td>0102</td><td>0102</td><td>0102</td></tr>
<tr><td>清远敏学102</td><td><div class="kbcontent1">设计模式<br>叶东东<br>1-16周<br>软件工程1班</div></td><td colspan="6"></td></tr>
<tr><td>清远敏学103</td><td colspan="7"></td></tr>
</table>
"""
GRADE_HTML = """
<table id="dataList">
<tr><th>序号</th><th>开课学期</th><th>课程编号</th><th>课程名称</th><th>成绩</th><th>学分</th><th>总学时</th><th>绩点</th><th>考核方式</th><th>课程属性</th><th>课程性质</th></tr>
<tr><td>1</td><td>2025-2026-1</td><td>001</td><td>测试</td><td><a href="javascript:openWindow('/jsxsd/kscj/pscj_list.do?xs0101id=s&amp;jx0404id=t&amp;zcj=%E4%BC%98%E7%A7%80',700,500)">优秀</a></td><td>1</td><td>16</td><td>4</td><td>考查</td><td>专业课程</td><td>必修</td></tr>
</table>
"""


def test_login_binds_captcha_cookie_and_encodes_credentials() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/jsxsd/":
            return httpx.Response(200, text=LOGIN, headers={"set-cookie": "JSESSIONID=session-a; Path=/"})
        if request.url.path == "/jsxsd/verifycode.servlet":
            assert request.headers["cookie"] == "JSESSIONID=session-a"
            return httpx.Response(200, content=b"jpeg", headers={"content-type": "image/jpeg"})
        if request.url.path == "/jsxsd/xk/LoginToXk":
            assert request.headers["cookie"] == "JSESSIONID=session-a"
            form = parse_qs(request.content.decode(), keep_blank_values=True)
            expected = base64.b64encode(b"user").decode() + "%%%" + base64.b64encode(b"secret").decode()
            assert form == {"encoded": [expected], "RANDOMCODE": ["abcd"]}
            return httpx.Response(302, headers={"location": "/jsxsd/framework/xsMain.jsp"})
        if request.url.path == "/jsxsd/framework/xsMain.jsp":
            return httpx.Response(200, text=HOME)
        raise AssertionError(request.url)

    client = JwxtClient(transport=httpx.MockTransport(handler))
    captcha = client.get_captcha()
    result = client.login("user", "secret", "abcd")
    assert captcha.content == b"jpeg"
    assert result.success and result.username == "user"
    assert client.is_logged_in
    client.close()


def test_login_requires_captcha_first() -> None:
    client = JwxtClient(transport=httpx.MockTransport(lambda request: httpx.Response(500)))
    with pytest.raises(CaptchaError):
        client.login("user", "secret", "abcd")
    client.close()


def test_failed_login_raises_authentication_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/jsxsd/":
            return httpx.Response(200, text=LOGIN, headers={"set-cookie": "JSESSIONID=x; Path=/"})
        if request.url.path == "/jsxsd/verifycode.servlet":
            return httpx.Response(200, content=b"x", headers={"content-type": "image/jpeg"})
        return httpx.Response(200, text=LOGIN)

    client = JwxtClient(transport=httpx.MockTransport(handler))
    client.get_captcha()
    with pytest.raises(AuthenticationError):
        client.login("user", "wrong", "abcd")
    client.close()


def test_keep_alive_detects_expired_session() -> None:
    client = JwxtClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, text=LOGIN)))
    client._logged_in = True
    with pytest.raises(SessionExpiredError):
        client.keep_alive()
    assert not client.is_logged_in
    client.close()


def test_get_cookies_returns_independent_serializable_copy() -> None:
    client = JwxtClient(transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    client._client.cookies.set("JSESSIONID", "saved")

    cookies = client.get_cookies()

    assert cookies == {"JSESSIONID": "saved"}
    cookies["JSESSIONID"] = "changed"
    assert client.get_cookies() == {"JSESSIONID": "saved"}
    client.close()


def test_restore_session_validates_cookie_and_marks_client_logged_in() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, text=HOME)

    client = JwxtClient(transport=httpx.MockTransport(handler))

    assert client.restore_session({"JSESSIONID": "saved"})
    assert client.is_logged_in
    assert requests[0].url.path == "/jsxsd/framework/xsMain.jsp"
    assert requests[0].headers["cookie"] == "JSESSIONID=saved"
    client.close()


def test_restore_session_keeps_cookie_rotated_by_server() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["cookie"] == "JSESSIONID=old"
        return httpx.Response(
            200,
            text=HOME,
            headers={"set-cookie": "JSESSIONID=new; Path=/"},
        )

    client = JwxtClient(transport=httpx.MockTransport(handler))
    client.restore_session({"JSESSIONID": "old"})
    assert client.get_cookies() == {"JSESSIONID": "new"}
    client.close()


def test_restore_session_rejects_expired_cookie_and_clears_state() -> None:
    client = JwxtClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=LOGIN))
    )

    with pytest.raises(SessionExpiredError):
        client.restore_session({"JSESSIONID": "expired"})

    assert not client.is_logged_in
    assert client.get_cookies() == {}
    client.close()


def test_restore_session_rejects_unrecognized_response_and_clears_state() -> None:
    client = JwxtClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text="maintenance"))
    )

    with pytest.raises(ParseError):
        client.restore_session({"JSESSIONID": "unknown"})

    assert not client.is_logged_in
    assert client.get_cookies() == {}
    client.close()


@pytest.mark.parametrize(
    "cookies",
    [
        {},
        [],
        {"": "value"},
        {"JSESSIONID": ""},
        {1: "value"},
        {"JSESSIONID": 1},
    ],
)
def test_restore_session_validates_cookie_mapping(cookies: object) -> None:
    client = JwxtClient(transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    client._client.cookies.set("existing", "preserved")

    with pytest.raises(ValidationError):
        client.restore_session(cookies)  # type: ignore[arg-type]

    assert client.get_cookies() == {"existing": "preserved"}
    client.close()


def test_cookie_session_methods_reject_closed_client() -> None:
    client = JwxtClient(transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    client.close()

    with pytest.raises(RequestError):
        client.get_cookies()
    with pytest.raises(RequestError):
        client.restore_session({"JSESSIONID": "saved"})


def test_get_student_info_requests_academic_card() -> None:
    requests: list[httpx.Request] = []
    html = """
    <table id="xjkpTable">
      <tr><td>院系：计算机学院</td><td>专业：软件工程</td><td>学制：4</td><td>班级：1班</td><td>学号：2024001</td></tr>
      <tr><td>姓名</td><td>张三</td><td>性别</td><td>男</td></tr>
    </table>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, text=html)

    client = JwxtClient(transport=httpx.MockTransport(handler))
    client._logged_in = True
    info = client.get_student_info()
    assert info.student_id == "2024001"
    assert requests[0].method == "GET"
    assert requests[0].url.path == "/jsxsd/grxx/xsxx"
    client.close()


def test_two_clients_keep_cookies_isolated_across_threads() -> None:
    seen: dict[str, str] = {}
    seen_lock = threading.Lock()

    def make_transport(session: str) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/jsxsd/":
                return httpx.Response(200, text=LOGIN, headers={"set-cookie": f"JSESSIONID={session}; Path=/"})
            if request.url.path == "/jsxsd/verifycode.servlet":
                with seen_lock:
                    seen[session] = request.headers["cookie"]
                return httpx.Response(200, content=b"x", headers={"content-type": "image/jpeg"})
            raise AssertionError(request.url)

        return httpx.MockTransport(handler)

    clients = [JwxtClient(transport=make_transport(name)) for name in ("alice", "bob")]
    threads = [threading.Thread(target=client.get_captcha) for client in clients]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert seen == {"alice": "JSESSIONID=alice", "bob": "JSESSIONID=bob"}
    for client in clients:
        client.close()


def test_term_and_range_validation() -> None:
    client = JwxtClient(transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    client._logged_in = True
    with pytest.raises(ValidationError):
        client.get_schedule("2025-2025-3")
    with pytest.raises(ValidationError):
        client.get_classroom_schedule("2025-2026-2", start_week=5, end_week=3)
    client.close()


def test_classroom_schedule_posts_all_page_form_fields() -> None:
    requests: list[httpx.Request] = []
    periods = "".join(f"<td>{period}</td>" for _ in range(7) for period in ("0102",))
    weekdays = "".join(f"<th>星期{day}</th>" for day in "一二三四五六日")
    html = f"""
    <table id="kbtable"><tr><th></th>{weekdays}</tr><tr><td>教室/节次</td>{periods}</tr></table>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/jsxsd/kbxx/initJc":
            return httpx.Response(200, json={"mtdjs": 6})
        return httpx.Response(200, text=html)

    client = JwxtClient(transport=httpx.MockTransport(handler))
    client._logged_in = True
    client.get_classroom_schedule(
        "2025-2026-2",
        department="54",
        campus="1",
        building="b1",
        start_week=1,
        end_week=2,
        start_period=1,
        end_period=2,
    )
    form = parse_qs(requests[-1].content.decode(), keep_blank_values=True)
    assert form == {
        "xnxqh": ["2025-2026-2"],
        "skyx": ["54"],
        "xqid": ["1"],
        "jzwid": ["b1"],
        "zc1": ["1"],
        "zc2": ["2"],
        "jc1": ["01"],
        "jc2": ["02"],
    }
    client.close()


def test_classroom_grid_posts_form_and_returns_all_classrooms() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/jsxsd/kbxx/initJc":
            return httpx.Response(200, json={"mtdjs": 6})
        return httpx.Response(200, text=CLASSROOM_GRID_HTML)

    client = JwxtClient(transport=httpx.MockTransport(handler))
    client._logged_in = True
    grid = client.get_classroom_grid(
        "2025-2026-2",
        department="54",
        campus="r0",
        building="mx",
        start_week=3,
        end_week=None,
        start_period=1,
        end_period=10,
    )
    assert isinstance(grid, ClassroomGrid)
    assert grid.classrooms == ("清远敏学102", "清远敏学103")
    assert len(grid.entries) == 1
    assert requests[-1].method == "POST"
    assert requests[-1].url.path == "/jsxsd/kbcx/kbxx_classroom_ifr"
    assert parse_qs(requests[-1].content.decode(), keep_blank_values=True) == {
        "xnxqh": ["2025-2026-2"],
        "skyx": ["54"],
        "xqid": ["r0"],
        "jzwid": ["mx"],
        "zc1": ["3"],
        "zc2": [""],
        "jc1": ["01"],
        "jc2": ["10"],
    }
    client.close()


def test_classroom_grid_login_validation_and_expired_session() -> None:
    client = JwxtClient(transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    with pytest.raises(SessionExpiredError):
        client.get_classroom_grid("2025-2026-2")
    client._logged_in = True
    with pytest.raises(ValidationError):
        client.get_classroom_grid("2025-2025-2")
    with pytest.raises(ValidationError):
        client.get_classroom_grid("2025-2026-2", start_week=6, end_week=5)
    client.close()

    def period_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/jsxsd/kbxx/initJc":
            return httpx.Response(200, json={"mtdjs": 6})
        return httpx.Response(200, text=CLASSROOM_GRID_HTML)

    invalid_period = JwxtClient(transport=httpx.MockTransport(period_handler))
    invalid_period._logged_in = True
    with pytest.raises(ValidationError):
        invalid_period.get_classroom_grid("2025-2026-2", start_period=13)
    invalid_period.close()

    expired = JwxtClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=LOGIN))
    )
    expired._logged_in = True
    with pytest.raises(SessionExpiredError):
        expired.get_classroom_grid("2025-2026-2")
    assert not expired.is_logged_in
    expired.close()


def test_ssl_verification_is_enabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[bool] = []

    def make_transport(*, verify: bool) -> httpx.MockTransport:
        seen.append(verify)
        return httpx.MockTransport(lambda request: httpx.Response(200))

    monkeypatch.setattr(httpx, "HTTPTransport", make_transport)
    client = JwxtClient()
    assert seen == [True]
    client.close()


def test_ssl_verification_can_be_explicitly_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[bool] = []

    def make_transport(*, verify: bool) -> httpx.MockTransport:
        seen.append(verify)
        return httpx.MockTransport(lambda request: httpx.Response(200))

    monkeypatch.setattr(httpx, "HTTPTransport", make_transport)
    client = JwxtClient(verify_ssl=False)
    assert seen == [False]
    client.close()


def test_custom_transport_is_preserved_without_creating_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_to_create_transport(*, verify: bool) -> httpx.BaseTransport:
        raise AssertionError(f"unexpected default transport with verify={verify}")

    custom = httpx.MockTransport(lambda request: httpx.Response(200))
    monkeypatch.setattr(httpx, "HTTPTransport", fail_to_create_transport)
    client = JwxtClient(verify_ssl=False, transport=custom)
    assert client._client._transport is custom
    client.close()


def test_schedule_resubmits_dynamic_hidden_values() -> None:
    posted: list[dict[str, list[str]]] = []
    html = """
    <select id="xnxq01id"><option value="2025-2026-2" selected>2025-2026-2</option></select>
    <table id="kbtable"><tr><th></th><th>一</th><th>二</th><th>三</th><th>四</th><th>五</th><th>六</th><th>日</th></tr>
    <tr><th>第一大节</th><td><input name="jx0415zbdiv_1" value="a"><input name="jx0415zbdiv_2" value="b"></td><td></td><td></td><td></td><td></td><td></td><td></td></tr></table>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        posted.append(parse_qs(request.content.decode(), keep_blank_values=True))
        return httpx.Response(200, text=html)

    client = JwxtClient(transport=httpx.MockTransport(handler))
    client._logged_in = True
    client.get_schedule("2025-2026-2", 1)
    assert len(posted) == 2
    assert "jx0415zbdiv_1" not in posted[0]
    assert posted[1]["jx0415zbdiv_1"] == ["a"]
    assert posted[1]["jx0415zbdiv_2"] == ["b"]
    client.close()


def test_grade_detail_rejects_grade_from_another_client_and_encodes_score() -> None:
    detail_query = ""

    def first_handler(request: httpx.Request) -> httpx.Response:
        nonlocal detail_query
        if request.url.path == "/jsxsd/kscj/cjcx_list":
            return httpx.Response(200, text=GRADE_HTML)
        if request.url.path == "/jsxsd/kscj/pscj_list.do":
            detail_query = request.url.query.decode()
            return httpx.Response(200, text="""
              <table id="dataList"><tr><th>序号</th><th>期末成绩</th><th>期末成绩比例</th><th>期中成绩</th><th>期中成绩比例</th><th>平时成绩</th><th>平时成绩比例</th><th>总成绩</th></tr>
              <tr><td>1</td><td>90</td><td>60%</td><td>0</td><td>0%</td><td>100</td><td>40%</td><td>优秀</td></tr></table>
            """)
        raise AssertionError(request.url)

    first = JwxtClient(transport=httpx.MockTransport(first_handler))
    second = JwxtClient(transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    first._logged_in = True
    second._logged_in = True
    grade = first.get_grades().items[0]
    with pytest.raises(ValidationError):
        second.get_grade_detail(grade)
    assert first.get_grade_detail(grade).total_score == "优秀"
    assert "%E4%BC%98%E7%A7%80" in detail_query
    first.close()
    second.close()


def test_logout_clears_cookie_and_state() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["method"] == "exit"
        assert request.headers["cookie"] == "JSESSIONID=active"
        return httpx.Response(200, text="<script>window.location.href='/jsxsd/'</script>")

    client = JwxtClient(transport=httpx.MockTransport(handler))
    client._client.cookies.set("JSESSIONID", "active", domain="jwxt.gduf.edu.cn", path="/")
    client._logged_in = True
    client.logout()
    assert not client.is_logged_in
    assert len(client._client.cookies) == 0
    client.close()
