from __future__ import annotations

import base64
import threading
from urllib.parse import parse_qs

import httpx
import pytest

from jwxtapi import (
    AuthenticationError,
    CaptchaError,
    JwxtClient,
    SessionExpiredError,
    ValidationError,
)


LOGIN = '<form action="/jsxsd/xk/LoginToXk"><input id="userAccount"><input name="RANDOMCODE">用户登录</form>'
HOME = "<html><title>学生个人中心</title></html>"
BLANK = "<html></html>"
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
