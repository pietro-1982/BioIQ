"""BioIQ LeadFinder - motore automatico per Biopolis: Stagionatura Prosciutti (Zero Residui) + Lombardia & Piemonte."""
import datetime
import json
import os
import re
import urllib.request
from online import fetch_all_online
from ai_classifier import refine_with_ai
from telegram_alert import notify_top_leads

API = "https://api.ted.europa.eu/v3/notices/search"
SEEN_PATH = "docs/seen_leads.json"

SERVIZI = {
    "Stagionatura Prosciutti & Salumifici (Zero Residui)": {
        "cpv": ["90921000", "90922000", "90923000", "90920000"],
        "kw": [
            "stagionatura", "prosciutt", "salumific", "salumi", "insaccati",
            "carni stagionate", "acaro", "tyrophagus", "piophila", "dermestes",
            "derrate alimentari", "zero residui", "senza residui", "brc", "ifs",
        ],
    },
    "HACCP & GDO / Industria Alimentare": {
        "cpv": ["90921000", "90923000", "90900000"],
        "kw": [
            "haccp", "pest control", "monitoraggio infestanti", "grande distribuzione",
            "supermercat", "alimentar", "stabilimento", "logistica", "magazzino", "caseific",
        ],
    },
    "Disinfestazione & Derattizzazione": {
        "cpv": ["90920000", "90921000", "90922000", "90923000", "90924000"],
        "kw": ["disinfestazione", "derattizzazione", "deblattizzazione", "roditori", "blatte", "scarafaggi", "cimici", "zanzare"],
    },
    "Sanificazione & Igiene": {
        "cpv": ["90921000", "90910000", "90911000"],
        "kw": ["sanificazione", "disinfezione", "igienizzazione", "bonifica ambientale", "legionella"],
    },
    "Allontanamento Volatili": {
        "cpv": ["90922000", "90920000"],
        "kw": ["volatili", "piccioni", "dissuasori", "guano", "allontanamento uccelli"],
    },
}

CPV_ALL = sorted({c for s in SERVIZI.values() for c in s["cpv"]})
FIELDS = [
    "publication-number", "notice-title", "buyer-name", "publication-date",
    "deadline-receipt-tender-date-lot", "classification-cpv", "notice-type",
    "buyer-city", "estimated-value-lot", "description-lot",
]

LOMBARDIA_RE = re.compile(
    r"\b(?:lombardia|milano|monza|brianza|bergamo|brescia|como|varese|pavia|cremona|mantova|lecco|lodi|sondrio)\b",
    re.I,
)
PIEMONTE_RE = re.compile(
    r"\b(?:piemonte|torino|novara|alessandria|asti|cuneo|biella|vercelli|verbania|verbano|cusio|ossola)\b",
    re.I,
)
DISTRETTO_PROSCIUTTI_RE = re.compile(
    r"\b(?:parma|langhirano|felino|sala baganza|lesignano|san daniele|friuli|emilia|modena|reggio emilia)\b",
    re.I,
)


def txt(v):
    if isinstance(v, dict):
        for k in ("ita", "eng"):
            if k in v:
                return " ".join(v[k]) if isinstance(v[k], list) else str(v[k])
        return " ".join(" ".join(x) if isinstance(x, list) else str(x) for x in v.values())
    if isinstance(v, list):
        return " ".join(map(str, v))
    return str(v or "")


def detect_area(text):
    has_lom = bool(LOMBARDIA_RE.search(text))
    has_pie = bool(PIEMONTE_RE.search(text))
    has_dist = bool(DISTRETTO_PROSCIUTTI_RE.search(text))
    if has_lom and has_pie:
        return "Lombardia & Piemonte"
    if has_lom:
        return "Lombardia"
    if has_pie:
        return "Piemonte"
    if has_dist:
        return "Distretto Salumi / Prosciutti"
    return "Altra area Italia"


