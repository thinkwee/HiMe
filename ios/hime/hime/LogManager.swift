import Foundation
import Combine

struct LogEntry: Identifiable {
    let id = UUID()
    let text: String
}

/// Owns the on-disk log file. Every method must be called on `queue`, which
/// serialises handle access and keeps writes in emission order. Nothing here
/// runs on the main thread: appending fired a synchronous write per log line,
/// and the 1 MB truncation read and rewrote the whole file inline.
private final class LogFileWriter: @unchecked Sendable {
    let queue = DispatchQueue(label: "hime.logmanager", qos: .utility)

    /// Maximum log file size in bytes (1 MB).
    private let maxFileSize: UInt64 = 1_024 * 1_024
    /// Path to the rolling log file on disk.
    private let logFileURL: URL
    /// File handle kept open for appending (lazily opened).
    private var fileHandle: FileHandle?

    init(url: URL) {
        logFileURL = url
    }

    deinit {
        try? fileHandle?.close()
    }

    func append(_ line: String) {
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
        if handle.offsetInFile > maxFileSize {
            truncateLogFile()
        }
    }

    /// Returns up to `limit` of the most recent lines, oldest-first.
    func loadRecent(limit: Int) -> [String] {
        guard let data = try? Data(contentsOf: logFileURL),
              let content = String(data: data, encoding: .utf8) else { return [] }

        let lines = content.components(separatedBy: "\n")
            .filter { !$0.isEmpty }
        return Array(lines.suffix(limit))
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
}

@MainActor
final class LogManager: ObservableObject {
    static let shared = LogManager()

    @Published var logs: [LogEntry] = []

    /// Maximum number of log lines kept in memory for the UI.
    private let maxMemoryLogs = 500

    private let writer: LogFileWriter

    private init() {
        let docs = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        writer = LogFileWriter(url: docs.appendingPathComponent("hime_activity.log"))

        // Load recent lines from disk into memory for the UI
        loadFromDisk()
    }

    func log(_ message: String) {
        let timestamp = Date().formatted(date: .omitted, time: .standard)
        let logLine = "[\(timestamp)] \(message)"
        logs.insert(LogEntry(text: logLine), at: 0)

        if logs.count > maxMemoryLogs {
            logs.removeLast()
        }

        print(logLine)

        // Persist to disk off the main thread
        let writer = self.writer
        writer.queue.async { writer.append(logLine) }
    }

    /// Load the most recent lines from disk into the in-memory array on startup.
    private func loadFromDisk() {
        let writer = self.writer
        let limit = maxMemoryLogs
        writer.queue.async {
            let lines = writer.loadRecent(limit: limit)
            guard !lines.isEmpty else { return }
            Task { @MainActor in
                // Lines on disk are oldest-first; UI wants newest-first. Anything
                // logged while the read was in flight is newer, so it stays on top.
                self.logs.append(contentsOf: lines.reversed().map { LogEntry(text: $0) })
                if self.logs.count > limit {
                    self.logs.removeLast(self.logs.count - limit)
                }
            }
        }
    }
}
