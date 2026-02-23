# Agent Architecture & Context Management — Design Document

## 1. Problem Statement

The chat LLM is blind and powerless regarding the internal task/goal system:

1. **No API awareness** — System prompt says "helps with tasks" but the LLM knows nothing about the task schema, available actions, or how to invoke them.
2. **No tasks in chat context** — `_retrieve_context()` includes emails, events, and a goal *summary*, but NOT individual tasks.
3. **No write capability** — The LLM generates text only. "Remind me to call John" produces a text response but creates nothing.
4. **No manual task creation** — There is no `POST /api/tasks` endpoint.
5. **Unbounded context** — `context_budget_tokens = 8000` is defined in config but never used. Conversations accumulate forever with no summarization.
6. **Background agents not wired** — Scheduler never calls `extract_tasks()` or `update_goal_progress_from_sync()` (actually it does call them — verified in scheduler.py `_run_sync()`).

---

## 2. Design Principles

### 2.1 Lean Main Agent, Rich Secondary Agents

The main chat LLM should be **fast and brief**. It answers the user's question and moves on. Heavy lifting — task extraction, progress detection, fact learning, conversation compression — is **delegated to secondary agents** that run post-response using the cheap filter model.

The main LLM is told: *"You don't need to do everything. Background agents handle task creation, progress tracking, and memory. Focus on being helpful and concise."*

### 2.2 Model Assignment Rule

> **Filter model** (8B): anything that's classification, extraction, or structured JSON output.
> **Assistant model** (30B/80B): anything that requires judgment, synthesis, creativity, or multi-step reasoning.

The filter model handles high-frequency, low-latency work (every chat turn, every sync). The assistant model handles low-frequency, high-value work (user queries, strategy, reviews).

### 2.3 Context is a Budget, Not a Dump

Every token in the LLM's context window costs inference time and competes for attention. Context must be **budgeted, not dumped**. The system must:
- Enforce a total token budget that adapts to the model's context window
- Prioritize context by query relevance
- Compress old conversations instead of discarding them
- Use the DB as extended memory, pulling in detail on demand

### 2.4 Adaptive to Model Size

A user running Qwen3-0.6B-4bit on a MacBook Air needs different context budgets than one running Qwen3-80B on an M4 Max. The system must **scale context to the model**, not use a fixed budget.

---

## 3. Model Routing

### 3.1 Filter Model (8B) — Fast Structured Extraction

| Agent | Trigger | max_tokens | temp | Input |
|-------|---------|-----------|------|-------|
| **Intent Detector** | Every chat turn (post-response) | 512 | 0.1 | query + response + task/goal titles |
| **Conversation Tagger** | Every chat turn (post-response) | 128 | 0.1 | query + response |
| **Task Extractor** | Every sync cycle | 512 | 0.3 | email/event batches *(exists)* |
| **Progress Detector (sync)** | Every sync cycle | 512 | 0.2 | recent emails + goal titles *(exists)* |
| **Progress Detector (chat)** | Every chat turn (post-response) | 256 | 0.2 | query + response + goal titles *(exists)* |
| **Task Linker** | On task create | 256 | 0.1 | task title + goal titles |
| **Summary Compressor** | When active window > threshold | 256 | 0.2 | old turns + existing summary |
| **Fact Extractor** | During daily review | 256 | 0.1 | session summary + review |
| **Stale Task Detector** | Daily (before review) | 256 | 0.1 | overdue tasks + recent context |

### 3.2 Assistant Model (30B/80B) — Reasoning & Synthesis

| Agent | Trigger | max_tokens | temp | Input |
|-------|---------|-----------|------|-------|
| **Chat Handler** | User query | 2048 | 0.7 | full context window *(exists)* |
| **Onboarding** | First run | 2048 | 0.7 | observations + history *(exists)* |
| **Strategy Generator** | 6h cadence | 1024 | 0.4 | goal + profile *(exists)* |
| **Daily Review** | 6PM daily | 1024 | 0.4 | tasks + events + goals *(exists)* |
| **Proactive Suggestions** | On demand | 1024 | 0.5 | tasks + events + goals *(exists)* |
| **Goal Inference** | Weekly | 1024 | 0.4 | profile + patterns *(exists)* |
| **Weekly Reflection** | Sunday evening | 1024 | 0.4 | completed tasks + goal progress |
| **Tactical Planner** | On demand | 1024 | 0.4 | objective + context *(exists)* |