def load_seen():
    if not os.path.exists(SEEN_PATH):
        return set()
    try:
        with open(SEEN_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return set(data if isinstance(data, list) else [])
    except Exception:
        return set()


def save_seen(seen_ids):
    os.makedirs("docs", exist_ok=True)
    with open(SEEN_PATH, "w", encoding="utf-8") as f:
        json.dump(sorted(seen_ids), f, ensure_ascii=False, indent=1)


def fetch_ted(days=30):
    since = (datetime.date.today() - datetime.timedelta(days=days)).strftime("%Y%m%d")
    q = (
        f"buyer-country=ITA AND classification-cpv IN ({' '.join(CPV_ALL)}) "
        f"AND publication-date>={since} AND notice-type IN (cn-standard cn-social pin-only)"
    )
    out = []
    page = 1
    while page <= 10:
        body = json.dumps({"query": q, "fields": FIELDS, "limit": 100, "page": page}).encode()
        r = urllib.request.Request(API, body, {"Content-Type": "application/json"})
        d = json.load(urllib.request.urlopen(r, timeout=60))
        n = d.get("notices", [])
        out += n
        if len(n) < 100:
            break
        page += 1
    return out


def classify_ted(n):
    title = txt(n.get("notice-title"))
    desc = txt(n.get("description-lot"))[:1500]
    ente = txt(n.get("buyer-name"))
    citta = txt(n.get("buyer-city"))
    full = f" {title} {desc} {ente} {citta} ".lower()
    cpvs = set(n.get("classification-cpv", []))

    if any(w in title.lower() for w in ["rifiuti urbani", "raccolta differenziata", "spazzamento strade", "lavanderia"]):
        return None

    area = detect_area(full)

    hits = []
    for s, r in SERVIZI.items():
        score = 0
        why = []
        if cpvs & set(r["cpv"]):
            score += 45
            why.append("CPV " + ",".join(sorted(cpvs & set(r["cpv"]))))
        kws = [k.strip() for k in r["kw"] if k in full]
        if kws:
            score += 25 + 10 * min(len(kws), 3)
            why.append("parole: " + ", ".join(kws))
        if score:
            if s == "Stagionatura Prosciutti & Salumifici (Zero Residui)":
                score += 25
            if area in ("Lombardia", "Piemonte", "Lombardia & Piemonte", "Distretto Salumi / Prosciutti"):
                score += 15
            hits.append((score, s, "; ".join(why)))

    if not hits:
        return None
    hits.sort(reverse=True)

    dl = [x[:10] for x in n.get("deadline-receipt-tender-date-lot", []) if x]
    dl = min(dl) if dl else None
    today = datetime.date.today().isoformat()
    stato = "scaduta" if dl and dl < today else "aperta" if dl else "scadenza non indicata"
    pn = n["publication-number"]

    return {
        "id": pn,
        "titolo": title,
        "ente": ente,
        "citta": citta or area,
        "area": area,
        "segmento": "Ente / Consorzio / Gara",
        "canale": "ted",
        "pubblicazione": txt(n.get("publication-date"))[:10],
        "scadenza": dl,
        "stato": stato,
        "tipo": "Avviso preliminare" if n.get("notice-type") == "pin-only" else "Bando di gara",
        "servizio": hits[0][1],
        "altri": [h[1] for h in hits[1:]],
        "punteggio": min(100, hits[0][0]),
        "motivo": hits[0][2],
        "estratto": desc[:400],
        "valore": txt(n.get("estimated-value-lot"))[:40],
        "url": f"https://ted.europa.eu/it/notice/-/detail/{pn}",
        "fonte": "TED – appalti pubblici UE",
    }


def main():
    log = {
        "eseguito": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "fonti": [],
    }
    try:
        raw = fetch_ted()
        log["fonti"].append({"fonte": "TED", "esito": "ok", "avvisi_letti": len(raw)})
    except Exception as e:
        raw = []
        log["fonti"].append({"fonte": "TED", "esito": "ERRORE", "dettaglio": str(e)})

    seen_ted = {}
    for n in raw:
        c = classify_ted(n)
        k = (c["titolo"], c["ente"]) if c else None
        if c and k not in seen_ted:
            seen_ted[k] = c

    ted_items = list(seen_ted.values())
    online_logs, online_items = fetch_all_online()
    log["fonti"].extend(online_logs)

    all_items = ted_items + online_items

    # Deduplicazione rispetto alle scansioni precedenti
    seen_history = load_seen()
    new_items = [it for it in all_items if it["id"] not in seen_history]
    log["gia_visti_filtrati"] = len(all_items) - len(new_items)

    # Classificazione AI con Gemini solo sui nuovi lead
    new_items, ai_log = refine_with_ai(new_items)
    log["ai"] = ai_log

    # Ordinamento: prima servizio punta stagionatura, poi Lombardia/Piemonte, poi punteggio
    new_items.sort(
        key=lambda x: (
            x.get("stato") == "scaduta",
            x.get("servizio") != "Stagionatura Prosciutti & Salumifici (Zero Residui)",
            x.get("area") == "Altra area Italia",
            -x.get("punteggio", 0),
            x.get("scadenza") or "9999",
        )
    )

    log["classificate_nuove"] = len(new_items)

    for it in new_items:
        seen_history.add(it["id"])
    save_seen(seen_history)

    log["telegram"] = notify_top_leads(new_items)

    os.makedirs("docs", exist_ok=True)
    with open("docs/data.json.tmp", "w", encoding="utf-8") as stream:
        json.dump({"log": log, "opportunita": new_items}, stream, ensure_ascii=False, indent=1)
    os.replace("docs/data.json.tmp", "docs/data.json")
    print(json.dumps(log, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()