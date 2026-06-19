#!/usr/bin/env python3
"""Zadar price monitor - option semi-manuelle compatible GitHub Actions.

Ce script ne scrape pas Booking/Airbnb. Il génère des liens de vérification et un
CSV à remplir manuellement, puis historise les prix saisis.
"""
from __future__ import annotations

import argparse
import csv
import html
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config" / "properties.yaml"
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "output"
MANUAL_DIR = ROOT / "manual_prices"
DB_PATH = DATA_DIR / "prices.sqlite3"
HISTORY_CSV_PATH = DATA_DIR / "price_history.csv"
PARIS_TZ = ZoneInfo("Europe/Paris")

HISTORY_FIELDS = [
    "collected_at",
    "property_id",
    "name",
    "platform",
    "rank_from_report",
    "baseline_price_eur",
    "price_total_eur",
    "available",
    "cancellation_policy",
    "notes",
    "source_url",
]

MANUAL_FIELDS = [
    "property_id",
    "name",
    "platform",
    "rank_from_report",
    "baseline_price_eur",
    "price_total_eur",
    "available",
    "cancellation_policy",
    "notes",
    "source_url",
]


@dataclass(frozen=True)
class Property:
    id: str
    name: str
    platform: str
    baseline_price_eur: float | None
    rank_from_report: int | None
    url: str
    notes: str


def load_config() -> dict[str, Any]:
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_properties(config: dict[str, Any]) -> list[Property]:
    items = []
    for raw in config.get("properties", []):
        items.append(
            Property(
                id=str(raw["id"]),
                name=str(raw["name"]),
                platform=str(raw["platform"]).lower(),
                baseline_price_eur=to_float(raw.get("baseline_price_eur")),
                rank_from_report=int(raw["rank_from_report"]) if raw.get("rank_from_report") else None,
                url=str(raw.get("url") or "").strip(),
                notes=str(raw.get("notes") or ""),
            )
        )
    return sorted(items, key=lambda p: (p.rank_from_report or 999, p.name))


def today_str() -> str:
    return datetime.now(PARIS_TZ).date().isoformat()


def ensure_dirs() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    OUTPUT_DIR.mkdir(exist_ok=True)
    MANUAL_DIR.mkdir(exist_ok=True)


def to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("\u202f", " ").replace("€", "").replace(",", ".")
    text = re.sub(r"[^0-9.\-]", "", text)
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def normalize_available(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"1", "yes", "y", "oui", "true", "vrai", "available", "disponible"}:
        return True
    if text in {"0", "no", "n", "non", "false", "faux", "unavailable", "indisponible"}:
        return False
    # Par défaut, si un prix est saisi, on considérera le bien disponible au moment de l'ingestion.
    return False


def money(value: float | None) -> str:
    if value is None:
        return "—"
    if abs(value - round(value)) < 0.005:
        return f"{int(round(value))} €"
    return f"{value:.2f} €"


def pct(value: float | None) -> str:
    if value is None:
        return "—"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.1f} %"


def source_url(prop: Property, trip: dict[str, Any]) -> str:
    if prop.url:
        return prop.url

    checkin = trip["checkin"]
    checkout = trip["checkout"]
    adults = str(trip.get("adults", 2))
    rooms = str(trip.get("rooms", 1))
    destination = trip.get("destination", "Zadar, Croatia")
    query = f"{prop.name} {destination}"

    if prop.platform == "booking":
        params = {
            "ss": query,
            "checkin": checkin,
            "checkout": checkout,
            "group_adults": adults,
            "no_rooms": rooms,
            "group_children": "0",
            "selected_currency": trip.get("currency", "EUR"),
        }
        return "https://www.booking.com/searchresults.html?" + urlencode(params)

    if prop.platform == "airbnb":
        params = {
            "query": query,
            "checkin": checkin,
            "checkout": checkout,
            "adults": adults,
        }
        return "https://www.airbnb.com/s/Zadar--Croatia/homes?" + urlencode(params)

    params = {"q": query}
    return "https://www.google.com/search?" + urlencode(params)