### 3.3 Combined Post-Chat Call

The three per-turn filter-model agents (Intent Detector, Conversation Tagger, Chat Progress Detector) share a single LLM call to minimize lock contention:

```
POST-CHAT AGENT (filter model, single call)
├── Intent detection  → task/goal/draft/memory intents
├── Topic tagging     → conversation classification
└── Progress signals  → goal progress from chat content
```

This runs **after** the chat response is fully streamed and the `_llm_lock` is released. One lock acquisition, ~0.5s total, invisible to the user.

---

## 4. Context Management

### 4.1 Adaptive Context Budget

The context budget scales with the model. Different models have different context windows (Qwen3-0.6B: 32K, Qwen3-8B: 128K, Qwen3-30B-A3B: 128K, Qwen3-80B: 128K). But raw context window ≠ usable budget — attention degrades with length, and larger context = slower inference.

**Model-adaptive budget profiles:**

```toml
# config.default.toml
[llm]
context_budget_tokens = 8000   # default, overridden by auto-detection

# Internal profiles (not in config, derived from model):
# ≤ 1B params  → budget = 2000 tokens  (keep it minimal)
# ≤ 8B params  → budget = 4000 tokens  (moderate context)
# ≤ 32B params → budget = 8000 tokens  (full context)
# > 32B params → budget = 12000 tokens (rich context)
```

Implementation: `_effective_budget(config)` reads the model ID, estimates param count from the name (already have `_parse_model_name()`), and returns the appropriate budget. If `context_budget_tokens` is explicitly set by the user, use that instead.

### 4.2 Budget Allocation

The budget is split across five slots. Allocation is **fixed by slot** (not query-dependent) for simplicity and predictability:

```
TOTAL BUDGET: B tokens (e.g. 8000)
─────────────────────────────────────
System prompt + profile   : 5% of B  (~400 tok)   FIXED
Current user query        : 5% of B  (~400 tok)   FIXED
Conversation memory       : 25% of B (~2000 tok)  SLIDING
Retrieved context         : 55% of B (~4400 tok)  DYNAMIC
Generation headroom       : 10% of B (~800 tok)   RESERVED
```

### 4.3 Conversation Memory — Three Tiers

```
┌─────────────────────────────────────────────┐
│  TIER 1: Active Window                      │
│  Last N turns, raw text, full fidelity      │
│  Target: 70% of conversation budget         │
│  Storage: conversations table               │
│  Eviction: oldest turns compress to Tier 2  │
└──────────────┬──────────────────────────────┘
               │ filter model compresses
┌──────────────▼──────────────────────────────┐
│  TIER 2: Session Summary                    │
│  Running summary of today's conversation    │
│  Target: 30% of conversation budget         │
│  Storage: profile_data["session_summary"]   │
│  Updated: when active window shifts         │
│  Reset: at end of day / daily review        │
└──────────────┬──────────────────────────────┘
               │ filter model extracts facts
┌──────────────▼──────────────────────────────┐
│  TIER 3: Learned Facts                      │
│  Permanent user preferences and facts       │
│  Part of profile (always in system prompt)  │
│  Storage: profile_data["learned_facts"]     │
│  Updated: during daily review               │
│  Examples: "prefers morning meetings"        │
│            "traveling to Munich in March"    │
│            "dislikes Slack notifications"    │
└─────────────────────────────────────────────┘
```

**Compression trigger**: After each chat turn, count the estimated tokens in the active window. If it exceeds 70% of the conversation budget, compress the oldest 2 turns into the session summary using the filter model.

**Session summary prompt** (filter model, ~256 tok output):

```
Compress these older conversation turns into a running session log.
Keep: decisions, tasks created/completed, facts shared, topics discussed.
Drop: greetings, pleasantries, verbose explanations.

Previous session summary: {existing_summary}

Turns to compress:
{old_turns}

Updated summary (max 150 words): /no_think
```

**Fact extraction** (filter model, during daily review):

```
From today's session summary, extract permanent user facts.
Only extract durable preferences and facts, not transient info.

Session summary: {session_summary}
Existing facts: {current_facts}

Return JSON: {"new_facts": ["fact1"], "obsolete_facts": ["old_fact"]} /no_think
```

### 4.4 Retrieved Context — Budget-Aware

Replace the current "grab everything" approach with truncation-aware retrieval:

