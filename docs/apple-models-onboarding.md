# Architecture: Apple Foundation Models for Onboarding

## Problem

Today's first-launch flow downloads an 8B MLX model (~4.5 GB) before the user can do
anything — even before model selection and the onboarding interview. This creates a long
wait with a progress bar as the very first experience. The 8B model is used during
bootstrap for two things:

1. **Model recommendation** — LLM-guided scoring of HuggingFace candidates against
   the user's hardware.
2. **Onboarding interview** — multi-turn conversational interview to learn about
   the user (3–5 questions, profile extraction, goal seeding).

Both of these are language understanding + structured output tasks — exactly what Apple's
on-device Foundation Model (~3B, ~1.2 GB RAM, already on every Apple Intelligence device)
is designed for.

## Proposal

Use Apple's `FoundationModels` framework (macOS 26+) to conduct model selection and the
onboarding interview **entirely on the Swift side**, eliminating the need to download any
MLX model before the user's chosen models are ready. The bootstrap flow becomes:

```
OLD: unknown → download_8B (4.5 GB wait) → model_selection → download_user_models → ready → sync → onboarding (8B) → operational
NEW: unknown → apple_onboarding (instant) → model_selection → download_user_models → ready → sync → operational
```

The onboarding interview and model recommendation happen **immediately** on first launch
with zero download, using the Apple model that's already on-device.

## Apple Foundation Models — Key Characteristics

| Property | Value |
|---|---|
| Model size | ~3B parameters, ~1.2 GB RAM |
| Context window | 4,096 tokens (input + output combined) |
| Availability | macOS 26+, Apple Intelligence enabled |
| Cost | Free, on-device, no network required |
| Strengths | Language understanding, structured output (`@Generable`), tool calling |
| Weaknesses | Not suited for code gen, math, or deep factual Q&A |
| Streaming | Snapshot-based (`streamResponse`), not token-delta |
| Structured output | Constrained decoding via `@Generable` / `@Guide` macros |
| Tool calling | `Tool` protocol with `@Generable` arguments |

The 4K context window is sufficient for onboarding (each question-answer pair is ~200–400
tokens; 5 turns ≈ 2K tokens + system prompt ≈ 500 tokens = well within budget).

## Architecture

### Design Principles

1. **Swift-native onboarding** — The onboarding interview runs entirely in the Swift app
   via `FoundationModels`, not via the Python server. This means onboarding can start
   before the Python daemon is even running.

2. **Server stays authoritative** — The Python server's bootstrap state machine remains the
   single source of truth. The Swift app sends the completed profile to the server once
   models are downloaded, and the server persists it.

3. **Graceful degradation** — If Apple Intelligence is unavailable (older hardware, disabled
   by user, or pre-macOS 26), fall back to the current flow (download 8B first, then
   onboard via Python).

4. **Reuse prompt design** — The onboarding prompts and profile extraction schema stay
   conceptually the same, just expressed in Swift using `@Generable` structs instead of
   Pydantic models + regex parsing.

### New Bootstrap State Machine

```
                                    ┌─────────────────────────┐
                                    │   Apple Intelligence     │
                                    │   available?             │
                                    └────┬──────────┬──────────┘
                                         │ YES      │ NO
                                         ▼          ▼
                              ┌──────────────┐  ┌─────────────────────┐
                              │ apple_onboard │  │ downloading_default  │
                              │ (Swift-side)  │  │ _model (old flow)    │
                              └──────┬───────┘  └──────────┬──────────┘
                                     │                     │
                          onboarding │              old flow continues
                          + model    │              (8B → model select
                          selection  │               → download → sync
                          done       │               → onboarding → op)
                                     ▼
                              ┌──────────────┐
                              │ awaiting      │  ← model selection also
                              │ _model_select │    happens during apple
                              │ (or skip)     │    onboarding phase
                              └──────┬───────┘
                                     │
                                     ▼
                              ┌──────────────┐
                              │ downloading   │
                              │ _user_models  │
                              └──────┬───────┘
                                     │
                                     ▼
                              ┌──────────────┐
                              │    ready      │  ← server receives profile
                              └──────┬───────┘    from Swift app
                                     │
                                     ▼
                              ┌──────────────┐
                              │   syncing     │
                              └──────┬───────┘
                                     │
                                     ▼              (no server-side
                              ┌──────────────┐       onboarding needed)
                              │  operational  │
                              └──────────────┘
```

