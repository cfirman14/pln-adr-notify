"""Build and deliver ADR notification e-mails.

EMAIL_MODE="file" writes each message to ./outbox (safe default for testing);
EMAIL_MODE="smtp" sends it through the configured SMTP server.
"""
import os
import smtplib
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

import config


def rupiah(x: float) -> str:
    return "Rp " + f"{x:,.0f}".replace(",", ".")


def customer_link(token: str) -> str:
    return f"{config.APP_BASE_URL}/{config.CUSTOMER_PAGE}?token={token}"


_STYLE = """
body{font-family:Arial,Helvetica,sans-serif;background:#f3f5f8;margin:0;padding:24px;color:#1f2a44}
.card{max-width:560px;margin:auto;background:#fff;border-radius:10px;overflow:hidden;border:1px solid #dde2ea}
.head{background:#1f2a44;color:#fff;padding:18px 24px}
.head small{color:#e0a106;font-weight:bold;letter-spacing:.5px}
.body{padding:20px 24px;font-size:14px;line-height:1.5}
table{width:100%;border-collapse:collapse;margin:12px 0}
td{padding:7px 4px;border-bottom:1px solid #eef1f5}
td.v{text-align:right;font-weight:bold}
.inc{background:#e8f6f3;border-left:4px solid #0f7c8c;padding:10px 12px;margin:12px 0}
.pen{background:#fdf0ef;border-left:4px solid #c0392b;padding:10px 12px;margin:12px 0}
.btn{display:inline-block;background:#0f7c8c;color:#fff !important;text-decoration:none;
     padding:12px 22px;border-radius:6px;font-weight:bold}
.foot{font-size:11px;color:#6b7280;padding:12px 24px 20px}
"""


def _wrap(kicker, title, inner):
    return f"""<html><head><style>{_STYLE}</style></head><body><div class="card">
<div class="head"><small>{kicker}</small><div style="font-size:18px;font-weight:bold">{title}</div></div>
<div class="body">{inner}</div>
<div class="foot">PLN Automated Demand Response - Proof of Concept. This is a test message sent to a test mailbox.</div>
</div></body></html>"""


def request_email(req: dict, event: dict):
    """Iteration 1 or 2 curtailment request. Incentive-first framing (paper, Sec. II-E)."""
    kwh = req["target_kw"] * req["duration_h"]
    rows = f"""<table>
<tr><td>Feeder</td><td class="v">{event['feeder']}</td></tr>
<tr><td>Requested reduction</td><td class="v">{req['target_kw']:.2f} kW</td></tr>
<tr><td>Duration</td><td class="v">{req['duration_h']:g} hour(s)</td></tr>
<tr><td>Energy to reduce</td><td class="v">{kwh:.2f} kWh</td></tr>
<tr><td>Event starts</td><td class="v">{event['event_start'][:16].replace('T', ' ')} WIB</td></tr>
<tr><td>Respond before</td><td class="v">{req['deadline'][:16].replace('T', ' ')} WIB</td></tr>
</table>"""
    if req["iteration"] == 1:
        money = (f'<div class="inc">Estimated incentive if you take part: '
                 f'<b>{rupiah(req["est_incentive_idr"])}</b>, credited to your monthly electricity bill.</div>')
        title, subject_tag = "Demand response request", "Request"
    else:
        money = (f'<div class="pen">Follow-up request. If you accept and do not deliver the committed reduction, '
                 f'a penalty of up to <b>{rupiah(req["max_penalty_idr"])}</b> is added to your monthly bill.</div>')
        title, subject_tag = "Follow-up demand response request", "Follow-up request"
    inner = (f"<p>Dear {req['customer_name']},</p>"
             f"<p>The feeder that supplies your site is close to its safe loading limit. "
             f"PLN asks you to reduce your electricity use for a short period.</p>"
             f"{money}{rows}"
             f"<p style='text-align:center;margin:22px 0'><a class='btn' href='{customer_link(req['token'])}'>"
             f"Review and respond</a></p>"
             + (f"<p style='font-size:12px;color:#6b7280'>If we do not receive a response within "
                f"{config.RESPONSE_WINDOW_MIN} minutes, the request is treated as accepted.</p>"
                if req["iteration"] == 1 else
                f"<p style='font-size:12px;color:#6b7280'>This follow-up applies only if you accept it "
                f"within {config.RESPONSE_WINDOW_MIN} minutes.</p>"))
    subject = f"[PLN ADR] {subject_tag}: reduce {req['target_kw']:.2f} kW for {req['duration_h']:g} h"
    return subject, _wrap("PLN DEMAND RESPONSE", title, inner)


