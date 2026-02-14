# Synapse als Engram Pro Feature — Implementierungsplan

**Erstellt:** 2026-02-14
**Status:** Planung (noch nicht implementiert)

---

## Übersicht

Synapse (Agent-to-Agent Message Bus) wird als exklusives **Engram Pro Feature** integriert.
Free-User bekommen Engram Memory pur. Pro-User bekommen zusätzlich den Synapse Message Bus
für Multi-Agent-Kommunikation.

**Kernprinzip:** `engram-core` bleibt schlank. Synapse ist ein optionales Extra (`pip install engram[synapse]`).

---

## 1. engram-core Package (`pyproject.toml`)

### 1.1 Neues Optional Extra

```toml
[project.optional-dependencies]
synapse = ["synapse-bus>=0.1.0"]
embeddings = ["sentence-transformers>=2.2.0"]
all = ["synapse-bus>=0.1.0", "sentence-transformers>=2.2.0"]
```

**Warum:** Synapse-Bus ist bereits als eigenständiges PyPI-Package veröffentlicht (`synapse-bus==0.1.0`).
Das Extra installiert es als Dependency, ohne den Core aufzublähen.

### 1.2 Neues Tier-Limit in `server/tiers.py`

```python
@dataclass
class TierLimits:
    # ... bestehende Felder ...
    synapse_bus: bool = False          # Zugang zum Synapse Message Bus
    synapse_channels: int = 0          # Max eigene Channels (0 = kein Zugang)
    synapse_messages_per_day: int = 0  # Max Messages/Tag (0 = unlimited)
```

**Tier-Werte:**

| Feature                   | Free  | Pro      | Enterprise |
|---------------------------|-------|----------|------------|
| `synapse_bus`             | False | True     | True       |
| `synapse_channels`        | 0     | 10       | 0 (unlim)  |
| `synapse_messages_per_day`| 0     | 50,000   | 0 (unlim)  |

### 1.3 Keine Änderung an `src/engram/`

Der Core-Library-Code (`client.py`, `sessions.py`, etc.) bleibt unberührt.
Synapse ist ein separater Service, kein Teil der Memory-API.

---

## 2. API Server (`server/`)

### 2.1 Neue Gate-Funktion in `server/api.py`

```python
def _check_synapse(user: AuthUser) -> None:
    """Block Synapse features for Free tier."""
    if not user.limits.synapse_bus:
        raise HTTPException(
            403,
            "Synapse Message Bus is a Pro feature. "
            "Upgrade at https://engram-ai.dev/#pricing"
        )
```

### 2.2 Neue Synapse-Proxy-Endpoints in `server/api.py`

Engram API Server wird zum **authentifizierten Proxy** für den Synapse Bus.
Der Synapse-Server selbst läuft weiterhin intern auf Port 8200 — die Engram API
leitet Pro-User-Requests an ihn weiter.

```
POST   /v1/synapse/publish          → Synapse POST /publish
GET    /v1/synapse/inbox             → Synapse GET  /inbox/{agent_name}
DELETE /v1/synapse/inbox             → Synapse DELETE /inbox/{agent_name}
GET    /v1/synapse/channels          → Synapse GET  /channels
GET    /v1/synapse/history/{channel} → Synapse GET  /history/{channel}
GET    /v1/synapse/agents            → Synapse GET  /agents
POST   /v1/synapse/agents/register   → Synapse POST /agents/register
WS     /v1/synapse/ws                → Synapse WS   /ws/{agent_name}
```

**Jeder Endpoint:**
1. Prüft Auth (JWT oder API Key)
2. Prüft `_check_synapse(user)`
3. Prüft Rate-Limits
4. Leitet an internen Synapse-Server weiter
5. Gibt Response zurück

### 2.3 Synapse-Router als eigenes Modul

Neues File: `server/synapse_routes.py`

