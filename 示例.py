from pathlib import Path
from jwxtapi import JwxtClient

#配置信息，输入账号密码
username=""     #学号
password=""     #密码


#创建实例并获取验证码
client = JwxtClient()
captcha = client.get_captcha()
Path("验证码.jpg").write_bytes(captcha.content)

##输入验证码并登录
captcha_text = input("请输入 验证码.jpg 中的验证码：")
client.login(username, password, captcha_text)

##会话保活
print("会话保活",client.keep_alive())

##登录状态
print("登陆状态",client.is_logged_in)

##查询个人课表。
schedule = client.get_schedule(term="2026-2027-1")
print("===============课表=============\n",schedule,"\n============================\n")

##获取教学楼代码
buildings=client.get_buildings("r0")
print("===============教学楼代码=============\n",buildings,"\n============================\n")

##获取教室课表
# classroom_schedule=client.get_classroom_schedule("2026-2027-1", department="", campus="", building="", start_week=None, end_week=None, start_period=None, end_period=None)
# print("===============教学楼代码=============\n",classroom_schedule,"\n============================\n")


#获取成绩单
grades=client.get_grades(extra_form=None)
print("===============成绩单=============\n",grades,"\n============================\n")

# # 获取详细成绩,传入grade
# grade_detail=client.get_grade_detail
# print("===============详细成绩=============\n",grade_detail,"\n============================\n")


##获取教学计划
# training_plan=client.get_training_plan()
# print("===============教学计划=============\n",training_plan,"\n============================\n")

