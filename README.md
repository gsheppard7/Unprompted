# Unprompted

A text chat that can start a turn on its own, and a local Brain that remembers the person it is talking with.

This is a local MVP: React, FastAPI, WebSockets, and SQLite. A visible rule-based policy chooses whether to speak. Direct replies can use an OpenAI-compatible model when a key is set. Without one, they use saved memories and say when they don't know.

The Brain is separate from conversation notes. It stores accepted facts about the user, where each fact came from, and which relationships were stated rather than inferred. Chat can retrieve a few relevant accepted memories. It does not save a turn into the Brain unless you ask.

Jordan Hale, the player in the sample conversation, is fictional. The demo ledger is not a live source, and the app says so when it cites it.

## Run

```bash
cd api
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

```bash
cd web
npm install
npm run dev
```

Open http://localhost:5173

Optional model, in a `.env` file at the repo root:

```bash
OPENAI_API_KEY=
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o-mini
```

The speak-or-silent decision stays on the local policy either way. The API key stays on the server. If it is set, Brain extraction, question wording, and summary refresh may send the relevant text to that provider. The Brain page says so before you teach it or upload a file. With no key, those steps use the on-device fallback.

## Try the chat

The seeded conversation, "Example: passer debate," includes one verified note: Jordan Hale averaged 9.1 assists per game in the 2025-26 season.

1. Leave proactivity on Conservative.
2. Send: `I think Jordan Hale was the best passer in the league last year.`
   Nothing should answer. Activity explains the silence.
3. Send: `No way, he barely averaged five assists.`
   After a short pause, it should correct the number and say it has not checked a live source.
4. Switch proactivity to Off. Further claims get a normal reply and no unsolicited message.
5. On Balanced, send `I wonder what Jordan Hale averaged last season.` Leave the conversation open. After about ten quiet seconds it can bring the saved note back.

`remember that ...` stores a verified fact for that conversation. Deleting the fact stops it from being cited. Those notes are not copied into the Brain.

Idle follow-ups run only while that conversation's socket is open. The socket closes when you leave the chat for the Brain tab, and closing the page does not send anything.

A user message that looks like a self-description gets a **Suggest saving to Brain** action. Nothing is stored until you accept the extracted candidates.

## Try the Brain

Open **Brain** in the sidebar.

1. On Teach, paste: `I'm a computer science student interested in robotics, machine learning, and software engineering. I enjoy climbing and baseball.`
2. Accept the separate memories. The original paragraph stays on the source.
3. Overview compiles a summary from accepted memories only. Refresh asks the model to write a narrative when a key is set, and keeps the compiled summary beside it.
4. Questions suggests at most three gaps. Answer, skip, or dismiss them. Dismissed questions are not asked again. Pause stops new suggestions.
5. Graph shows accepted entities and relationships. Solid edges were stated. Dashed edges were inferred.
6. Upload accepts `.txt`, `.md`, and `.pdf` up to 2 MB. The file is read locally. DOCX is rejected until an extractor is added.
7. Export downloads the Brain as JSON. Clear removes the local Brain and leaves conversations in place.

## Tests

```bash
cd api
.venv/bin/pytest
.venv/bin/python -m app.eval
```

The labeled set compares the speak/silent policy with three baselines: never speak, always speak, and a lower-threshold score with no evidence gate. Brain tests cover extraction, review, deduplication, uploads, question state, retrieval, the graph, and the boundary with conversation memory.

## Architecture

Chat and Brain share one SQLite file (`api/data/unprompted.db`, or `UNPROMPTED_DB`). New tables are created with `CREATE TABLE IF NOT EXISTS` on startup. Existing conversation tables are left as they are.

```text
api/app/brain/
  store.py        SQLite tables and writes
  extract.py      pattern fallback and validated model JSON
  ingest.py       txt, markdown, and pdf text
  retrieve.py     FTS or token overlap; the function signature is the vector-search seam
  synthesize.py   summary compiled only from accepted memories
  questions.py    gap ranking, with optional model rephrasing
  service.py      teach, review, graph, questions, chat retrieval
  hooks.py        observer seam for a future cognition loop
  routes.py       /brain REST routes and /ws/brain
```

