# Tracker Configuration Guide

This directory contains YAML configuration files for tracker integration.
New trackers can be added without writing Python code - just create a YAML file.

## Quick Start

1. Copy `_template.yaml` to `your_tracker.yaml`
2. Fill in your tracker's API details
3. Add tracker to database with `adapter_type = 'config'`
4. Test with the health check endpoint

## Configuration Structure

```yaml
# Required sections
tracker:      # Tracker identity
auth:         # Authentication method
endpoints:    # API endpoints
upload:       # Upload field mappings

# Optional sections
cloudflare:   # Cloudflare bypass settings
options:      # Metadata mapping (language, quality, genre, etc.)
categories:   # Category mappings
torrent:      # Torrent generation settings
response:     # Response parsing
```

## Authentication Types

### Bearer Token (API Key in header)
```yaml
auth:
  type: "bearer"
  header: "Authorization"
  prefix: "Bearer "
```

### API Key (separate header)
```yaml
auth:
  type: "api_key"
  header: "X-API-Key"
```

### Passkey (in URL)
```yaml
auth:
  type: "passkey"
  passkey_param: "passkey"
```

### Cookie/Session (with FlareSolverr)
```yaml
auth:
  type: "cookie"
cloudflare:
  enabled: true
  service: "flaresolverr"
```

## Upload Field Types

| Type | Description | Example |
|------|-------------|---------|
| `file` | Binary file upload | Torrent, NFO |
| `string` | Text string | Title, description |
| `json` | JSON-encoded object | options, tmdb_data |
| `boolean` | true/false | is_exclusive |
| `repeated` | Multiple values (tags[]=1&tags[]=2) | tag_ids |
| `number` | Numeric value | category_id |

## Options Mapping

Options mapping converts metadata (resolution, language, genre) to tracker-specific IDs.

### Language Example
```yaml
options:
  language:
    type: "1"              # API option type
    multi_select: true
    default: [4]           # Multi
    auto_multi: true       # Add multi if both fr+en detected
    auto_multi_value: 4
    mappings:
      english: 1
      french: 2
      multi: 4
      vostfr: 8
```

### Quality Example
```yaml
options:
  quality:
    type: "2"
    multi_select: false
    default: 25
    mappings:
      2160p_web: 26
      1080p_web: 25
      720p_web: 24
      remux: 12
    resolution_fallback:
      2160p: 26
      1080p: 25
```

### Genre Example (TMDB mapping)
```yaml
options:
  genre:
    type: "5"
    multi_select: true
    tmdb_mappings:        # Map by TMDB genre ID
      28: 39   # Action
      35: 49   # Comedy
    name_mappings:        # Fallback by name
      action: 39
      comedy: 49
```

### Season/Episode Example
```yaml
options:
  season:
    type: "7"
    complete_value: 118   # Complete series
    base_value: 120       # S01=121, S02=122
    max_value: 150        # Max S30
  episode:
    type: "6"
    complete_value: 96    # Complete season
    base_value: 96        # E01=97, E02=98
    max_value: 116        # Max E20
```

## Category Mapping

Map media types to category IDs:
```yaml
categories:
  movie_4k: "42"
  movie_1080p: "6"
  tv_1080p: "7"
  tv_4k: "43"
  movie_category: "1"    # Parent category for movies
  tv_category: "2"       # Parent category for TV
```

## Response Parsing

Configure how to parse upload responses:
```yaml
response:
  success_field: "success"
  error_field: "error"
  torrent_id_field: "data.id"
  torrent_url_template: "{tracker_url}/torrent/{torrent_id}"
```

## Testing Your Config

1. Start the application
2. Add tracker to database:
   ```sql
   INSERT INTO trackers (name, slug, tracker_url, api_key, adapter_type, enabled)
   VALUES ('My Tracker', 'mytracker', 'https://api.example.com', 'key123', 'config', 1);
   ```
3. Test connection: `GET /api/trackers/{id}/test`
4. Check logs for detailed output

## Existing Configs

| File | Tracker | Auth Type |
|------|---------|-----------|
| `c411.yaml` | C411 | Bearer token |
| `lacale.yaml` | La Cale | Passkey + Cloudflare |

## Troubleshooting

- **Config validation errors**: Check required sections are present
- **Auth failures**: Verify api_key/passkey is set on tracker model
- **Category errors**: Ensure category IDs match tracker's actual IDs
- **Options not sent**: Check field names match tracker API docs