def init_db() -> None:
    ensure_dirs()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS price_snapshots (
                collected_at TEXT NOT NULL,
                property_id TEXT NOT NULL,
                name TEXT NOT NULL,
                platform TEXT NOT NULL,
                rank_from_report INTEGER,
                baseline_price_eur REAL,
                price_total_eur REAL,
                available INTEGER NOT NULL,
                cancellation_policy TEXT,
                notes TEXT,
                source_url TEXT,
                PRIMARY KEY (collected_at, property_id)
            )
            """
        )
        conn.commit()


def init_history_csv() -> None:
    if not HISTORY_CSV_PATH.exists():
        with HISTORY_CSV_PATH.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=HISTORY_FIELDS)
            writer.writeheader()


def generate_review(collection_date: str | None = None) -> Path:
    ensure_dirs()
    config = load_config()
    trip = config["trip"]
    properties = load_properties(config)
    collection_date = collection_date or today_str()

    daily_dir = OUTPUT_DIR / collection_date
    daily_dir.mkdir(parents=True, exist_ok=True)

    csv_path = daily_dir / f"manual_prices_{collection_date}.csv"
    md_path = daily_dir / f"releve_prix_{collection_date}.md"

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=MANUAL_FIELDS)
        writer.writeheader()
        for prop in properties:
            writer.writerow(
                {
                    "property_id": prop.id,
                    "name": prop.name,
                    "platform": prop.platform,
                    "rank_from_report": prop.rank_from_report or "",
                    "baseline_price_eur": prop.baseline_price_eur if prop.baseline_price_eur is not None else "",
                    "price_total_eur": "",
                    "available": "oui",
                    "cancellation_policy": "",
                    "notes": prop.notes,
                    "source_url": source_url(prop, trip),
                }
            )

    with md_path.open("w", encoding="utf-8") as f:
        f.write(f"# Relevé manuel des prix — Zadar — {collection_date}\n\n")
        f.write(
            f"Séjour surveillé : **{trip['checkin']} → {trip['checkout']}**, "
            f"**{trip.get('adults', 2)} adultes**, devise **{trip.get('currency', 'EUR')}**.\n\n"
        )
        f.write("## Comment faire\n\n")
        f.write("1. Ouvre chaque lien.\n")
        f.write("2. Vérifie que les dates et le nombre de voyageurs sont corrects.\n")
        f.write("3. Saisis le prix total visible dans le CSV généré.\n")
        f.write("4. Copie le CSV complété dans `manual_prices/` à la racine du repo, par exemple `manual_prices/" + collection_date + ".csv`.\n")
        f.write("5. Commit le fichier : le workflow `Ingest manual prices` mettra à jour l'historique et le rapport.\n\n")
        f.write("## Logements à vérifier\n\n")
        f.write("| Rang | Logement | Plateforme | Référence | Lien |\n")
        f.write("|---:|---|---|---:|---|\n")
        for prop in properties:
            url = source_url(prop, trip)
            f.write(
                f"| {prop.rank_from_report or ''} | {prop.name} | {prop.platform} | "
                f"{money(prop.baseline_price_eur)} | [ouvrir]({url}) |\n"
            )
        f.write("\n")
        f.write(f"CSV à remplir : `{csv_path.name}`.\n")

    print(f"Review pack created: {csv_path}")
    print(f"Review markdown created: {md_path}")
    return csv_path


def read_csv_flexible(path: Path) -> list[dict[str, str]]:
    text = path.read_text(encoding="utf-8-sig")
    sample = text[:2048]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(text.splitlines(), dialect=dialect)
    return [{k: (v or "") for k, v in row.items()} for row in reader]


def upsert_snapshot(row: dict[str, Any]) -> None:
    init_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO price_snapshots (
                collected_at, property_id, name, platform, rank_from_report,
                baseline_price_eur, price_total_eur, available,
                cancellation_policy, notes, source_url
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(collected_at, property_id) DO UPDATE SET
                name=excluded.name,
                platform=excluded.platform,
                rank_from_report=excluded.rank_from_report,
                baseline_price_eur=excluded.baseline_price_eur,
                price_total_eur=excluded.price_total_eur,
                available=excluded.available,
                cancellation_policy=excluded.cancellation_policy,
                notes=excluded.notes,
                source_url=excluded.source_url
            """,
            (
                row["collected_at"],
                row["property_id"],
                row["name"],
                row["platform"],
                row.get("rank_from_report"),
                row.get("baseline_price_eur"),
                row.get("price_total_eur"),
                1 if row.get("available") else 0,
                row.get("cancellation_policy", ""),
                row.get("notes", ""),
                row.get("source_url", ""),
            ),
        )
        conn.commit()


