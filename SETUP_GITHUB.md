# Mise en route GitHub Actions

## 1. Créer le repo

```bash
git init
git add .
git commit -m "Initial Zadar price monitor"
git branch -M main
git remote add origin git@github.com:TON_COMPTE/zadar-price-monitor.git
git push -u origin main
```

## 2. Activer les workflows

Dans GitHub :

1. Ouvre le repo.
2. Va dans **Actions**.
3. Accepte l'activation des workflows si GitHub le demande.
4. Lance manuellement `Daily Zadar manual price review` pour tester.

## 3. Routine quotidienne

Le workflow planifié crée un artifact contenant :

```text
manual_prices_YYYY-MM-DD.csv
releve_prix_YYYY-MM-DD.md
```

Après vérification des prix :

1. Renomme le CSV rempli en `YYYY-MM-DD.csv`.
2. Dépose-le dans `manual_prices/`.
3. Commit et push.
4. Le workflow d'ingestion se déclenche automatiquement.

## 4. Lire le rapport

Après ingestion, consulte :

```text
output/latest_report.md
output/dashboard.html
```

