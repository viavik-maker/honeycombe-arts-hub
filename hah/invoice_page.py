"""The printable invoice page (families at /account/invoices/<number>, staff
at /admin/invoices/<number>). "Print / save as PDF" uses the browser's print
dialog — no PDF library needed."""
import datetime

from . import booking_settings, catalogue, money
from .markup import html as esc
from .web import csp, csp_header

STATUS = {"issued": "Unpaid", "part_paid": "Part paid", "paid": "Paid", "void": "Void", "credited": "Credited"}


def _d(iso):
    return datetime.date.fromisoformat(iso[:10]).strftime("%-d %B %Y")


def _uk_day(at):
    """A UTC *_at instant → its UK date (a payment at 00:30 BST is that day, not the day before)."""
    return catalogue.parse_utc(at).astimezone(catalogue.UK).strftime("%-d %B %Y")


def render(c, inv, *, can_pay=False, nonce="", staff=False):
    s = booking_settings.get_all(c)
    data = money.invoice_json(c, inv)
    bal = data["balance_pence"]
    rows = "".join(
        "<tr><td>%s</td><td>%s</td><td>%s%s</td><td class=num>%s</td></tr>" % (
            esc(_d(l["service_date"])) if l["service_date"] else "", esc(l["participant_name"] or ""),
            esc(l["description"]), " <em>(%s)</em>" % esc(l["funding_note"]) if l["funding_note"] else "",
            esc(money.pounds(l["amount_pence"]))) for l in data["lines"])
    credits = "".join("<tr><td colspan=3>Credit note %s — %s</td><td class=num>−%s</td></tr>" % (
        esc(cn["number"]), esc(cn["reason"]), esc(money.pounds(cn["total_pence"]))) for cn in data["credit_notes"])
    pays = "".join("<tr><td colspan=3>Paid: %s, %s</td><td class=num>−%s</td></tr>" % (
        esc(p["method"]), esc(_uk_day(p["received_at"])), esc(money.pounds(p["amount_pence"]))) for p in data["payments"])
    bank = ""
    if s["bank_account_number"]:
        bank = "<p><strong>Bank transfer:</strong> %s · sort code %s · account %s · reference <strong>%s</strong></p>" % (
            esc(s["bank_name"]), esc(s["bank_sort_code"]), esc(s["bank_account_number"]), esc(inv["number"]))
    pay_btn = ('<button type="button" id="payBtn" class="btn">Pay %s by card</button>' % esc(money.pounds(bal))
               if can_pay and bal > 0 and not staff else "")
    back = "/admin/" if staff else "/account/bookings"
    ids = " · ".join(x for x in (("Registered charity %s" % s["charity_number"]) if s["charity_number"] else "",
                                  ("Ofsted URN %s" % s["ofsted_urn"]) if s["ofsted_urn"] else "") if x)
    return """<!DOCTYPE html><html lang="en-GB"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex">
<title>Invoice %(number)s — %(issuer)s</title>
<style>
body{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;color:#222;max-width:820px;margin:24px auto;padding:0 16px;line-height:1.45}
header{display:flex;justify-content:space-between;gap:24px;flex-wrap:wrap;border-bottom:3px solid #f5a300;padding-bottom:12px}
h1{margin:0 0 4px;font-size:1.6rem}.muted{color:#555;font-size:.92rem}
table{width:100%%;border-collapse:collapse;margin:18px 0}th,td{text-align:left;padding:8px 6px;border-bottom:1px solid #ddd;vertical-align:top}
.num{text-align:right;white-space:nowrap}tfoot td{font-weight:600;border-bottom:none}
.status{display:inline-block;padding:2px 10px;border-radius:99px;background:#eee;font-weight:600}
.status--paid{background:#dff3e3;color:#1b5e20}.status--issued,.status--part_paid{background:#fff3cd;color:#7a5200}
.btn{background:#c75a00;color:#fff;border:0;border-radius:8px;padding:10px 18px;font-size:1rem;cursor:pointer}
.btn--ghost{background:#fff;color:#222;border:2px solid #222}.actions{display:flex;gap:10px;flex-wrap:wrap;margin:16px 0}
@media print{.actions{display:none}body{margin:0}}
</style></head><body>
<div class="actions"><a class="btn btn--ghost" href="%(back)s">← Back</a>
<button type="button" class="btn btn--ghost" id="printBtn">Print / save as PDF</button>%(pay_btn)s</div>
<header><div><h1>Invoice %(number)s</h1>
<span class="status status--%(status_key)s">%(status)s</span></div>
<div class="muted"><strong>%(issuer)s</strong><br>%(address)s<br>%(ids)s</div></header>
<p><strong>Bill to:</strong> %(bill_to)s%(bill_addr)s<br>
<strong>Issued:</strong> %(issued)s · <strong>Due:</strong> %(due)s</p>
<table><thead><tr><th>Date</th><th>Who</th><th>Description</th><th class=num>Amount</th></tr></thead>
<tbody>%(rows)s</tbody>
<tfoot><tr><td colspan=3>Total</td><td class=num>%(total)s</td></tr>%(credits)s%(pays)s
<tr><td colspan=3>Balance to pay</td><td class=num>%(balance)s</td></tr></tfoot></table>
<p class="muted">You can pay by card from your account, by bank transfer, or with childcare vouchers
(Edenred, Computershare, Kiddivouchers, Sodexo/Pluxee, Care 4) or Tax-Free Childcare — please quote
<strong>%(number)s</strong> as the reference.</p>%(bank)s
<p class="muted">%(footer)s</p>
<script nonce="%(nonce)s">
document.getElementById("printBtn").onclick=function(){window.print()};
var pb=document.getElementById("payBtn");
if(pb){pb.onclick=async function(){pb.disabled=true;pb.textContent="Please wait…";
 try{var me=await (await fetch("/api/account/me")).json();
  var r=await fetch("/api/account/invoices/%(number)s/pay",{method:"POST",headers:{"Content-Type":"application/json","X-CSRF-Token":me.csrf},body:"{}"});
  var d=await r.json(); if(d.redirect){location.assign(d.redirect);return}
  alert(d.message||d.error||"Something went wrong");location.reload();
 }catch(e){alert("We couldn't reach the website — please try again.");pb.disabled=false}}}
</script></body></html>""" % {
        "number": esc(inv["number"]), "issuer": esc(s["issuer_name"]),
        "address": esc(s["issuer_address"]).replace("\n", "<br>"), "ids": esc(ids), "bill_to": esc(inv["bill_to_name"]),
        "bill_addr": ("<br>" + esc(inv["bill_to_address"])) if inv["bill_to_address"] else "",
        "issued": esc(_d(inv["issue_date"])), "due": esc(_d(inv["due_date"])), "rows": rows, "credits": credits,
        "pays": pays, "total": esc(money.pounds(inv["total_pence"])), "balance": esc(money.pounds(max(bal, 0))),
        "status": esc(STATUS.get(inv["status"], inv["status"])), "status_key": esc(inv["status"]), "bank": bank,
        "footer": esc(s["invoice_footer"]), "pay_btn": pay_btn, "back": back, "nonce": esc(nonce)}


def send(h, page):
    nonce = page.split('<script nonce="', 1)[1].split('"', 1)[0]
    return h.send(200, page.encode(), "text/html; charset=utf-8",
                  {"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow", csp_header(): csp(nonce)})
