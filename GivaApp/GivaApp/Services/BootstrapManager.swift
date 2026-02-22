// BootstrapManager.swift - First-run setup: venv, pip install, launchd daemon.

import Foundation

enum BootstrapPhase: String {
    case findingPython = "Looking for Python..."
    case creatingVenv = "Creating virtual environment..."
    case installingDeps = "Installing dependencies (this may take a minute)..."
    case downloadingDefaultModel = "Downloading base AI model (~4 GB)..."
    case installingDaemon = "Setting up background service..."
    case startingServer = "Starting Giva server..."
    case done = "Ready!"
    case failed = "Setup failed"
}

@MainActor
class BootstrapManager: ObservableObject {
    @Published var phase: BootstrapPhase = .findingPython
    @Published var isComplete = false
    @Published var errorMessage: String?
    @Published var logLines: [String] = []

    /// Guard against concurrent bootstrap runs (e.g. `.task` re-firing
    /// while `upgrade()` is already running `runBootstrap()`).
    private var isRunning = false

    // Paths
    static let dataDir = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent(".local/share/giva")
    static let venvDir = dataDir.appendingPathComponent(".venv")
    static let venvPython = venvDir.appendingPathComponent("bin/python3")
    static let venvPip = venvDir.appendingPathComponent("bin/pip")
    static let venvGivaServer = venvDir.appendingPathComponent("bin/giva-server")

    private static let launchdLabel = "com.giva.server"
    private static let launchdPlistURL: URL = {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/LaunchAgents/com.giva.server.plist")
    }()

    /// Source project root — derived by walking up from the app bundle.
    /// Works in dev (Xcode run) because the app sits inside DerivedData while
    /// the project root is the repo. We store it at first bootstrap and reuse.
    static let projectRootKey = "GivaProjectRoot"

    /// Dirty flag key — set at start of bootstrap, cleared on success.
    /// If the app launches and this is true, the previous bootstrap failed
    /// partway through, so we must redo it from scratch.
    private static let dirtyKey = "GivaBootstrapDirty"

    var isBootstrapped: Bool {
        // If a previous bootstrap was interrupted or failed, treat as not bootstrapped.
        if UserDefaults.standard.bool(forKey: Self.dirtyKey) {
            return false
        }
        return FileManager.default.isExecutableFile(atPath: Self.venvPython.path)
    }

    // MARK: - Auto-start (called from .task on the view)

    /// Decide whether to do a full bootstrap or just reconnect to an existing daemon.
    func start() async {
        guard !isComplete && phase != .failed && !isRunning else { return }

        // If a previous bootstrap was interrupted, clean up before retrying.
        if UserDefaults.standard.bool(forKey: Self.dirtyKey) {
            log("Previous setup was incomplete — starting fresh...")
            cleanVenv()
        }

        if isBootstrapped {
            // Fast path: venv already exists from a previous run.
            phase = .startingServer
            log("Already bootstrapped — reconnecting to daemon...")
            try? ensureDaemonRunning()
            let healthy = await checkHealth()
            if !healthy {
                // One more attempt
                try? ensureDaemonRunning()
                _ = await checkHealth()
            }

            // Check if the source code has changed since last install.
            // If the git commit doesn't match, reinstall to pick up changes.
            if await shouldUpgradeForNewCommit() {
                log("Source code updated — reinstalling...")
                await upgrade()
                return
            }

            phase = .done
            isComplete = true
        } else {
            await runBootstrap()
        }
    }

    /// Compare the server's installed commit against the current source repo commit.
    /// Returns true if the source has changed and the server needs a reinstall.
    private func shouldUpgradeForNewCommit() async -> Bool {
        guard let projectRoot = UserDefaults.standard.string(forKey: Self.projectRootKey) else {
            return false
        }

        let localCommit = getLocalGitCommit(projectRoot: projectRoot)
        guard !localCommit.isEmpty, localCommit != "unknown" else { return false }

        let serverCommit = await getServerCommit()
        guard !serverCommit.isEmpty, serverCommit != "unknown" else { return false }

        if localCommit != serverCommit {
            log("Version mismatch: server=\(serverCommit.prefix(8)), local=\(localCommit.prefix(8))")
            return true
        }
        return false
    }

