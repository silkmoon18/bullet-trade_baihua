"""JoinQuant private runtime profile template.

Copy this file to ``jq_runtime_config.py``, fill the private values locally, and
upload that private file to the JoinQuant research root.  The private filename
is ignored by Git; never commit credentials.
"""

PROFILE_SCHEMA_VERSION = 1

# 每个策略可独立切换。缺少某个 strategy_id 时 helper 安全地默认使用 JQ。
EXECUTION_MODES = {
    "good_etf": "JQ",
}

PROFILES = {
    "good_etf-prod": {
        "strategy_id": "good_etf",
        # Deliberately empty: startup must fail until both values are supplied.
        "host": "",
        "token": "",
        "port": 58620,
        "account_key": None,
        "tls_cert": None,
        "rpc_timeout": 60.0,
    },
}
