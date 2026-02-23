#!/usr/bin/env swift
// GivaSSETest.swift — Standalone regression test for SSE event delivery.
//
// Usage:
//   swift GivaSSETest.swift              # basic: verify session + stream events
//   swift GivaSSETest.swift --reset      # full: trigger reset, watch lifecycle
//   swift GivaSSETest.swift --upgrade    # full: trigger upgrade, watch lifecycle
//
// Requires the Giva server running on http://127.0.0.1:7483.

import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

// MARK: - Configuration

let baseURL = "http://127.0.0.1:7483"
let sseTimeout: TimeInterval = 30  // max seconds to wait for events
let lifecycleTimeout: TimeInterval = 120  // max seconds for full lifecycle

// MARK: - Helpers

enum TestResult {
    case pass(String)
    case fail(String)
}

var results: [TestResult] = []
var passCount = 0
var failCount = 0

func record(_ result: TestResult) {
    results.append(result)
    switch result {
    case .pass(let msg):
        passCount += 1
        print("  ✅ PASS: \(msg)")
    case .fail(let msg):
        failCount += 1
        print("  ❌ FAIL: \(msg)")
    }
}

func printHeader(_ title: String) {
    print("\n━━━ \(title) ━━━")
}

func printSummary() {
    print("\n━━━ Summary ━━━")
    print("  ✅ \(passCount) passed")
    print("  ❌ \(failCount) failed")
    if failCount > 0 {
        print("\n  RESULT: FAILED")
    } else {
        print("\n  RESULT: ALL PASSED")
    }
}

// MARK: - SSE Parser (byte-level, same as APIService.swift)

struct SSEEvent {
    let event: String
    let data: String
}

/// Collect SSE events from a stream URL for up to `timeout` seconds.
func collectSSEEvents(
    url: URL,
    method: String = "GET",
    body: Data? = nil,
    maxEvents: Int = 50,
    timeout: TimeInterval = 30
) async throws -> [SSEEvent] {
    var request = URLRequest(url: url)
    request.httpMethod = method
    request.setValue("text/event-stream", forHTTPHeaderField: "Accept")
    if let body = body {
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = body
    }

    let sessionConfig = URLSessionConfiguration.default
    sessionConfig.timeoutIntervalForRequest = timeout + 5
    let session = URLSession(configuration: sessionConfig)
    defer { session.invalidateAndCancel() }

    let (bytes, response) = try await session.bytes(for: request)
    guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
        let code = (response as? HTTPURLResponse)?.statusCode ?? 0
        throw NSError(domain: "SSE", code: code, userInfo: [
            NSLocalizedDescriptionKey: "HTTP \(code)"
        ])
    }

    var events: [SSEEvent] = []
    var currentEvent = ""
    var currentData = ""
    var lineBuffer = Data()
    let deadline = Date().addingTimeInterval(timeout)

    for try await byte in bytes {
        if Date() > deadline { break }

        if byte == UInt8(ascii: "\n") {
            let line = String(decoding: lineBuffer, as: UTF8.self)
            lineBuffer.removeAll(keepingCapacity: true)

            if line.hasPrefix("event: ") {
                currentEvent = String(line.dropFirst(7))
            } else if line.hasPrefix("data: ") {
                currentData = String(line.dropFirst(6))
            } else if line == "data:" {
                currentData = ""
            } else if line.hasPrefix(":") {
                // SSE comment / keepalive — ignore
            } else if line.isEmpty {
                // Empty line = end of event block
                if !currentEvent.isEmpty {
                    events.append(SSEEvent(event: currentEvent, data: currentData))
                    if events.count >= maxEvents { break }
                }
                currentEvent = ""
                currentData = ""
            }
        } else if byte != UInt8(ascii: "\r") {
            lineBuffer.append(byte)
        }
    }

    return events
}

// MARK: - Test 1: GET /api/session decoding

