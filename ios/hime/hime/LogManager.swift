import Foundation
import Combine

struct LogEntry: Identifiable {
    let id = UUID()
    let text: String
}

@MainActor
final class LogManager: ObservableObject {
    static let shared = LogManager()

    @Published var logs: [LogEntry] = []

    /// Maximum log file size in bytes (1 MB).
    private let maxFileSize: UInt64 = 1_024 * 1_024
    /// Path to the rolling log file on disk.
    private let logFileURL: URL
    /// File handle kept open for appending (lazily opened).
    private var fileHandle: FileHandle?

    private init() {
        let docs = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        logFileURL = docs.appendingPathComponent("hime_activity.log")

        // Load recent lines from disk into memory for the UI
        loadFromDisk()
    }

    deinit {
        try? fileHandle?.close()
    }

    func log(_ message: String) {
        let timestamp = Date().formatted(date: .omitted, time: .standard)
        let logLine = "[\(timestamp)] \(message)"
        logs.insert(LogEntry(text: logLine), at: 0)

        // Keep only last 100 logs in memory for the UI
        if logs.count > 500 {
            logs.removeLast()
        }

        print(logLine)

        // Persist to disk
        appendToDisk(logLine)
    }

    // MARK: - Disk Persistence

    private func appendToDisk(_ line: String) {
        let data = (line + "\n").data(using: .utf8) ?? Data()

        // Create file if it doesn't exist
        if !FileManager.default.fileExists(atPath: logFileURL.path) {
            FileManager.default.createFile(atPath: logFileURL.path, contents: nil)
            fileHandle = nil // Force re-open
        }

        if fileHandle == nil {
            do {
                fileHandle = try FileHandle(forWritingTo: logFileURL)
                fileHandle?.seekToEndOfFile()
            } catch {
                print("[LogManager] Failed to open log file: \(error.localizedDescription)")
                return
            }
        }

        guard let handle = fileHandle else { return }
        handle.seekToEndOfFile()
        handle.write(data)

        // Truncate if over max size
        let currentSize = handle.offsetInFile
        if currentSize > maxFileSize {
            truncateLogFile()
        }
    }

    /// Truncates the log file by keeping only the most recent half.
    private func truncateLogFile() {
        try? fileHandle?.close()
        fileHandle = nil

        guard let data = try? Data(contentsOf: logFileURL),
              let content = String(data: data, encoding: .utf8) else { return }

        let lines = content.components(separatedBy: "\n")
        // Keep the newest half of lines
        let keepCount = lines.count / 2
        let kept = lines.suffix(keepCount).joined(separator: "\n")

        try? kept.data(using: .utf8)?.write(to: logFileURL, options: .atomic)

        // Re-open handle
        fileHandle = try? FileHandle(forWritingTo: logFileURL)
        fileHandle?.seekToEndOfFile()
    }

    /// Load the last 100 lines from disk into the in-memory array on startup.
    private func loadFromDisk() {
        guard let data = try? Data(contentsOf: logFileURL),
              let content = String(data: data, encoding: .utf8) else { return }

        let lines = content.components(separatedBy: "\n")
            .filter { !$0.isEmpty }

        // Lines on disk are oldest-first; UI wants newest-first
        logs = lines.suffix(500).reversed().map { LogEntry(text: $0) }
    }
}