```python
"""Synapse proxy routes — Pro-gated access to the message bus."""

from fastapi import APIRouter, Depends
import httpx

from server.auth.dependencies import get_current_user, AuthUser

router = APIRouter(prefix="/v1/synapse", tags=["synapse"])

SYNAPSE_INTERNAL = "http://localhost:8200"

@router.post("/publish")
async def proxy_publish(req: PublishRequest, user: AuthUser = Depends(get_current_user)):
    _check_synapse(user)
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{SYNAPSE_INTERNAL}/publish", json=req.dict())
    return resp.json()

# ... analog für alle Endpoints
```

**Include in `server/api.py`:**
```python
from server.synapse_routes import router as synapse_router
app.include_router(synapse_router)
```

### 2.4 Synapse-Nutzung tracken

Neues Feld in User-DB oder separater Counter:
- `synapse_messages_today: int` — Reset um Mitternacht UTC
- Enforcement in `proxy_publish()` gegen `user.limits.synapse_messages_per_day`

### 2.5 MCP Server (`mcp_server/`)

Keine Änderung am MCP Server nötig. Der MCP Server läuft lokal und
verbindet direkt zum Synapse-Bus. Pro-Gating betrifft nur die Cloud-API.

Lokale Nutzer (pip install engram) haben immer vollen Synapse-Zugang
über den MCP Server — das ist das Engram-Prinzip: lokal = alles.

---

## 3. Landing Page (`website/index.html`)

### 3.1 Pro-Feature-Liste erweitern

Im Pricing-Card "Pro" (€14.90/mo) hinzufügen:

```
✓ Synapse Message Bus
✓ Multi-Agent Communication
✓ Real-time WebSocket Events
✓ 6 Built-in Channels
✓ 50,000 Messages/Day
```

### 3.2 Feature-Section hinzufügen

Neue Feature-Card nach den bestehenden 6 Features:

```html
<div class="feature-card">
  <div class="feature-icon">🔗</div>
  <h3>Synapse Message Bus</h3>
  <p>Connect your AI agents. Pub/sub channels, real-time events,
     and cross-agent communication — all through one bus.</p>
</div>
```

### 3.3 Vergleichstabelle erweitern

Neue Zeilen in der Feature-Comparison-Table:

| Feature              | Free | Pro    | Enterprise |
|----------------------|------|--------|------------|
| Synapse Message Bus  | —    | ✓      | ✓          |
| Synapse Channels     | —    | 10     | Unlimited  |
| Messages/Day         | —    | 50,000 | Unlimited  |
| WebSocket Streaming  | —    | ✓      | ✓          |
| Agent Dashboard      | —    | ✓      | ✓          |

### 3.4 Synapse Dashboard Link

Button oder Link im Pro-Bereich der Landing Page:
"View Synapse Dashboard →" → `https://synapse.engram-ai.dev/dashboard`

(Dashboard ist bereits live über Cloudflare Tunnel erreichbar.)

### 3.5 Hero/Tagline Update

Aktuelle Tagline: "Persistent memory for AI agents"

Vorschlag: "Memory + Communication for AI agents"
oder: "Memory & message bus for AI agents"

**Entscheidung:** Levent soll die finale Tagline bestätigen.

---

## 4. READMEs

### 4.1 Engram README (`engram/README.md`)

**Installation-Section erweitern:**

```markdown
## Installation

```bash
pip install engram              # Core memory
pip install engram[synapse]     # + Synapse message bus (Pro)
pip install engram[embeddings]  # + Semantic search
pip install engram[all]         # Everything
```

**Features-Section erweitern:**

```markdown
### Pro Features
- **Synapse Message Bus** — Pub/sub channels for multi-agent communication
- **WebSocket Streaming** — Real-time event delivery
- **Agent Dashboard** — Monitor all connected agents
- **Semantic Search** — Embedding-based memory retrieval
- **Sessions** — Save/restore conversation checkpoints
```

### 4.2 Synapse README (`synapse/README.md`)

**Integration-Section hinzufügen:**

```markdown
## Part of Engram Pro

