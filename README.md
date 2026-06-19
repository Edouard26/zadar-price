# Zadar price monitor — option semi-manuelle via GitHub Actions

Suivi quotidien des prix de 10 logements à Zadar pour 2 personnes, du **30 juillet 2026** au **3 août 2026**.

Cette version ne scrape pas Booking.com ou Airbnb. Elle génère chaque jour un pack de vérification manuel avec les bons liens, puis historise les prix que tu saisis dans un CSV.

## Pourquoi ce mode semi-manuel ?

Booking.com et Airbnb limitent fortement l'accès automatisé à leurs plateformes. Ce projet évite donc les bots, crawlers, scrapers et collectes automatiques non autorisées. Il automatise ce qui est sûr : génération des liens, stockage, comparaison, rapport et alertes.

## Structure

```text
zadar-price-monitor/
  config/properties.yaml              # top 10 logements + prix de référence
  zadar_monitor.py                    # CLI Python
  manual_prices/                      # CSV remplis manuellement à déposer ici
  data/price_history.csv              # historique généré automatiquement
  output/latest_report.md             # rapport Markdown généré
  output/dashboard.html               # dashboard HTML généré
  .github/workflows/daily-review.yml  # génère le pack quotidien
  .github/workflows/ingest-manual-prices.yml # ingère les CSV remplis
```

## Installation locale

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python zadar_monitor.py init
```

## Générer le CSV quotidien à remplir

```bash
python zadar_monitor.py generate-review --date 2026-06-17
```

Cela crée :

```text
output/2026-06-17/manual_prices_2026-06-17.csv
output/2026-06-17/releve_prix_2026-06-17.md
```

Tu ouvres les liens, tu saisis les prix dans le CSV, puis tu copies le fichier complété dans :

```text
manual_prices/2026-06-17.csv
```

## Ingérer un CSV rempli

```bash
python zadar_monitor.py ingest --prices-file manual_prices/2026-06-17.csv
python zadar_monitor.py report
```

Le script met à jour :

```text
data/price_history.csv
output/latest_report.md
output/dashboard.html
```

## Utilisation avec GitHub Actions

1. Crée un nouveau repo GitHub, par exemple `zadar-price-monitor`.
2. Copie tout le contenu de ce dossier dans le repo.
3. Commit et push.
4. Va dans l'onglet **Actions** du repo.
5. Lance `Daily Zadar manual price review` manuellement une première fois, ou attends l'exécution quotidienne.
6. Télécharge l'artifact `zadar-manual-review-*`.
7. Remplis le CSV avec les prix observés.
8. Ajoute le CSV rempli dans `manual_prices/YYYY-MM-DD.csv` et commit.
9. Le workflow `Ingest manual Zadar prices` mettra à jour l'historique et les rapports.

## Personnaliser les URLs directes

Dans `config/properties.yaml`, remplace les champs `url: ""` par les URLs directes des fiches logements si tu les as. Si l'URL reste vide, le script génère un lien de recherche prérempli avec les dates et le nombre d'adultes.

## Format du CSV manuel

Colonnes importantes :

- `property_id` : identifiant stable du logement, ne pas modifier.
- `price_total_eur` : prix total affiché pour le séjour, en euros.
- `available` : `oui` ou `non`.
- `cancellation_policy` : optionnel.
- `notes` : optionnel.

Exemple : voir `docs/example_manual_prices.csv`.

## Règles d'alerte

Les seuils sont dans `config/properties.yaml` :

```yaml
alert_rules:
  price_drop_percent: 8
  price_drop_absolute_eur: 40
  max_budget_eur: 450
```

