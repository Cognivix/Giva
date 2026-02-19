// ServerManager.swift - Connects to the launchd-managed Giva server daemon.

import Foundation

@MainActor
class ServerManager: ObservableObject {
    @Published var isRunning = false
    @Published var lastError: String?

    private let port: Int = 7483
    private let host: String = "127.0.0.1"

    var baseURL: URL {
        URL(string: "http://\(host):\(port)")!
    }

    /// Connect to the daemon-managed server by polling its health endpoint.
    func connectToDaemon() async {
        guard !isRunning else { return }

        let ready = await waitForHealth(timeout: 30)
        if ready {
            isRunning = true
            lastError = nil
        } else {
            lastError = "Server not responding. Check launchd service."
            isRunning = false
        }
    }

    // MARK: - Health Polling

    func waitForHealth(timeout: TimeInterval) async -> Bool {
        let start = Date()
        let healthURL = baseURL.appendingPathComponent("api/health")

        while Date().timeIntervalSince(start) < timeout {
            do {
                let (_, response) = try await URLSession.shared.data(from: healthURL)
                if let httpResponse = response as? HTTPURLResponse,
                   httpResponse.statusCode == 200 {
                    return true
                }
            } catch {
                // Server not ready yet, wait and retry
            }
            try? await Task.sleep(nanoseconds: 500_000_000) // 0.5s
        }
        return false
    }
}
