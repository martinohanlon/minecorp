# MineCorp agent service

Receives questions from the Minecraft plugin over HTTP and answers them. **Stub stage:** it returns a canned reply; the LLM loop and the graph tools come next (see [docs/architecture.md](../docs/architecture.md)).

## Run

From the project root (needs the `.venv`; `python -m venv .venv` if it does not exist):

```powershell
./scripts/run-agent.ps1
```

It installs the requirements, reads settings from `.env` and listens on `http://127.0.0.1:8080`. Stop it with Ctrl+C.

Settings (environment or `.env`; see `.env.example`):

| Name | Default | Notes |
|---|---|---|
| `AGENT_SHARED_SECRET` | none, required | 16+ characters. The plugin sends it in the `X-MineCorp-Key` header. Must match the plugin config. |
| `AGENT_HOST` | `127.0.0.1` | Keep on localhost. |
| `AGENT_PORT` | `8080` | |
| `OPENAI_API_KEY` | | Not used yet (LLM loop to come). |

## API

| | |
|---|---|
| `GET /health` | `{"status":"ok"}`; no secret needed |
| `POST /ask` | Header `X-MineCorp-Key: <secret>` (401 without it). JSON body: `playerUuid`, `playerName`, `sessionId`, `question` (1-500 characters). Reply: `{"requestId": "...", "answer": "..."}`. Invalid bodies return 422. |

Try it:

```powershell
curl http://127.0.0.1:8080/health
curl -X POST http://127.0.0.1:8080/ask -H "Content-Type: application/json" -H "X-MineCorp-Key: <secret>" `
  -d '{"playerUuid":"u1","playerName":"Steve","sessionId":"u1","question":"hello"}'
```

## Layout

```
agent/
  requirements.txt
  minecorp_agent/
    config.py   settings and .env loading
    core.py     answer(question) -> text; independent of the transport (HTTP now, MCP possible later)
    app.py      FastAPI app: /health, /ask, shared-secret check
    __main__.py python -m minecorp_agent
```

`core.answer()` is the only place that will change as the agent grows.

## Use with the plugin

See [plugin/README.md](../plugin/README.md): build the plugin, start this service, start the Minecraft server, then type `/agent <question>`.
