# ConvoBox Web UI Architecture

## Overview

A lightweight, local-only web UI that streams live ConvoBox events to a browser while maintaining persistent history in SQLite. Shares the event model with the existing TUI.

**Design principles:**
- Local-only by default (127.0.0.1, not 0.0.0.0)
- Persistent history via SQLite (working directory)
- Real-time event streaming (Server-Sent Events)
- Reuse existing Orchestrator event stream
- Privacy-first: history is sensitive, gitignored by default
- Optional: users can enable history tracking in private repos

---

## Storage Layer

### SQLite Schema

```sql
-- Single session's entire history
CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,                      -- UUID, e.g., 2026-07-23T143022
    timestamp REAL NOT NULL,                       -- time.monotonic(), for ordering
    event_type TEXT NOT NULL,                      -- "transcript", "response", "approval", "tool_call", etc.
    user_transcript TEXT,                          -- STT output (user spoke this)
    backend_response TEXT,                         -- LLM/agent response
    tool_name TEXT,                                -- approval: tool being called
    tool_input TEXT,                               -- approval: JSON input
    approval_explanation TEXT,                     -- what was shown to user before approval
    user_decision TEXT,                            -- "approve", "deny", "explain"
    backend_event_json TEXT NOT NULL,              -- full BackendEvent as JSON (for replay/export)
    created_at TEXT NOT NULL                       -- ISO 8601 timestamp, for UI/export
);

CREATE INDEX idx_session_timestamp ON events(session_id, timestamp);
CREATE INDEX idx_event_type ON events(event_type);
```

### Session Lifecycle

```python
# convobox/web/history.py
class HistoryDB:
    """SQLite-backed event storage and query interface."""
    
    def __init__(self, db_path: Path = Path(".convobox-history/events.db")):
        self.db = sqlite3.connect(db_path)
        self._ensure_schema()
    
    def append_event(
        self,
        session_id: str,
        backend_event: BackendEvent,
        approval_explanation: str | None = None,
        user_decision: str | None = None
    ) -> None:
        """Write event to database."""
        # Extract fields from backend_event
        # Store full JSON in backend_event_json for replay
        # Store parsed fields for indexing/filtering
    
    def get_session_events(
        self,
        session_id: str,
        limit: int = 1000,
        offset: int = 0
    ) -> list[Event]:
        """Load events for a session, most recent first."""
    
    def get_active_session(self) -> str:
        """Return the most recent session_id."""
    
    def list_sessions(self) -> list[tuple[str, datetime]]:
        """List all sessions: [(session_id, last_activity), ...]."""
    
    def export_session_json(self, session_id: str) -> str:
        """Export session as JSON for backup/sharing."""
    
    def clear_session(self, session_id: str) -> None:
        """Delete all events for a session."""
    
    def close(self) -> None:
        """Close database connection."""
```

---

## Backend (FastAPI)

### Server Structure

```
convobox/web/
├── app.py              # FastAPI app, routes, startup/shutdown
├── history.py          # SQLite access layer
├── events.py           # Event model, normalization
├── stream.py           # SSE event broadcasting (in-memory for now)
└── config.py           # Web config (bind_address, port, history_dir)
```

### Core Server (`convobox/web/app.py`)

```python
from fastapi import FastAPI, BackgroundTasks
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import asyncio
import json

app = FastAPI()

# CORS: localhost only by default
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:*", "http://localhost:*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

db: HistoryDB | None = None
event_queue: asyncio.Queue = asyncio.Queue()

@app.on_event("startup")
async def startup():
    global db
    db = HistoryDB(Path(".convobox-history/events.db"))
    db.db.execute("PRAGMA journal_mode=WAL")  # Allow concurrent reads

@app.on_event("shutdown")
async def shutdown():
    if db:
        db.close()

# ===== REST API =====

@app.get("/api/sessions")
async def list_sessions():
    """List all sessions."""
    sessions = db.list_sessions()
    return {"sessions": [{"id": s[0], "last_activity": s[1].isoformat()} for s in sessions]}

@app.get("/api/sessions/{session_id}/events")
async def get_session_events(session_id: str, limit: int = 100, offset: int = 0):
    """Load historical events for a session."""
    events = db.get_session_events(session_id, limit, offset)
    return {"events": [e.dict() for e in events]}

@app.post("/api/sessions/{session_id}/clear")
async def clear_session(session_id: str):
    """Delete all events for a session (security/cleanup)."""
    db.clear_session(session_id)
    return {"status": "cleared"}

@app.get("/api/sessions/{session_id}/export")
async def export_session(session_id: str):
    """Export session as JSON (for backup/sharing via secure channel)."""
    json_data = db.export_session_json(session_id)
    return StreamingResponse(
        iter([json_data]),
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename={session_id}.json"}
    )

# ===== Server-Sent Events (live stream) =====

@app.get("/api/events/stream")
async def stream_events(session_id: str = ""):
    """
    SSE stream: push live backend events to browser as they happen.
    
    Browser stays connected; on reconnect, client requests GET /api/sessions/{session_id}/events
    to fill gaps.
    """
    async def generate():
        while True:
            try:
                event = await asyncio.wait_for(event_queue.get(), timeout=30)
                yield f"data: {json.dumps(event.dict())}\n\n"
            except asyncio.TimeoutError:
                # Keep-alive heartbeat every 30s
                yield ": heartbeat\n\n"
    
    return StreamingResponse(generate(), media_type="text/event-stream")

# ===== Integration hook =====

def on_backend_event_for_web(
    event: BackendEvent,
    session_id: str,
    approval_explanation: str | None = None,
    user_decision: str | None = None
) -> None:
    """
    Called from run_convobox.py's _on_backend_event (or new web callback).
    Appends to history and broadcasts to connected browsers.
    """
    db.append_event(session_id, event, approval_explanation, user_decision)
    # Queue for live SSE broadcast
    asyncio.create_task(event_queue.put(event))

# ===== Health check =====

@app.get("/health")
async def health():
    return {"status": "ok"}
```

