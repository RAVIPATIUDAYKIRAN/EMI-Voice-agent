"""Authenticated, read-only dashboard for operational call outcomes."""

import hmac

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import Integer, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.db.database import get_db
from app.models import Call, Customer, EMI

router = APIRouter(tags=["Dashboard"])

_DASHBOARD_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>EMI Voice Agent Dashboard</title><style>
body{font:16px system-ui,sans-serif;margin:2rem;color:#182230;background:#f4f7fb}
main{max-width:1100px;margin:auto}input,button{font:inherit;padding:.55rem;border:1px solid #9aa7b5;border-radius:.35rem}
button{cursor:pointer;background:#164e8a;color:white}table{width:100%;border-collapse:collapse;background:white;margin-top:1rem}
th,td{text-align:left;padding:.6rem;border-bottom:1px solid #d9e0e8}#error{color:#a21b1b}
</style></head><body><main><h1>EMI Voice Agent</h1>
<p>Operational call status and payment commitment overview.</p>
<form id="auth"><label>Dashboard key <input id="key" type="password" autocomplete="current-password" required></label>
<button>Load dashboard</button></form><p id="error" role="alert"></p><section id="content"></section>
<script>
document.getElementById('auth').addEventListener('submit',async e=>{e.preventDefault();
const key=document.getElementById('key').value; const out=document.getElementById('content');
const err=document.getElementById('error'); err.textContent='';
try { const r=await fetch('/dashboard/data',{headers:{'X-Dashboard-Key':key}});
if(!r.ok) throw new Error(r.status===401?'Dashboard key is not valid.':'Dashboard is unavailable.');
const d=await r.json(); out.replaceChildren();
const totals=document.createElement('p'); totals.textContent=`Total ${d.total_calls} · Confirmed ${d.confirmed_commitments} · Open ${d.open_calls}`; out.append(totals);
const table=document.createElement('table'), head=document.createElement('thead'), body=document.createElement('tbody');
const labels=['Call','Customer','EMI','Amount','Status','Commitment','Promised date','Pending date','Outcome','Summary'];
const header=document.createElement('tr'); labels.forEach(v=>{const th=document.createElement('th');th.textContent=v;header.append(th)}); head.append(header);
d.calls.forEach(c=>{const tr=document.createElement('tr'); [c.call_id,c.customer_id,c.emi_id,c.emi_amount.toFixed(2),c.call_status,c.payment_commitment_confirmed?'Confirmed':'Not confirmed',c.promised_payment_date||'—',c.pending_payment_date||'—',c.reason||'—',c.call_summary||'—'].forEach(v=>{const td=document.createElement('td');td.textContent=String(v);tr.append(td)});body.append(tr)});
table.append(head,body); out.append(table);
} catch(e) { err.textContent=e.message; out.replaceChildren(); }});
</script></main></body></html>"""


@router.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
async def dashboard_page() -> HTMLResponse:
    return HTMLResponse(_DASHBOARD_HTML)


@router.get("/dashboard/data")
async def dashboard_data(
    x_dashboard_key: str | None = Header(default=None, alias="X-Dashboard-Key"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    configured_key = (
        settings.dashboard_api_key.get_secret_value()
        if settings.dashboard_api_key
        else ""
    )
    if len(configured_key) < 32:
        raise HTTPException(
            status_code=503,
            detail="Set DASHBOARD_API_KEY to a secret of at least 32 characters before accessing dashboard data",
        )
    if not x_dashboard_key or not hmac.compare_digest(x_dashboard_key, configured_key):
        raise HTTPException(status_code=401, detail="Invalid dashboard key")

    result = await db.execute(
        select(
            Call.call_id,
            Call.customer_id,
            Call.emi_id,
            EMI.emi_amount,
            Call.call_status,
            Call.payment_commitment_confirmed,
            Call.promised_payment_date,
            Call.pending_payment_date,
            Call.reason,
            Call.call_summary,
        )
        .join(Customer, Customer.customer_id == Call.customer_id)
        .join(EMI, EMI.emi_id == Call.emi_id)
        .where(EMI.customer_id == Call.customer_id)
        .order_by(Call.started_at.desc(), Call.call_id.desc())
        .limit(100)
    )
    calls = [
        {
            "call_id": row.call_id,
            "customer_id": row.customer_id,
            "emi_id": row.emi_id,
            "emi_amount": float(row.emi_amount),
            "call_status": row.call_status,
            "payment_commitment_confirmed": row.payment_commitment_confirmed,
            "promised_payment_date": (
                row.promised_payment_date.isoformat()
                if row.promised_payment_date
                else None
            ),
            "pending_payment_date": (
                row.pending_payment_date.isoformat()
                if row.pending_payment_date
                else None
            ),
            "reason": row.reason,
            "call_summary": row.call_summary,
        }
        for row in result.all()
    ]
    counts = await db.execute(
        select(
            func.count(Call.call_id),
            func.sum(Call.payment_commitment_confirmed.cast(Integer)),
            func.sum((Call.call_status == "INITIATED").cast(Integer)),
        )
        .join(Customer, Customer.customer_id == Call.customer_id)
        .join(EMI, EMI.emi_id == Call.emi_id)
        .where(EMI.customer_id == Call.customer_id)
    )
    total_calls, confirmed_commitments, open_calls = counts.one()
    return {
        "total_calls": int(total_calls or 0),
        "confirmed_commitments": int(confirmed_commitments or 0),
        "open_calls": int(open_calls or 0),
        "calls_returned": len(calls),
        "calls": calls,
    }