### Component Design

#### 1. `AppleModelService` (new Swift service)

A new `@Observable` service that wraps `FoundationModels.LanguageModelSession` and
provides onboarding + model recommendation capabilities.

```swift
// GivaApp/Services/AppleModelService.swift

import FoundationModels

@Observable
final class AppleModelService {

    // MARK: - Availability

    /// Whether Apple Intelligence is available on this device.
    static var isAvailable: Bool {
        SystemLanguageModel.default.isAvailable  // false on unsupported hardware
    }

    // MARK: - Session

    private var session: LanguageModelSession?

    // MARK: - Onboarding Interview

    /// Conduct one turn of the onboarding interview.
    /// Returns the assistant's visible response and any extracted profile updates.
    func conductInterview(
        history: [OnboardingMessage],
        userResponse: String?,
        observations: OnboardingObservations
    ) async throws -> OnboardingTurn {
        // Build instructions (system prompt equivalent)
        // Call session.respond(to:) or session.streamResponse(to:)
        // Parse structured output via @Generable types
    }

    // MARK: - Model Recommendation

    /// Given hardware specs and available models, recommend assistant + filter.
    func recommendModels(
        hardware: HardwareInfo,
        candidates: [ModelCandidate]
    ) async throws -> ModelRecommendation {
        // Use guided generation to produce structured recommendation
    }
}
```

**Key decisions:**
- Uses `LanguageModelSession` with `Instructions` for the system prompt.
- Each interview turn appends to the same session (stateful transcript).
- 4K context is managed by keeping observations concise and compressing
  history for turns > 3.
- Structured output via `@Generable` eliminates regex/JSON parsing.

#### 2. `@Generable` Data Models (new Swift file)

```swift
// GivaApp/Models/OnboardingModels.swift

import FoundationModels

/// Profile data extracted from an onboarding turn.
@Generable
struct ProfileUpdate {
    @Guide(description: "User's full name if mentioned")
    var name: String?

    @Guide(description: "User's primary occupation or role")
    var occupation: String?

    @Guide(description: "Key professional interests or domains")
    var interests: [String]?

    @Guide(description: "Work schedule preferences (e.g., '9-5', 'flexible')")
    var workSchedule: String?

    @Guide(description: "Communication style preferences")
    var communicationStyle: String?

    @Guide(description: "Whether the interview should continue (true) or is complete (false)")
    var continueInterview: Bool
}

/// A single onboarding turn response.
@Generable
struct OnboardingResponse {
    @Guide(description: "The visible message to show the user")
    var message: String

    @Guide(description: "Extracted profile updates from this turn")
    var profileUpdate: ProfileUpdate?
}

/// Model recommendation output.
@Generable
struct ModelRecommendation {
    @Guide(description: "Recommended assistant model HuggingFace ID")
    var assistantModel: String

    @Guide(description: "Recommended filter model HuggingFace ID")
    var filterModel: String

    @Guide(description: "One-sentence explanation of why these were chosen")
    var reasoning: String
}

/// Hardware info passed to model recommendation.
@Generable
struct HardwareInfo {
    var chipName: String
    var totalMemoryGB: Int
    var gpuCores: Int
}
```

**Why `@Generable`:** Apple's constrained decoding guarantees the output conforms to the
struct schema. No regex fallbacks, no JSON extraction, no multi-level parsing. The model
is post-trained on this exact schema format.

#### 3. `OnboardingViewModel` (new or extended ViewModel)

Manages the Swift-side onboarding flow. This is a new `@Observable` class that drives
the onboarding UI when Apple Intelligence is available.

```swift
// GivaApp/ViewModels/OnboardingViewModel.swift

@Observable
final class OnboardingViewModel {
    let appleModel: AppleModelService

    var messages: [OnboardingMessage] = []
    var isStreaming = false
    var isComplete = false
    var extractedProfile: ProfileData = .empty

    /// Start the onboarding interview (first question).
    func startInterview(observations: OnboardingObservations) async {
        // Ask Apple model to generate first question based on observations
        // Append assistant message to messages
    }

    /// User responds to a question.
    func respond(_ text: String) async {
        // Append user message
        // Call appleModel.conductInterview(...)
        // Parse OnboardingResponse
        // Merge profileUpdate into extractedProfile
        // If !continueInterview → isComplete = true
    }

    /// Send completed profile to the Python server.
    func submitProfile(via api: any APIServiceProtocol) async throws {
        // POST /api/onboarding/profile with extractedProfile
        // Server persists to DB, skips server-side onboarding
    }
}
```

