"""Fonti online aperte per BioIQ: News Salumifici/Stagionatura + Community Lombardia & Piemonte."""
import datetime as dt
import html
import json
import re
import time
import urllib.request
from email.utils import parsedate_to_datetime
from urllib.parse import quote
import xml.etree.ElementTree as ET

USER_AGENT = "BioIQ-LeadFinder/1.2 (biopolis-stagionatura-zero-residui)"

PATTERNS = {
    "Stagionatura Prosciutti & Salumifici (Zero Residui)": (
        r"\b(?:stagionatur\w*|prosciutt\w*|salumific\w*|salum\w*|insaccat\w*|"
        r"tyrophagus|piophila|dermestes|acaro del prosciutto|mosca del prosciutto|"
        r"zero residui|senza residui|derrate alimentari)\b"
    ),
    "HACCP & GDO / Industria Alimentare": (
        r"\b(?:haccp|pest control|monitoraggio infestanti|gdo|grande distribuzione|"
        r"supermercat\w*|magazzin\w*|capannon\w*|laboratorio alimentare|ristorante|bar|hotel|logistica|caseific\w*)\b"
    ),
    "Disinfestazione & Derattizzazione": (
        r"\b(?:disinfestazion\w*|derattizzazion\w*|deblattizzazion\w*|top[oi]|ratt[oi]|"
        r"blatte|scarafagg\w*|cimic\w*|formich\w*|zanzar\w*|vesp\w*|calabron\w*|tarl\w*|pulc\w*|zecc\w*)\b"
    ),
    "Sanificazione & Igiene": r"\b(?:sanificazion\w*|disinfezion\w*|igienizzazion\w*|legionella|muffa|bonifica)\b",
    "Allontanamento Volatili": r"\b(?:piccion\w*|volatil\w*|guano|dissuasor\w*|gabbian\w*)\b",
}

LOMBARDIA_RE = re.compile(
    r"\b(?:lombardia|milano|milan|monza|brianza|bergamo|brescia|como|varese|pavia|cremona|mantova|lecco|lodi|sondrio)\b",
    re.I,
)
PIEMONTE_RE = re.compile(
    r"\b(?:piemonte|torino|turin|novara|alessandria|asti|cuneo|biella|vercelli|verbania|verbano)\b",
    re.I,
)
DISTRETTO_PROSCIUTTI_RE = re.compile(
    r"\b(?:parma|langhirano|felino|san daniele|friuli|emilia|modena|reggio emilia)\b",
    re.I,
)
CONDOMINIO_PRIVATO_RE = re.compile(
    r"\b(?:condomini\w*|amministratore|appartament\w*|casa|cantina|garage|cortile|vicin\w*|inquilin\w*)\b",
    re.I,
)
AZIENDA_GDO_RE = re.compile(
    r"\b(?:aziend\w*|pmi|negozi\w*|local\w*|ristorant\w*|bar|supermercat\w*|gdo|magazzin\w*|capannon\w*|haccp|uffici\w*|prosciutt\w*|salum\w*)\b",
    re.I,
)


def compact(s):
    return " ".join(html.unescape(s or "").split())