```python
def _retrieve_context(query: str, store: Store, budget: int) -> str:
    """Retrieve context within a token budget."""
    parts = []
    remaining = budget

    # 1. Tasks (always included — they're short and high-signal)
    tasks = store.get_tasks(status="pending", limit=10)
    tasks_text = _format_tasks(tasks)
    tasks_tok = _estimate_tokens(tasks_text)
    if tasks_text and tasks_tok < remaining * 0.25:
        parts.append(tasks_text)
        remaining -= tasks_tok

    # 2. FTS email search (highest relevance)
    emails = store.search_emails(query, limit=8)
    emails_text = _format_emails_within_budget(emails, remaining * 0.4)
    parts.append(emails_text)
    remaining -= _estimate_tokens(emails_text)

    # 3. Events
    events = store.get_upcoming_events(days=7)
    events_text = _format_events_within_budget(events, remaining * 0.5)
    parts.append(events_text)
    remaining -= _estimate_tokens(events_text)

    # 4. Goals (fill remaining budget)
    goals_text = get_goals_summary(store, include_progress=True)
    goals_text = _truncate_to_budget(goals_text, remaining)
    parts.append(goals_text)

    return "\n\n".join(p for p in parts if p)
```

**Token estimation** — heuristic, no tokenizer needed:

```python
def _estimate_tokens(text: str) -> int:
    """Conservative estimate: ~4 chars per token for English."""
    return len(text) // 4 + 1
```

### 4.5 On-Demand Deep Context

When the user asks about a specific item and the summary isn't enough, the system can pull richer detail:

- **Specific email**: FTS returns ≤2 results → fetch full bodies, allocate more budget
- **Specific task**: search by title → include source email/event context
- **Specific goal**: include strategy text, all progress entries, linked tasks

This is driven by the _existing_ FTS search — low result count implies a specific query. No new mechanism needed.

### 4.6 Tasks FTS Index

Add FTS on tasks so users can ask about specific tasks:

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS tasks_fts USING fts5(
    title, description, content='tasks', content_rowid='id'
);
```

With triggers to keep it synced, mirroring the existing `emails_fts` pattern.

---

## 5. Chat LLM Awareness — Lean and Delegating

### 5.1 Updated System Prompt

The chat LLM needs to know: (a) what context it has, (b) that background agents handle actions, (c) to be concise.

```python
SYSTEM_PROMPT = """You are Giva, a personal assistant for email, calendar, tasks, and goals.

Current date and time: {now}

{profile_section}

## Your capabilities
- You can see the user's emails, calendar events, pending tasks, and active goals (included below as context).
- Background agents automatically detect and act on your conversations:
  • If the user mentions creating a task → it will be created automatically.
  • If the user reports progress on a goal → it will be logged automatically.
  • If the user shares a preference → it will be remembered automatically.
- You do NOT need to confirm these actions or ask "should I create a task?" — just respond naturally.

## Guidelines
- Be concise. Short, actionable answers. The user's tasks and goals are visible in the sidebar.
- Reference specific emails, events, or tasks by name when relevant.
- When the user asks about tasks: their pending tasks are in your context below.
- When the user asks to do something (create task, draft email, etc.): acknowledge naturally. A background agent will handle the action.
- If you lack information to answer, say so.
- Never fabricate emails, events, or tasks that aren't in your context."""
```

Key changes from current:
1. **Tells the LLM about background agents** — "it will be created automatically"
2. **Removes the need for action confirmation** — "you do NOT need to confirm"
3. **Enforces brevity** — "Be concise. Short, actionable answers."
4. **Mentions tasks and goals explicitly** in capabilities

### 5.2 Structured Action Tags (NOT used)

We considered having the LLM emit `[TASK: ...]` tags, but this is fragile and couples the LLM output format to the action system. Instead:

- The LLM responds naturally ("Sure, I'll note that down" / "Got it, calling John tomorrow")
- The Intent Detector (filter model, post-response) parses the *meaning* of the exchange and routes actions
- This decouples the chat experience from the action system completely

---

## 6. Trigger Map

```
TRIGGER                    │ AGENT(s) FIRED                │ MODEL  │ TIMING
───────────────────────────┼───────────────────────────────┼────────┼─────────────
Every chat turn            │ Combined Post-Chat Agent      │ Filter │ After response
  (after stream completes) │   ├ Intent Detector           │        │ ~0.5s total
                           │   ├ Conversation Tagger       │        │
                           │   └ Progress Detector (chat)  │        │
                           │                               │        │
                           │ Conversation Compressor       │ Filter │ If window > budget
                           │   (only when Tier 1 overflows)│        │ ~0.3s
                           │                               │        │