def confirmation_email(req: dict, event: dict):
    inner = (f"<p>Dear {req['customer_name']},</p><p>Thank you. Your response has been recorded.</p><table>"
             f"<tr><td>Your response</td><td class='v'>{req['response'].replace('_', ' ').title()}</td></tr>"
             f"<tr><td>Committed reduction</td><td class='v'>{req['committed_kw']:.2f} kW</td></tr>"
             f"<tr><td>Event starts</td><td class='v'>{event['event_start'][:16].replace('T', ' ')} WIB</td></tr>"
             f"<tr><td>Duration</td><td class='v'>{req['duration_h']:g} hour(s)</td></tr></table>"
             f"<p><a href='{customer_link(req['token'])}'>View event status</a></p>")
    return "[PLN ADR] Response recorded", _wrap("PLN DEMAND RESPONSE", "Event confirmation", inner)


def settlement_email(req: dict, event: dict):
    adj = req["penalty_idr"] - req["incentive_idr"]
    effect = (f"<div class='inc'>Bill credit: <b>{rupiah(-adj)}</b></div>" if adj < 0 else
              f"<div class='pen'>Bill charge: <b>{rupiah(adj)}</b></div>" if adj > 0 else
              "<p>No change to your bill for this event.</p>")
    inner = (f"<p>Dear {req['customer_name']},</p><p>The event on feeder {event['feeder']} has been verified.</p>"
             f"<table><tr><td>Verified reduction</td><td class='v'>{req['delivered_kw']:.2f} kW</td></tr>"
             f"<tr><td>Verified energy</td><td class='v'>{req['delivered_kw'] * req['duration_h']:.2f} kWh</td></tr>"
             f"<tr><td>Incentive</td><td class='v'>{rupiah(req['incentive_idr'])}</td></tr>"
             f"<tr><td>Penalty</td><td class='v'>{rupiah(req['penalty_idr'])}</td></tr></table>{effect}"
             f"<p><a href='{customer_link(req['token'])}'>See your monthly bill summary</a></p>")
    return "[PLN ADR] Settlement summary", _wrap("PLN DEMAND RESPONSE", "Settlement summary", inner)


def send(to: str, subject: str, html: str) -> str:
    """Deliver one message. Returns a short description of where it went."""
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = config.SENDER, to, subject
    msg["Date"], msg["Message-ID"] = formatdate(localtime=True), make_msgid()
    msg.set_content("This message requires an HTML-capable e-mail client.")
    msg.add_alternative(html, subtype="html")

    if config.EMAIL_MODE == "smtp":
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=20) as s:
            s.starttls()
            if config.SMTP_USER:
                s.login(config.SMTP_USER, config.SMTP_PASSWORD)
            s.send_message(msg)
        return f"sent to {to}"

    os.makedirs(config.OUTBOX_DIR, exist_ok=True)
    stem = f"{len(os.listdir(config.OUTBOX_DIR)) // 2 + 1:03d}_{to.split('@')[0]}"
    with open(os.path.join(config.OUTBOX_DIR, stem + ".eml"), "wb") as f:
        f.write(bytes(msg))
    with open(os.path.join(config.OUTBOX_DIR, stem + ".html"), "w", encoding="utf-8") as f:
        f.write(html)
    return f"saved outbox/{stem}.html"
