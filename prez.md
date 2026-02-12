# Seedarr v2.0

**Automatisez la publication de vos torrents en quelques clics.**

---

## Le problème

Publier un torrent sur un tracker privé est un processus long et répétitif :

1. Analyser le fichier média (codec, résolution, audio...)
2. Rechercher les métadonnées (titre, année, synopsis...)
3. Renommer selon les conventions scene
4. Prendre des screenshots
5. Créer le fichier .torrent
6. Rédiger la description BBCode
7. Uploader sur le tracker
8. Ajouter à son client torrent pour seeder

**Seedarr automatise tout ce workflow.**

---

## Comment ça marche

Le pipeline traite vos fichiers en **7 étapes** :

| Étape | Action | Description |
|:-----:|--------|-------------|
| 1 | **Scan** | Découvre les fichiers média dans votre dossier d'entrée |
| 2 | **Analyze** | Extrait les infos techniques (MediaInfo) et récupère les métadonnées TMDB |
| 3 | **Approve** | Vous validez les informations avant de continuer |
| 4 | **Prepare** | Génère les screenshots et les upload sur un hébergeur d'images |
| 5 | **Rename** | Formate le nom selon les conventions scene (Title.Year.Resolution.Codec-GROUP) |
| 6 | **Generate** | Crée le fichier .torrent et la description .nfo en BBCode |
| 7 | **Upload** | Publie sur le tracker et ajoute automatiquement à qBittorrent pour le seeding |

---

## Fonctionnalités clés

- **Multi-trackers** : Publiez sur plusieurs trackers simultanément (cross-seeding)
- **Générateur de présentation** : Crée automatiquement des descriptions BBCode complètes avec poster, synopsis, casting, infos techniques
- **Templates personnalisables** : Créez vos propres templates de présentation par tracker
- **Gestion des NFO** : Génération automatique des fichiers .nfo avec toutes les métadonnées
- **Détection de doublons** : Évitez de publier un torrent déjà présent sur le tracker
- **Notifications** : Recevez des alertes Discord ou Email à chaque upload
- **Statistiques** : Suivez vos uploads, ratio et activité
- **Historique complet** : Retrouvez tous vos torrents publiés
- **Reprise sur erreur** : Le pipeline reprend là où il s'est arrêté en cas de problème

---

## Intégrations

| Service | Utilisation |
|---------|-------------|
| **TMDB** | Récupération des métadonnées (titre, synopsis, poster, casting...) |
| **qBittorrent** | Ajout automatique des torrents pour le seeding |
| **FlareSolverr** | Bypass de la protection Cloudflare des trackers |
| **Prowlarr** | Gestion centralisée des indexers/trackers |
| **ImgBB** | Hébergement des screenshots |

---

## Interface

L'application propose une interface web complète :

| Page | Description |
|------|-------------|
| **Dashboard** | Vue d'ensemble et statut du pipeline |
| **File Manager** | Navigateur de fichiers pour sélectionner vos médias |
| **Queue** | File d'attente des fichiers en cours de traitement |
| **History** | Historique de tous les uploads effectués |
| **Trackers** | Configuration des trackers (credentials, catégories) |
| **Templates** | Éditeur de templates BBCode |
| **Stats** | Statistiques d'utilisation et métriques |
| **Settings** | Configuration générale de l'application |

---

## Stack technique

- **Backend** : Python 3.10+, FastAPI, SQLAlchemy
- **Frontend** : TailwindCSS, DaisyUI, HTMX
- **Base de données** : SQLite

---

## Démarrage rapide

```bash
# Cloner le projet
git clone https://github.com/your-repo/seedarr.git

# Installer les dépendances
pip install -r backend/requirements.txt

# Lancer l'application
python backend/dev.py
```

Accédez à l'interface sur `http://localhost:8000`
