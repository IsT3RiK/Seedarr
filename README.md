# Seedarr

Application web pour automatiser la publication de torrents sur trackers prives.

## Fonctionnalites

- **Multi-tracker** - Upload simultanement sur plusieurs trackers (La Cale, C411...)
- **Cross-seeding** - Un .torrent unique par tracker avec hash different
- **Analyse MediaInfo** - Extraction automatique codec, resolution, audio
- **Enrichissement TMDB** - Titre, synopsis, casting, poster
- **Renommage scene** - Format standardise compatible tous trackers
- **Templates BBCode** - Presentations personnalisables avec 40+ variables
- **Screenshots** - Generation automatique via FFmpeg + upload ImgBB
- **Detection doublons** - Verification avant upload
- **Integration qBittorrent** - Seeding automatique

## Installation

```bash
git clone https://github.com/IsT3RiK/Seedarr.git
cd Seedarr
cp .env.example .env
docker compose up -d
```

Accessible sur http://localhost:8000

## Configuration

Apres le premier lancement, configurez l'application via l'interface web:

1. **Settings** (`/settings`)
   - TMDB API Key (obligatoire) - https://www.themoviedb.org/settings/api
   - qBittorrent (host, username, password)
   - FlareSolverr URL (si tracker avec Cloudflare)

2. **Trackers** (`/trackers`)
   - Ajouter vos trackers (URL, passkey, adapter type)
   - Tester la connexion
   - Activer le tracker

3. **Templates** (`/bbcode-templates`) - Optionnel
   - Personnaliser les presentations BBCode

## Utilisation

1. **File Manager** - Selectionner un fichier media
2. **Add to Queue** - Ajouter a la file d'attente
3. **Process** - Le pipeline s'execute automatiquement
4. **Dashboard** - Suivre la progression en temps reel

## Pages

| Page | Route | Description |
|------|-------|-------------|
| Dashboard | `/dashboard` | Vue d'ensemble pipeline |
| Queue | `/queue` | File d'attente |
| File Manager | `/filemanager` | Navigateur fichiers |
| History | `/history` | Historique uploads |
| Trackers | `/trackers` | Gestion trackers |
| Templates | `/bbcode-templates` | Templates BBCode |
| Statistics | `/statistics` | Statistiques |
| Settings | `/settings` | Configuration |

## Trackers supportes

| Tracker | Adapter | Auth | Cloudflare |
|---------|---------|------|------------|
| La Cale | lacale | Passkey | Oui |
| C411 | c411 | API Key | Non |
| Generic | generic | Passkey | Non |

## Variables d'environnement (.env)

| Variable | Description | Defaut |
|----------|-------------|--------|
| `APP_PORT` | Port de l'application | 8000 |
| `MEDIA_PATH` | Chemin vers vos fichiers media | ./media |
| `TZ` | Timezone | Europe/Paris |

## API

Documentation Swagger: http://localhost:8000/docs

---

## Developpement

```bash
git clone https://github.com/IsT3RiK/Seedarr.git
cd Seedarr

python -m venv venv
venv\Scripts\activate  # Windows
source venv/bin/activate  # Linux/Mac

pip install -r backend/requirements.txt
cd backend && alembic upgrade head
python dev.py
```

## License

MIT
