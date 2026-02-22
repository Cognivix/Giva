// BootstrapManager.swift - Thin launcher + observer.
//
// The app only does two things that require local process control:
//   1. Run giva-setup.py (create venv + pip install) when no venv exists
//   2. Call launchctl to load/unload the daemon
//
// Everything else (model downloads, config validation, readiness) is
// owned by the giva-server daemon.  This manager observes the server's
// bootstrap state via REST/SSE and mirrors it for SwiftUI.

import Foundation

/// Setup script progress (JSON lines from giva-setup.py stdout)
struct SetupProgress: Codable {
    let step: String
    let status: String
    var detail: String?
    var error: String?
    var version: String?
}

@MainActor
class BootstrapManager: ObservableObject {
    // --- Published state for the UI ---

    /// Server-reported bootstrap status (nil until server is reachable)
    @Published var serverStatus: BootstrapStatusResponse?

    /// True once the server reports ready
    @Published var isReady = false

    /// True while giva-setup.py is running (pre-server phase)
    @Published var isSettingUp = false

    /// True once the server is reachable (health check passes)
    @Published var isServerReachable = false

    /// Current display message for the UI
    @Published var displayMessage = "Starting..."

    /// Error message (setup script or server)
    @Published var errorMessage: String?

    /// Log lines from the setup script (pre-server phase)
    @Published var logLines: [String] = []

    /// Download progress from server bootstrap (model_id → progress info)
    @Published var downloadProgress: [String: BootstrapStepProgress] = [:]

    /// API service (created once server is reachable)
    private(set) var apiService: APIService?

    /// SSE observation task
    private var observeTask: Task<Void, Never>?

    // --- Paths ---

    static let dataDir = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent(".local/share/giva")
    static let venvDir = dataDir.appendingPathComponent(".venv")
    static let venvPython = venvDir.appendingPathComponent("bin/python3")

    private static let launchdLabel = "com.giva.server"
    private static let launchdPlistURL: URL = {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/LaunchAgents/com.giva.server.plist")
    }()

    static let projectRootKey = "GivaProjectRoot"

    // --- Main Entry ---

    func start() async {
        guard !isReady && !isSettingUp else { return }

        // Phase 1: Ensure venv exists (local operation)
        if !isVenvHealthy() {
            isSettingUp = true
            displayMessage = "First-time setup..."
            logLines = []
            errorMessage = nil

            let success = await runSetupScript()
            isSettingUp = false

            guard success else { return }
        }

        // Phase 2: Ensure daemon is loaded via launchctl
        ensureLaunchdLoaded()

        // Phase 3: Wait for server health
        displayMessage = "Starting server..."
        isServerReachable = await waitForHealth(timeout: 90)

        guard isServerReachable else {
            errorMessage = "Server didn't start. Check logs at ~/.local/share/giva/logs/"
            displayMessage = "Server failed to start"
            return
        }

        apiService = APIService(baseURL: URL(string: "http://127.0.0.1:7483")!)

        // Phase 4: Kick off bootstrap on the server (if needed)
        do {
            let status = try await apiService!.startBootstrap()
            applyServerStatus(status)
        } catch {
            // Server is healthy but bootstrap endpoint failed — still observe
        }

        // Phase 5: Observe server bootstrap state via SSE
        observeBootstrapStream()
    }

    /// Retry: if setup script failed, re-run it.  If server bootstrap failed, tell server.
    func retry() async {
        errorMessage = nil
        logLines = []

        if !isServerReachable {
            // Need to redo everything from setup script
            await start()
            return
        }

        // Server is reachable — ask it to retry
        guard let api = apiService else {
            await start()
            return
        }

        do {
            let status = try await api.retryBootstrap()
            applyServerStatus(status)
            observeBootstrapStream()
        } catch {
            errorMessage = "Retry failed: \(error.localizedDescription)"
        }
    }