Every sync (15 min)        │ Task Extractor                │ Assist │ Background
                           │ Progress Detector (sync)      │ Filter │ Background
                           │                               │        │
Task status change         │ Task Linker (on create)       │ Filter │ Inline
                           │ Progress Aggregator (on done) │ Code   │ Inline
                           │                               │        │
Daily (review hour)        │ Stale Task Detector           │ Filter │ Pre-review
                           │ Daily Review                  │ Assist │ Scheduled
                           │ Fact Extractor                │ Filter │ Post-review
                           │ Session Summary → reset       │ Code   │ Post-review
                           │                               │        │
Weekly                     │ Goal Inference                │ Assist │ Scheduled
                           │ Weekly Reflection             │ Assist │ Scheduled
                           │                               │        │
6-hour cadence             │ Strategy Generator            │ Assist │ Scheduled
```

---

## 7. Knowledge Flow: Tasks → Goals → Long-term

### 7.1 Upward Promotion

```
SHORT-TERM TASKS
  │ completed → Progress Aggregator (code)
  │   "3/5 tasks done for objective X"
  ▼
MID-TERM OBJECTIVES  (goal.tier == "mid_term")
  │ all tasks done → suggest marking complete
  │ progress logged → Weekly Reflection (assist)
  │   "pattern: user consistently ships features → promote to career goal"
  ▼
LONG-TERM GOALS  (goal.tier == "long_term")
  │ quarterly Goal Inference (assist) reviews:
  │   - completed objectives
  │   - chat history themes
  │   - email patterns
  │   → proposes new long-term goals or retires stale ones
```

### 7.2 Downward Decomposition

```
LONG-TERM GOALS
  │ Strategy Generator (assist, 6h cadence)
  │   → proposes objectives + action items
  ▼
MID-TERM OBJECTIVES
  │ Tactical Planner (assist, on demand)
  │   → proposes tasks, email drafts, calendar blocks
  ▼
SHORT-TERM TASKS
  │ Task Extractor (assist, every sync)
  │   → extracts from emails/events
  │ Intent Detector (filter, every chat)
  │   → extracts from conversation
  │ Task Linker (filter, on create)
  │   → auto-links new tasks to matching objectives
```

### 7.3 Auto-Linking

When a task is created (from any source), the Task Linker runs:

```
Given this new task and the list of active goals, which goal (if any) is this task most relevant to?

Task: "{task_title}" — {task_description}
Goals:
- ID 1: Launch product beta (mid_term, career)
- ID 3: Improve fitness routine (long_term, health)
- ID 5: Close NTT Data deal (mid_term, career)

