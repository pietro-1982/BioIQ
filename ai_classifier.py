"""Classificatore semantico BioIQ tramite Google Gemini API (zero dipendenze esterne)."""
import json
import os
import time
import urllib.error
import urllib.request

GEMINI_MODEL = "gemini-3.5-flash-lite"
API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

CATEGORIE_AMMESSE = [
    "Stagionatura Prosciutti & Salumifici (Zero Residui)",
    "HACCP & GDO / Industria Alimentare",
    "Disinfestazione & Derattizzazione",
    "Sanificazione & Igiene",
    "Allontanamento Volatili",
    "SCARTA",
]

PROMPT_SISTEMA = """Sei l'analista commerciale opinionated di BioIQ per Biopolis, azienda specializzata in disinfestazione, pest control e sanificazione.
Il nostro SERVIZIO DI PUNTA assoluto sono i centri di stagionatura dei prosciutti crudi, salumifici e stabilimenti alimentari con prodotto esposto: abbiamo una tecnologia/prodotto che NON lascia residui chimici, risolvendo infestazioni critiche (come acaro del prosciutto / Tyrophagus, mosca del prosciutto / Piophila, dermestidi, roditori) senza contaminare i prosciutti né fermare la stagionatura.

Il nostro perimetro commerciale completo è:
1. PRIORITÀ MASSIMA: Centri di stagionatura prosciutti crudi, salumifici, aziende alimentari e ambienti sensibili dove l'assenza di residui chimici è decisiva (in Lombardia, Piemonte e distretti salumieri come Parma/San Daniele).
2. LOMBARDIA: condomini (amministratori, stabili, cantine, cortili), privati ed enti/bandi locali.
3. PIEMONTE e LOMBARDIA: PMI, logistica, magazzini e Grande Distribuzione (GDO) con esigenze di monitoraggio HACCP e pest control continuo.

Analizza la lista JSON ricevuta e per ciascun elemento restituisci un oggetto JSON con:
- "id": lo stesso id ricevuto in input
- "servizio": scegli ESATTAMENTE una voce tra: "Stagionatura Prosciutti & Salumifici (Zero Residui)", "HACCP & GDO / Industria Alimentare", "Disinfestazione & Derattizzazione", "Sanificazione & Igiene", "Allontanamento Volatili", oppure "SCARTA"
- "punteggio": intero da 10 a 100 (85-100 se riguarda stagionatura prosciutti/salumifici/zero residui; 65-85 se riguarda GDO/HACCP/condomini/enti in Lombardia o Piemonte; 0 se SCARTA)
- "motivo": giudizio commerciale sintetico in italiano (max 140 caratteri) evidenziando il problema e l'angolo "zero residui chimici" o il fit per Biopolis.

Regole rigide ("SCARTA"):
- Ricette di cucina, recensioni gastronomiche, sagre o post su come tagliare/mangiare il prosciutto senza alcun legame con aziende, stabilimenti, igiene o infestanti.
- Cronaca generica senza opportunità commerciale, battute/meme, offerte di lavoro per operai o concorrenti che si pubblicizzano.
- Elementi fuori area (es. Sud Italia o estero) se non riguardano grandi centri di stagionatura/salumifici o gare nazionali con lotti al Nord.
- Rispondi SOLO con un array JSON valido, senza markdown o testo aggiuntivo."""


def _call_gemini(batch, api_key):
    payload = {
        "systemInstruction": {"parts": [{"text": PROMPT_SISTEMA}]},
        "contents": [{"parts": [{"text": json.dumps(batch, ensure_ascii=False)}]}],
        "generationConfig": {
            "temperature": 0.1,
            "responseMimeType": "application/json",
        },
    }
    req = urllib.request.Request(
        f"{API_URL}?key={api_key}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=45) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    text = data["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(text)


def refine_with_ai(items):
    """Raffina con Gemini i lead raccolti per Biopolis."""
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        return items, {"attiva": False, "motivo": "GEMINI_API_KEY non configurata"}

    results_map = {}
    errors = 0
    batch_size = 15

    for i in range(0, len(items), batch_size):
        chunk = items[i : i + batch_size]
        compact_chunk = [
            {
                "id": it["id"],
                "canale": it.get("canale"),
                "area": it.get("area"),
                "segmento": it.get("segmento"),
                "titolo": it.get("titolo", "")[:200],
                "ente": it.get("ente", "")[:100],
                "estratto": it.get("estratto", "")[:500],
            }
            for it in chunk
        ]
        try:
            ai_out = _call_gemini(compact_chunk, api_key)
            for row in ai_out:
                if isinstance(row, dict) and row.get("id") and row.get("servizio") in CATEGORIE_AMMESSE:
                    results_map[row["id"]] = row
        except (urllib.error.URLError, ValueError, KeyError, json.JSONDecodeError) as e:
            errors += 1
            print(f"[AI] Errore batch {i}: {e}")
        if i + batch_size < len(items):
            time.sleep(2)

    refined = []
    scartati = 0
    for it in items:
        ai_res = results_map.get(it["id"])
        if ai_res:
            if ai_res["servizio"] == "SCARTA":
                scartati += 1
                continue
            it["servizio"] = ai_res["servizio"]
            it["punteggio"] = max(10, min(100, int(ai_res.get("punteggio", it["punteggio"]))))
            if ai_res.get("motivo"):
                it["motivo"] = "AI · " + ai_res["motivo"]
        refined.append(it)

    stats = {
        "attiva": True,
        "modello": GEMINI_MODEL,
        "analizzati": len(items),
        "riclassificati": len(results_map),
        "scartati": scartati,
        "errori_batch": errors,
    }
    return refined, stats