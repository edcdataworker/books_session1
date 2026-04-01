# Projet de Scraping - Books to Scrape

## 1) Site scrappé

Ce projet scrape le site public d'entraînement:

- https://books.toscrape.com/

Le scraping se fait **dans l'ordre des catégories affichées sur le site**.

## 2) But du code de scraping

L'objectif est de construire un pipeline de collecte de données simple et robuste pour:

- extraire les informations des livres,
- produire un fichier CSV exploitable pour l'analyse,
- démontrer une architecture claire avec séparation des responsabilités.

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

### c) Gestion des erreurs

Le pipeline gère les erreurs sans bloquer toute l'exécution:

- `try / except` sur les requêtes HTTP,
- messages d'erreur clairs,
- continuation du pipeline en cas d'échec ponctuel.

## 5) Architecture du projet

```text
project/
├── app/
│   ├── scraper.py      # extraction des données du site
│   ├── transform.py    # formatage des données et export CSV
│   └── main.py         # orchestration du pipeline
├── data/
│   └── books.csv       # sortie finale
├── docs/
│   └── pipeline_schema.pdf
└── README.md
```

## 6) Schéma du pipeline (PDF)

Le schéma est disponible ici:

- `docs/pipeline_schema.pdf`

## 7) Installation

```bash
pip install requests beautifulsoup4
```

## 8) Lancement

Depuis le dossier `project`:

```bash
python3 app/main.py
```

Sortie:

- `data/books.csv`

## 9) Notes importantes

- Le scraping respecte l'ordre des catégories du site.
- Les notes sont formatées en notation sur 5 avec une décimale (ex: `4,0/5`).
- Le CSV produit est prêt pour une analyse (Excel, pandas, BI).