def sync_history_csv_from_db() -> None:
    init_db()
    init_history_csv()
    with sqlite3.connect(DB_PATH) as conn, HISTORY_CSV_PATH.open("w", newline="", encoding="utf-8") as f:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM price_snapshots ORDER BY collected_at, rank_from_report, name"
        ).fetchall()
        writer = csv.DictWriter(f, fieldnames=HISTORY_FIELDS)
        writer.writeheader()
        for row in rows:
            out = dict(row)
            out["available"] = "oui" if row["available"] else "non"
            writer.writerow(out)



def bootstrap_db_from_history_csv() -> None:
    """Recharge SQLite depuis l'historique CSV commité dans le repo.

    GitHub Actions repart souvent d'un workspace propre : on évite donc de
    dépendre d'un fichier SQLite binaire commité. Le CSV est la source durable.
    """
    init_db()
    if not HISTORY_CSV_PATH.exists():
        return
    with HISTORY_CSV_PATH.open("r", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if not row.get("collected_at") or not row.get("property_id"):
                continue
            snapshot = {
                "collected_at": row["collected_at"],
                "property_id": row["property_id"],
                "name": row.get("name", ""),
                "platform": row.get("platform", ""),
                "rank_from_report": int(row["rank_from_report"]) if row.get("rank_from_report") else None,
                "baseline_price_eur": to_float(row.get("baseline_price_eur")),
                "price_total_eur": to_float(row.get("price_total_eur")),
                "available": normalize_available(row.get("available")),
                "cancellation_policy": row.get("cancellation_policy", ""),
                "notes": row.get("notes", ""),
                "source_url": row.get("source_url", ""),
            }
            upsert_snapshot(snapshot)

def ingest_manual_prices(prices_file: str, collection_date: str | None = None) -> None:
    ensure_dirs()
    bootstrap_db_from_history_csv()
    config = load_config()
    trip = config["trip"]
    properties = {p.id: p for p in load_properties(config)}
    path = Path(prices_file)
    if not path.is_absolute():
        path = ROOT / path
    if not path.exists():
        raise FileNotFoundError(f"Fichier introuvable: {path}")

    collection_date = collection_date or infer_date_from_filename(path) or today_str()
    rows = read_csv_flexible(path)
    if not rows:
        raise ValueError(f"Aucune ligne lisible dans {path}")

    count = 0
    for raw in rows:
        prop_id = (raw.get("property_id") or raw.get("id") or "").strip()
        if not prop_id:
            continue
        prop = properties.get(prop_id)
        if prop is None:
            print(f"Warning: property_id inconnu ignoré: {prop_id}", file=sys.stderr)
            continue

        price = to_float(raw.get("price_total_eur") or raw.get("price") or raw.get("prix"))
        available_raw = raw.get("available") or raw.get("disponible")
        available = normalize_available(available_raw)
        if price is not None:
            available = True

        row = {
            "collected_at": collection_date,
            "property_id": prop.id,
            "name": prop.name,
            "platform": prop.platform,
            "rank_from_report": prop.rank_from_report,
            "baseline_price_eur": prop.baseline_price_eur,
            "price_total_eur": price,
            "available": available,
            "cancellation_policy": raw.get("cancellation_policy", ""),
            "notes": raw.get("notes", prop.notes),
            "source_url": raw.get("source_url") or source_url(prop, trip),
        }
        upsert_snapshot(row)
        count += 1

    sync_history_csv_from_db()
    print(f"{count} relevés ingérés pour {collection_date} depuis {path}")


def infer_date_from_filename(path: Path) -> str | None:
    match = re.search(r"(20\d{2}-\d{2}-\d{2})", path.name)
    if not match:
        return None
    try:
        return date.fromisoformat(match.group(1)).isoformat()
    except ValueError:
        return None


def load_history() -> list[dict[str, Any]]:
    if not HISTORY_CSV_PATH.exists():
        return []
    with HISTORY_CSV_PATH.open("r", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        row["price_total_eur"] = to_float(row.get("price_total_eur"))
        row["baseline_price_eur"] = to_float(row.get("baseline_price_eur"))
        row["available_bool"] = normalize_available(row.get("available"))
        try:
            row["rank_from_report"] = int(row.get("rank_from_report") or 999)
        except ValueError:
            row["rank_from_report"] = 999
    return rows


def latest_date(rows: list[dict[str, Any]]) -> str | None:
    dates = sorted({r["collected_at"] for r in rows if r.get("collected_at")})
    return dates[-1] if dates else None


def previous_date(rows: list[dict[str, Any]], current: str) -> str | None:
    dates = sorted({r["collected_at"] for r in rows if r.get("collected_at") and r["collected_at"] < current})
    return dates[-1] if dates else None


def generate_report(collection_date: str | None = None) -> Path:
    ensure_dirs()
    rows = load_history()
    if not rows:
        md_path = OUTPUT_DIR / "latest_report.md"
        md_path.write_text("# Rapport prix Zadar\n\nAucun relevé de prix ingéré pour l'instant.\n", encoding="utf-8")
        (OUTPUT_DIR / "dashboard.html").write_text("<h1>Rapport prix Zadar</h1><p>Aucun relevé.</p>", encoding="utf-8")
        print("No history yet.")
        return md_path

    collection_date = collection_date or latest_date(rows)
    if collection_date is None:
        raise ValueError("Impossible de déterminer la date du rapport")
    prev = previous_date(rows, collection_date)

    current_rows = [r for r in rows if r.get("collected_at") == collection_date]
    prev_by_id = {r["property_id"]: r for r in rows if r.get("collected_at") == prev}
    current_rows.sort(key=lambda r: (r.get("price_total_eur") is None, r.get("price_total_eur") or 10**9))

    alert_rules = load_config().get("alert_rules", {})
    drop_pct_threshold = float(alert_rules.get("price_drop_percent", 8))
    drop_abs_threshold = float(alert_rules.get("price_drop_absolute_eur", 40))
    max_budget = float(alert_rules.get("max_budget_eur", 450))

    enriched = []
    for row in current_rows:
        prev_row = prev_by_id.get(row["property_id"])
        price = row.get("price_total_eur")
        prev_price = prev_row.get("price_total_eur") if prev_row else None
        delta = price - prev_price if price is not None and prev_price is not None else None
        delta_pct = (delta / prev_price * 100) if delta is not None and prev_price else None
        baseline = row.get("baseline_price_eur")
        vs_baseline = price - baseline if price is not None and baseline is not None else None
        alerts = []
        if price is not None and price <= max_budget:
            alerts.append("sous budget")
        if delta is not None and delta <= -drop_abs_threshold:
            alerts.append(f"baisse ≥ {int(drop_abs_threshold)} €")
        if delta_pct is not None and delta_pct <= -drop_pct_threshold:
            alerts.append(f"baisse ≥ {drop_pct_threshold:.0f} %")
        if price is None or not row.get("available_bool"):
            alerts.append("indisponible / prix manquant")
        enriched.append({**row, "prev_price": prev_price, "delta": delta, "delta_pct": delta_pct, "vs_baseline": vs_baseline, "alerts": alerts})

    md_path = OUTPUT_DIR / "latest_report.md"
    with md_path.open("w", encoding="utf-8") as f:
        f.write(f"# Rapport prix Zadar — {collection_date}\n\n")
        f.write("Séjour : **30 juillet 2026 → 3 août 2026**, **2 personnes**.\n\n")
        if prev:
            f.write(f"Comparaison avec le précédent relevé : **{prev}**.\n\n")
        else:
            f.write("Premier relevé disponible, pas encore de comparaison jour précédent.\n\n")
        available_with_price = [r for r in enriched if r.get("price_total_eur") is not None and r.get("available_bool")]
        if available_with_price:
            best = min(available_with_price, key=lambda r: r["price_total_eur"])
            f.write(f"**Meilleure offre actuelle : {best['name']} — {money(best['price_total_eur'])}.**\n\n")
        f.write("| Logement | Plateforme | Prix actuel | Prix précédent | Variation | Vs référence | Alertes |\n")
        f.write("|---|---|---:|---:|---:|---:|---|\n")
        for r in enriched:
            alerts = ", ".join(r["alerts"]) if r["alerts"] else "—"
            f.write(
                f"| {r['name']} | {r['platform']} | {money(r.get('price_total_eur'))} | "
                f"{money(r.get('prev_price'))} | {pct(r.get('delta_pct'))} | "
                f"{money(r.get('vs_baseline'))} | {alerts} |\n"
            )

    html_path = OUTPUT_DIR / "dashboard.html"
    html_path.write_text(render_html_dashboard(collection_date, prev, enriched), encoding="utf-8")
    print(f"Report created: {md_path}")
    print(f"Dashboard created: {html_path}")
    return md_path


def render_html_dashboard(collection_date: str, prev: str | None, rows: list[dict[str, Any]]) -> str:
    generated_at = datetime.now(PARIS_TZ).strftime("%Y-%m-%d %H:%M %Z")
    body_rows = []
    for r in rows:
        alert_text = ", ".join(r["alerts"]) if r["alerts"] else "—"
        link = r.get("source_url") or "#"
        body_rows.append(
            "<tr>"
            f"<td>{html.escape(str(r.get('rank_from_report') or ''))}</td>"
            f"<td><a href=\"{html.escape(link, quote=True)}\">{html.escape(r['name'])}</a></td>"
            f"<td>{html.escape(r['platform'])}</td>"
            f"<td>{money(r.get('price_total_eur'))}</td>"
            f"<td>{money(r.get('prev_price'))}</td>"
            f"<td>{pct(r.get('delta_pct'))}</td>"
            f"<td>{money(r.get('vs_baseline'))}</td>"
            f"<td>{html.escape(alert_text)}</td>"
            "</tr>"
        )
    rows_html = "\n".join(body_rows)
    prev_text = prev or "aucun relevé précédent"
    return f"""<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Dashboard prix Zadar</title>
  <style>
    body {{ font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 2rem; line-height: 1.45; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #ddd; padding: 0.5rem; text-align: left; }}
    th:nth-child(4), th:nth-child(5), th:nth-child(6), th:nth-child(7),
    td:nth-child(4), td:nth-child(5), td:nth-child(6), td:nth-child(7) {{ text-align: right; }}
    th {{ background: #f5f5f5; }}
    .meta {{ color: #555; }}
  </style>
</head>
<body>
  <h1>Dashboard prix Zadar — {html.escape(collection_date)}</h1>
  <p class="meta">Séjour : 30 juillet 2026 → 3 août 2026, 2 personnes. Comparaison : {html.escape(prev_text)}. Généré le {html.escape(generated_at)}.</p>
  <table>
    <thead>
      <tr>
        <th>Rang</th>
        <th>Logement</th>
        <th>Plateforme</th>
        <th>Prix actuel</th>
        <th>Prix précédent</th>
        <th>Variation</th>
        <th>Vs référence</th>
        <th>Alertes</th>
      </tr>
    </thead>
    <tbody>
      {rows_html}
    </tbody>
  </table>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Zadar price monitor")
    sub = parser.add_subparsers(dest="command", required=True)

    p_review = sub.add_parser("generate-review", help="Génère le CSV quotidien à remplir")
    p_review.add_argument("--date", dest="collection_date", default=None, help="Date YYYY-MM-DD, par défaut aujourd'hui Europe/Paris")

    p_ingest = sub.add_parser("ingest", help="Ingère un CSV de prix rempli manuellement")
    p_ingest.add_argument("--prices-file", required=True, help="Chemin du CSV rempli")
    p_ingest.add_argument("--date", dest="collection_date", default=None, help="Date YYYY-MM-DD, sinon inférée du nom du fichier")

    p_report = sub.add_parser("report", help="Génère le rapport à partir de l'historique")
    p_report.add_argument("--date", dest="collection_date", default=None, help="Date YYYY-MM-DD, par défaut dernière date disponible")

    sub.add_parser("init", help="Initialise les fichiers de données")

    args = parser.parse_args()
    if args.command == "init":
        ensure_dirs()
        init_db()
        init_history_csv()
        print("Initialisation terminée.")
    elif args.command == "generate-review":
        generate_review(args.collection_date)
    elif args.command == "ingest":
        ingest_manual_prices(args.prices_file, args.collection_date)
    elif args.command == "report":
        generate_report(args.collection_date)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