#### 4. Server-Side Changes

Minimal changes to the Python server — it receives the completed profile from Swift
rather than conducting the interview itself.

**New endpoint:**
```
POST /api/onboarding/profile
Body: { "profile_data": { ... }, "onboarding_history": [ ... ] }

→ Persists to DB via store.update_profile()
→ Sets profile.onboarding_completed = True
→ Sets bootstrap checkpoint past onboarding
```

**Bootstrap state machine changes** (`bootstrap.py`):
- New checkpoint: `apple_onboarding` (optional, transient — means Swift is handling it).
- When the server receives `POST /api/onboarding/profile`, it treats onboarding as
  complete and advances directly from `ready` → `syncing` → `operational` (skipping the
  server-side onboarding phase).
- The `session_stream` lifecycle detects `onboarding_completed` in the profile and skips
  the onboarding phase entirely.

**No changes to existing onboarding code** — `intelligence/onboarding.py` remains as the
fallback path. The server's `session_stream` already checks `is_onboarding_needed()` and
skips if `profile.onboarding_completed` is true.

#### 5. Model Recommendation Without MLX

Currently, `models.py:recommend_models()` uses the 8B filter model to score candidates.
With Apple Foundation Models, this happens in Swift:

1. **Hardware detection** moves to Swift (or is fetched from `GET /api/hardware` once the
   server is up — but we want this before server startup).
   - Swift can use `ProcessInfo` + `sysctlbyname` directly for chip/RAM/GPU detection.
   - We already have hardware detection code on the Python side; mirror the essentials.

2. **Model candidate list** is fetched from HuggingFace API directly from Swift
   (or from a bundled/cached list for offline first-launch).
   - Alternatively, once the daemon is healthy, fetch via `GET /api/models/available`.

3. **LLM-guided recommendation** uses Apple's model with a prompt like:
   ```
   Given this Mac hardware: M4 Pro, 48GB RAM, 16 GPU cores
   And these available MLX models: [list with sizes]
   Recommend the best assistant model (largest that fits with 60% RAM headroom)
   and filter model (smallest Qwen3 ≤ 5GB).
   ```
   Output is a `ModelRecommendation` via guided generation.

4. **Fallback**: If Apple model gives a bad recommendation (model doesn't exist, too
   large), the Swift side validates and falls back to the heuristic scoring already in
   `models.py:_heuristic_recommendation()`.

### Revised First-Launch Sequence

```
User launches GivaApp for the first time
  │
  ├─ [1] BootstrapManager checks: AppleModelService.isAvailable?
  │   │
  │   ├─ YES: Start Apple-powered onboarding immediately
  │   │   │
  │   │   ├─ [2a] Show OnboardingView (chat UI)
  │   │   │   ├─ Gather observations (contacts, calendar via EventKit, Spotlight recents)
  │   │   │   ├─ Apple model asks 3-5 interview questions
  │   │   │   ├─ Each response extracts profile via @Generable
  │   │   │   └─ Interview completes → extractedProfile ready
  │   │   │
  │   │   ├─ [2b] In parallel: BootstrapManager sets up venv + daemon
  │   │   │   ├─ giva-setup.py (venv, pip install, launchd plist)
  │   │   │   ├─ Start daemon, wait for health
  │   │   │   └─ POST /api/bootstrap/start
  │   │   │
  │   │   ├─ [3] Once daemon healthy + onboarding done:
  │   │   │   ├─ Fetch model candidates from GET /api/models/available
  │   │   │   ├─ Apple model recommends models (or heuristic fallback)
  │   │   │   └─ Show ModelSetupView with recommendation
  │   │   │
  │   │   ├─ [4] User confirms models → POST /api/models/select
  │   │   │   └─ Server downloads user models (progress via SSE)
  │   │   │
  │   │   ├─ [5] Models ready → POST /api/onboarding/profile
  │   │   │   └─ Server persists profile, marks onboarding complete
  │   │   │
  │   │   └─ [6] Server: ready → syncing → operational
  │   │       └─ (skips server-side onboarding since profile exists)
  │   │
  │   └─ NO: Fall back to current flow
  │       └─ download_8B → model_select → download → ready → sync → onboarding → operational
  │
  └─ Done. User is operational.
```

