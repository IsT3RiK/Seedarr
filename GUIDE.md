# Guide Seedarr v2.4
### Tout ce que vous pouvez faire, expliqué simplement

---

## C'est quoi Seedarr ?

**Seedarr** automatise tout le travail ennuyeux quand vous voulez partager un film ou une série sur un tracker privé.

**Avant** : Vous deviez...
- Analyser le fichier manuellement avec MediaInfo
- Copier/coller les infos dans un template
- Chercher le film sur TMDB
- Faire des captures d'écran avec VLC
- Uploader les images une par une
- Créer le fichier .torrent
- Remplir le formulaire du tracker
- Ajouter le torrent dans qBittorrent

**Maintenant** :
1. Vous sélectionnez un fichier
2. L'application fait tout automatiquement
3. Vous recevez une notification quand c'est fini

---

## Ce que vous pouvez faire

### 📁 Gérer vos fichiers

**Naviguer dans vos dossiers**
- Parcourez vos fichiers média comme dans un explorateur
- Visualisez les infos de chaque fichier (taille, format)
- Sélectionnez les fichiers à traiter

**Traitement automatique**
- Cliquez sur "Add to Queue" → tout se fait automatiquement
- Le fichier passe par 5 étapes : Scan, Analyze, Rename, Metadata, Upload
- Suivez la progression en temps réel sur le dashboard

**Traitement par lots**
- Sélectionnez 10, 20, 50 fichiers d'un coup
- L'application les traite un par un automatiquement
- Configurez le nombre de traitements simultanés

---

### 🎬 Enrichir vos uploads

**Métadonnées automatiques depuis TMDB**
- Titre et titre original
- Année de sortie et pays
- Synopsis complet
- Note des utilisateurs
- Genres (Action, Drama, etc.)
- Réalisateur et acteurs
- Poster et backdrop en haute qualité
- Lien vers la bande-annonce YouTube

**Informations techniques depuis MediaInfo**
- Résolution (720p, 1080p, 4K)
- Codec vidéo (H.264, HEVC, AV1)
- Type HDR (Dolby Vision, HDR10, HDR10+)
- Pistes audio (langue, format, canaux)
- Sous-titres disponibles
- Taille du fichier et durée

**Captures d'écran automatiques**
- 4 screenshots pris à des moments intelligents du film
- Évite automatiquement l'intro et le générique
- Upload automatique sur ImgBB
- Insertion automatique dans la description BBCode

---

### 🎨 Créer vos présentations

**Templates BBCode personnalisables**
- Créez vos propres templates de présentation
- Interface visuelle avec aperçu en direct
- 40+ variables disponibles (titre, année, codec, acteurs, etc.)
- Cliquez sur une variable pour l'insérer

**Exemples de ce que vous pouvez créer**

Template minimaliste :
```
[b]{{title}}[/b] ({{year}}) - Note : {{rating_10}}
{{overview}}
Qualité : {{quality}} | Audio : {{audio_list}}
```

Template avec casting :
```
[center]{{poster_url}}[/center]

[b]🎬 {{title}}[/b] ({{year}})
⭐ Note TMDB : {{rating_10}}
🎭 Genres : {{genres}}

📖 Synopsis :
{{overview}}

👥 Casting :
{{cast_1_card}}
{{cast_2_card}}
{{cast_3_card}}

💿 Qualité : {{quality}}
🎵 Audio : {{audio_list}}
```

Template premium avec tout :
```
[center]{{backdrop_url}}[/center]

[size=200][b]{{title}}[/b][/size]
[i]{{tagline}}[/i]

⭐ {{rating_10}} | 🎬 {{director}} | 🌍 {{country}} | ⏱️ {{runtime}}

📖 SYNOPSIS
{{overview}}

👥 CASTING
{{cast_1_card}} {{cast_2_card}} {{cast_3_card}}
{{cast_4_card}} {{cast_5_card}} {{cast_6_card}}

💿 INFORMATIONS TECHNIQUES
Format : {{format}}
Vidéo : {{video_codec}} | {{resolution}}
HDR : {{hdr}}
Audio : {{audio_list}}
Sous-titres : {{subtitles}}
Taille : {{file_size}}

📸 CAPTURES D'ÉCRAN
{{screenshot_1}} {{screenshot_2}}
{{screenshot_3}} {{screenshot_4}}

🔗 LIENS
TMDB : {{tmdb_url}}
Bande-annonce : {{trailer_url}}
```

**Aperçu en direct**
- Le panneau de droite montre le rendu en temps réel
- Testez votre template avec de vraies données
- Modal plein écran pour voir le résultat final

---

### 🌐 Gérer plusieurs trackers

**Multi-tracker sans effort**
- Ajoutez autant de trackers que vous voulez
- Un seul upload → publié sur tous les trackers actifs
- Chaque tracker a son propre fichier .torrent optimisé

**Trackers supportés**
- **La Cale** (avec Cloudflare)
- **C411** (avec piece size spécial)
- **Generic** (pour tout autre tracker)

**Configuration facile**
- Page dédiée `/trackers` pour tout gérer
- Test de connexion en un clic
- Active/désactive un tracker sans le supprimer
- Configuration du piece size automatique ou manuel

