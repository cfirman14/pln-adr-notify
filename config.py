"""Central configuration for the ADR notification PoC.

Values can be overridden with environment variables, or (when running under
Streamlit) with entries in .streamlit/secrets.toml using the same names.
"""
import os
from zoneinfo import ZoneInfo


def _get(name, default=None):
    # Streamlit secrets take priority when available
    try:
        import streamlit as st  # noqa: WPS433
        if name in st.secrets:
            return st.secrets[name]
    except Exception:
        pass
    return os.environ.get(name, default)


TZ = ZoneInfo("Asia/Jakarta")  # WIB

# --- ADR rules (from the paper) ---------------------------------------------
TRIGGER_LOADING = 0.80          # hard trigger: S >= 0.8 x S_rated
RESPONSE_WINDOW_MIN = 30        # customer response window (implicit approval after)
PARTIAL_ACCEPT_SHARE = 0.80     # "Accept 80%" option
DEFAULT_DURATION_H = 1.0        # curtailment duration if the CSV does not give one
EVENT_LEAD_MIN = 30             # event starts after the response window closes

# --- Tariff / cost parameters (IDR/kWh) -------------------------------------
# C_avoided: placeholder pending PLN BPP figure (paper, Limitation 11)
C_AVOIDED = float(_get("C_AVOIDED", 1500))
# C_avoidable: replacement peaker cost; default = PLTG avg total op. cost (Table 1)
C_AVOIDABLE = float(_get("C_AVOIDABLE", 2454.85))
INCENTIVE_FACTOR = 1 / 3        # Eq. (2)
PENALTY_FACTOR = 0.80           # Eq. (3)

# --- Paths --------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = _get("ADR_DB_PATH", os.path.join(BASE_DIR, "adr.db"))
CUSTOMERS_CSV = os.path.join(BASE_DIR, "data", "customers.csv")
OUTBOX_DIR = os.path.join(BASE_DIR, "outbox")

# --- E-mail -------------------------------------------------------------------
# EMAIL_MODE = "file"  -> e-mails are written to ./outbox as .html/.eml (no SMTP needed)
# EMAIL_MODE = "smtp"  -> e-mails are sent through the SMTP server below
EMAIL_MODE = _get("EMAIL_MODE", "file")
SMTP_HOST = _get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(_get("SMTP_PORT", 587))
SMTP_USER = _get("SMTP_USER", "")
SMTP_PASSWORD = _get("SMTP_PASSWORD", "")
SENDER = _get("SENDER", SMTP_USER or "adr-noreply@example.com")

# Public URL of the Streamlit app; the customer page lives at <APP_BASE_URL>/Customer
APP_BASE_URL = _get("APP_BASE_URL", "http://localhost:8501").rstrip("/")
CUSTOMER_PAGE = "Customer"
