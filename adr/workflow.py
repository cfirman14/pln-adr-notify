"""Two-iteration ADR workflow: CSV -> events -> e-mail requests -> responses -> settlement."""
import secrets
from datetime import datetime, timedelta

import pandas as pd

import config
from adr import core, db, mailer


def now_wib() -> datetime:
    return datetime.now(config.TZ).replace(microsecond=0)


def _iso(t: datetime) -> str:
    return t.isoformat()


def _event(con, event_id):
    return db.rows(con, "SELECT * FROM events WHERE event_id=?", (event_id,))[0]


# ------------------------------------------------------------ issue requests
def _issue(con, event: dict, customers: pd.DataFrame, target_kw: float, iteration: int, now: datetime):
    """Allocate target_kw across customers, store requests, send e-mails."""
    alloc = core.allocate(target_kw, customers)
    deadline = now + timedelta(minutes=config.RESPONSE_WINDOW_MIN)
    log = []
    for _, c in alloc.iterrows():
        req = {
            "token": secrets.token_urlsafe(16),
            "event_id": event["event_id"],
            "iteration": iteration,
            "customer_id": str(c["customer_id"]),
            "customer_name": c["name"],
            "email": c["email"],
            "target_kw": float(c["target_kw"]),
            "duration_h": event["duration_h"],
            "est_incentive_idr": round(core.incentive_idr(c["target_kw"], event["duration_h"])) if iteration == 1 else 0,
            "max_penalty_idr": round(core.penalty_idr(c["target_kw"], event["duration_h"])) if iteration == 2 else 0,
            "sent_at": _iso(now),
            "deadline": _iso(deadline),
        }
        con.execute(f"INSERT INTO requests ({','.join(req)}) VALUES ({','.join('?' * len(req))})",
                    tuple(req.values()))
        subject, html = mailer.request_email(req, event)
        log.append({"customer": req["customer_name"], "email": req["email"], "iteration": iteration,
                    "target_kw": req["target_kw"], "delivery": mailer.send(req["email"], subject, html),
                    "link": mailer.customer_link(req["token"])})
    return log


def create_events(snapshot: pd.DataFrame, customers: pd.DataFrame, now: datetime = None, db_path=None):
    """Create one ADR event per triggered feeder row and send Iteration 1 requests."""
    now = now or now_wib()
    classified = core.classify(snapshot)
    triggered = classified[classified["status"] == "TRIGGER"]
    log = []
    with db.connect(db_path) as con:
        for _, r in triggered.iterrows():
            event_id = f"{r['feeder']}-{r['timestamp']:%Y%m%d%H%M}".replace(" ", "")
            if db.rows(con, "SELECT 1 FROM events WHERE event_id=?", (event_id,)):
                continue  # already processed this interval
            on_feeder = customers[customers["feeder"] == r["feeder"]]
            if on_feeder.empty:
                log.append({"customer": "-", "email": "-", "iteration": 1, "target_kw": r["target_kw"],
                            "delivery": f"no enrolled customer on {r['feeder']}", "link": ""})
                continue
            event = {
                "event_id": event_id, "substation": r["substation"], "feeder": r["feeder"],
                "detected_at": _iso(r["timestamp"].to_pydatetime()), "loading": float(r["loading"]),
                "feeder_target_kw": float(r["target_kw"]),
                "event_start": _iso(now + timedelta(minutes=config.RESPONSE_WINDOW_MIN + config.EVENT_LEAD_MIN)),
                "duration_h": float(r["duration_h"]), "status": "ITER1_OPEN",
            }
            con.execute(f"INSERT INTO events ({','.join(event)}) VALUES ({','.join('?' * len(event))})",
                        tuple(event.values()))
            log += _issue(con, event, on_feeder, event["feeder_target_kw"], 1, now)
    return classified, log


# ---------------------------------------------------------------- responses
def get_request(token: str, db_path=None):
    with db.connect(db_path) as con:
        r = db.rows(con, "SELECT * FROM requests WHERE token=?", (token,))
        if not r:
            return None, None
        return r[0], _event(con, r[0]["event_id"])


def expire_pending(now: datetime = None, db_path=None) -> int:
    """Close PENDING requests whose response window has passed."""
    now = now or now_wib()
    with db.connect(db_path) as con:
        # Iteration 1: silence = implicit approval (paper, Sec. III-B).
        # Iteration 2 carries a penalty, so silence is NOT treated as acceptance.
        a = con.execute("UPDATE requests SET response='AUTO_APPROVED', responded_at=? "
                        "WHERE response='PENDING' AND iteration=1 AND deadline<=?", (_iso(now), _iso(now)))
        b = con.execute("UPDATE requests SET response='NO_RESPONSE', responded_at=? "
                        "WHERE response='PENDING' AND iteration=2 AND deadline<=?", (_iso(now), _iso(now)))
        return a.rowcount + b.rowcount


def respond(token: str, response: str, now: datetime = None, db_path=None) -> str:
    """Record a customer's choice: ACCEPT_100, ACCEPT_80 or DECLINED."""
    assert response in {"ACCEPT_100", "ACCEPT_80", "DECLINED"}
    now = now or now_wib()
    expire_pending(now, db_path)
    with db.connect(db_path) as con:
        rq = db.rows(con, "SELECT * FROM requests WHERE token=?", (token,))
        if not rq:
            return "Request not found."
        rq = rq[0]
        if rq["response"] != "PENDING":
            return f"This request was already closed ({rq['response'].replace('_', ' ').lower()})."
        con.execute("UPDATE requests SET response=?, responded_at=? WHERE token=?", (response, _iso(now), token))
        rq["response"] = response
        rq["committed_kw"] = core.committed_kw(rq["target_kw"], response)
        if response != "DECLINED":
            subject, html = mailer.confirmation_email(rq, _event(con, rq["event_id"]))
            mailer.send(rq["email"], subject, html)
    return "Response recorded."


