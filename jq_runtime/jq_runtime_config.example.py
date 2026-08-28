"""JoinQuant private runtime profile template.

Copy this file to ``jq_runtime_config.py``, fill the private values locally, and
upload that private file to the JoinQuant research root.  The private filename
is ignored by Git; never commit credentials.
"""

# 固定为3；这是helper校验的配置结构版本，不是用户可选的运行模式。
PROFILE_SCHEMA_VERSION = 3

# 必须填写下方PROFILES中已经存在的profile名称，例如"qmt-main"。
# 未在STRATEGIES中声明的策略使用该连接，并安全地默认只启用JQ账户。
DEFAULT_PROFILE = "qmt-main"

# STRATEGIES的key必须与各策略脚本顶部的STRATEGY_ID完全一致：
# - 允许字符：字母、数字、点、下划线、连字符；长度1~128；必须唯一。
# - profile：填写PROFILES中存在的名称；省略时使用DEFAULT_PROFILE。
# - jq_account_enabled：bool；True时维护聚宽模拟账户。
# - qmt_account_enabled：bool；True时维护QMT StrategyLedger账户。
# - 两者可同时为True，但不能同时为False。
# - 省略或找不到策略key时默认为JQ=True、QMT=False。
# - 回测始终由run_type自动识别，只运行聚宽账户；无需额外配置。
STRATEGIES = {
    "good_etf_remote": {
        "profile": "qmt-main",
        "jq_account_enabled": True,
        "qmt_account_enabled": False,
    },
}

PROFILES = {
    "qmt-main": {
        # host：BulletTrade服务器的公网IP或域名，不要带http://、端口或路径。
        # 必填；示例故意留空，填好前启动会失败关闭。
        "host": "",
        # token：必须与服务器QMT_SERVER_TOKEN完全一致；非空字符串。
        "token": "",
        # port：1~65535的整数；默认及当前部署端口为58620。
        "port": 58620,
        # account_key：单账户填None；多账户时填服务器QMT_SERVER_ACCOUNTS
        # 中等号左侧的key，例如"main"。
        "account_key": None,
        # tls_cert：未启用TLS填None；启用时填聚宽研究文件中可读取的
        # CA/服务器证书路径，并与服务器TLS配置匹配。
        "tls_cert": None,
        # rpc_timeout：5~300秒的int/float；远程下单/查询等待响应的总超时。
        "rpc_timeout": 60.0,
    },
}