func testSessionEndpoint() async {
    printHeader("Test 1: GET /api/session")

    guard let url = URL(string: "\(baseURL)/api/session") else {
        record(.fail("Invalid URL"))
        return
    }

    do {
        var request = URLRequest(url: url)
        request.timeoutInterval = 10
        let (data, response) = try await URLSession.shared.data(for: request)

        guard let http = response as? HTTPURLResponse else {
            record(.fail("No HTTP response"))
            return
        }
        record(http.statusCode == 200
            ? .pass("HTTP 200")
            : .fail("HTTP \(http.statusCode)"))

        // Parse JSON
        guard let json = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            record(.fail("Response is not a JSON object"))
            return
        }

        // Check 'phase' field
        if let phase = json["phase"] as? String {
            record(.pass("phase = \"\(phase)\""))
        } else {
            record(.fail("Missing 'phase' field"))
        }

        // Check 'messages' field
        if json["messages"] is [Any] {
            record(.pass("'messages' is an array"))
        } else {
            record(.fail("Missing or invalid 'messages' field"))
        }

        // Check 'stats' field (this was the decoding bug — syncs array)
        if let stats = json["stats"] as? [String: Any] {
            let hasEmails = stats["emails"] is Int
            let hasEvents = stats["events"] is Int
            let hasSyncs = stats["syncs"] is [Any]  // the problematic field
            record(.pass("stats decoded (emails=\(hasEmails), events=\(hasEvents), syncs_array=\(hasSyncs))"))

            // Verify our SessionStats model would work: it ignores syncs
            if hasEmails && hasEvents {
                record(.pass("SessionStats fields present (emails, events)"))
            } else {
                record(.fail("SessionStats missing required fields"))
            }
        } else if json["stats"] == nil || json["stats"] is NSNull {
            record(.pass("stats is null (acceptable)"))
        } else {
            record(.fail("stats is unexpected type"))
        }

        // Check 'needs_response' field
        if json["needs_response"] is Bool {
            record(.pass("'needs_response' is a Bool"))
        } else {
            record(.pass("'needs_response' missing (defaults to false — OK)"))
        }
    } catch {
        record(.fail("Request failed: \(error.localizedDescription)"))
    }
}

// MARK: - Test 2: GET /api/session/stream SSE events

func testSSEStream() async {
    printHeader("Test 2: GET /api/session/stream (SSE)")

    guard let url = URL(string: "\(baseURL)/api/session/stream") else {
        record(.fail("Invalid URL"))
        return
    }

    do {
        let events = try await collectSSEEvents(url: url, timeout: sseTimeout)

        record(events.isEmpty
            ? .fail("0 events received in \(Int(sseTimeout))s")
            : .pass("\(events.count) events received"))

        // First event should be a 'phase' event
        if let first = events.first {
            record(first.event == "phase"
                ? .pass("First event is 'phase' with data=\"\(first.data)\"")
                : .fail("First event is '\(first.event)' (expected 'phase')"))
        }

        // Check for heartbeats (should arrive every 15s)
        let heartbeats = events.filter { $0.event == "heartbeat" }
        if !heartbeats.isEmpty {
            record(.pass("\(heartbeats.count) heartbeat(s) received"))
        } else if sseTimeout >= 20 {
            record(.fail("No heartbeats in \(Int(sseTimeout))s (expected every 15s)"))
        } else {
            record(.pass("No heartbeats (timeout < 20s — OK)"))
        }

        // Print all events
        print("\n  Events received:")
        for (i, ev) in events.enumerated() {
            let dataPreview = ev.data.prefix(80)
            print("    [\(i+1)] \(ev.event): \(dataPreview)")
        }
    } catch {
        record(.fail("SSE stream failed: \(error.localizedDescription)"))
    }
}

// MARK: - Test 3: POST /api/reset + full lifecycle