    /// Trigger a lightweight upgrade (pip install only, then daemon restart)
    func triggerUpgrade() async {
        guard let api = apiService else { return }
        guard let projectRoot = resolveProjectRoot() else { return }

        displayMessage = "Upgrading..."

        do {
            let response = try await api.triggerUpgrade(projectRoot: projectRoot)
            if response.restartRequired {
                displayMessage = "Restarting server..."
                restartDaemon()
                isServerReachable = false
                apiService = nil
                serverStatus = nil
                isReady = false

                // Wait for daemon to come back
                isServerReachable = await waitForHealth(timeout: 60)
                if isServerReachable {
                    apiService = APIService(baseURL: URL(string: "http://127.0.0.1:7483")!)
                    let status = try await apiService!.getBootstrapStatus()
                    applyServerStatus(status)
                    if !status.ready {
                        observeBootstrapStream()
                    }
                } else {
                    errorMessage = "Server didn't restart after upgrade"
                    displayMessage = "Upgrade failed"
                }
            }
        } catch {
            errorMessage = "Upgrade failed: \(error.localizedDescription)"
        }
    }

    // MARK: - Phase 1: Setup Script

    private func isVenvHealthy() -> Bool {
        FileManager.default.isExecutableFile(atPath: Self.venvPython.path)
    }

    /// Run giva-setup.py and parse JSON lines from stdout
    private func runSetupScript() async -> Bool {
        guard let projectRoot = resolveProjectRoot() else {
            errorMessage = "Could not locate Giva project source (pyproject.toml)"
            displayMessage = "Setup failed"
            return false
        }

        guard let python = findSystemPython() else {
            errorMessage = "Python 3.11+ not found. Install via: brew install python3"
            displayMessage = "Setup failed"
            return false
        }

        let setupScript = findSetupScript(projectRoot: projectRoot)
        guard let script = setupScript else {
            errorMessage = "giva-setup.py not found in \(projectRoot)/scripts/"
            displayMessage = "Setup failed"
            return false
        }

        displayMessage = "Setting up environment..."

        return await withCheckedContinuation { continuation in
            DispatchQueue.global(qos: .userInitiated).async { [self] in
                let proc = Process()
                proc.executableURL = URL(fileURLWithPath: python)
                proc.arguments = [script, "--project-root", projectRoot]

                var env = ProcessInfo.processInfo.environment
                let extra = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
                env["PATH"] = extra + ":" + (env["PATH"] ?? "")
                proc.environment = env

                let outPipe = Pipe()
                let errPipe = Pipe()
                proc.standardOutput = outPipe
                proc.standardError = errPipe

                // Read stdout line by line for JSON progress
                outPipe.fileHandleForReading.readabilityHandler = { handle in
                    let data = handle.availableData
                    guard !data.isEmpty else { return }
                    guard let line = String(data: data, encoding: .utf8)?
                        .trimmingCharacters(in: .whitespacesAndNewlines),
                          !line.isEmpty else { return }

                    // Parse each JSON line
                    for jsonLine in line.components(separatedBy: "\n") {
                        let trimmed = jsonLine.trimmingCharacters(in: .whitespacesAndNewlines)
                        guard !trimmed.isEmpty else { continue }

                        if let jsonData = trimmed.data(using: .utf8),
                           let progress = try? JSONDecoder().decode(SetupProgress.self, from: jsonData) {
                            Task { @MainActor in
                                self.handleSetupProgress(progress)
                            }
                        }
                    }
                }

                do {
                    try proc.run()
                } catch {
                    Task { @MainActor in
                        self.errorMessage = "Failed to run setup: \(error.localizedDescription)"
                        self.displayMessage = "Setup failed"
                    }
                    continuation.resume(returning: false)
                    return
                }

                proc.waitUntilExit()
                outPipe.fileHandleForReading.readabilityHandler = nil

                let success = proc.terminationStatus == 0
                if !success {
                    let errData = errPipe.fileHandleForReading.readDataToEndOfFile()
                    let errStr = String(data: errData, encoding: .utf8) ?? "unknown error"
                    Task { @MainActor in
                        if self.errorMessage == nil {
                            self.errorMessage = String(errStr.suffix(500))
                        }
                        self.displayMessage = "Setup failed"
                    }
                }
                continuation.resume(returning: success)
            }
        }
    }

    private func handleSetupProgress(_ progress: SetupProgress) {
        let stepNames: [String: String] = [
            "finding_python": "Finding Python",
            "creating_venv": "Creating environment",
            "installing_deps": "Installing dependencies",
            "writing_plist": "Configuring service",
            "checkpoint": "Saving state",
            "complete": "Environment ready",
        ]

        let label = stepNames[progress.step] ?? progress.step

        switch progress.status {
        case "running":
            displayMessage = progress.detail ?? "\(label)..."
            logLines.append("\(label)...")
        case "done":
            if let detail = progress.detail {
                logLines.append("\(label): \(detail)")
            } else {
                logLines.append("\(label) ✓")
            }
            if progress.step == "complete" {
                displayMessage = "Environment ready"
            }
        case "failed":
            errorMessage = progress.error ?? "\(label) failed"
            displayMessage = "Setup failed"
        default:
            break
        }
    }