Synapse is the communication layer of [Engram](https://engram-ai.dev).
Available as a Pro feature (€14.90/mo) or free for local/self-hosted use.

- **Cloud API:** Authenticated access via `api.engram-ai.dev/v1/synapse/*`
- **Self-hosted:** Run your own Synapse server with `synapse run`
- **MCP Integration:** Works with Claude Code, Cursor, and any MCP client

> Self-hosted Synapse is always free. Cloud-hosted Synapse requires Engram Pro.
```

### 4.3 PyPI Descriptions

Beide Packages (`engram` und `synapse-bus`) bekommen Cross-Links in ihren
PyPI long_description:

- engram → "Includes Synapse message bus as Pro feature"
- synapse-bus → "Part of the Engram ecosystem — engram-ai.dev"

---

## 5. Stripe/Billing-Änderungen

### 5.1 Price Description Update

In `server/billing/routes.py`:

```python
PRICE_CONFIG = {
    "pro": {
        "name": "Engram Pro",
        "description": "250K memories, semantic search, Synapse message bus, WebSocket, analytics",
        #                                                ^^^^^^^^^^^^^^^^^^^^ NEU
        "amount": 1490,
        "currency": "eur",
    },
}
```

### 5.2 Kein Preisänderung

Synapse ist inkludiert im Pro-Preis (€14.90/mo). Kein separates Add-on.
Das macht das Angebot attraktiver und die Billing-Logik einfach.

---

## 6. Implementierungsreihenfolge

### Phase 1: Backend (1 Session)
1. `server/tiers.py` — Neue `synapse_*` Felder zu `TierLimits`
2. `server/synapse_routes.py` — Neuer Router mit Proxy-Endpoints
3. `server/api.py` — Router includen + `_check_synapse()` Gate
4. Tests schreiben für Tier-Gating

### Phase 2: Package (1 Session)
5. `pyproject.toml` — `[synapse]` Extra hinzufügen
6. Bump Version auf 0.3.0 (oder nächste Minor)
7. Build + Test + PyPI Publish

### Phase 3: Frontend (1 Session)
8. `website/index.html` — Pricing Cards + Feature Section + Comparison Table
9. Deploy Landing Page auf Hetzner

### Phase 4: Docs (1 Session)
10. `engram/README.md` — Pro Features + Installation Extras
11. `synapse/README.md` — Engram Pro Integration Section
12. `server/billing/routes.py` — Description Update
13. Commits + Push + PyPI Republish

---

## 7. Was NICHT geändert wird

- **Synapse Server selbst** — Bleibt standalone auf Port 8200
- **Synapse MCP Server** — Bleibt direkte Verbindung (kein Pro-Gate lokal)
- **Genesis Bridge** — Bleibt unverändert
- **TESS Monitor** — Bleibt unverändert
- **Engram Core Library** — Kein neuer Code in `src/engram/`
- **Preis** — Bleibt €14.90/mo für Pro
- **Free Tier** — Bekommt keinen Synapse-Zugang in der Cloud, aber Self-hosted ist frei

---

## 8. Risiken & Offene Fragen

1. **httpx Dependency:** `server/synapse_routes.py` braucht `httpx` als async HTTP client.
   Bereits in `requirements.txt`? → Prüfen, ggf. hinzufügen.

2. **WebSocket Proxy:** WS-Proxy ist komplexer als HTTP-Proxy.
   Option A: `websockets` Library für WS forwarding.
   Option B: Pro-User verbinden direkt zum Synapse WS (mit Token-Auth).
   → **Empfehlung:** Option B (einfacher, weniger Latenz).

3. **Rate Limiting:** Synapse-Messages zählen gegen das tägliche Limit.
   Heartbeats (automatisch) sollten NICHT zählen.
   → Filter: `channel != "#heartbeat"` beim Counting.

4. **Agent Isolation:** In Cloud-Mode sollten Pro-User nur IHRE Agents sehen.
   → Namespace-Prefix: `{user_id}:agent-name` oder separater Synapse-Bus pro Tenant.
   → **Empfehlung:** Namespace-Prefix (einfacher, ein Bus für alle).

5. **Dashboard Auth:** Das Synapse Dashboard (`/dashboard`) ist aktuell public.
   Für Cloud-User: Auth-Check hinzufügen oder separates Pro-Dashboard.
   → Kann in Phase 2 gemacht werden. Erstmal: Dashboard nur für Self-hosted.
