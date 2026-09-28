"""Operator console: CSV -> trigger check -> e-mail requests -> monitor responses -> settlement.

Run:  streamlit run app.py
The customer page (pages/Customer.py) is opened from the link in each e-mail.
"""
import pandas as pd
import streamlit as st

import config
from adr import core, db, workflow as wf
from adr.mailer import rupiah

st.set_page_config(page_title="PLN ADR - Operator", page_icon="⚡", layout="wide")

NAVY, TEAL, RED, GREEN = "#1F2A44", "#0F7C8C", "#C0392B", "#2E8B57"
st.markdown(f"""<style>
.card{{border:1px solid #dde2ea;border-radius:10px;padding:14px 16px;background:#fff}}
.badge{{float:right;font-size:11px;font-weight:700;border-radius:12px;padding:2px 10px;border:1.5px solid}}
.kv{{font-size:12px;color:#6b7280;margin-top:6px}} .big{{font-size:26px;font-weight:700;color:{NAVY}}}
</style>""", unsafe_allow_html=True)


@st.cache_data
def sample_snapshot():
    return core.load_snapshot("data/feeder_snapshot.csv")


def customers():
    return core.load_customers()


st.title("⚡ PLN ADR - Operator Console")
st.caption(f"E-mail mode: **{config.EMAIL_MODE}**  ·  Customer links point to **{config.APP_BASE_URL}/"
           f"{config.CUSTOMER_PAGE}**  ·  Response window {config.RESPONSE_WINDOW_MIN} min")

tab_mon, tab_resp, tab_settle, tab_cust = st.tabs(
    ["1 · Monitor & send", "2 · Responses", "3 · Settlement (M&V)", "Customers"])

# ------------------------------------------------------------------ monitor
with tab_mon:
    up = st.file_uploader("Feeder snapshot CSV (timestamp, substation, feeder, s_kva, s_rated_kva[, duration_h])",
                          type="csv")
    snap = core.load_snapshot(up) if up else sample_snapshot()
    if not up:
        st.info("Using the sample file data/feeder_snapshot.csv (hypothetical trigger scenario).")
    cl = core.classify(snap)
    cust = customers()

    cols = st.columns(len(cl))
    for col, (_, r) in zip(cols, cl.iterrows()):
        color = RED if r["status"] == "TRIGGER" else GREEN
        col.markdown(f"""<div class="card"><b>F. {r['feeder']}</b>
<span class="badge" style="color:{color};border-color:{color}">{r['status']}</span>
<div class="kv">{r['timestamp']:%d-%m-%Y · %H:%M}</div>
<div class="big">{r['s_kva']:.2f} <span style="font-size:13px">kVA</span></div>
<div class="kv">Loading <b>{r['loading']:.1%}</b> · limit {r['limit_kva']:.0f} kVA</div>
<div class="kv">Target curtailment <b style="color:{color}">{r['target_kw']:.2f} kW</b></div></div>""",
                     unsafe_allow_html=True)

    st.subheader("Allocation per customer")
    alloc = []
    for _, r in cl[cl["status"] == "TRIGGER"].iterrows():
        on = cust[cust["feeder"] == r["feeder"]]
        if on.empty:
            continue
        a = core.allocate(r["target_kw"], on)
        a["duration_h"] = r["duration_h"]
        a["energy_kwh"] = (a["target_kw"] * r["duration_h"]).round(2)
        a["est_incentive"] = [rupiah(core.incentive_idr(k, r["duration_h"])) for k in a["target_kw"]]
        alloc.append(a[["feeder", "name", "email", "weight", "target_kw", "duration_h", "energy_kwh", "est_incentive"]])
    if alloc:
        st.dataframe(pd.concat(alloc), hide_index=True, use_container_width=True,
                     column_config={"weight": st.column_config.NumberColumn(format="%.3f")})
        if st.button("📧 Send ADR requests (Iteration 1)", type="primary"):
            _, log = wf.create_events(snap, cust)
            if log:
                st.success(f"{len(log)} request(s) created.")
                st.dataframe(pd.DataFrame(log), hide_index=True, use_container_width=True)
            else:
                st.warning("No new events: these intervals were already processed.")
    else:
        st.success("No feeder at or above 80% loading. No curtailment needed.")

# ---------------------------------------------------------------- responses
with tab_resp:
    if st.button("🔄 Refresh"):
        st.rerun()
    wf.expire_pending()
    with db.connect() as con:
        events = pd.DataFrame(db.rows(con, "SELECT * FROM events ORDER BY event_start DESC"))
        reqs = pd.DataFrame(db.rows(con, "SELECT * FROM requests ORDER BY event_id, iteration, customer_name"))
    if events.empty:
        st.info("No ADR events yet.")
    for _, ev in events.iterrows():
        r = reqs[reqs["event_id"] == ev["event_id"]].copy()
        r["committed_kw"] = [core.committed_kw(t, s) for t, s in zip(r["target_kw"], r["response"])]
        committed = r["committed_kw"].sum()
        with st.expander(f"{ev['event_id']}  ·  {ev['status']}  ·  committed {committed:.2f} / "
                         f"{ev['feeder_target_kw']:.2f} kW", expanded=ev["status"] in ("ITER1_OPEN", "ITER2_OPEN")):
            st.progress(min(committed / ev["feeder_target_kw"], 1.0) if ev["feeder_target_kw"] else 1.0)
            st.dataframe(r[["iteration", "customer_name", "target_kw", "response", "committed_kw", "deadline",
                            "responded_at"]], hide_index=True, use_container_width=True)
            if ev["status"] in ("ITER1_OPEN", "ITER2_OPEN"):
                label = "Close Iteration 1" if ev["status"] == "ITER1_OPEN" else "Close Iteration 2"
                if st.button(label, key=f"close_{ev['event_id']}"):
                    msg, log = wf.close_iteration(ev["event_id"], customers())
                    st.success(msg)
                    if log:
                        st.dataframe(pd.DataFrame(log), hide_index=True)

# --------------------------------------------------------------- settlement
with tab_settle:
    with db.connect() as con:
        ready = [e["event_id"] for e in db.rows(con, "SELECT event_id FROM events WHERE status='CONFIRMED'")]
    if not ready:
        st.info("No confirmed event waiting for settlement.")
    else:
        ev_id = st.selectbox("Event", ready)
        st.caption("M&V CSV columns: customer_id, delivered_kw (verified average reduction vs. baseline "
                   "during the event, from the customer meter).")
        mv_file = st.file_uploader("M&V result CSV", type="csv", key="mv")
        if mv_file and st.button("Settle event and send summaries", type="primary"):
            res = wf.settle_event(ev_id, pd.read_csv(mv_file))
            st.success("Settlement complete. Summary e-mails sent.")
            st.dataframe(res, hide_index=True, use_container_width=True)

# ---------------------------------------------------------------- customers
with tab_cust:
    st.caption("Enrolled ADR customers. Use test e-mail addresses only during the PoC.")
    st.dataframe(customers(), hide_index=True, use_container_width=True)
    new = st.file_uploader("Replace customer list (CSV)", type="csv", key="cust")
    if new and st.button("Save customer list"):
        df = core.load_customers(new)
        df.to_csv(config.CUSTOMERS_CSV, index=False)
        st.success(f"Saved {len(df)} customers.")