func testResetLifecycle() async {
    printHeader("Test 3: POST /api/reset + lifecycle")

    guard let resetURL = URL(string: "\(baseURL)/api/reset"),
          let streamURL = URL(string: "\(baseURL)/api/session/stream") else {
        record(.fail("Invalid URL"))
        return
    }

    // Step 1: Trigger reset
    do {
        var request = URLRequest(url: resetURL)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = "{}".data(using: .utf8)
        request.timeoutInterval = 30

        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            record(.fail("No HTTP response from reset"))
            return
        }
        record(http.statusCode == 200
            ? .pass("Reset returned HTTP 200")
            : .fail("Reset returned HTTP \(http.statusCode)"))

        if let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let status = json["status"] as? String {
            record(.pass("Reset status: \"\(status)\""))
        }
    } catch {
        record(.fail("Reset request failed: \(error.localizedDescription)"))
        return
    }

    // Step 2: Connect to SSE stream and watch lifecycle
    print("\n  Watching lifecycle (up to \(Int(lifecycleTimeout))s)...")
    do {
        let events = try await collectSSEEvents(
            url: streamURL,
            maxEvents: 200,
            timeout: lifecycleTimeout
        )

        record(events.isEmpty
            ? .fail("0 events after reset")
            : .pass("\(events.count) events received after reset"))

        // Check for expected phase transitions
        let phases = events.filter { $0.event == "phase" }.map { $0.data }
        let syncProgress = events.filter { $0.event == "sync_progress" }
        let syncComplete = events.filter { $0.event == "sync_complete" }

        print("\n  Phase transitions: \(phases.joined(separator: " → "))")
        print("  sync_progress events: \(syncProgress.count)")
        print("  sync_complete events: \(syncComplete.count)")

        // Verify phase=ready appears (initial state after reset)
        record(phases.contains("ready")
            ? .pass("Phase 'ready' received")
            : .fail("Phase 'ready' not received"))

        // Verify phase=syncing appears
        record(phases.contains("syncing")
            ? .pass("Phase 'syncing' received")
            : .fail("Phase 'syncing' not received"))

        // Verify at least one sync_progress event
        record(!syncProgress.isEmpty
            ? .pass("\(syncProgress.count) sync_progress events")
            : .fail("No sync_progress events"))

        // Verify sync_complete
        record(!syncComplete.isEmpty
            ? .pass("sync_complete received")
            : .fail("No sync_complete event"))

        // Verify terminal phase (onboarding or operational)
        let terminal = phases.contains("operational") || phases.contains("onboarding")
        record(terminal
            ? .pass("Terminal phase reached: \(phases.last ?? "?")")
            : .fail("No terminal phase (onboarding/operational) reached"))

        // Print all events
        print("\n  Full event log:")
        for (i, ev) in events.enumerated() {
            let dataPreview = ev.data.prefix(100)
            print("    [\(i+1)] \(ev.event): \(dataPreview)")
        }
    } catch {
        record(.fail("SSE stream after reset failed: \(error.localizedDescription)"))
    }
}

// MARK: - Test 4: POST /api/upgrade + lifecycle

func testUpgradeLifecycle() async {
    printHeader("Test 4: POST /api/upgrade + lifecycle")

    guard let upgradeURL = URL(string: "\(baseURL)/api/upgrade"),
          let streamURL = URL(string: "\(baseURL)/api/session/stream") else {
        record(.fail("Invalid URL"))
        return
    }

    // Step 1: Trigger upgrade
    do {
        var request = URLRequest(url: upgradeURL)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = "{}".data(using: .utf8)
        request.timeoutInterval = 120  // upgrades can be slow

        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            record(.fail("No HTTP response from upgrade"))
            return
        }
        record(http.statusCode == 200
            ? .pass("Upgrade returned HTTP 200")
            : .fail("Upgrade returned HTTP \(http.statusCode)"))

        if let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let status = json["status"] as? String {
            record(.pass("Upgrade status: \"\(status)\""))
        }
    } catch {
        record(.fail("Upgrade request failed: \(error.localizedDescription)"))
        return
    }

    // Step 2: Connect to SSE stream and verify events flow
    print("\n  Watching post-upgrade stream (up to \(Int(sseTimeout))s)...")
    do {
        let events = try await collectSSEEvents(
            url: streamURL,
            maxEvents: 50,
            timeout: sseTimeout
        )

        record(events.isEmpty
            ? .fail("0 events after upgrade")
            : .pass("\(events.count) events received after upgrade"))

        let phases = events.filter { $0.event == "phase" }.map { $0.data }
        print("\n  Phases: \(phases.joined(separator: " → "))")

        if let first = events.first {
            record(first.event == "phase"
                ? .pass("First event is 'phase' = \"\(first.data)\"")
                : .fail("First event is '\(first.event)'"))
        }
    } catch {
        record(.fail("SSE stream after upgrade failed: \(error.localizedDescription)"))
    }
}

// MARK: - Test 5: POST /api/session/respond (onboarding answer)

