// ServerManager.swift - Tracks live connection state for the Giva server daemon.
//
// Connection health is determined by two signals:
//   1. Session stream heartbeats (every 15s from server — primary signal)
//   2. Periodic /api/health polling (fallback when SSE drops)
//
// Three states: connected (green), connecting (yellow), offline (red).

import Foundation
import Observation

enum ConnectionState: String {
    case connected = "Connected"
    case connecting = "Connecting..."
    case offline = "Offline"

    var dotColor: String {
        switch self {
        case .connected: return "green"
        case .connecting: return "yellow"
        case .offline: return "red"
        }
    }
}

@MainActor @Observable
class ServerManager {
    var connectionState: ConnectionState = .offline
    var lastError: String?

    /// Shorthand for backward compatibility
    var isRunning: Bool {
        get { connectionState == .connected }
        set { connectionState = newValue ? .connected : .offline }
    }

    private let port: Int = 7483
    private let host: String = "127.0.0.1"

    /// Seconds since last heartbeat/response before marking stale
    private let staleThreshold: TimeInterval = 45.0

    /// When we last heard from the server (heartbeat, SSE event, health check)
    private var lastHeartbeat: Date?

    /// Health polling task (runs when session stream is not connected)
    private var healthPollTask: Task<Void, Never>?

    var baseURL: URL {
        URL(string: "http://\(host):\(port)")!
    }

    // MARK: - Heartbeat tracking

    /// Called by the ViewModel whenever it receives ANY event from the session stream
    /// (heartbeat, phase change, sync_complete, etc.)
    func recordHeartbeat() {
        lastHeartbeat = Date()
        if connectionState != .connected {
            connectionState = .connected
            lastError = nil
        }
    }

    /// Called by the ViewModel when the session stream disconnects
    func recordDisconnect() {
        connectionState = .connecting
        startHealthPolling()
    }

    /// Called when we want to explicitly mark offline (e.g., during restart)
    func markOffline() {
        connectionState = .offline
        lastHeartbeat = nil
        stopHealthPolling()
    }

    /// Called when we're actively trying to connect
    func markConnecting() {
        connectionState = .connecting
    }

    // MARK: - Health Polling

    /// Start periodic health polling (used when session stream is not active)
    func startHealthPolling() {
        stopHealthPolling()
        healthPollTask = Task {
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 5_000_000_000) // 5s
                guard !Task.isCancelled else { break }
                let healthy = await checkHealth()
                if healthy {
                    recordHeartbeat()
                } else if let last = lastHeartbeat,
                          Date().timeIntervalSince(last) > staleThreshold {
                    connectionState = .offline
                    lastError = "Server not responding"
                }
            }
        }
    }

    func stopHealthPolling() {
        healthPollTask?.cancel()
        healthPollTask = nil
    }

    /// Connect to the daemon-managed server by polling its health endpoint.
    func connectToDaemon() async {
        guard connectionState != .connected else { return }
        connectionState = .connecting

        let ready = await waitForHealth(timeout: 30)
        if ready {
            recordHeartbeat()
        } else {
            lastError = "Server not responding. Check launchd service."
            connectionState = .offline
        }
    }

    // MARK: - Health Check

    func waitForHealth(timeout: TimeInterval) async -> Bool {
        let start = Date()

        while Date().timeIntervalSince(start) < timeout {
            if await checkHealth() {
                return true
            }
            try? await Task.sleep(nanoseconds: 500_000_000) // 0.5s
        }
        return false
    }

    private func checkHealth() async -> Bool {
        let healthURL = baseURL.appendingPathComponent("api/health")
        do {
            let (_, response) = try await URLSession.shared.data(from: healthURL)
            if let httpResponse = response as? HTTPURLResponse,
               httpResponse.statusCode == 200 {
                return true
            }
        } catch {
            // Server not ready
        }
        return false
    }
}