Return JSON: {"goal_id": N or null, "confidence": "high|medium|low"} /no_think
```

Only link if confidence is "high". This runs on the filter model (cheap, fast).

---

## 8. Implementation Phases

### Phase 0 — Foundation (no new LLM calls)

Zero cost, fixes broken fundamentals:

1. **Add pending tasks to `_retrieve_context()`** — include top 10 pending tasks in chat context.
2. **Enforce context budget** — use `config.context_budget_tokens` in `_retrieve_context()` with truncation.
3. **Add `_effective_budget(config)`** — auto-scale budget based on model param count.
4. **Update system prompt** — tell the LLM about tasks, goals, background agents, brevity.
5. **Add `POST /api/tasks`** — manual task creation endpoint.
6. **Add `update_task()` to Store** — full field update, not just status.
7. **Fix `_llm_lock` in scheduler** — scheduler must acquire the lock before LLM calls.

### Phase 1 — Post-Chat Agent Pipeline

First new LLM calls (filter model only):

1. **Combined post-chat agent** — intent detection + tagging + progress, single filter-model call.
2. **Action router** — process detected intents (create task, log progress, save fact).
3. **UI feedback** — broadcast `agent_actions` SSE events for toast notifications.
4. **Conversation compressor** — Tier 1 → Tier 2 when active window overflows.

### Phase 2 — Context Enrichment

1. **Budget-aware retrieval** — replace grab-everything with truncation-aware context building.
2. **Tasks FTS index** — searchable tasks.
3. **Task auto-linking** — filter model matches new tasks to goals.
4. **Session summary in context** — include Tier 2 summary in chat context.

### Phase 3 — Intelligence Loop

1. **Fact extraction** — Tier 2 → Tier 3 during daily review.
2. **Weekly reflection** — assistant model reviews completed tasks, proposes goal updates.
3. **Stale task detection** — flag overdue/orphaned tasks before daily review.
4. **Progress aggregation** — auto-log goal progress when linked tasks complete.

---

## 7. SwiftUI UI Design Guidelines

### 7.1 Design Principles (Apple HIG)

The menu bar app (420×520 popover) follows Apple Human Interface Guidelines:

1. **Progressive disclosure** — Infrequent system actions (Restart, Upgrade, Reset, CLI) live in the header gear menu, not the main surface. Only daily-use actions are always visible.
2. **Content-first** — Minimize chrome. Chat and task content fill the majority of the window. Status banners appear only when contextually relevant.
3. **Single source of truth** — `viewModel.serverPhase` drives all UI state. No shadow booleans. Computed properties derive from the phase.
4. **Native patterns** — Use `Menu` for grouped actions. Use `.segmented` picker for tabs. **No system dialogs**: `.confirmationDialog`, `.alert`, and `.sheet` do not work reliably inside `MenuBarExtra(.window)` — always use inline confirmation banners instead (see `MainPanelView.confirmationBanner(for:)`).
5. **Thin observer** — The SwiftUI app is a display layer. All orchestration (bootstrap, sync, onboarding, goal intelligence) is server-driven via SSE events.

### 7.2 Layout Structure

```
┌─────────────────────────────────┐
│ Giva    counts...        ●  ⚙  │  ← header: title, stats, connection dot, gear menu
│─────────────────────────────────│
│ [phase banner if relevant]      │  ← contextual: sync progress, onboarding, system action
│─────────────────────────────────│
│ [  Chat  |  Tasks  ] (segmented)│  ← tab picker
│─────────────────────────────────│
│                                 │
│    Chat or Task content area    │  ← fills remaining space
│                                 │
│─────────────────────────────────│
│ [error banner if present]       │  ← dismissible warning
│─────────────────────────────────│
│   🔄 Sync    🎯 Goals   📋 Rev │  ← primary actions only (2-3 buttons)
└─────────────────────────────────┘
```

### 7.3 Component Hierarchy

| Component | Location | Purpose |
|-----------|----------|---------|
| **Gear Menu** | Header, right side | System actions, profile, CLI, quit |
| **Phase Banner** | Below header | Sync progress, onboarding hint, system action status |
| **Tab Picker** | Below banner | Chat ↔ Tasks toggle |
| **Content Area** | Center (fills) | ChatView or TaskListView |
| **Error Banner** | Above actions | Dismissible error/warning |
| **Quick Actions** | Bottom bar | Sync, Goals, Review (when due) |

### 7.4 Action Classification

**Bottom bar** (always visible, daily-use):
- Sync — manual email/calendar sync
- Goals — open Goals window
- Review — daily review (conditional, only when due)

**Gear menu** (header, infrequent):
- Profile — view user analytics
- Open CLI — launch terminal REPL
- Restart Server — with inline confirmation banner
- Upgrade Code — with inline confirmation banner
- Reset All Data — with inline destructive confirmation banner
- Quit Giva

**Rule**: If a user does it less than once per day, it goes in the gear menu. If a user does it multiple times per session, it goes in the bottom bar.

### 7.5 Markdown Rendering

Assistant messages render Markdown using `AttributedString` with `.inlineOnlyPreservingWhitespace`. User messages render as plain text. Key implementation details:
- Fenced code blocks (` ``` `) are pre-processed into inline code spans
- Parsing failures fall back to plain text (safe during streaming)
- `MarkdownText` view in `ChatView.swift` handles this

### 7.6 State Management Rules

1. `serverPhase` is the **only** authoritative state variable. Everything else derives from it.
2. Transient action flags (`isRestarting`, `isUpgrading`, `isResetting`) are client-side overlays that auto-clear when the action completes.
3. `handleSessionEvent()` is the **only** function that mutates `serverPhase`.
4. All SSE events flow through a single session stream. No parallel polling.
5. The ViewModel never drives server state — it only observes and renders.
