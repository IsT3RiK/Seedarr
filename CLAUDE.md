# CLAUDE.md - Seedarr v2.0

Ce fichier aide Claude (et les développeurs) à comprendre rapidement l'architecture et le fonctionnement de l'application.

## Vue d'ensemble

**Seedarr** est une application web Python/FastAPI qui automatise la publication de torrents sur un tracker privé (La Cale). Elle scanne des fichiers média, extrait les métadonnées, génère les fichiers .torrent et .nfo, puis upload automatiquement sur le tracker.

## Stack technique

- **Backend**: Python 3.10+, FastAPI, SQLAlchemy, Alembic
- **Frontend**: Jinja2 templates, TailwindCSS, DaisyUI, HTMX
- **Database**: SQLite (via SQLAlchemy ORM)
- **Services externes**: TMDB API, FlareSolverr, qBittorrent

## Structure du projet

```
backend/
├── app/
│   ├── main.py              # Point d'entrée FastAPI
│   ├── database.py          # Configuration SQLAlchemy
│   ├── config.py            # Configuration de l'app
│   ├── api/                  # Routes API
│   │   ├── dashboard_routes.py
│   │   ├── filemanager_routes.py
│   │   └── settings_routes.py
│   ├── models/              # Modèles SQLAlchemy
│   │   ├── file_entry.py    # Fichiers en cours de traitement
│   │   ├── settings.py      # Configuration (singleton)
│   │   ├── tags.py          # Tags du tracker
│   │   └── tmdb_cache.py    # Cache TMDB
│   ├── services/            # Logique métier
│   │   ├── lacale_client.py # Client API tracker
│   │   ├── media_analyzer.py # Analyse MediaInfo
│   │   ├── cloudflare_session_manager.py
│   │   ├── tag_manager.py
│   │   └── tmdb_cache_service.py
│   ├── adapters/            # Abstractions tracker
│   │   ├── tracker_adapter.py # Interface abstraite
│   │   └── lacale_adapter.py  # Implémentation La Cale
│   └── processors/
│       └── pipeline.py      # Pipeline de traitement
├── templates/               # Templates Jinja2
├── static/                  # CSS/JS/Images
├── alembic/                 # Migrations DB
├── tests/                   # Tests pytest
├── dev.py                   # Script de développement
└── requirements.txt
```

## Pipeline de traitement (5 étapes)

1. **Scan**: Découvre les fichiers média dans le dossier d'entrée
2. **Analyze**: Extrait les métadonnées via MediaInfo (codec, résolution, audio)
3. **Rename**: Formate le nom selon les conventions scene (Title.Year.Resolution.Codec-GROUP)
4. **Metadata**: Récupère les infos TMDB, génère .nfo et .torrent
5. **Upload**: Upload sur le tracker via API, ajoute à qBittorrent pour seeding

## Modèles de données clés

### Settings (singleton)
Configuration globale stockée en DB:
- `tracker_url`, `tracker_passkey` → URL du tracker et passkey
- `announce_url` → Propriété calculée: `{tracker_url}/announce?passkey={passkey}`
- `flaresolverr_url` → Pour bypass Cloudflare
- `qbittorrent_host/username/password` → Client torrent
- `tmdb_api_key` → API TMDB pour métadonnées
- `input_media_path`, `output_dir` → Dossiers de travail

### FileEntry
Représente un fichier dans le pipeline:
- `file_path` → Chemin du fichier source
- `status` → PENDING, SCANNED, ANALYZED, RENAMED, METADATA_GENERATED, UPLOADED, FAILED
- Timestamps de checkpoint pour reprise idempotente

### Tags
Tags du tracker synchronisés dynamiquement depuis l'API.

## Commandes de développement

```bash
# Lancer en mode dev (avec hot reload)
python backend/dev.py

# Lancer en production
uvicorn backend.app.main:app --host 0.0.0.0 --port 8000

# Migrations Alembic
cd backend && alembic upgrade head

# Tests
pytest backend/tests -v
```

## Pages de l'interface

| Route | Description |
|-------|-------------|
| `/dashboard` | Vue d'ensemble du pipeline et jobs récents |
| `/queue` | File d'attente des fichiers à traiter |
| `/filemanager` | Navigateur de fichiers pour sélectionner les médias |
| `/history` | Historique des uploads |
| `/settings` | Configuration de l'application |
| `/logs` | Logs en temps réel |

## Points d'attention

1. **Cloudflare**: Le tracker utilise Cloudflare, donc FlareSolverr est requis
2. **Passkey**: Jamais exposé directement, l'announce URL est flouttée dans l'UI
3. **Idempotence**: Le pipeline peut reprendre après un échec grâce aux timestamps
4. **Singleton Settings**: Une seule ligne en DB, accès via `Settings.get_settings(db)`

## Variables d'environnement

Voir `.env.example` pour la liste complète. Seule `DATABASE_URL` reste en env, tout le reste est dans la DB Settings.

## Docker

```bash
docker-compose up -d
```

Services: app (port 8000), flaresolverr (8191), qbittorrent (8080)
