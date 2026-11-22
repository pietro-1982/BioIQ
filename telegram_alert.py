"""Invio notifiche Telegram per BioIQ (Biopolis)."""
import html
import json
import os
import urllib.request


def send_message(token, chat_id, text):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=25) as resp:
        return json.loads(resp.read().decode("utf-8"))


def notify_top_leads(items, max_items=8, min_score=55):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return {"attiva": False, "motivo": "Secret Telegram non configurati"}

    top = [
        it for it in items
        if it.get("stato") != "scaduta" and it.get("punteggio", 0) >= min_score
    ][:max_items]

    if not top:
        return {"attiva": True, "inviati": 0, "motivo": "Nessun nuovo lead sopra soglia oggi"}

    lines = [f"<b>🛡️ BioIQ · Biopolis — {len(top)} nuove opportunità</b>\n"]
    for idx, it in enumerate(top, 1):
        titolo = html.escape(it.get("titolo", "")[:120])
        area = html.escape(it.get("area", ""))
        servizio = html.escape(it.get("servizio", ""))
        ente = html.escape(it.get("ente", "")[:70])
        motivo = html.escape(it.get("motivo", "")[:140])
        url = it.get("url", "")
        scad = f" · Scad. {it['scadenza']}" if it.get("scadenza") else ""

        lines.append(
            f"<b>{idx}. [{area}] {servizio}</b> ({it.get('punteggio', 0)}/100)\n"
            f"• <b>{titolo}</b>\n"
            f"• <i>{ente}</i>{scad}\n"
            f"• {motivo}\n"
            f"👉 <a href=\"{url}\">Apri dettaglio</a>\n"
        )

    msg = "\n".join(lines)
    try:
        res = send_message(token, chat_id, msg[:4000])
        return {"attiva": True, "inviati": len(top), "ok": res.get("ok", False)}
    except Exception as e:
        return {"attiva": True, "inviati": 0, "errore": str(e)}