The chat runtime calls `retrieve_memories` for the latest turn and passes a handful of accepted notes to the reply. Explicit accepted notes can be cited. Inferences are labeled and are not treated as verified evidence for an interruption. Proposed, rejected, and superseded notes stay out of the prompt.

`hooks.notify` records that a memory or question changed. A later process can observe those events, retrieve memories, and form candidate thoughts. It does not speak. Sending a message remains the chat policy's decision.

### Schema

| Table | Role |
| --- | --- |
| `brain_profiles` | One local profile: name, question pause, optional model narrative |
| `brain_sources` | Original text, filename, and how it arrived |
| `brain_memories` | Content, category, status, stance, heuristic score, validity, source |
| `brain_entities` | People, fields, interests, projects, tools |
| `brain_relationships` | Typed edges, each tied to the memory that produced them |
| `brain_questions` | Pending, answered, skipped, or dismissed, with a stable gap key |
| `brain_changes` | Short activity log |
| `brain_memory_fts` | Full-text index. Search falls back to token overlap if FTS5 is missing |

Memory status is `proposed`, `accepted`, `rejected`, or `superseded`. Stance is `explicit`, `inferred`, or `hypothesis`. The numeric score is fixed for that stance. It is not a probability, including when a model produced the wording.

Editing a memory does not rewrite its source. Superseding marks the older memory and keeps it. Deleting a memory removes it from the Brain. The activity log can still mention it until the Brain is cleared. Clearing does not delete conversation history, and it cannot delete text a model provider already received.

There is no account system. Every Brain route uses the single local profile, and the API is meant to stay on localhost. Do not expose it on an open network without authentication.

### Brain routes

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/brain` | Overview, compiled summary, counts |
| PATCH | `/brain` | Rename, pause or resume questions |
| DELETE | `/brain?confirm=true` | Clear the local Brain |
| POST | `/brain/summary` | Refresh the summary |
| GET | `/brain/export` | JSON export |
| POST | `/brain/teach` | Extract candidates from typed text |
| POST | `/brain/upload` | Extract candidates from a file |
| GET | `/brain/memories` | List or search |
| PATCH, DELETE | `/brain/memories/{id}` | Edit or delete |
| POST | `/brain/memories/{id}/accept` and `/reject` | Review one memory |
| POST | `/brain/memories/review` | Accept and reject a batch |
| GET | `/brain/graph` | Nodes and edges |
| GET | `/brain/retrieve?q=` | Retrieval used by chat |
| GET | `/brain/sources/{id}` | Original source text |
| GET | `/brain/questions` | Up to three pending questions |
| POST | `/brain/questions/generate` | Suggest, or pass `{"refresh": true}` for different ones |
| POST | `/brain/questions/{id}/answer`, `/skip`, `/dismiss` | Record what happened |
| WS | `/ws/brain` | `brain.updated` after a change |

`/ws/brain` tells the open Brain page to reload. Chat stays on its own socket.

## What uses a model

With `OPENAI_API_KEY` set, the configured chat model can be called for:

- direct chat replies
- splitting teach, upload, and question answers into memory candidates
- rephrasing questions that were already chosen from real gaps
- an optional narrative on summary refresh

The provider sees the text needed for that call. Candidates are schema-checked, and invalid items are dropped. If the call fails or no key is set, the on-device fallback runs. That fallback recognizes patterns such as study, interests, projects, and tools. It does not understand arbitrary prose. Question ranking does not need a model. The speak/silent policy never calls one.

## Limits

The fallback extractor misses wording it does not recognize. Uploaded text kept in SQLite is capped at 50,000 characters and PDFs at 30 pages. Retrieval is keyword search, not embeddings. There is one local user. Confidence scores are labels, not calibrated probabilities. A deleted memory can remain in the activity log until you clear the Brain, and in a provider's logs if that text was sent. The graph shows accepted entities and relationships, not every word.

## Left for later

The speak/silent policy is hand-written. A future cognition loop can subscribe through `hooks.register`, watch memory changes and open questions, and decide whether a thought is worth saying. That loop is not implemented. Embedding search, DOCX ingestion, accounts, and voice are not implemented either.