func testOnboardingRespond() async {
    printHeader("Test 5: POST /api/session/respond")

    // First, check current phase
    guard let sessionURL = URL(string: "\(baseURL)/api/session") else {
        record(.fail("Invalid URL"))
        return
    }

    do {
        let (data, _) = try await URLSession.shared.data(from: sessionURL)
        guard let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let phase = json["phase"] as? String else {
            record(.fail("Could not read session phase"))
            return
        }

        if phase != "onboarding" {
            record(.pass("Skipping respond test — phase is '\(phase)' (not onboarding)"))
            return
        }
    } catch {
        record(.fail("Session check failed: \(error.localizedDescription)"))
        return
    }

    // Get the response text from command line or use default
    let responseText: String
    if let idx = CommandLine.arguments.firstIndex(of: "--respond"),
       idx + 1 < CommandLine.arguments.count {
        responseText = CommandLine.arguments[idx + 1]
    } else {
        responseText = "I am a software engineer working on AI projects."
    }

    print("  Sending response: \"\(responseText.prefix(80))\"")

    guard let respondURL = URL(string: "\(baseURL)/api/session/respond") else {
        record(.fail("Invalid URL"))
        return
    }

    // Send POST and collect SSE events
    do {
        let bodyJSON = try JSONSerialization.data(
            withJSONObject: ["response": responseText]
        )
        let events = try await collectSSEEvents(
            url: respondURL,
            method: "POST",
            body: bodyJSON,
            maxEvents: 500,
            timeout: 90
        )

        record(events.isEmpty
            ? .fail("0 events from respond")
            : .pass("\(events.count) events received"))

        // Categorize events
        let modelLoading = events.filter { $0.event == "model_loading" }
        let thinking = events.filter { $0.event == "onboarding_thinking" }
        let tokens = events.filter { $0.event == "onboarding_token" }
        let done = events.filter { $0.event == "onboarding_done" }
        let complete = events.filter { $0.event == "onboarding_complete" }
        let errors = events.filter { $0.event == "error" }

        print("\n  Event breakdown:")
        print("    model_loading: \(modelLoading.count)")
        print("    onboarding_thinking: \(thinking.count)")
        print("    onboarding_token: \(tokens.count)")
        print("    onboarding_done: \(done.count)")
        print("    onboarding_complete: \(complete.count)")
        print("    error: \(errors.count)")

        // Check for errors
        if !errors.isEmpty {
            for err in errors {
                record(.fail("Error event: \(err.data.prefix(200))"))
            }
        }

        // Check tokens arrived
        record(!tokens.isEmpty
            ? .pass("\(tokens.count) onboarding_token events")
            : .fail("No onboarding_token events"))

        // Check done signal
        record(!done.isEmpty
            ? .pass("onboarding_done received")
            : .fail("No onboarding_done event"))

        // Reconstruct visible text from tokens
        let visibleText = tokens.map { $0.data }.joined()
        print("\n  Visible response text (\(visibleText.count) chars):")
        print("    \"\(visibleText.prefix(300))\"")

        // Check for leaked tags
        let hasLeakedTags = visibleText.contains("<profile") || visibleText.contains("</profile")
        record(!hasLeakedTags
            ? .pass("No leaked <profile_update> tags")
            : .fail("Leaked tags in visible text!"))

        // Print all events for debugging
        print("\n  Full event log:")
        for (i, ev) in events.enumerated() {
            let dataPreview = ev.data.prefix(100)
            print("    [\(i+1)] \(ev.event): \(dataPreview)")
        }
    } catch {
        record(.fail("Respond stream failed: \(error.localizedDescription)"))
    }
}

// MARK: - Main (top-level async)

func main() async {
    let args = CommandLine.arguments

    print("╔══════════════════════════════════════════╗")
    print("║     Giva SSE Regression Test Suite       ║")
    print("╚══════════════════════════════════════════╝")
    print("Server: \(baseURL)")

    // Check server is reachable
    printHeader("Pre-check: Server reachable?")
    do {
        var req = URLRequest(url: URL(string: "\(baseURL)/api/health")!)
        req.timeoutInterval = 5
        let (_, resp) = try await URLSession.shared.data(for: req)
        if let http = resp as? HTTPURLResponse, http.statusCode == 200 {
            record(.pass("Server is reachable"))
        } else {
            record(.fail("Server returned non-200"))
            printSummary()
            return
        }
    } catch {
        record(.fail("Server not reachable: \(error.localizedDescription)"))
        print("\n  ⚠️  Make sure giva-server is running on \(baseURL)")
        printSummary()
        return
    }

    // Always run basic tests
    await testSessionEndpoint()
    await testSSEStream()

    // Optional lifecycle tests
    if args.contains("--reset") {
        await testResetLifecycle()
    }

    if args.contains("--respond") {
        await testOnboardingRespond()
    }

    if args.contains("--upgrade") {
        await testUpgradeLifecycle()
    }

    if !args.contains("--reset") && !args.contains("--upgrade") {
        print("\n  💡 Run with --reset or --upgrade for full lifecycle tests")
    }

    printSummary()

    // Exit with appropriate code
    if failCount > 0 { exit(1) }
}

// Run the async main
let semaphore = DispatchSemaphore(value: 0)
Task {
    await main()
    semaphore.signal()
}
semaphore.wait()