# ---------------------------------------------------------- close iterations
def close_iteration(event_id: str, customers: pd.DataFrame, now: datetime = None, db_path=None):
    """Close the open iteration. After Iteration 1, issue Iteration 2 if commitments fall short."""
    now = now or now_wib()
    with db.connect(db_path) as con:
        # the operator closes the iteration explicitly: any remaining PENDING become implicit approvals
        con.execute("UPDATE requests SET response='AUTO_APPROVED', responded_at=? "
                    "WHERE event_id=? AND response='PENDING' AND iteration=1", (_iso(now), event_id))
        con.execute("UPDATE requests SET response='NO_RESPONSE', responded_at=? "
                    "WHERE event_id=? AND response='PENDING' AND iteration=2", (_iso(now), event_id))
        ev = _event(con, event_id)
        reqs = db.rows(con, "SELECT * FROM requests WHERE event_id=?", (event_id,))
        committed = sum(core.committed_kw(r["target_kw"], r["response"]) for r in reqs)
        shortfall = round(ev["feeder_target_kw"] - committed, 2)

        if ev["status"] == "ITER1_OPEN" and shortfall > 0.005:
            con.execute("UPDATE events SET status='ITER2_OPEN' WHERE event_id=?", (event_id,))
            on_feeder = customers[customers["feeder"] == ev["feeder"]]
            log = _issue(con, ev, on_feeder, shortfall, 2, now)
            return f"Committed {committed:.2f} of {ev['feeder_target_kw']:.2f} kW. " \
                   f"Iteration 2 issued for the {shortfall:.2f} kW shortfall.", log
        con.execute("UPDATE events SET status='CONFIRMED' WHERE event_id=?", (event_id,))
        return f"Committed {committed:.2f} of {ev['feeder_target_kw']:.2f} kW. Event confirmed.", []


# --------------------------------------------------------------- settlement
def settle_event(event_id: str, mv: pd.DataFrame, db_path=None):
    """Apply M&V results. mv columns: customer_id, delivered_kw (verified average reduction).

    A customer's verified reduction is applied to the Iteration 1 commitment first,
    and any remainder to the Iteration 2 commitment.
    """
    mv = mv.copy()
    mv.columns = [c.strip().lower() for c in mv.columns]
    delivered = dict(zip(mv["customer_id"].astype(str), mv["delivered_kw"].astype(float)))
    out = []
    with db.connect(db_path) as con:
        ev = _event(con, event_id)
        reqs = db.rows(con, "SELECT * FROM requests WHERE event_id=? ORDER BY iteration", (event_id,))
        remaining = dict(delivered)
        for r in reqs:
            commit = core.committed_kw(r["target_kw"], r["response"])
            avail = remaining.get(r["customer_id"], 0.0)
            used = min(avail, commit) if r["iteration"] == 1 else avail
            remaining[r["customer_id"]] = avail - used if r["iteration"] == 1 else 0.0
            s = core.settle(r["iteration"], r["target_kw"], r["response"], used, r["duration_h"])
            con.execute("UPDATE requests SET delivered_kw=?, incentive_idr=?, penalty_idr=? WHERE token=?",
                        (s.delivered_kw, s.incentive_idr, s.penalty_idr, r["token"]))
            r.update(delivered_kw=s.delivered_kw, incentive_idr=s.incentive_idr, penalty_idr=s.penalty_idr)
            if commit > 0:
                subject, html = mailer.settlement_email(r, ev)
                mailer.send(r["email"], subject, html)
            out.append({"customer": r["customer_name"], "iteration": r["iteration"], "response": r["response"],
                        "committed_kw": round(commit, 2), "delivered_kw": round(s.delivered_kw, 2),
                        "incentive_idr": s.incentive_idr, "penalty_idr": s.penalty_idr})
        con.execute("UPDATE events SET status='SETTLED' WHERE event_id=?", (event_id,))
    return pd.DataFrame(out)


# ------------------------------------------------------------- bill impact
def bill_summary(customer_id: str, customers: pd.DataFrame, db_path=None) -> pd.DataFrame:
    """Monthly bill: B_net = B - sum(incentive) + sum(penalty)   (paper, Eq. 7)."""
    base = float(customers.loc[customers["customer_id"].astype(str) == str(customer_id), "monthly_bill_idr"].iloc[0])
    with db.connect(db_path) as con:
        r = db.rows(con, "SELECT substr(e.event_start,1,7) AS month, "
                         "COALESCE(SUM(q.incentive_idr),0) AS incentive, COALESCE(SUM(q.penalty_idr),0) AS penalty, "
                         "COUNT(DISTINCT q.event_id) AS events "
                         "FROM requests q JOIN events e USING(event_id) "
                         "WHERE q.customer_id=? AND e.status='SETTLED' GROUP BY month ORDER BY month DESC",
                    (str(customer_id),))
    df = pd.DataFrame(r, columns=["month", "incentive", "penalty", "events"])
    if df.empty:
        return df
    df["regular_bill"] = base
    df["net_bill"] = df["regular_bill"] - df["incentive"] + df["penalty"]
    return df[["month", "events", "regular_bill", "incentive", "penalty", "net_bill"]]
