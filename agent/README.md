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

## Tests

Two kinds, and they answer different questions.

**Tests** (`tests/agent/`, no model, no tokens) check that the code around the model behaves:

| File | Needs | What it checks |
|---|---|---|
| `test_tools_unit.py` | nothing | the tool registry and the specs sent to the model are valid; how recipes, requirements and routes are written as text; how conditions are put into words |
| `test_agent_loop.py` | nothing | the tool-calling loop against a scripted fake model: tool calls and results, a tool that raises, over-long results cut at the cap, the turn limit (last turn forbids tools, fallback answer), model failures, trace lines written |
| `test_config.py` | nothing | `.env` reading (never overrides real variables) and the settings: secret of 16+ characters, safe defaults (localhost), overrides and types |
| `test_app.py` | nothing | `/health`, the secret check (exact match only, done before the body is read), field limits, one request id per call, start-up problems turned into a friendly message |
| `test_tools_graph.py` (marker `integration`) | Neo4j Enterprise and the Paper jar | every tool against the real graph, rebuilt from source in a throwaway database: name resolution and ambiguity, recipe shapes, categories and exclusions, limit clamping, `how_to_get` wording and ordering, and `what_do_i_need` on 47 items to prove every step only uses things gathered or made before it |

**Evals** (`agent/evals`, calls the model, costs tokens) check that the model chooses the right tools and gives a good answer. Run them after changing the prompt or a tool description, not on every change:

```powershell
cd agent
python -m evals.run_evals
```

Run the tests from the project root with `./scripts/test.ps1` (add `-Integration` for the graph tests). More in [../tests/README.md](../tests/README.md).