### Integration with run_convobox.py

Add a new callback alongside the existing TUI callback:

```python
# In scripts/run_convobox.py, around line ~1500 (where on_event is wired):

def _on_backend_event_web(
    event: BackendEvent,
    db_handler: WebUIEventHandler | None = None,
    session_id: str | None = None,
) -> None:
    """Forward events to the web UI history."""
    if db_handler and session_id:
        db_handler.append_event(event, session_id)

# Then wire it up:
if config.web and config.web.enabled:
    web_handler = WebUIEventHandler(...)
    # Create second callback that forwards to web
    original_on_event = on_event_func
    on_event = lambda e: (original_on_event(e), _on_backend_event_web(e, web_handler, session_id))
```

Or simpler: modify the existing `_on_backend_event` to also call web storage if enabled.

---

## Frontend (React/Vue)

### Project Structure

```
web/frontend/
├── src/
│   ├── components/
│   │   ├── Transcript.tsx      # Chat-like scrollable history
│   │   ├── ApprovalPanel.tsx   # Pending approvals with explain button
│   │   ├── EventLog.tsx        # Raw event view
│   │   └── SessionList.tsx     # Switch between sessions
│   ├── hooks/
│   │   ├── useEvents.ts        # Fetch + stream events
│   │   └── useApproval.ts      # Local approval decision logic
│   ├── App.tsx
│   └── index.css
├── public/
│   └── index.html
└── package.json
```

### Core Components (Simplified)

**useEvents hook:**
```typescript
// Fetch historical events + subscribe to live stream
const useEvents = (sessionId: string) => {
    const [events, setEvents] = useState<BackendEvent[]>([]);
    const [isConnected, setIsConnected] = useState(false);
    
    useEffect(() => {
        // Load historical events
        fetch(`/api/sessions/${sessionId}/events?limit=100`)
            .then(r => r.json())
            .then(data => setEvents(data.events));
        
        // Subscribe to live events
        const eventSource = new EventSource(`/api/events/stream?session_id=${sessionId}`);
        eventSource.onmessage = (e) => {
            const event = JSON.parse(e.data);
            setEvents(prev => [...prev, event]);
        };
        eventSource.onerror = () => setIsConnected(false);
        
        return () => eventSource.close();
    }, [sessionId]);
    
    return { events, isConnected };
};
```

**Transcript component:**
```typescript
// Chat-like UI: user utterances + backend responses
const Transcript = ({ events }: { events: BackendEvent[] }) => {
    return (
        <div className="transcript">
            {events.map(event => {
                if (event.type === "transcript") {
                    return <UserMessage key={event.id}>{event.user_transcript}</UserMessage>;
                }
                if (event.type === "response") {
                    return <AssistantMessage key={event.id}>{event.backend_response}</AssistantMessage>;
                }
                return null;
            })}
        </div>
    );
};
```

**Approval panel:**
```typescript
// Shows pending approval with context + buttons
const ApprovalPanel = ({ event, onDecide }: { event: ApprovalEvent, onDecide: (decision: string) => void }) => {
    return (
        <div className="approval">
            <h3>⚠️ Approval Needed</h3>
            <div className="context">
                <p><strong>Tool:</strong> {event.tool_name}</p>
                <p><strong>What:</strong> {event.approval_explanation}</p>
            </div>
            <div className="buttons">
                <button onClick={() => onDecide("explain")}>Explain</button>
                <button onClick={() => onDecide("approve")}>Approve</button>
                <button onClick={() => onDecide("deny")}>Deny</button>
            </div>
        </div>
    );
};
```

---

## Configuration

### Config Schema

