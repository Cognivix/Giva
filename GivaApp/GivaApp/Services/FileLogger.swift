// FileLogger.swift - Dual-destination logger: os.Logger (Console.app) + file.
//
// All Swift-side log calls go through `Log.make(category:)` which returns a
// `FileLogger`.  Each call writes to both the unified log (queryable via
// `log stream --predicate 'subsystem == "com.giva.app"'`) AND a rolling
// text file at `~/.local/share/giva/logs/giva-app.log`.
//
// The file can be tailed during development:
//     tail -f ~/.local/share/giva/logs/giva-app.log
//
// Log levels mirror os.Logger: debug, info, warning (notice), error, fault.
// The file logger respects `GIVA_LOG_LEVEL` env var (DEBUG, INFO, WARNING,
// ERROR) and defaults to INFO.

import Foundation
import os

/// Central log factory — call `Log.make(category:)` in each file.
enum Log {
    /// Create a logger for the given category (e.g., "Session", "Bootstrap").
    static func make(category: String) -> FileLogger {
        FileLogger(category: category)
    }
}

/// Logger that writes to both os.Logger and a shared log file.
struct FileLogger {
    let osLog: Logger
    let category: String

    init(category: String) {
        self.osLog = Logger(subsystem: "com.giva.app", category: category)
        self.category = category
    }

    func debug(_ message: String) {
        osLog.debug("\(message)")
        FileLogWriter.shared.write(level: .debug, category: category, message: message)
    }

    func info(_ message: String) {
        osLog.info("\(message)")
        FileLogWriter.shared.write(level: .info, category: category, message: message)
    }

    func warning(_ message: String) {
        osLog.warning("\(message)")
        FileLogWriter.shared.write(level: .warning, category: category, message: message)
    }

    func error(_ message: String) {
        osLog.error("\(message)")
        FileLogWriter.shared.write(level: .error, category: category, message: message)
    }
}

// MARK: - File Writer (singleton, thread-safe)

final class FileLogWriter: @unchecked Sendable {
    static let shared = FileLogWriter()

    enum Level: Int, Comparable {
        case debug = 0
        case info = 1
        case warning = 2
        case error = 3

        var label: String {
            switch self {
            case .debug:   return "DEBUG"
            case .info:    return "INFO"
            case .warning: return "WARN"
            case .error:   return "ERROR"
            }
        }

        static func < (lhs: Level, rhs: Level) -> Bool {
            lhs.rawValue < rhs.rawValue
        }
    }

    private let queue = DispatchQueue(label: "com.giva.app.filelogger")
    private var fileHandle: FileHandle?
    private let dateFormatter: DateFormatter
    private let minLevel: Level

    private init() {
        dateFormatter = DateFormatter()
        dateFormatter.dateFormat = "yyyy-MM-dd HH:mm:ss.SSS"

        // Respect GIVA_LOG_LEVEL env var (same as Python side)
        let envLevel = ProcessInfo.processInfo.environment["GIVA_LOG_LEVEL"]?.uppercased()
        switch envLevel {
        case "DEBUG":   minLevel = .debug
        case "WARNING": minLevel = .warning
        case "ERROR":   minLevel = .error
        default:        minLevel = .info
        }

        openLogFile()
    }

    private func openLogFile() {
        let logsDir = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".local/share/giva/logs")

        try? FileManager.default.createDirectory(
            at: logsDir, withIntermediateDirectories: true
        )

        let logFile = logsDir.appendingPathComponent("giva-app.log")

        // Create file if it doesn't exist
        if !FileManager.default.fileExists(atPath: logFile.path) {
            FileManager.default.createFile(atPath: logFile.path, contents: nil)
        }

        fileHandle = try? FileHandle(forWritingTo: logFile)
        fileHandle?.seekToEndOfFile()

        // Write startup marker
        if let handle = fileHandle,
           let data = "\n--- Giva App started at \(dateFormatter.string(from: Date())) ---\n"
            .data(using: .utf8) {
            handle.write(data)
        }
    }

    func write(level: Level, category: String, message: String) {
        guard level >= minLevel else { return }

        queue.async { [self] in
            guard let handle = fileHandle else { return }
            let timestamp = dateFormatter.string(from: Date())
            let line = "\(timestamp) [\(level.label)] \(category): \(message)\n"
            if let data = line.data(using: .utf8) {
                handle.write(data)
            }
        }
    }

    deinit {
        try? fileHandle?.close()
    }
}
