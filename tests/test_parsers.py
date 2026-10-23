import pytest
from jwxtapi import ParseError
from jwxtapi.parsers import (
    parse_classroom_entries,
    parse_classroom_grid,
    parse_grade_detail,
    parse_grades,
    parse_schedule,
    parse_student_info,
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


def test_parse_personal_schedule_maps_repeated_major_sections_to_real_periods() -> None:
    """重复的第二、第三大节标题必须保留第5、8节独立小节。"""
    headings = ("第一大节", "第二大节", "第二大节", "第三大节", "第三大节", "第四大节", "第五大节")
    rows = "".join(
        f'<tr><th>{heading}</th><td><div class="kbcontent">课程{index}<font title="周次(节次)">1-16(周)</font></div></td>'
        + "<td></td>" * 6
        + "</tr>"
        for index, heading in enumerate(headings, start=1)
    )
    schedule = parse_schedule(f'<table id="kbtable"><tr><th></th>'
        f'<th>一</th><th>二</th><th>三</th><th>四</th><th>五</th><th>六</th><th>日</th></tr>{rows}</table>', "2026-2027-1", None)
    spans = [(item.start_period, item.end_period) for item in schedule.items]
    assert spans == [(1, 2), (3, 4), (5, 5), (6, 7), (8, 8), (9, 10), (11, 12)]


def _friday_schedule(content: str) -> str:
    """构造周五第6至7节单元格，复现同一时段的不同周次课程。"""
    return (
        '<table id="kbtable"><tr><th></th></tr><tr><th>第三大节</th>'
        + '<td></td>' * 4 + f'<td>{content}</td><td></td><td></td></tr></table>'
    )


def _course_html(name: str, teacher: str, weeks: str, room: str) -> str:
    """使用教务页面字段标签生成一门课，便于组合真实结构的多课程场景。"""
    return (
        f'<span>{name}</span><br><font title="老师">{teacher}</font><br>'
        f'<font title="周次(节次)">{weeks}</font><br>'
        f'<font title="教室">{room}</font>'
    )


@pytest.mark.parametrize("separator", ['<br>----------------<br>', '<hr>', '<br><span>———</span><br>'])
def test_same_container_preserves_alternating_courses(separator: str) -> None:
    """同容器单双周课程必须分别保留名称、老师、教室和真实节次。"""
    odd = _course_html('Web程序设计', '黄老师', '1,3,5,7,9,11,13,15(周)', '敏学414')
    even = _course_html('数据库原理与应用', '李老师', '2,4,6,8,10,12,14,16(双周)', '敏学317')
    result = parse_schedule(_friday_schedule(f'<div class="kbcontent">{odd}{separator}{even}</div>'), '2026-2027-1', None)
    assert [(c.course_name, c.teacher, c.classroom, c.weeks) for c in result.items] == [
        ('Web程序设计', '黄老师', '敏学414', tuple(range(1, 17, 2))),
        ('数据库原理与应用', '李老师', '敏学317', tuple(range(2, 17, 2))),
    ]
    assert {(c.weekday, c.start_period, c.end_period) for c in result.items} == {(5, 6, 7)}


@pytest.mark.parametrize("nested", [False, True])
def test_mixed_containers_and_multiple_week_groups(nested: bool) -> None:
    """混合及嵌套容器不丢课、不重复；课程名里的连字符不触发切段。"""
    parts = [
        f'<div class="{kind} course">{_course_html(name, "老师", weeks, "教室")}</div>'
        for kind, name, weeks in [('kbcontent', 'Web-程序设计', '3-4(周)'),
                                  ('kbcontent1', '数据库', '6(周)'),
                                  ('kbcontent', '实验', '8,10(周)')]
    ]
    content = ''.join(parts)
    if nested:
        content = f'<div class="kbcontent1"><div><span>{content}</span></div></div>'
    result = parse_schedule(_friday_schedule(content), '2026-2027-1', None)
    assert [(c.course_name, c.weeks) for c in result.items] == [
        ('Web-程序设计', (3, 4)), ('数据库', (6,)), ('实验', (8, 10)),
    ]


def test_ambiguous_repeated_fields_raise_instead_of_overwriting() -> None:
    """缺少可靠分隔边界时明确报错，禁止第一门课名称混用第二门课字段。"""
    content = _course_html('Web', '黄老师', '1-16单周', '414') + '<br>' + _course_html('数据库', '李老师', '2-16双周', '317')
    with pytest.raises(ParseError, match='分隔边界'):
        parse_schedule(_friday_schedule(f'<div class="kbcontent">{content}</div>'), '2026-2027-1', None)


def test_personal_schedule_prefers_detailed_view_of_same_logical_cell() -> None:
    """真实页面同时提供简略和含教师的隐藏视图，两者是同一课程而非多教师授课。

    同格的单双周课程仍必须分别保留；其他未配对课程容器不能被丢弃。
    """
    odd = _course_html('Web程序设计', '教师甲', '1,3,5,7,9,11,13,15(周)', '敏学414')
    even = _course_html('数据库原理与应用', '教师乙', '2,4,6,8,10,12,14,16(双周)', '敏学317')
    detailed = odd + '<br>---------------------<br>' + even
    brief = detailed.replace('<font title="老师">教师甲</font>', '').replace('<font title="老师">教师乙</font>', '')
    html = _friday_schedule(f'<div class="kbcontent1" id="logical-cell-1">{brief}</div>'
                            f'<div class="kbcontent" id="logical-cell-2" style="display: none;">{detailed}</div>')
    schedule = parse_schedule(html, '2026-2027-1', None)
    assert [(item.course_name, item.teacher, item.weeks) for item in schedule.items] == [
        ('Web程序设计', '教师甲', tuple(range(1, 17, 2))),
        ('数据库原理与应用', '教师乙', tuple(range(2, 17, 2))),
    ]


def _classroom_grid_html() -> str:
    periods = "".join(f"<td>{period}</td>" for _ in range(7) for period in ("0102", "0304"))
    weekdays = "".join(f'<th colspan="2">星期{day}</th>' for day in "一二三四五六日")
    return f"""
    <table id="kbtable">
      <tr><th></th>{weekdays}</tr>
      <tr><td>教室\\节次</td>{periods}</tr>
      <tr><td>清远敏学102</td><td colspan="5"></td><td colspan="2">
        <div class="kbcontent1">设计模式<br>叶东东<br>(1-16周)<br>软件工程1班</div>
        <div class="kbcontent">软件测试<br>李老师<br>2-8双周<br>软件工程2班</div>
      </td><td colspan="7"></td></tr>
      <tr><td>清远敏学103</td><td colspan="14">&nbsp;</td></tr>
    </table>
    """


def test_parse_classroom_grid_includes_free_rooms_and_maps_colspan() -> None:
    html = _classroom_grid_html()
    grid = parse_classroom_grid(html)
    assert grid.classrooms == ("清远敏学102", "清远敏学103")
    assert len(grid.entries) == 2
    assert {entry.classroom for entry in grid.entries} == {"清远敏学102"}
    assert grid.entries[0].weekday == 3
    assert grid.entries[0].period == "0304"
    assert grid.entries[0].teacher == "叶东东"
    assert grid.entries[0].class_name == "软件工程1班"
    assert grid.entries[1].weeks == (2, 4, 6, 8)
    assert grid.to_dict()["classrooms"] == ("清远敏学102", "清远敏学103")


def test_parse_classroom_entries_remains_occupancy_only() -> None:
    html = _classroom_grid_html()
    items = parse_classroom_entries(html)
    assert items == parse_classroom_grid(html).entries
    assert len(items) == 2
    assert items[0].weekday == 3
    assert items[0].period == "0304"
    assert items[0].teacher == "叶东东"
    assert items[0].class_name == "软件工程1班"


def test_parse_classroom_grid_requires_kbtable() -> None:
    with pytest.raises(ParseError):
        parse_classroom_grid("<html></html>")


def test_parse_classroom_grid_requires_weekday_and_period_headers() -> None:
    with pytest.raises(ParseError):
        parse_classroom_grid('<table id="kbtable"><tr><th></th></tr></table>')
    periods = "".join("<td>0102</td>" for _ in range(7))
    html = f'<table id="kbtable"><tr><th></th></tr><tr><td>教室</td>{periods}</tr></table>'
    with pytest.raises(ParseError):
        parse_classroom_grid(html)


def test_parse_classroom_grid_rejects_period_count_not_divisible_by_seven() -> None:
    weekdays = "".join(f"<th>星期{day}</th>" for day in "一二三四五六日")
    periods = "".join("<td>0102</td>" for _ in range(8))
    html = f'<table id="kbtable"><tr><th></th>{weekdays}</tr><tr><td>教室</td>{periods}</tr></table>'
    with pytest.raises(ParseError):
        parse_classroom_grid(html)


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


def test_parse_student_info_excludes_private_fields_and_parses_records() -> None:
    html = """
    <table id="xjkpTable">
      <tr><td>院系：计算机学院</td><td>专业：软件工程</td><td>学制：4</td><td>班级：1班</td><td>学号：2024001</td></tr>
      <tr><td>姓名</td><td>张三</td><td>性别</td><td>男</td><td>姓名拼音</td><td>Zhang San</td></tr>
      <tr><td>本人电话</td><td>13800000000</td><td>家庭电话</td><td>该部分隐私不作为接口显示</td></tr>
      <tr><td>入学日期</td><td>202409</td><td>毕业日期</td><td></td></tr>
      <tr><td>学习及工作简历</td></tr>
      <tr><td>起止年月</td><td>学校或工作单位</td><td>职务</td></tr>
      <tr><td>2020-2024</td><td>某中学</td><td>学生</td></tr>
      <tr><td>家庭成员情况</td></tr>
      <tr><td>姓名</td><td>与本人关系</td><td>工作单位</td><td>职务</td></tr>
      <tr><td>李四</td><td>父亲</td><td>某公司</td><td>职员</td></tr>
      <tr><td>学籍变动情况</td></tr>
      <tr><td>异动类别</td><td>异动原因</td><td>异动日期</td><td>原院系</td><td>原专业</td><td>原年级</td></tr>
      <tr><td>奖励情况</td></tr>
      <tr><td>奖励年度</td><td>奖励名称</td><td>颁奖单位</td><td>奖励等级</td></tr>
      <tr><td>处分情况</td></tr>
      <tr><td>处分日期</td><td>处分名称</td><td>处分单位</td><td>处分原因</td></tr>
    </table>
    """
    info = parse_student_info(html)
    assert info.name == "张三"
    assert info.student_id == "2024001"
    assert info.study_work_experiences[0].organization == "某中学"
    assert info.family_members[0].relationship == "父亲"
    payload = info.to_dict()
    assert "本人电话" not in payload
    assert "家庭电话" not in payload
    assert "13800000000" not in str(payload)
