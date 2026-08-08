"""JoinQuant private runtime profile template.

Copy this file to ``jq_runtime_config.py``, fill the private values locally, and
upload that private file to the JoinQuant research root.  The private filename
is ignored by Git; never commit credentials.
"""

PROFILE_SCHEMA_VERSION = 1

PROFILES = {
    "good_etf-prod": {
        "strategy_id": "good_etf",
        # Deliberately empty: startup must fail until both values are supplied.
        "host": "",
        "token": "",
        "port": 58620,
        "account_key": None,
        "sub_account_id": None,
        "tls_cert": None,
        "retries": 2,
        "retry_interval": 0.5,
        "rpc_timeout": 60.0,
        "place_order_timeout_margin": 30.0,
        "default_wait_timeout": 16.0,
        "debug": False,
    },
}