def fetch_bytes(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return res.read(2_500_000)


def detect_area_and_segment(text, default_area="Altra area Italia"):
    has_lom = bool(LOMBARDIA_RE.search(text))
    has_pie = bool(PIEMONTE_RE.search(text))
    has_dist = bool(DISTRETTO_PROSCIUTTI_RE.search(text))

    if has_lom and has_pie:
        area = "Lombardia & Piemonte"
    elif has_lom:
        area = "Lombardia"
    elif has_pie:
        area = "Piemonte"
    elif has_dist:
        area = "Distretto Salumi / Prosciutti"
    else:
        area = default_area

    if re.search(r"\b(?:stagionatur\w*|prosciutt\w*|salumific\w*|salum\w*)\b", text, re.I):
        segmento = "Salumificio / Centro Stagionatura"
    elif AZIENDA_GDO_RE.search(text):
        segmento = "PMI / Attività / GDO"
    elif CONDOMINIO_PRIVATO_RE.search(text):
        segmento = "Condominio / Privato"
    else:
        segmento = "Privato / Da qualificare"

    return area, segmento


def classify_text(title, description, default_area="Altra area Italia", allow_national_salumi=True):
    full = f"{title} {description}"
    area, segmento = detect_area_and_segment(full, default_area=default_area)

    matches = []
    for category, pattern in PATTERNS.items():
        t_hits = list(re.finditer(pattern, title, re.I))
        b_hits = list(re.finditer(pattern, description, re.I))
        terms = list(dict.fromkeys(m.group().lower() for m in t_hits + b_hits))
        if terms:
            score = 50 + 20 * bool(t_hits) + 5 * min(len(terms), 5)
            if category == "Stagionatura Prosciutti & Salumifici (Zero Residui)":
                score += 20
            matches.append((score, category, terms))

    if not matches:
        return None

    matches.sort(key=lambda v: -v[0])
    top_cat = matches[0][1]

    if area == "Altra area Italia":
        if not (allow_national_salumi and top_cat == "Stagionatura Prosciutti & Salumifici (Zero Residui)"):
            return None

    return area, segmento, matches


REDDIT_QUERIES = [
    ("italy", "prosciuttificio OR salumificio OR stagionatura OR tyrophagus OR dermestes OR (acari prosciutto)", "Altra area Italia"),
    ("milano", "topi OR ratti OR blatte OR scarafaggi OR cimici OR disinfestazione OR condominio OR piccioni", "Lombardia"),
    ("Lombardia", "disinfestazione OR derattizzazione OR topi OR blatte OR cimici OR salumificio OR stagionatura", "Lombardia"),
    ("torino", "topi OR ratti OR blatte OR scarafaggi OR cimici OR disinfestazione OR magazzino OR piccioni", "Piemonte"),
    ("Piemonte", "disinfestazione OR derattizzazione OR topi OR blatte OR haccp OR magazzino OR aziendale", "Piemonte"),
]


def fetch_reddit():
    out = []
    read_count = 0
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=21)

    for idx, (sub, query, default_area) in enumerate(REDDIT_QUERIES):
        if idx > 0:
            time.sleep(1.5)
        url = f"https://www.reddit.com/r/{sub}/search.json?q={quote(query)}&restrict_sr=1&sort=new&t=month&limit=25"
        try:
            data = json.loads(fetch_bytes(url).decode("utf-8"))
        except Exception:
            continue

        posts = data.get("data", {}).get("children", [])
        read_count += len(posts)

        for p in posts:
            d = p.get("data", {})
            created = dt.datetime.fromtimestamp(d.get("created_utc", 0), tz=dt.timezone.utc)
            if created < cutoff:
                continue

            title = compact(d.get("title", ""))
            body = compact(d.get("selftext", ""))
            permalink = "https://www.reddit.com" + d.get("permalink", "")

            res = classify_text(title, body, default_area=default_area)
            if not res:
                continue

            area, segmento, hits = res
            out.append({
                "id": permalink,
                "url": permalink,
                "titolo": title,
                "ente": f"Segnalazione r/{sub}",
                "fonte": f"Reddit r/{sub}",
                "canale": "online",
                "area": area,
                "citta": area,
                "segmento": segmento,
                "servizio": hits[0][1],
                "altri": [x[1] for x in hits[1:]],
                "punteggio": min(100, hits[0][0]),
                "motivo": f"{segmento} · " + ", ".join(hits[0][2][:4]),
                "estratto": body[:400] or title,
                "valore": "",
                "tipo": segmento,
                "stato": "aperta",
                "pubblicazione": created.date().isoformat(),
                "scadenza": None,
            })

    return read_count, out


NEWS_QUERIES = [
    '"prosciuttificio" OR "stagionatura prosciutti" (stabilimento OR ampliamento OR investimento OR export OR qualità)',
    '"salumificio" (stabilimento OR nuovo impianto OR BRC OR IFS OR HACCP OR disinfestazione)',
    '(disinfestazione OR derattizzazione) (Lombardia OR Piemonte OR condomini OR GDO)',
]


def fetch_salumi_news():
    out = []
    read_count = 0
    cutoff = dt.date.today() - dt.timedelta(days=21)

    for idx, q in enumerate(NEWS_QUERIES):
        if idx > 0:
            time.sleep(1.0)
        rss_url = f"https://news.google.com/rss/search?q={quote(q)}&hl=it&gl=IT&ceid=IT:it"
        try:
            xml_bytes = fetch_bytes(rss_url)
            items = ET.fromstring(xml_bytes).findall("./channel/item")
        except Exception:
            continue

        read_count += len(items)
        for item in items[:25]:
            title = compact(item.findtext("title"))
            link = compact(item.findtext("link"))
            source_name = compact(item.findtext("source")) or "Google News"
            raw_desc = re.sub(r"<[^>]+>", " ", item.findtext("description") or "")
            desc = compact(raw_desc)

            try:
                pub_date = parsedate_to_datetime(item.findtext("pubDate") or "").date()
            except (ValueError, TypeError):
                pub_date = dt.date.today()

            if pub_date < cutoff or not link:
                continue

            res = classify_text(title, desc, default_area="Altra area Italia")
            if not res:
                continue

            area, segmento, hits = res
            out.append({
                "id": link,
                "url": link,
                "titolo": title,
                "ente": source_name,
                "fonte": f"News · {source_name}",
                "canale": "online",
                "area": area,
                "citta": area,
                "segmento": segmento,
                "servizio": hits[0][1],
                "altri": [x[1] for x in hits[1:]],
                "punteggio": min(100, hits[0][0]),
                "motivo": f"Segnale settore · " + ", ".join(hits[0][2][:4]),
                "estratto": desc[:400] or title,
                "valore": "",
                "tipo": "Notizia / Segnale aziendale",
                "stato": "aperta",
                "pubblicazione": pub_date.isoformat(),
                "scadenza": None,
            })

    return read_count, out


def fetch_all_online():
    all_projects = []
    logs = []
    seen_urls = set()

    for name, fn in [
        ("News Salumifici & Settore", fetch_salumi_news),
        ("Reddit & Community", fetch_reddit),
    ]:
        try:
            read, items = fn()
            added = 0
            for it in items:
                if it["url"] not in seen_urls:
                    seen_urls.add(it["url"])
                    all_projects.append(it)
                    added += 1
            logs.