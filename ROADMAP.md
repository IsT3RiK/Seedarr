# Roadmap - Seedarr

## Priorite CRITIQUE

| #   | Tache                  | Description                                             | Status |
| --- | ---------------------- | ------------------------------------------------------- | ------ |
| 1   | Tests unitaires        | Ajouter des tests pytest pour les services critiques    | DONE   |
| 2   | Gestion des erreurs    | Ameliorer la gestion des erreurs dans le pipeline       | DONE   |
| 3   | Validation des entrees | Valider les donnees utilisateur (paths, URLs, API keys) | DONE   |
| 4   | Retry mechanism        | Ajouter retry automatique pour les appels API/tracker   | DONE   |

## Priorite HAUTE

| #   | Tache             | Description                                 | Status |
| --- | ----------------- | ------------------------------------------- | ------ |
| 5   | Logging structure | Centraliser et structurer les logs (JSON)   | DONE   |
| 6   | Health checks     | Endpoints de sante pour services externes   | DONE   |
| 7   | Rate limiting     | Limiter les appels API (TMDB, trackers)     | DONE   |
| 8   | Queue persistence | Rendre la queue persistante (Redis ou DB)   | DONE   |

## Priorite MOYENNE

| #   | Tache               | Description                              | Status |
| --- | ------------------- | ---------------------------------------- | ------ |
| 9   | Documentation API   | Ameliorer la documentation OpenAPI       | DONE   |
| 10  | Batch processing    | Support du traitement par lots           | DONE   |
| 11  | Notifications       | Webhooks/notifications (Discord, email)  | DONE   |
| 12  | Duplicate detection | Detection des doublons avant upload      | DONE   |

## Priorite BASSE

| #   | Tache                | Description                               | Status |
| --- | -------------------- | ----------------------------------------- | ------ |
| 13  | Dark/Light theme     | Toggle theme dans l'UI                    | DONE   |
| 14  | Export/Import config | Backup/restore des settings               | DONE   |
| 15  | Statistiques         | Dashboard avec graphiques (uploads/jour)  | DONE   |

---

## Changelog

### v2.4 (Features Completes)

- **Added**: Module `duplicate_check_service.py` - Detection des doublons multi-tracker (TMDB ID, IMDB ID, release name)
- **Added**: Module `notification_service.py` - Service de notifications centralise
- **Added**: Module `discord_client.py` - Webhooks Discord avec embeds riches
- **Added**: Module `email_client.py` - Client SMTP avec templates HTML
- **Added**: Modele `NotificationLog` pour l'historique des notifications
- **Added**: Module `batch_service.py` - Gestion des jobs batch avec controle de concurrence
- **Added**: Modele `BatchJob` avec statuts, priorites et tracking de progression
- **Added**: Routes `/api/batch` pour creation, demarrage, annulation et suivi des batches
- **Added**: Module `statistics_service.py` - Service de statistiques d'upload
- **Added**: Modeles `DailyStatistics` et `TrackerStatistics` pour metriques agregees
- **Added**: Page `/statistics` avec graphiques Chart.js (timeline, pie, bar charts)
- **Added**: Package `schemas/` avec modeles Pydantic pour requetes et reponses API
- **Added**: Documentation OpenAPI enrichie avec tags, descriptions et exemples
- **Added**: Theme toggle dark/light avec persistence localStorage et detection systeme
- **Added**: Modals de confirmation pour export/import avec preview des donnees
- **Added**: Navigation "Statistics" dans la sidebar
- **Improved**: Export inclut toutes les donnees sensibles pour backup complet
- **Improved**: Import affiche un apercu des champs avant confirmation
- **Improved**: Avertissement de securite sur les exports contenant des credentials

### v2.3.1 (Bugfix)

- **Fixed**: Methode `search_torrents()` manquante dans C411Client et LaCaleClient
- **Fixed**: Detection des doublons retournait des resultats incorrects (films non lies)
- **Fixed**: Queue worker bloquait l'event loop avec operations DB synchrones
- **Fixed**: Renommage `metadata` -> `extra_data` dans NotificationLog (mot reserve SQLAlchemy)
- **Improved**: Queue worker utilise `asyncio.to_thread()` pour operations DB thread-safe

### v2.3 (Infrastructure et Performance)

- **Added**: Module `structured_logging.py` avec logging JSON, correlation IDs et context manager
- **Added**: Module `health_check_service.py` avec checks pour tous les services externes (DB, FlareSolverr, qBittorrent, TMDB, Tracker, Prowlarr)
- **Added**: Routes `/health/live`, `/health/ready`, `/health/detailed` pour Kubernetes compatibility
- **Added**: Module `rate_limiter.py` avec token bucket algorithm et decorateur `@rate_limited`
- **Added**: Rate limiting applique aux services TMDB (4 req/s) et Tracker (1 req/s)
- **Added**: Modele `ProcessingQueue` avec priorites (high/normal/low) et retry automatique
- **Added**: `QueueService` pour gestion complete de la file d'attente persistante
- **Added**: `queue_worker` avec demarrage/arret automatique au lifespan
- **Improved**: Middleware `RequestLoggingMiddleware` avec X-Request-ID correlation

### v2.2 (Robustesse et Validation)

- **Added**: Module `validators.py` avec fonctions de validation (URL, path, API key)
- **Added**: Protection contre path traversal dans les champs de configuration
- **Added**: Sanitization des caracteres Unicode invisibles dans les paths
- **Added**: `@retry_on_network_error` decorator sur C411Client, ProwlarrClient, TMDBCacheService, ImgBBAdapter
- **Added**: 85 tests unitaires (validation, retry, error handling)
- **Improved**: Validation Pydantic dans `SettingsUpdateRequest` (Field constraints, validators)
- **Improved**: Gestion des erreurs dans le pipeline (preservation des erreurs retryables)
- **Improved**: Classification des erreurs HTTP (502/503/504 retryables, 4xx non-retryables)

### v2.1 (Optimisation)

- **Fixed**: Suppression de la route dupliquee `/api/dashboard/refresh-jobs`
- **Fixed**: Consolidation des imports datetime dupliques
- **Fixed**: Ajout du modele `Categories` manquant dans `__init__.py`
- **Removed**: Scripts de debug (`show_upload_request.py`, `test_upload.py`)
- **Removed**: Fichier de documentation temporaire (`apic411.txt`)
