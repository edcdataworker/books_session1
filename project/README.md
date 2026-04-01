# Books Scraper

Structure recommandee appliquee:

```text
project/
├── app/
│   ├── scraper.py
│   ├── transform.py
│   └── main.py
├── data/
│   └── books.csv
└── README.md
```

## Objectifs

- Code lisible
- Separation des responsabilites
- Scraping rapide (session HTTP unique + lecture des cartes produits)

## Installation

```bash
pip install requests beautifulsoup4
```

## Execution

Depuis le dossier `project`:

```bash
python3 app/main.py
```

Le CSV est genere dans:

```text
data/books.csv
```