```yaml
# convobox.yaml
web:
  enabled: false                             # Opt-in (default off)
  bind_address: 127.0.0.1                    # Localhost only
  port: 5173                                 # Vue/React dev port convention
  history_tracking_enabled: false            # Opt-in (requires privacy acknowledgment)
  history_dir: .convobox-history             # Gitignored
  cors_origins: ["http://127.0.0.1:*"]      # Whitelist browsers
```

### Config Class

```python
# src/convobox/config.py

class WebConfig(BaseModel):
    enabled: bool = False
    bind_address: str = "127.0.0.1"
    port: int = 5173
    history_tracking_enabled: bool = False
    history_dir: str = ".convobox-history"
    
    @field_validator("bind_address")
    @classmethod
    def _validate_bind_address(cls, v: str) -> str:
        if v not in ("127.0.0.1", "localhost", "0.0.0.0"):
            if not v.startswith("127.") and v != "::1":  # IPv4 loopback or IPv6
                raise ValueError(
                    f"bind_address {v!r} is remote. "
                    "Set to 127.0.0.1 (localhost) for safety. "
                    "To allow remote access, explicitly set to 0.0.0.0 (and ensure private network)."
                )
        return v

class ConvoBoxConfig(BaseModel):
    # ... existing fields ...
    web: WebConfig | None = None
```

---

## Startup & Integration

### Minimal Launch Script

```bash
# Start ConvoBox with web UI:
python scripts/run_convobox.py --web

# Or enable via config:
# web:
#   enabled: true
```

### Multi-Process Launch (Optional)

For development, can run web server in separate process:

```bash
# Terminal 1: Start ConvoBox normally
python scripts/run_convobox.py

# Terminal 2: Start web server (reads from same .convobox-history DB)
python -m convobox.web.app --port 5173
```

Decoupling is optional; can also embed in run_convobox.py with asyncio background tasks.

---

## Security Considerations

### Network
- Bind to 127.0.0.1 by default (localhost only)
- Config validator warns if attempting 0.0.0.0 without explicit acknowledgment
- No authentication (local device trust model)

### Data at Rest
- SQLite in `.convobox-history/` (gitignored by default)
- File permissions: 600 on Unix/macOS (owner-readable only)
- No encryption (to-do for future if repo goes remote)

### Data in Transit
- SSE over HTTP (localhost only, so no HTTPS needed)
- Full BackendEvent JSON streamed (contains transcripts, approvals)
- Users responsible for not exposing the web server to untrusted networks

### XSS Prevention
- Escape tool_input, command output before rendering
- Sanitize user transcripts (unlikely to contain HTML, but defensive)

---

## Phase 1 MVP

**Scope (Phase 1):**
- ✅ SQLite schema + HistoryDB class
- ✅ FastAPI app with /api/sessions, /api/events/stream
- ✅ React frontend: Transcript + ApprovalPanel
- ✅ Integration: run_convobox.py → HistoryDB
- ✅ Config: web.enabled, web.history_tracking_enabled
- ✅ Security: localhost-only bind, privacy docs

**Out of scope (Phase 2+):**
- Persistent browser UI state (sidebar collapse, scroll position)
- Advanced filtering/search
- Session comparison
- Export to PDF/CSV
- Remote access (requires encryption, auth)
- Approval UI wiring (approve/deny from browser)

---

## Testing

### Unit Tests
- HistoryDB: append, query, export
- Config validation: bind_address warnings
- Event model: normalization

### Integration Tests
- SSE stream: client connects, receives events
- Session lifecycle: create, append, query, clear
- CORS: localhost allowed, remote blocked

### Manual Testing
1. Start ConvoBox with web UI enabled
2. Open http://localhost:5173
3. Speak to trigger events
4. Verify transcript appears in browser (real-time SSE)
5. Trigger approval, verify in panel
6. Approve from browser or TUI (whichever works first)
7. Refresh browser, verify history loads

---

## Next Steps

1. **Implement HistoryDB** (convobox/web/history.py)
   - SQLite schema initialization
   - CRUD operations
   - Query interface

2. **Implement FastAPI app** (convobox/web/app.py)
   - Routes: /api/sessions, /api/events/stream
   - Event broadcasting (asyncio.Queue)
   - Health check + startup/shutdown

3. **Integrate with run_convobox.py**
   - Pass session_id to callbacks
   - Wire web event handler alongside TUI
   - Add --web flag

4. **Implement React frontend** (web/frontend/)
   - Fetch + stream events
   - Render Transcript + ApprovalPanel
   - Session switching

5. **Add config schema** (src/convobox/config.py)
   - WebConfig model
   - Validation (bind_address warning)
   - Integration into ConvoBoxConfig

6. **Documentation**
   - Update README: web UI section
   - Add docs/WEB-UI-USAGE.md for users
   - Add docs/WEB-UI-DEV.md for contributors

---

**Status:** Architecture complete, ready for implementation.  
**Estimated effort:** Phase 1 = ~3-4 days (1 person, full-time).