**Cross-seeding intelligent**
- Chaque tracker a un hash de torrent unique
- Possibilité de seed sur plusieurs trackers en même temps
- Évite les conflits de fichiers

---

### 🔍 Éviter les doublons

**Vérification automatique avant upload**

Avant chaque upload, l'application vérifie :
1. Ce film existe-t-il déjà sur le tracker ?
2. Dans la même qualité (1080p, 2160p, etc.) ?
3. Faut-il vraiment l'uploader ?

**Méthodes de détection**
- Par **TMDB ID** (le plus précis)
- Par **IMDB ID** (si pas de TMDB)
- Par **nom du film** (en dernier recours)

**Rapport détaillé**
- Liste de tous les doublons trouvés
- Taille, seeders, leechers
- Lien direct vers le torrent existant
- Décision : continuer ou annuler l'upload

---

### 📊 Suivre vos statistiques

**Dashboard avec graphiques**
- Nombre d'uploads par jour/semaine/mois
- Répartition par tracker (pie chart)
- Top contenus uploadés (bar chart)
- Taux de succès/échec

**Filtres temporels**
- 7 derniers jours
- 30 derniers jours
- 90 derniers jours
- Année complète

**Export des données**
- Export CSV de toutes les stats
- Utilisez Excel ou Google Sheets pour analyse
- Historique complet de vos uploads

---

### 🔔 Recevoir des notifications

**Discord**
- Notifications riches avec couleurs (vert = succès, rouge = échec)
- Informations : titre, tracker, taille, qualité
- Liens directs vers TMDB et tracker
- Embed avec poster du film

**Email**
- Templates HTML professionnels
- Récapitulatifs quotidiens ou hebdomadaires
- Support TLS/SSL sécurisé

**Événements notifiés**
- ✅ Upload réussi
- ❌ Upload échoué
- ⚠️ Doublon détecté
- 📦 Batch terminé
- 🔴 Erreur critique

---

### 🔄 Intégration Prowlarr

**C'est quoi Prowlarr ?**
Prowlarr est un gestionnaire centralisé de trackers. Vous configurez tous vos trackers une seule fois dans Prowlarr, et toutes vos applications peuvent les utiliser.

**Import automatique**
1. Configurez vos trackers dans Prowlarr
2. Connectez Seedarr à Prowlarr (URL + API key)
3. Cliquez sur "Import from Prowlarr"
4. ✅ Tous vos trackers sont importés automatiquement

**Recherche multi-tracker**
- Prowlarr interroge tous vos trackers en une seule requête
- Détection de doublons ultra-rapide
- Statistiques centralisées

**Synchronisation**
- Modification d'une passkey dans Prowlarr = mise à jour automatique dans TP
- Plus besoin de configurer deux fois

---

### ⚙️ Automatisation avancée

**Queue persistante**
- La file d'attente survit aux redémarrages
- Priorités : High > Normal > Low
- Retry automatique sur erreurs réseau (3 tentatives max)

**Renommage universel**
- Format standard : `Title.Year.Resolution.Codec-GROUP`
- Compatible avec tous les trackers
- Détection automatique de la source (BluRay, WEB-DL, etc.)

**Génération de .torrent optimisée**
- Piece size adapté à la taille du fichier
- Source flag unique par tracker (pour cross-seeding)
- Announce URL sécurisée avec votre passkey

**Seeding automatique**
- Ajout automatique dans qBittorrent après upload
- Démarrage immédiat du seeding
- Organisation par catégories (optionnel)

---

### 🛠️ Fonctionnalités techniques

**Health Checks**
- `/health/live` : L'API est-elle active ?
- `/health/ready` : Tous les services sont-ils prêts ?
- `/health/detailed` : État détaillé de tous les services

**Rate Limiting**
- Protection contre les bans API
- TMDB : 4 requêtes/seconde max
- Trackers : 1 requête/seconde max
- Respect des Terms of Service

**Logging structuré**
- Logs au format JSON
- Correlation IDs pour tracer les requêtes
- Niveaux configurables (DEBUG, INFO, WARNING, ERROR)
- Rotation automatique des logs

**Sécurité**
- Protection contre path traversal
- Sanitization des entrées utilisateur
- Passkeys jamais exposées dans l'interface
- Support HTTPS et TLS

---

## Cas d'usage pratiques

### Cas 1 : Je veux uploader un film

1. Allez sur `/filemanager`
2. Naviguez vers votre dossier films
3. Cliquez sur le fichier → "Add to Queue"
4. Attendez 2-5 minutes (selon la taille)
5. ✅ Recevez une notification Discord
6. Le film est publié, le seeding a commencé

### Cas 2 : Je veux uploader 20 films d'un coup

1. Allez sur `/filemanager`
2. Cochez les 20 films
3. Cliquez "Batch Process"
4. Choisissez la priorité et le nombre de traitements simultanés (ex: 3)
5. Lancez le batch
6. Suivez la progression sur `/dashboard`
7. ✅ Notification quand tout est terminé

### Cas 3 : Je veux créer ma propre présentation