    // MARK: - Phase 2: Launchctl

    /// Check if the launchd service is currently loaded.
    private func isServiceLoaded() -> Bool {
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: "/bin/launchctl")
        proc.arguments = ["print", "gui/\(getuid())/\(Self.launchdLabel)"]
        proc.standardOutput = FileHandle.nullDevice
        proc.standardError = FileHandle.nullDevice
        try? proc.run()
        proc.waitUntilExit()
        return proc.terminationStatus == 0
    }

    /// Bootout the service only if it is currently loaded.
    /// Returns true if the service was booted out (or wasn't loaded).
    private func bootoutIfLoaded() -> Bool {
        guard isServiceLoaded() else { return true }

        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: "/bin/launchctl")
        proc.arguments = ["bootout", "gui/\(getuid())/\(Self.launchdLabel)"]
        proc.standardOutput = FileHandle.nullDevice
        proc.standardError = FileHandle.nullDevice
        try? proc.run()
        proc.waitUntilExit()

        // Give launchd a moment to clean up
        Thread.sleep(forTimeInterval: 0.5)

        return proc.terminationStatus == 0
    }

    private func ensureLaunchdLoaded() {
        guard FileManager.default.fileExists(atPath: Self.launchdPlistURL.path) else {
            return
        }

        // If already loaded, we're done — kickstart to ensure it's running
        if isServiceLoaded() {
            let kick = Process()
            kick.executableURL = URL(fileURLWithPath: "/bin/launchctl")
            kick.arguments = ["kickstart", "gui/\(getuid())/\(Self.launchdLabel)"]
            kick.standardOutput = FileHandle.nullDevice
            kick.standardError = FileHandle.nullDevice
            try? kick.run()
            kick.waitUntilExit()
            return
        }

        // Not loaded — bootstrap it
        let load = Process()
        load.executableURL = URL(fileURLWithPath: "/bin/launchctl")
        load.arguments = ["bootstrap", "gui/\(getuid())", Self.launchdPlistURL.path]
        load.standardOutput = FileHandle.nullDevice
        let errPipe = Pipe()
        load.standardError = errPipe
        try? load.run()
        load.waitUntilExit()

        if load.terminationStatus != 0 {
            let errData = errPipe.fileHandleForReading.readDataToEndOfFile()
            let errStr = String(data: errData, encoding: .utf8) ?? ""
            // 37 = already loaded (race), that's fine
            if !errStr.contains("37") && load.terminationStatus != 37 {
                logLines.append("Warning: launchctl error: \(errStr)")
            }
        }
    }

    private func restartDaemon() {
        _ = bootoutIfLoaded()
        ensureLaunchdLoaded()
    }

    // MARK: - Phase 3: Health Check

    private func waitForHealth(timeout: TimeInterval) async -> Bool {
        let start = Date()
        let url = URL(string: "http://127.0.0.1:7483/api/health")!

        while Date().timeIntervalSince(start) < timeout {
            do {
                let (_, response) = try await URLSession.shared.data(from: url)
                if let http = response as? HTTPURLResponse, http.statusCode == 200 {
                    return true
                }
            } catch {
                // Not ready yet
            }
            try? await Task.sleep(nanoseconds: 1_000_000_000)
        }
        return false
    }

    // MARK: - Phase 4+5: Observe Server Bootstrap

    private func applyServerStatus(_ status: BootstrapStatusResponse) {
        serverStatus = status
        isReady = status.ready
        displayMessage = status.displayMessage

        if let error = status.error {
            errorMessage = error
        }

        // Extract download progress from steps
        var newProgress: [String: BootstrapStepProgress] = [:]
        for step in status.steps {
            if let progress = step.progress {
                for (modelId, info) in progress {
                    newProgress[modelId] = info
                }
            }
        }
        if !newProgress.isEmpty {
            downloadProgress = newProgress
        }
    }

    private func observeBootstrapStream() {
        observeTask?.cancel()
        guard let api = apiService else { return }

        observeTask = Task {
            var retryDelay: UInt64 = 2_000_000_000  // Start at 2s

            while !Task.isCancelled && !isReady {
                do {
                    let stream = api.streamBootstrapStatus()
                    retryDelay = 2_000_000_000  // Reset on successful connection

                    for try await event in stream {
                        guard !Task.isCancelled else { return }

                        if let data = event.data.data(using: .utf8),
                           let status = try? JSONDecoder().decode(BootstrapStatusResponse.self, from: data) {
                            applyServerStatus(status)
                        }

                        if event.event == "ready" || event.event == "error" {
                            return
                        }
                    }
                    // Stream ended normally (server closed it) — reconnect
                } catch {
                    if Task.isCancelled { return }
                }

                // Reconnect after delay (with backoff up to 10s)
                try? await Task.sleep(nanoseconds: retryDelay)
                retryDelay = min(retryDelay + 1_000_000_000, 10_000_000_000)

                // Refresh status via REST in case we missed updates
                if !Task.isCancelled, !isReady {
                    if let status = try? await api.getBootstrapStatus() {
                        applyServerStatus(status)
                        if status.ready { return }
                    }
                }
            }
        }
    }

    // MARK: - Helpers

    private func findSystemPython() -> String? {
        let candidates = [
            "/opt/homebrew/bin/python3",
            "/usr/local/bin/python3",
            "/usr/bin/python3",
        ]

        for path in candidates {
            if FileManager.default.isExecutableFile(atPath: path) {
                if let version = pythonVersion(path),
                   version.0 > 3 || (version.0 == 3 && version.1 >= 11) {
                    return path
                }
            }
        }

        // Fallback: which python3
        if let path = whichExecutable("python3"),
           let version = pythonVersion(path),
           version.0 > 3 || (version.0 == 3 && version.1 >= 11) {
            return path
        }

        return nil
    }

    private func pythonVersion(_ path: String) -> (Int, Int)? {
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: path)
        proc.arguments = ["--version"]
        let pipe = Pipe()
        proc.standardOutput = pipe
        proc.standardError = pipe
        try? proc.run()
        proc.waitUntilExit()
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        guard let output = String(data: data, encoding: .utf8) else { return nil }
        let parts = output.trimmingCharacters(in: .whitespacesAndNewlines)
            .replacingOccurrences(of: "Python ", with: "")
            .split(separator: ".")
        guard parts.count >= 2,
              let major = Int(parts[0]),
              let minor = Int(parts[1]) else { return nil }
        return (major, minor)
    }

    private func whichExecutable(_ name: String) -> String? {
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: "/usr/bin/which")
        proc.arguments = [name]
        let pipe = Pipe()
        proc.standardOutput = pipe
        try? proc.run()
        proc.waitUntilExit()
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        let path = String(data: data, encoding: .utf8)?
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return path.isEmpty ? nil : path
    }

    func resolveProjectRoot() -> String? {
        // Check stored value first
        if let stored = UserDefaults.standard.string(forKey: Self.projectRootKey),
           FileManager.default.fileExists(atPath: stored + "/pyproject.toml") {
            return stored
        }

        let searchRoots: [URL] = [
            Bundle.main.bundleURL
                .deletingLastPathComponent()
                .deletingLastPathComponent()
                .deletingLastPathComponent()
                .deletingLastPathComponent(),
            FileManager.default.homeDirectoryForCurrentUser
                .appendingPathComponent("Developer/Giva"),
            URL(fileURLWithPath: FileManager.default.currentDirectoryPath),
        ]

        for root in searchRoots {
            var candidate = root
            for _ in 0..<6 {
                let pyproject = candidate.appendingPathComponent("pyproject.toml")
                if FileManager.default.fileExists(atPath: pyproject.path) {
                    if let content = try? String(contentsOf: pyproject, encoding: .utf8),
                       content.contains("name = \"giva\"") {
                        let path = candidate.path
                        UserDefaults.standard.set(path, forKey: Self.projectRootKey)
                        return path
                    }
                }
                candidate = candidate.deletingLastPathComponent()
            }
        }

        return nil
    }

    private func findSetupScript(projectRoot: String) -> String? {
        let candidates = [
            projectRoot + "/scripts/giva-setup.py",
        ]
        for path in candidates {
            if FileManager.default.fileExists(atPath: path) {
                return path
            }
        }
        return nil
    }
}