    /// Get the current git HEAD commit from the local source checkout.
    private func getLocalGitCommit(projectRoot: String) -> String {
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: "/usr/bin/git")
        proc.arguments = ["rev-parse", "HEAD"]
        proc.currentDirectoryURL = URL(fileURLWithPath: projectRoot)
        let pipe = Pipe()
        proc.standardOutput = pipe
        proc.standardError = FileHandle.nullDevice
        do {
            try proc.run()
            proc.waitUntilExit()
            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            return String(data: data, encoding: .utf8)?
                .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        } catch {
            return ""
        }
    }

    /// Query the running server's /api/health endpoint for its commit hash.
    private func getServerCommit() async -> String {
        let url = URL(string: "http://127.0.0.1:7483/api/health")!
        do {
            let (data, response) = try await URLSession.shared.data(from: url)
            guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
                return ""
            }
            let health = try JSONDecoder().decode(HealthResponse.self, from: data)
            return health.commit
        } catch {
            return ""
        }
    }

    // MARK: - Main Entry

    func runBootstrap() async {
        isRunning = true
        defer { isRunning = false }

        // Mark as dirty — only cleared on full success.
        // If the app is killed or bootstrap fails, the next launch will redo from scratch.
        UserDefaults.standard.set(true, forKey: Self.dirtyKey)

        do {
            // Step 1: Find system python3
            phase = .findingPython
            let systemPython = try findSystemPython()
            log("Found Python: \(systemPython)")

            // Step 2: Resolve project root (where pyproject.toml lives)
            let projectRoot = try resolveProjectRoot()
            log("Project root: \(projectRoot)")

            // Step 3: Create venv
            phase = .creatingVenv
            try await createVenv(systemPython: systemPython)
            log("Virtual environment created")

            // Step 4: Install giva into venv
            phase = .installingDeps
            try await installPackage(projectRoot: projectRoot)
            log("Dependencies installed")

            // Step 5: Pre-download default AI model into HuggingFace cache
            phase = .downloadingDefaultModel
            try await downloadDefaultModel()
            log("Default model ready")

            // Step 6: Install launchd daemon
            phase = .installingDaemon
            try installLaunchdAgent()
            log("Background service installed")

            // Step 7: Start the daemon
            phase = .startingServer
            try startLaunchdAgent()
            log("Server starting...")

            // Wait for health check
            let healthy = await waitForHealth(timeout: 90)
            if healthy {
                log("Server is healthy!")
            } else {
                log("Warning: server didn't respond to health check yet (may still be loading models)")
            }

            phase = .done
            isComplete = true
            // Bootstrap succeeded — clear the dirty flag.
            UserDefaults.standard.set(false, forKey: Self.dirtyKey)
        } catch {
            phase = .failed
            errorMessage = error.localizedDescription
            log("ERROR: \(error.localizedDescription)")
            // Dirty flag stays true — next launch will redo from scratch.
        }
    }

    // MARK: - Step 1: Find Python

    private func findSystemPython() throws -> String {
        let candidates = [
            "/opt/homebrew/bin/python3",
            "/usr/local/bin/python3",
            "/usr/bin/python3",
        ]

        for path in candidates {
            if FileManager.default.isExecutableFile(atPath: path) {
                if let (major, minor) = pythonVersion(path),
                   major > 3 || (major == 3 && minor >= 11) {
                    return path
                }
            }
        }

        // Fallback: which python3
        if let path = whichExecutable("python3"),
           let (major, minor) = pythonVersion(path),
           major > 3 || (major == 3 && minor >= 11) {
            return path
        }

        throw BootstrapError.pythonNotFound
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
        // "Python 3.13.2"
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

    // MARK: - Step 2: Resolve Project Root

    private func resolveProjectRoot() throws -> String {
        // Check if already stored from a previous run
        if let stored = UserDefaults.standard.string(forKey: Self.projectRootKey),
           FileManager.default.fileExists(atPath: stored + "/pyproject.toml") {
            return stored
        }

        // Strategy: walk up from the app bundle location looking for pyproject.toml
        // In dev: Bundle is in DerivedData, but we can use __FILE__ equivalent
        // at build time. Instead, try common locations.
        let searchRoots: [URL] = [
            // The repo checkout — app bundle is at GivaApp/ inside the repo
            Bundle.main.bundleURL
                .deletingLastPathComponent()  // Build/Products/Debug or Release
                .deletingLastPathComponent()  // Build/Products
                .deletingLastPathComponent()  // Build
                .deletingLastPathComponent(), // DerivedData/GivaApp-xxx
            // Common dev location
            FileManager.default.homeDirectoryForCurrentUser
                .appendingPathComponent("Developer/Giva"),
            // Current working directory (when run from Xcode)
            URL(fileURLWithPath: FileManager.default.currentDirectoryPath),
        ]

        for root in searchRoots {
            // Walk up to 5 levels
            var candidate = root
            for _ in 0..<6 {
                let pyproject = candidate.appendingPathComponent("pyproject.toml")
                if FileManager.default.fileExists(atPath: pyproject.path) {
                    // Verify it's the giva project
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

        throw BootstrapError.projectNotFound
    }

    // MARK: - Step 3: Create Venv

    private func createVenv(systemPython: String) async throws {
        // Ensure parent directory exists
        try FileManager.default.createDirectory(
            at: Self.dataDir, withIntermediateDirectories: true
        )

        // Skip if venv already exists and has a working python
        if FileManager.default.isExecutableFile(atPath: Self.venvPython.path) {
            log("Venv already exists, skipping creation")
            return
        }

        try await runProcess(
            executable: systemPython,
            arguments: ["-m", "venv", Self.venvDir.path]
        )
    }

    // MARK: - Step 4: Install Package

    private func installPackage(projectRoot: String) async throws {
        // Step 1: Upgrade pip itself
        try await runProcess(
            executable: Self.venvPip.path,
            arguments: ["install", "--upgrade", "pip"]
        )
        // Step 2: Install the project editable with voice extras
        // pip requires the extras in the same argument as the path: ".[voice]"
        // We use "." as the path and set the working directory via the project root.
        try await runProcess(
            executable: Self.venvPip.path,
            arguments: ["install", "-e", ".\(Self.voiceExtras)"],
            workingDirectory: projectRoot
        )
    }

    /// Extras specifier for pip install. Includes voice dependencies.
    private static let voiceExtras = "[voice]"

    // MARK: - Step 5: Download Default Model

    /// Default model ID that fits any M-series Mac (~4.5GB).
    /// Also serves as the filter model and bootstrap advisor.
    private static let defaultModelId = "mlx-community/Qwen3-8B-4bit"

    private func downloadDefaultModel() async throws {
        // Use the venv python to download via huggingface_hub (installed as mlx-lm dep)
        // snapshot_download will skip if already cached.
        let script = """
        from huggingface_hub import snapshot_download
        snapshot_download('\(Self.defaultModelId)')
        print('Model downloaded successfully')
        """
        try await runProcess(
            executable: Self.venvPython.path,
            arguments: ["-c", script]
        )
    }

    // MARK: - Step 6: Install launchd Agent

    private func installLaunchdAgent() throws {
        let plistDir = Self.launchdPlistURL.deletingLastPathComponent()
        try FileManager.default.createDirectory(
            at: plistDir, withIntermediateDirectories: true
        )

        let logDir = Self.dataDir.appendingPathComponent("logs")
        try FileManager.default.createDirectory(
            at: logDir, withIntermediateDirectories: true
        )

        let plistContent: [String: Any] = [
            "Label": Self.launchdLabel,
            "ProgramArguments": [
                Self.venvPython.path,
                "-m", "giva.server",
            ],
            "RunAtLoad": true,
            "KeepAlive": [
                "SuccessfulExit": false,  // Restart on crash, not on clean exit
            ],
            "StandardOutPath": logDir.appendingPathComponent("server.log").path,
            "StandardErrorPath": logDir.appendingPathComponent("server.err").path,
            "EnvironmentVariables": [
                "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/usr/sbin:/bin:/sbin",
                "HOME": FileManager.default.homeDirectoryForCurrentUser.path,
            ],
            "ProcessType": "Interactive",
        ]

        let data = try PropertyListSerialization.data(
            fromPropertyList: plistContent,
            format: .xml,
            options: 0
        )
        try data.write(to: Self.launchdPlistURL)
        log("Wrote plist to \(Self.launchdPlistURL.path)")
    }

    // MARK: - Upgrade (delete venv, reinstall from scratch)

    /// Full upgrade: stop daemon, delete venv, reinstall everything (including voice).
    func upgrade() async {
        guard !isRunning else { return }

        // Reset state so the UI switches back to the bootstrap view
        isComplete = false
        errorMessage = nil
        logLines = []
        phase = .findingPython
        log("Starting upgrade — stopping server...")

        // Stop the launchd daemon
        stopDaemon()

        // Delete the entire venv directory
        log("Removing virtual environment...")
        cleanVenv()

        // Re-run the full bootstrap (which will recreate venv, install with voice, etc.)
        await runBootstrap()
    }

    /// Remove the venv directory so the next bootstrap starts fresh.
    private func cleanVenv() {
        let fm = FileManager.default
        if fm.fileExists(atPath: Self.venvDir.path) {
            try? fm.removeItem(at: Self.venvDir)
            log("Removed stale virtual environment")
        }
    }

    /// Stop the launchd daemon (bootout). Ignores errors if not running.
    private func stopDaemon() {
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: "/bin/launchctl")
        proc.arguments = ["bootout", "gui/\(getuid())/\(Self.launchdLabel)"]
        proc.standardOutput = FileHandle.nullDevice
        proc.standardError = FileHandle.nullDevice
        try? proc.run()
        proc.waitUntilExit()
    }

    // MARK: - Public Helpers (used by GivaApp for reconnect)

    func ensureDaemonRunning() throws {
        // If plist exists, try to start the agent
        guard FileManager.default.fileExists(atPath: Self.launchdPlistURL.path) else {
            // No plist — need full bootstrap
            return
        }
        try startLaunchdAgent()
    }

    func checkHealth() async -> Bool {
        return await waitForHealth(timeout: 15)
    }

    // MARK: - Step 6: Start Agent

    private func startLaunchdAgent() throws {
        // Unload first (ignore errors if not loaded)
        let unload = Process()
        unload.executableURL = URL(fileURLWithPath: "/bin/launchctl")
        unload.arguments = ["bootout", "gui/\(getuid())/\(Self.launchdLabel)"]
        unload.standardOutput = FileHandle.nullDevice
        unload.standardError = FileHandle.nullDevice
        try? unload.run()
        unload.waitUntilExit()

        // Load
        let load = Process()
        load.executableURL = URL(fileURLWithPath: "/bin/launchctl")
        load.arguments = ["bootstrap", "gui/\(getuid())", Self.launchdPlistURL.path]
        let errPipe = Pipe()
        load.standardError = errPipe
        load.standardOutput = FileHandle.nullDevice
        try load.run()
        load.waitUntilExit()

        if load.terminationStatus != 0 {
            let errData = errPipe.fileHandleForReading.readDataToEndOfFile()
            let errStr = String(data: errData, encoding: .utf8) ?? "unknown"
            // Error 37 = "already loaded" — that's fine
            if !errStr.contains("37") && load.terminationStatus != 37 {
                throw BootstrapError.launchdFailed(errStr)
            }
        }
    }

    // MARK: - Health Check

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
            try? await Task.sleep(nanoseconds: 1_000_000_000) // 1s
        }
        return false
    }

    // MARK: - Process Runner

    private func runProcess(executable: String, arguments: [String], workingDirectory: String? = nil) async throws {
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            DispatchQueue.global(qos: .userInitiated).async {
                let proc = Process()
                proc.executableURL = URL(fileURLWithPath: executable)
                proc.arguments = arguments

                if let wd = workingDirectory {
                    proc.currentDirectoryURL = URL(fileURLWithPath: wd)
                }

                // Minimal PATH so pip/python can find system tools
                var env = ProcessInfo.processInfo.environment
                let extraPaths = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
                env["PATH"] = extraPaths + ":" + (env["PATH"] ?? "")
                proc.environment = env

                let outPipe = Pipe()
                let errPipe = Pipe()
                proc.standardOutput = outPipe
                proc.standardError = errPipe

                do {
                    try proc.run()
                } catch {
                    continuation.resume(throwing: error)
                    return
                }

                proc.waitUntilExit()

                let outData = outPipe.fileHandleForReading.readDataToEndOfFile()
                let errData = errPipe.fileHandleForReading.readDataToEndOfFile()
                let outStr = String(data: outData, encoding: .utf8) ?? ""
                let errStr = String(data: errData, encoding: .utf8) ?? ""

                if proc.terminationStatus != 0 {
                    let combined = (outStr + "\n" + errStr).trimmingCharacters(in: .whitespacesAndNewlines)
                    continuation.resume(throwing: BootstrapError.commandFailed(
                        cmd: ([executable] + arguments).joined(separator: " "),
                        output: String(combined.suffix(500))
                    ))
                    return
                }

                // Log last few lines of stdout for visibility
                if !outStr.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                    let lines = outStr.split(separator: "\n").suffix(3)
                    Task { @MainActor in
                        for line in lines {
                            self.logLines.append(String(line))
                        }
                    }
                }

                continuation.resume(returning: ())
            }
        }
    }

    // MARK: - Logging

    private func log(_ message: String) {
        logLines.append(message)
    }
}

// MARK: - Errors

enum BootstrapError: LocalizedError {
    case pythonNotFound
    case projectNotFound
    case commandFailed(cmd: String, output: String)
    case launchdFailed(String)

    var errorDescription: String? {
        switch self {
        case .pythonNotFound:
            return "Python 3.11+ not found. Install via: brew install python3"
        case .projectNotFound:
            return "Could not locate the Giva project source (pyproject.toml). "
                + "Make sure the app is run from the repository checkout."
        case .commandFailed(let cmd, let output):
            return "Command failed: \(cmd)\n\(output)"
        case .launchdFailed(let msg):
            return "Could not start background service: \(msg)"
        }
    }
}
