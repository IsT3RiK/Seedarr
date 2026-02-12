# Roadmap - Seedarr

## Priorite CRITIQUE

| #   | Tache                       | Description                                                       | Status |
| --- | --------------------------- | ----------------------------------------------------------------- | ------ |
| 1   | Tests unitaires             | Ajouter des tests pytest pour les services critiques              | DONE   |
| 2   | Gestion des erreurs         | Ameliorer la gestion des erreurs dans le pipeline                 | DONE   |
| 3   | Validation des entrees      | Valider les donnees utilisateur (paths, URLs, API keys)           | DONE   |
| 4   | Retry mechanism             | Ajouter retry automatique pour les appels API/tracker             | DONE   |
| 16  | Unification Multi-Tracker   | Un seul ConfigAdapter + YAML pour tous les trackers               | DONE   |
| 17  | Suppression adapters legacy | Supprimer LaCaleAdapter, C411Adapter et code hardcode             | DONE   |

## Priorite HAUTE

| #   | Tache                    | Description                                                  | Status |
| --- | ------------------------ | ------------------------------------------------------------ | ------ |
| 5   | Logging structure        | Centraliser et structurer les logs (JSON)                    | DONE   |
| 6   | Health checks            | Endpoints de sante pour services externes                    | DONE   |
| 7   | Rate limiting            | Limiter les appels API (TMDB, trackers)                      | DONE   |
| 8   | Queue persistence        | Rendre la queue persistante (Redis ou DB)                    | DONE   |
| 18  | Prowlarr auto-matching   | Import Prowlarr auto-detecte le YAML config via definitions  | DONE   |
| 19  | Rate limiting par action | Token bucket par action (upload, search, categories) en YAML | DONE   |

## Priorite MOYENNE

| #   | Tache                    | Description                                         | Status |
| --- | ------------------------ | --------------------------------------------------- | ------ |
| 9   | Documentation API        | Ameliorer la documentation OpenAPI                  | DONE   |
| 10  | Batch processing         | Support du traitement par lots                      | DONE   |
| 11  | Notifications            | Webhooks/notifications (Discord, email)             | DONE   |
| 12  | Duplicate detection      | Detection des doublons avant upload                 | DONE   |
| 20  | Test endpoints configs   | Endpoints test-auth, test-search, test-upload (dry) | DONE   |
| 21  | Validation upload YAML   | Validation des donnees upload configurable en YAML  | DONE   |

## Priorite BASSE

| #   | Tache                   | Description                                           | Status  |
| --- | ----------------------- | ----------------------------------------------------- | ------- |
| 13  | Dark/Light theme        | Toggle theme dans l'UI                                | DONE    |
| 14  | Export/Import config    | Backup/restore des settings                           | DONE    |
| 15  | Statistiques            | Dashboard avec graphiques (uploads/jour)              | DONE    |
| 22  | Contribution YAML       | Documentation template YAML pour nouveaux trackers    | DONE    |
| 23  | Tests parite adapters   | Tests comparant ConfigAdapter vs anciens adapters     | TODO    |
| 24  | Fallback chain trackers | Si Tracker A echoue, essayer Tracker B automatiquement | TODO    |

---

## Changelog

### v2.5 (Unification Multi-Tracker)

- **Added**: `_sync_multipart_post()` dans ConfigAdapter - Support des champs repetes (`tags=1&tags=2`) via `requests.Session` + `asyncio.to_thread()`
- **Added**: `_parse_torznab_xml()` dans ConfigAdapter - Parsing Torznab XML/RSS pour la recherche de doublons (C411)
- **Added**: Support wildcards `[*]` et index `[N]` dans `_get_nested_value()` pour les paths JSON complexes
- **Added**: `_RateLimiter` token bucket par action (upload, search, categories) configurable en YAML
- **Added**: `_validate_upload_data()` - Validation des donnees upload (required, min/max length, pattern) via YAML
- **Added**: `_sanitize_name()` - Pipeline de sanitization configurable (replace_spaces, remove_pattern, collapse_dots, etc.)
- **Added**: Auto-invocation `OptionsMapper` dans `upload_torrent()` quand le YAML a une section `options`
- **Added**: `build_tmdb_data()` dans ConfigAdapter - Remplace le code hardcode `_build_c411_tmdb_data()` du pipeline
- **Added**: `search.default_query` configurable en YAML (remplace "FRENCH" hardcode)
- **Added**: `cloudflare.use_requests_session` - Session Cloudflare fiable via `requests.Session` directe
- **Added**: Section `prowlarr:` dans les YAML (definitions, url_patterns, auto_config) pour auto-matching a l'import
- **Added**: `_match_yaml_slug()` dans ProwlarrClient - Detection automatique du YAML config par indexer Prowlarr
- **Added**: Endpoints `POST /api/config-schemas/{slug}/test-auth` - Test d'authentification tracker
- **Added**: Endpoints `POST /api/config-schemas/{slug}/test-search` - Test de recherche/doublons
- **Added**: Endpoints `POST /api/config-schemas/{slug}/test-upload` - Dry-run validation upload
- **Added**: Sections template YAML : prowlarr, tmdb_data, validation, rate_limiting, sanitize, search, response.upload
- **Added**: Migration Alembic 024 - Migre adapter_type legacy ('lacale', 'c411') vers 'config'
- **Improved**: `TrackerFactory` simplifie - Uniquement ConfigAdapter + GenericTrackerAdapter, legacy redirige
- **Improved**: `_auto_configure_tracker()` generique dans prowlarr_routes et tracker_routes (remplace C411-specifique)
- **Improved**: `_sync_categories_generic()` - Sync categories pour tout tracker (pas seulement C411)
- **Improved**: ProwlarrClient scan les YAML au lieu de `ADAPTER_MAP` et `TRACKER_CONFIGS` hardcodes
- **Improved**: Health routes - Check generique des credentials pour tous les trackers actifs
- **Removed**: `_build_c411_options()` du pipeline (~35 lignes)
- **Removed**: `_build_c411_tmdb_data()` du pipeline (~165 lignes)
- **Removed**: Tous les `if tracker.adapter_type == 'c411':` et `== 'lacale':` du pipeline
- **Removed**: `_auto_configure_c411_tracker()` des routes (remplace par generique)
- **Removed**: `_sync_c411_categories()` des routes (remplace par generique)
- **Removed**: Import `C411OptionsMapper` du pipeline
- **Removed**: Import `LaCaleAdapter` du pipeline et de TrackerFactory
- **Removed**: Import `C411Category` des prowlarr_routes
- **Removed**: `ADAPTER_MAP` et `TRACKER_CONFIGS` hardcodes de ProwlarrClient
- **Removed**: Source flag hardcode "lacale" (remplace par "seedarr")

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