**Key UX improvement:** Steps [2a] and [2b] run **in parallel**. While the user is
chatting with the Apple model during onboarding, the daemon is being set up in the
background. The user doesn't see a progress bar until model download (step 4), and by
then they've already had a meaningful interaction with Giva.

### File Changes Summary

| File | Change |
|---|---|
| `GivaApp/Services/AppleModelService.swift` | **New.** Wraps `FoundationModels` framework. |
| `GivaApp/Models/OnboardingModels.swift` | **New.** `@Generable` structs for profile, recommendation. |
| `GivaApp/ViewModels/OnboardingViewModel.swift` | **New.** Drives Swift-side onboarding interview. |
| `GivaApp/Views/OnboardingView.swift` | **New.** Chat UI for Apple model onboarding (reuse `ChatView` patterns). |
| `GivaApp/Services/BootstrapManager.swift` | **Modified.** Check `AppleModelService.isAvailable`, parallel onboarding + daemon setup. |
| `GivaApp/ViewModels/GivaViewModel.swift` | **Modified.** Accept onboarding from either Swift or server path. |
| `GivaApp/Models/APIModels.swift` | **Modified.** Add `appleOnboarding` to `ServerPhase` (optional). |
| `src/giva/server.py` | **Modified.** Add `POST /api/onboarding/profile` endpoint. |
| `src/giva/bootstrap.py` | **Modified.** Support skipping server-side onboarding when profile pre-exists. |
| `GivaApp.xcodeproj/project.pbxproj` | **Modified.** Add new Swift files. |

### Observation Gathering on Swift Side

The current Python onboarding gathers rich observations (contacts, email volume, calendar
events, MCP sources, writing style). For the Swift-side path, we gather what we can
natively:

| Observation | Swift Source | Availability |
|---|---|---|
| User identity | `NSFullUserName()`, Apple ID | Always |
| Calendar events | EventKit (`EKEventStore`) | Requires TCC permission |
| Contacts | Contacts framework | Requires TCC permission |
| Recent files | Spotlight (`NSMetadataQuery`) | Always |
| Email data | Not available until sync | Deferred to post-sync |
| MCP sources | Not available until daemon | Deferred |

**Strategy:** Gather what's available natively (identity, calendar, contacts, recents),
conduct the interview, then **enrich the profile post-sync** with email data and MCP
observations. The server already supports incremental profile updates.

### Context Window Management

The Apple model's 4,096 token context is tight but manageable:

| Slot | Tokens | Content |
|---|---|---|
| Instructions (system) | ~400 | Onboarding persona, extraction rules |
| Observations | ~600 | User identity, calendar summary, top contacts |
| History (3-5 turns) | ~2,000 | Previous Q&A pairs |
| Current prompt | ~200 | User's latest response |
| Response budget | ~800 | Assistant reply + structured output |
| **Total** | **~4,000** | Within 4,096 limit |

For turns > 3, compress earlier turns into a summary to stay within budget.
`LanguageModelSession` manages its own transcript, but we should monitor token usage
and start a fresh session with a compressed history if approaching the limit.

### Tool Calling for Observations

The Apple model's `Tool` protocol can be used to pull live data during the interview:

```swift
struct CalendarTool: Tool {
    let name = "getUpcomingEvents"
    let description = "Get the user's upcoming calendar events for the next 7 days"

    @Generable struct Arguments {}

    func call(arguments: Arguments) async throws -> ToolOutput {
        let events = try await fetchEvents(days: 7)
        return ToolOutput(events.formatted())
    }
}

struct ContactsTool: Tool {
    let name = "getFrequentContacts"
    let description = "Get the user's most frequent contacts"

    @Generable struct Arguments {}

    func call(arguments: Arguments) async throws -> ToolOutput {
        let contacts = try await fetchTopContacts(limit: 10)
        return ToolOutput(contacts.formatted())
    }
}
```

This lets the Apple model decide when to pull data rather than front-loading all
observations. More natural conversational flow, and uses context budget only when needed.

### Error Handling & Guardrails

Apple's model has content guardrails that may reject certain prompts. Handle gracefully:

