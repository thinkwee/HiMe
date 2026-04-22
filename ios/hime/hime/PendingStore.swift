import Foundation

/// Thread-safe, file-backed queue of HealthPayloads.
/// Uses a lock to ensure thread safety across different background/main threads.
final class PendingStore: @unchecked Sendable {
    static let shared = PendingStore()

    private let fileURL: URL
    private let lock = NSLock()
    private let maxSize = 100_000 
    
    private var memoryCache: [HealthPayload]? = nil
    /// Tracks the file modification date when cache was last populated.
    private var cachedFileModDate: Date? = nil

    private init() {
        let caches = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
        let url = caches.appendingPathComponent("hk_pending.json")
        self.fileURL = url
    }

    /// Returns the modification date of the backing file, or nil if absent.
    private func fileModificationDate() -> Date? {
        try? FileManager.default.attributesOfItem(atPath: fileURL.path)[.modificationDate] as? Date
    }

    // MARK: - Write

    func append(_ payloads: [HealthPayload]) {
        guard !payloads.isEmpty else { return }
        lock.lock()
        defer { lock.unlock() }

        var existing = performLoad()
        existing.append(contentsOf: payloads)
        
        if existing.count > maxSize {
            existing = Array(existing.suffix(maxSize))
        }
        
        performSave(existing)
    }

    // MARK: - Transactional Read

    func peek(limit: Int) -> [HealthPayload] {
        lock.lock()
        defer { lock.unlock() }
        let all = performLoad()
        return Array(all.prefix(limit))
    }

    func pop(count: Int) {
        guard count > 0 else { return }
        lock.lock()
        defer { lock.unlock() }
        var all = performLoad()
        let toRemove = min(count, all.count)
        all.removeFirst(toRemove)
        performSave(all)
    }

    var count: Int {
        lock.lock()
        defer { lock.unlock() }
        return performLoad().count
    }

    // MARK: - Private (Called within lock)

    private func performLoad() -> [HealthPayload] {
        let currentModDate = fileModificationDate()

        // Invalidate cache if the file was modified externally
        if let cached = memoryCache {
            if currentModDate == cachedFileModDate {
                return cached
            }
            // File changed on disk since we last cached — re-read
        }

        guard let data = try? Data(contentsOf: fileURL) else {
            memoryCache = []
            cachedFileModDate = nil
            return []
        }

        do {
            let decoded = try JSONDecoder().decode([HealthPayload].self, from: data)
            memoryCache = decoded
            cachedFileModDate = currentModDate
            return decoded
        } catch {
            // Decoding failed (e.g. format changed). Archive the corrupted file
            // before clearing, so data can potentially be recovered.
            HealthKitManager.bgLog("Store: Decoding failed (\(error)), archiving corrupted cache.")
            let backupURL = fileURL.deletingLastPathComponent()
                .appendingPathComponent("pending_backup_\(Int(Date().timeIntervalSince1970)).json")
            try? FileManager.default.moveItem(at: fileURL, to: backupURL)
            memoryCache = []
            cachedFileModDate = nil
            return []
        }
    }

    private func performSave(_ payloads: [HealthPayload]) {
        memoryCache = payloads
        do {
            let data = try JSONEncoder().encode(payloads)
            try data.write(to: fileURL, options: .atomic)
            cachedFileModDate = fileModificationDate()
        } catch {
            LogManager.shared.log("PendingStore encode failed: \(error.localizedDescription)")
        }
    }
}
