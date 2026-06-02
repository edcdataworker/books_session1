# Projet de Scraping - Books to Scrape 
## 1) Site scrappé

Ce projet scrape le site public d'entraînement:

- https://books.toscrape.com/

Le scraping se fait **dans l'ordre des catégories affichées sur le site**.

## 2) But du code de scraping

L'objectif est de construire un pipeline de collecte de donnees robuste et automatise pour:

- extraire les informations des livres,
- produire un fichier CSV exploitable pour l'analyse,
- demontrer une architecture claire avec separation des responsabilites,
- executer le pipeline sous Docker Compose et le planifier avec cron,
- produire des logs, des metriques et des garde-fous de qualite.

Les colonnes générées dans le CSV final sont:

- `DateHeureScraping`
- `NomLivre`
- `CategorieLivre`
- `PrixLivre`
- `NoteLivre`

## 3) Utilité du projet

Ce pipeline peut servir à:

- créer une base de données de livres structurée,
- comparer des prix entre catégories,
- observer la distribution des notes,
- entraîner des workflows data (collecte -> transformation -> export).

## 4) Techniques utilisées dans le code

### a) Scraping HTML

- `requests` pour les appels HTTP
- `BeautifulSoup` pour parser le HTML

### b) Optimisation de vitesse

Le projet applique deux choix techniques pour accélérer fortement l'exécution:

1. **Ne pas visiter chaque fiche produit**:
   - les données utiles (`nom`, `prix`, `note`) sont lues directement depuis les cartes produits des pages catégorie.
2. **Réutiliser une seule session HTTP** (`requests.Session`):
   - évite de recréer une connexion à chaque requête.

### c) Robustesse et monitoring

- retries HTTP avec backoff et codes d'erreur classes,
- validation des lignes et quarantaine JSONL,
- ecriture atomique du CSV,
- verrou contre les executions concurrentes,
- logs rotatifs et alertes en cas d'echecs consecutifs.

## 5) Architecture du projet

```text
project/
├── Dockerfile
├── docker-compose.yml
├── crontab.sh
├── app/
│   ├── scraper.py      # extraction resiliente et metriques
│   ├── transform.py    # validation et export CSV atomique
│   ├── main.py         # orchestration et garde-fous
│   ├── alerting.py     # notifications d'echecs consecutifs
│   ├── error_codes.py  # classification des erreurs
│   ├── logging_config.py
│   ├── runtime_lock.py
│   └── requirements.txt
├── data/
│   └── books.csv       # sortie finale persistee
└── README.md
```

## 6) Lancement

Depuis le dossier `project`:

```bash
docker compose up -d --build
docker compose exec -T python-app /usr/bin/python3 /app/main.py
docker compose exec -T python-app sh -lc "tail -n 40 /data/pipeline.log"
docker compose down
```

Sorties:

- `data/books.csv`
- `data/pipeline.log`
- `data/rejected_rows.jsonl` en cas de ligne rejetee

## 7) Notes importantes

- Le scraping respecte l'ordre des catégories du site.
- Les notes sont formatées en notation sur 5 avec une décimale (ex: `4,0/5`).
- Le volume Docker `./data:/data` conserve les sorties sur l'hote.
