"""JoinQuant private runtime profile template.

Copy this file to ``jq_runtime_config.py``, fill the private values locally, and
upload that private file to the JoinQuant research root.  The private filename
is ignored by Git; never commit credentials.
"""

PROFILE_SCHEMA_VERSION = 2

# 未在STRATEGIES中声明的策略使用该连接，并安全地默认为JQ模式。
DEFAULT_PROFILE = "qmt-main"

# profile和mode都可按策略覆盖；mode缺省时仍为JQ。
STRATEGIES = {
    "good_etf": {
        "profile": "qmt-main",
        "mode": "JQ",
    },
}

PROFILES = {
    "qmt-main": {
        # Deliberately empty: startup must fail until both values are supplied.
        "host": "",
        "token": "",
        "port": 58620,
        "account_key": None,
        "tls_cert": None,
        "rpc_timeout": 60.0,
    },
}
