"""县域通信共建规划账本的服务端包入口。"""

PROJECT_CODE = "county_network_ledger"


def project_info() -> dict[str, str]:
    """返回稳定的项目标识，供运行检查和诊断使用。"""
    return {"code": PROJECT_CODE, "title": "县域通信共建规划账本"}