1. Allez sur `/bbcode-templates`
2. Cliquez "Nouveau template"
3. Donnez un nom : "Ma présentation films 1080p"
4. Cliquez sur les variables pour les insérer
5. Écrivez votre BBCode autour
6. Vérifiez l'aperçu à droite
7. Sauvegardez
8. Définissez comme template par défaut
9. ✅ Tous les prochains uploads utiliseront ce template

### Cas 4 : Je veux éviter d'uploader des doublons

1. Allez sur `/settings`
2. Activez "Duplicate Check" (activé par défaut)
3. Uploadez un film
4. Si un doublon existe, vous recevez un rapport :
   - "Avengers Endgame 1080p existe déjà sur La Cale"
   - "Taille : 15.2 GB, Seeders : 42"
5. Décidez : continuer ou annuler

### Cas 5 : Je veux uploader sur 3 trackers en même temps

1. Allez sur `/trackers`
2. Ajoutez vos 3 trackers (La Cale, C411, autre)
3. Activez les 3 trackers
4. Uploadez un film normalement
5. ✅ Le film est publié sur les 3 trackers
6. 3 fichiers .torrent sont créés (un par tracker)
7. qBittorrent seed les 3 en même temps

### Cas 6 : J'ai déjà Prowlarr configuré

1. Allez sur `/settings`
2. Entrez Prowlarr URL : `http://localhost:9696`
3. Copiez votre API key depuis Prowlarr
4. Sauvegardez
5. Allez sur `/trackers`
6. Cliquez "Import from Prowlarr"
7. ✅ Tous vos indexers sont importés

### Cas 7 : Je veux des notifications sur Discord

1. Créez un webhook dans votre serveur Discord
2. Copiez l'URL du webhook
3. Allez sur `/settings` → Section Notifications
4. Collez l'URL Discord webhook
5. Sauvegardez
6. ✅ Vous recevrez une notification à chaque upload

---

## Résumé en 12 points

1. ✅ **Upload automatisé** - Sélectionnez un fichier, tout se fait automatiquement
2. 🎬 **Métadonnées riches** - TMDB, MediaInfo, screenshots, casting
3. 🎨 **Templates personnalisables** - Créez vos présentations BBCode uniques
4. 🌐 **Multi-tracker** - Uploadez sur plusieurs trackers en un clic
5. 📁 **Création de .torrent** - Génération automatique avec piece size optimisé
6. 💾 **Import automatique qBittorrent** - Ajout et démarrage du seeding instantané
7. 🔍 **Détection de doublons** - Évitez les uploads inutiles
8. 📦 **Traitement par lots** - Uploadez 10, 20, 50 films d'un coup
9. 🔔 **Notifications** - Discord et email pour chaque événement
10. 📊 **Statistiques** - Dashboard avec graphiques et export CSV
11. 🔄 **Prowlarr** - Import automatique de vos indexers
12. 🛠️ **Production-ready** - Health checks, rate limiting, retry automatique

---

## Questions fréquentes

**Q: Combien de temps prend un upload ?**
R: 2-5 minutes par film (scan, analyse, TMDB, screenshots, upload). Les fichiers plus gros prennent plus de temps pour les screenshots.

**Q: Puis-je uploader sans screenshots ?**
R: Oui, les screenshots sont optionnels. Si FFmpeg n'est pas installé, le pipeline continue sans eux.

**Q: Combien de trackers puis-je configurer ?**
R: Autant que vous voulez. Pas de limite.

**Q: Les templates BBCode sont-ils partagés entre trackers ?**
R: Oui, le même template est utilisé pour tous les trackers. Vous pouvez créer un template spécifique par tracker si besoin.

**Q: Que se passe-t-il si un upload échoue ?**
R: Le système retry automatiquement 3 fois. Si ça échoue encore, vous recevez une notification avec l'erreur détaillée.

**Q: Puis-je uploader des séries ?**
R: Pas encore dans la v2.4, mais prévu pour la v2.5 (voir ROADMAP.md).

**Q: FlareSolverr est-il obligatoire ?**
R: Seulement si votre tracker utilise Cloudflare (comme La Cale). Pour C411 ou d'autres trackers sans Cloudflare, ce n'est pas nécessaire.

**Q: Puis-je utiliser mes propres serveurs d'images ?**
R: Pour l'instant, seul ImgBB est supporté. D'autres hébergeurs (Imgur, Imgbox) sont prévus pour v2.5.

**Q: L'application stocke-t-elle mes passkeys en clair ?**
R: Les passkeys sont stockées dans la base de données SQLite locale. Elles ne sont jamais exposées dans l'interface (floutées) et jamais envoyées ailleurs que sur votre tracker.

---

## Pour aller plus loin

- **README.md** - Documentation technique complète
- **CLAUDE.md** - Architecture et structure du code
- **ROADMAP.md** - Fonctionnalités à venir et changelog
- **/docs** - Swagger UI pour l'API REST
- **/settings** - Configuration de l'application
- **/logs** - Logs en temps réel pour debugging

---

**Seedarr v2.4** - Automatisez vos uploads, profitez du résultat 🚀
