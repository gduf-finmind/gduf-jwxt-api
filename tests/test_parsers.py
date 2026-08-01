from jwxtapi.parsers import (
    parse_classroom_entries,
    parse_grade_detail,
    parse_grades,
    parse_schedule,
    parse_training_plan,
    parse_weeks,
)


def test_parse_weeks_supports_ranges_lists_and_parity() -> None:
    assert parse_weeks("1-4(周)") == (1, 2, 3, 4)
    assert parse_weeks("1,3,5,7(周)") == (1, 3, 5, 7)
    assert parse_weeks("1-8单周") == (1, 3, 5, 7)
    assert parse_weeks("2-8双周") == (2, 4, 6, 8)


def test_parse_personal_schedule() -> None:
    html = """
    <select id="xnxq01id"><option value="2025-2026-2" selected>2025-2026-2</option></select>
    <table id="kbtable">
      <tr><th></th><th>星期一</th><th>星期二</th><th>星期三</th><th>星期四</th><th>星期五</th><th>星期六</th><th>星期日</th></tr>
      <tr><th>第一大节</th>
        <td><div class="kbcontent">操作系统<br><font title="老师">毕君宇</font><br><font title="周次(节次)">1-16(周)</font><br><font title="教室">清远南教502</font></div></td>
        <td></td><td></td><td></td><td></td><td></td><td></td>
      </tr>
      <tr><th>备注:</th><td colspan="7">课程设计 1-16周</td></tr>
    </table>
    """
    schedule = parse_schedule(html, "2025-2026-2", 1)
    assert schedule.term == "2025-2026-2"
    assert schedule.remarks == "课程设计 1-16周"
    assert schedule.items[0].course_name == "操作系统"
    assert schedule.items[0].teacher == "毕君宇"
    assert schedule.items[0].weeks == tuple(range(1, 17))


def test_parse_classroom_schedule_grid() -> None:
    periods = "".join(f"<td>{period}</td>" for _ in range(7) for period in ("0102", "0304"))
    empty = "<td>&nbsp;</td>" * 13
    html = f"""
    <table id="kbtable">
      <tr><th></th>{''.join('<th colspan="2">星期</th>' for _ in range(7))}</tr>
      <tr><td>教室\\节次</td>{periods}</tr>
      <tr><td>清远敏学102</td>{empty}<td><div class="kbcontent1">设计模式<br>叶东东<br>(1-16周)<br>软件工程1班</div></td></tr>
    </table>
    """
    items = parse_classroom_entries(html)
    assert len(items) == 1
    assert items[0].weekday == 7
    assert items[0].period == "0304"
    assert items[0].teacher == "叶东东"
    assert items[0].class_name == "软件工程1班"


def test_parse_grades_and_detail_link() -> None:
    html = """
    <p>一共需要修读<span>225</span>学分，已修读<span>110</span>学分，还需修读<span>115</span>学分，
    主修课程平均学分绩点<span>2.66</span>，辅修课程平均学分绩点<span>0。</span></p>
    <table id="dataList">
      <tr><th>序号</th><th>开课学期</th><th>课程编号</th><th>课程名称</th><th>成绩</th><th>学分</th><th>总学时</th><th>绩点</th><th>考核方式</th><th>课程属性</th><th>课程性质</th></tr>
      <tr><td>1</td><td>2024-2025-1</td><td>108031001</td><td>专业导论</td><td><a href="javascript:openWindow('/jsxsd/kscj/pscj_list.do?xs0101id=student&amp;jx0404id=task&amp;zcj=%E4%BC%98%E7%A7%80',700,500)">优秀</a></td><td>1</td><td>16</td><td>4</td><td>考查</td><td>专业课程</td><td>专业必修课</td></tr>
    </table>
    """
    report = parse_grades(html)
    assert report.required_credits == "225"
    assert report.major_gpa == "2.66"
    assert report.items[0].detail_total_score == "优秀"
    assert report.items[0].has_detail

    detail = parse_grade_detail("""
      <table id="dataList"><tr><th>序号</th><th>期末成绩</th><th>期末成绩比例</th><th>期中成绩</th><th>期中成绩比例</th><th>平时成绩</th><th>平时成绩比例</th><th>总成绩</th></tr>
      <tr><td>1</td><td>74</td><td>60%</td><td>0</td><td>0%</td><td>79</td><td>40%</td><td>76</td></tr></table>
    """)
    assert detail.total_score == "76"
    assert detail.regular_ratio == "40%"


def test_parse_training_plan() -> None:
    html = """
    <table id="dataList">
      <tr><th>序号</th><th>开课学期</th><th>课程编号</th><th>课程名称</th><th>开课单位</th><th>学分</th><th>总学时</th><th>考核方式</th><th>课程属性</th><th>是否考试</th></tr>
      <tr><td>1</td><td>2024-2025-1</td><td>154211002</td><td>计算机导论</td><td>计算机学院</td><td>3</td><td>48</td><td>考试</td><td>必修</td><td>是</td></tr>
    </table>
    """
    plan = parse_training_plan(html)
    assert plan.items[0].course_name == "计算机导论"
    assert plan.to_dict()["items"][0]["credit"] == "3"