```swift
do {
    let response = try await session.respond(to: prompt)
    // Process normally
} catch let error as LanguageModelSession.GenerationError {
    switch error {
    case .guardrailViolation:
        // Rephrase the prompt or skip this observation
        log.warning("Guardrail triggered, rephrasing...")
    case .contextWindowExceeded:
        // Compress history and retry
        log.warning("Context exceeded, compressing...")
    default:
        // Fall back to server-side onboarding
        log.error("Apple model error: \(error)")
        fallbackToServerOnboarding()
    }
}
```

### Testing Strategy

| Layer | Approach |
|---|---|
| `AppleModelService` | Protocol-based (`AppleModelServiceProtocol`) for mock injection. Tests use a mock that returns canned `OnboardingResponse` / `ModelRecommendation`. |
| `OnboardingViewModel` | Unit tests with mock `AppleModelService`. Verify profile extraction, turn management, completion detection. |
| `OnboardingView` | Manual / UI testing (depends on live Apple model). |
| Server `POST /api/onboarding/profile` | pytest with fixture data — verify profile persistence and checkpoint advancement. |
| Fallback path | Test with `AppleModelService.isAvailable = false` — verify old flow still works. |
| Integration | End-to-end on macOS 26 device with Apple Intelligence enabled. |

### Migration & Compatibility

- **macOS 26+**: Uses Apple Foundation Models path (new flow).
- **macOS 15 (Sequoia) or earlier**: Falls back to current MLX-based onboarding.
- **Apple Intelligence disabled**: Falls back to current flow.
- **Existing users upgrading**: `onboarding_completed` already true in DB — no change.
- **`#if canImport(FoundationModels)`**: Compile-time gating for the new code. The app
  still builds on Xcode 16 / macOS 15 SDK without the framework.

```swift
#if canImport(FoundationModels)
import FoundationModels

extension AppleModelService {
    static var isAvailable: Bool {
        if #available(macOS 26, *) {
            return SystemLanguageModel.default.isAvailable
        }
        return false
    }
}
#else
extension AppleModelService {
    static var isAvailable: Bool { false }
}
#endif
```

## Trade-offs

### Advantages

1. **Instant first interaction** — No download wait. User starts chatting immediately.
2. **Zero-cost inference** — Apple model is free, on-device, private.
3. **Type-safe structured output** — `@Generable` eliminates JSON parsing fragility.
4. **Parallel setup** — Daemon setup happens while user chats.
5. **Better UX** — Onboarding feels like talking to a native assistant, not waiting for
   a download bar.
6. **Smaller download** — No need to download the 8B filter model upfront. User only
   downloads their chosen models.

### Risks & Mitigations

| Risk | Mitigation |
|---|---|
| 3B model quality vs 8B | Onboarding questions are simple conversational Q&A — well within 3B capabilities. Profile extraction uses `@Generable` (constrained decoding), more reliable than regex. |
| 4K context limit | Budget carefully (see table above). Compress history after 3 turns. Tool calling defers observation loading. |
| Guardrail false positives | Onboarding content is benign (name, job, schedule). Low risk. Handle `guardrailViolation` errors gracefully. |
| Apple Intelligence unavailable | Graceful fallback to current flow. `#if canImport` for compile-time, runtime availability check. |
| Model recommendation quality | Validate recommendations against hardware constraints. Fall back to heuristic scoring (`_heuristic_recommendation`) if Apple model gives nonsensical output. |
| Observation gap (no email data) | Profile enrichment happens post-sync. Server already supports incremental updates. First-launch profile is "good enough" from calendar + contacts + user interview. |

## Open Questions

1. **Should model recommendation also use Apple model, or just the heuristic?**
   The heuristic in `_heuristic_recommendation()` is already quite good. Using the Apple
   model adds complexity for marginal improvement. Could keep heuristic-only for v1.

2. **Should the onboarding UI be a separate view or integrated into ModelSetupView?**
   A combined flow (interview → model selection → download) might feel more cohesive
   than separate screens.

3. **Should we use `streamResponse` for onboarding?**
   Snapshot-based streaming would show partial responses in the chat UI. Good for UX
   (typing effect) but requires handling `PartiallyGenerated<OnboardingResponse>` where
   `.message` builds up incrementally and `.profileUpdate` is nil until complete.

4. **Default model download: skip entirely or download in background?**
   The 8B filter model is still needed for email classification during sync. Options:
   (a) Download it in background during onboarding (user doesn't wait).
   (b) Defer until after user model selection (download all models together).
   (c) Use Apple model for email filtering too (probably too slow/limited for bulk).
   Option (b) seems cleanest.
