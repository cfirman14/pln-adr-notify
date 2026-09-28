"""Customer page, opened from the link in the ADR e-mail: <APP_BASE_URL>/Customer?token=..."""
from datetime import datetime

import streamlit as st

import config
from adr import core, workflow as wf
from adr.mailer import rupiah

st.set_page_config(page_title="PLN ADR - Customer", page_icon="⚡", layout="centered")
st.markdown("""<style>
.hdr{background:#1F2A44;color:#fff;border-radius:10px;padding:16px 18px;margin-bottom:12px}
.hdr small{color:#E0A106;font-weight:700;letter-spacing:.5px}
.inc{background:#e8f6f3;border-left:4px solid #0F7C8C;padding:10px 12px;border-radius:4px;margin:8px 0}
.pen{background:#fdf0ef;border-left:4px solid #C0392B;padding:10px 12px;border-radius:4px;margin:8px 0}
div.stButton > button{width:100%;height:3em;font-weight:700}
</style>""", unsafe_allow_html=True)

token = st.query_params.get("token")
if not token:
    st.error("This page must be opened from the link in your ADR e-mail.")
    st.stop()

wf.expire_pending()
req, ev = wf.get_request(token)
if not req:
    st.error("Request not found or link has expired.")
    st.stop()

st.markdown(f"""<div class="hdr"><small>PLN DEMAND RESPONSE</small>
<div style="font-size:20px;font-weight:700">{req['customer_name']}</div>
<div style="font-size:13px;opacity:.85">Feeder {ev['feeder']} · {ev['substation']} ·
{'Request' if req['iteration'] == 1 else 'Follow-up request'}</div></div>""", unsafe_allow_html=True)

kwh = req["target_kw"] * req["duration_h"]
c1, c2, c3 = st.columns(3)
c1.metric("Reduce by", f"{req['target_kw']:.2f} kW")
c2.metric("For", f"{req['duration_h']:g} h")
c3.metric("Energy", f"{kwh:.2f} kWh")
st.caption(f"Event starts **{ev['event_start'][:16].replace('T', ' ')} WIB**")

# Incentive first; the penalty is shown only on an Iteration 2 request (paper, Sec. II-E / III-F)
if req["iteration"] == 1:
    st.markdown(f"<div class='inc'>Estimated incentive: <b>{rupiah(req['est_incentive_idr'])}</b><br>"
                f"<small>Credited to your monthly bill after verification. Accepting 80% earns an incentive "
                f"on the reduction you deliver.</small></div>", unsafe_allow_html=True)
else:
    st.markdown(f"<div class='pen'>If you accept and do not deliver, penalty up to "
                f"<b>{rupiah(req['max_penalty_idr'])}</b> is added to your monthly bill.<br>"
                f"<small>Declining or not responding carries no penalty.</small></div>", unsafe_allow_html=True)

# ------------------------------------------------------------- respond
if req["response"] == "PENDING":
    deadline = datetime.fromisoformat(req["deadline"])
    left = int((deadline - wf.now_wib()).total_seconds() // 60)
    st.warning(f"⏱ Please respond within **{max(left, 0)} minute(s)** "
               f"(before {req['deadline'][11:16]} WIB).")
    b1, b2, b3 = st.columns(3)
    choice = None
    if b1.button(f"✅ Accept 100%\n{req['target_kw']:.2f} kW", type="primary"):
        choice = "ACCEPT_100"
    if b2.button(f"Accept 80%\n{req['target_kw'] * config.PARTIAL_ACCEPT_SHARE:.2f} kW"):
        choice = "ACCEPT_80"
    if b3.button("Decline"):
        choice = "DECLINED"
    if choice:
        st.toast(wf.respond(token, choice))
        st.rerun()
else:
    committed = core.committed_kw(req["target_kw"], req["response"])
    label = {"ACCEPT_100": "Accepted 100%", "ACCEPT_80": "Accepted 80%", "DECLINED": "Declined",
             "AUTO_APPROVED": "Accepted automatically (no response in time)",
             "NO_RESPONSE": "No response - no commitment"}[req["response"]]
    st.success(f"Your response: **{label}** · committed **{committed:.2f} kW**")

    # ---------------------------------------------------- event status
    status = {"ITER1_OPEN": "Waiting for other customers", "ITER2_OPEN": "Follow-up round in progress",
              "CONFIRMED": "Confirmed - please reduce your load at the start time",
              "SETTLED": "Completed and verified"}[ev["status"]]
    st.info(f"Event status: **{status}**")

    if ev["status"] == "SETTLED" and committed > 0:
        s1, s2, s3 = st.columns(3)
        s1.metric("Verified reduction", f"{req['delivered_kw']:.2f} kW")
        s2.metric("Incentive", rupiah(req["incentive_idr"]))
        s3.metric("Penalty", rupiah(req["penalty_idr"]))

# ------------------------------------------------------------ monthly bill
st.divider()
st.subheader("Monthly bill impact")
bill = wf.bill_summary(req["customer_id"], core.load_customers())
if bill.empty:
    st.caption("No settled ADR events yet.")
else:
    m = bill.iloc[0]
    b1, b2 = st.columns(2)
    b1.metric(f"Regular bill ({m['month']})", rupiah(m["regular_bill"]))
    b2.metric("Net bill after ADR", rupiah(m["net_bill"]), delta=rupiah(m["net_bill"] - m["regular_bill"]),
              delta_color="inverse")
    show = bill.copy()
    for c in ("regular_bill", "incentive", "penalty", "net_bill"):
        show[c] = show[c].map(rupiah)
    st.dataframe(show, hide_index=True, use_container_width=True)
