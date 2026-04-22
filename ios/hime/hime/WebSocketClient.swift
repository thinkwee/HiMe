import Foundation
import Combine
import UIKit

// MARK: - WebSocket & HTTP Session Delegate

private final class SessionDelegate: NSObject, URLSessionWebSocketDelegate, URLSessionTaskDelegate, URLSessionDelegate, @unchecked Sendable {
    weak var client: WebSocketClient?

    func urlSessionDidFinishEvents(forBackgroundURLSession session: URLSession) {
        guard let identifier = session.configuration.identifier else { return }
        Task { @MainActor in
            client?.executeBackgroundCompletionHandler(for: identifier)
        }
    }

    func urlSession(_ session: URLSession, webSocketTask: URLSessionWebSocketTask, didOpenWithProtocol protocol: String?) {
        Task { @MainActor in
            await client?.onWSOpened()
        }
    }

    /// WebSocket/HTTP failure
    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        let isWS = task is URLSessionWebSocketTask
        let fileURLString = task.taskDescription
        
        Task { @MainActor in
            if let error = error {
                if isWS {
                    client?.onWSTaskFailed(task: task, error: error)
                } else {
                    client?.onHTTPTaskFailed(task: task, error: error)
                }
            } else if !isWS {
                // Background HTTP success
                client?.onHTTPTaskSucceeded(task: task)
            }
            
            // Cleanup temp file if this was an upload task
            if let desc = fileURLString, let fileURL = URL(string: desc) {
                try? FileManager.default.removeItem(at: fileURL)
            }
        }
    }
}

/// Guards a CheckedContinuation so racing callers (send completion vs. timeout
/// fallback) can't double-resume and won't leak the continuation if neither
/// path ever fires. Used by _sendWS. Touched only from @MainActor.
@MainActor
private final class ContinuationGuard {
    private var continuation: CheckedContinuation<Void, Never>?
    init(_ c: CheckedContinuation<Void, Never>) { continuation = c }
    @discardableResult
    func resume() -> Bool {
        guard let c = continuation else { return false }
        continuation = nil
        c.resume()
        return true
    }
}

@MainActor
final class WebSocketClient: ObservableObject {
    static let shared = WebSocketClient()

    @Published private(set) var isConnected = false
    @Published var serverConfig: ServerConfig = ServerConfig.load() {
        didSet { serverConfig.save() }
    }

    @Published var isSyncActive: Bool = !UserDefaults.standard.bool(forKey: "userRequestedDisconnect") {
        didSet {
            UserDefaults.standard.set(!isSyncActive, forKey: "userRequestedDisconnect")
        }
    }

    /// Global sync toggle based on whether the user has requested a connection.
    /// If false, no data will be sent via WS or HTTP.
    var isSyncEnabled: Bool {
        isSyncActive
    }

    /// WebSocket URL for data sync (watch exporter).
    var serverURL: String { serverConfig.watchURL }

    /// HTTP base URL for watch exporter uploads.
    var httpBaseURL: String { serverConfig.watchHTTPBaseURL }

    // MARK: - Private state

    private var wsTask: URLSessionWebSocketTask?
    private var shouldReconnect = false
    private var reconnectDelay: TimeInterval = 1.0
    private let maxReconnectDelay: TimeInterval = 60.0

    // Heartbeat / dead-connection detection
    private var heartbeatTimer: DispatchSourceTimer?
    private let heartbeatInterval: TimeInterval = 30.0
    private let heartbeatTimeout: TimeInterval = 10.0
    
    private let chunkSize = 500 // Max records per batch to avoid timeouts
    private var _isFlushing = false // Guard against concurrent flush calls

    // HTTP retry backoff: if an upload fails the queue has no natural pump,
    // so we must re-kick _flush ourselves. Exponential, resets on any success.
    private var httpRetryDelay: TimeInterval = 2.0
    private let maxHttpRetryDelay: TimeInterval = 60.0

    private let queue = DispatchQueue(label: "hime.websocket", qos: .utility)
    private let sessionDelegate = SessionDelegate()

    // Map of HTTP TaskIdentifier -> Number of payloads sent.
    // Persisted to UserDefaults so that background URLSession delegate callbacks
    // can correctly pop PendingStore even after app suspension/termination.
    private static let kPendingHTTPKey = "hime.pendingHTTPTasks"
    private var pendingHTTPTasks: [Int: Int] = [:] {
        didSet { _persistPendingHTTPTasks() }
    }

    private lazy var fgSession: URLSession = {
        let cfg = URLSessionConfiguration.default
        cfg.waitsForConnectivity = true
        cfg.timeoutIntervalForRequest = 30
        sessionDelegate.client = self
        return URLSession(configuration: cfg, delegate: sessionDelegate, delegateQueue: .main)
    }()

    private lazy var bgSession: URLSession = {
        let cfg = URLSessionConfiguration.background(withIdentifier: "com.hime.healthkit.upload")
        cfg.isDiscretionary = false
        cfg.sessionSendsLaunchEvents = true
        sessionDelegate.client = self
        return URLSession(configuration: cfg, delegate: sessionDelegate, delegateQueue: .main)
    }()

    private init() {
        // Restore pending HTTP task tracking from previous session
        if let dict = UserDefaults.standard.dictionary(forKey: Self.kPendingHTTPKey) as? [String: Int] {
            pendingHTTPTasks = Dictionary(uniqueKeysWithValues: dict.compactMap { k, v in Int(k).map { ($0, v) } })
        }
    }

    private func _persistPendingHTTPTasks() {
        let stringKeyed = Dictionary(uniqueKeysWithValues: pendingHTTPTasks.map { (String($0.key), $0.value) })
        UserDefaults.standard.set(stringKeyed, forKey: Self.kPendingHTTPKey)
    }

    // MARK: - Public API

    func connect() {
        self.isSyncActive = true
        queue.async { [weak self] in
            guard let self else { return }
            Task { @MainActor in
                // Explicitly kill any zombie task waiting for old IP connectivity
                self.wsTask?.cancel(with: .normalClosure, reason: nil)
                self.wsTask = nil
                self._openWS()
            }
        }
    }

    func reconnectIfNeeded() {
        queue.async { [weak self] in
            guard let self else { return }
            Task { @MainActor in
                guard self.isSyncActive else { return }
                self._openWS()
            }
        }
    }

    func disconnect(userInitiated: Bool = true) {
        if userInitiated {
            self.isSyncActive = false
        }
        queue.async { [weak self] in
            guard let self else { return }
            Task { @MainActor in
                self.shouldReconnect = false
                self._stopHeartbeat()
                self.wsTask?.cancel(with: .normalClosure, reason: nil)
                self.wsTask = nil
                self.isConnected = false
                HealthKitManager.bgLog("WS: User disconnected")
            }
        }
    }

    func send(_ payload: HealthPayload) {
        PendingStore.shared.append([payload])
        if isSyncEnabled {
            flushPending()
        }
    }

    func flushPending(appState: String = "foreground") {
        Task {
            guard self.isSyncEnabled else { return }
            await self._flush(appState: appState)
        }
    }

    func flushPendingAndWait(appState: String = "foreground") async {
        guard self.isSyncEnabled else { return }
        await self._flush(appState: appState)
    }

    // MARK: - Transports

    private func _flush(appState: String = "foreground") async {
        guard !_isFlushing else { return }
        _isFlushing = true
        defer { _isFlushing = false }

        let storeCount = PendingStore.shared.count
        guard storeCount > 0 else { return }

        let limit = chunkSize
        let payloads = PendingStore.shared.peek(limit: limit)
        guard !payloads.isEmpty else { return }

        let transport = (isConnected && appState != "background") ? "WS" : "HTTP"
        HealthKitManager.bgLog("📤 FLUSH: \(payloads.count)/\(storeCount) records via \(transport) (appState=\(appState), wsConnected=\(isConnected))")

        if isConnected && appState != "background" {
            await _sendWS(payloads)
        } else {
            _sendHTTP(payloads, appState: appState)
        }
    }

    private func _sendWS(_ payloads: [HealthPayload]) async {
        let wireData = payloads.map { ["ts": $0.ts, "f": $0.f, "v": $0.v] }
        guard let data = try? JSONSerialization.data(withJSONObject: wireData) else { return }

        return await withCheckedContinuation { continuation in
            guard let task = wsTask else {
                continuation.resume()
                return
            }

            let guardBox = ContinuationGuard(continuation)

            // If task.send's completion never fires (e.g. WS cancelled in a
            // weird state), this prevents _flush from hanging forever with
            // _isFlushing stuck true, which would freeze the entire queue.
            let timeoutTask = Task { @MainActor in
                try? await Task.sleep(nanoseconds: 60 * 1_000_000_000)
                guard !Task.isCancelled else { return }
                if guardBox.resume() {
                    HealthKitManager.bgLog("WS: Send timeout — forcing resume")
                    self.isConnected = false
                    self.wsTask?.cancel(with: .abnormalClosure, reason: nil)
                    self.wsTask = nil
                }
            }

            task.send(.data(data)) { [weak self] error in
                guard let self else {
                    Task { @MainActor in
                        timeoutTask.cancel()
                        guardBox.resume()
                    }
                    return
                }
                if let error = error {
                    Task { @MainActor in
                        timeoutTask.cancel()
                        HealthKitManager.bgLog("WS: Send ERR — \(error.localizedDescription)")
                        self.isConnected = false
                        guardBox.resume()
                    }
                } else {
                    Task { @MainActor in
                        timeoutTask.cancel()
                        PendingStore.shared.pop(count: payloads.count)
                        HealthKitManager.shared.markOldestAsSynced(count: payloads.count)
                        HealthKitManager.bgLog("WS: Sent \(payloads.count) records")
                        if PendingStore.shared.count > 0 {
                            await self._flush()
                        }
                        guardBox.resume()
                    }
                }
            }
        }
    }

    private func _sendHTTP(_ payloads: [HealthPayload], appState: String) {
        guard let url = URL(string: httpBaseURL + "/ingest") else {
            HealthKitManager.bgLog("HTTP: Invalid ingest URL from httpBaseURL: \(httpBaseURL)")
            return
        }
        var request = APIClient.request(url, method: "POST")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue(appState, forHTTPHeaderField: "X-Sync-Mode")
 
        let wireData = payloads.map { ["ts": $0.ts, "f": $0.f, "v": $0.v] }
        guard let data = try? JSONSerialization.data(withJSONObject: wireData) else { return }
        
        let tmp = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".json")
        try? data.write(to: tmp)

        let task = (appState == "foreground" ? fgSession : bgSession).uploadTask(with: request, fromFile: tmp)
        task.taskDescription = tmp.absoluteString
        pendingHTTPTasks[task.taskIdentifier] = payloads.count
        task.resume()
        HealthKitManager.bgLog("HTTP: Resume \(appState) upload (\(payloads.count)) — Backlog: \(PendingStore.shared.count)")
    }

    // MARK: - Delegate Callbacks

    func onWSTaskFailed(task: URLSessionTask, error: Error) {
        queue.async { [weak self] in
            guard let self else { return }
            Task { @MainActor in
                guard self.wsTask?.taskIdentifier == task.taskIdentifier else { return }
                self._stopHeartbeat()
                self.wsTask = nil
                self.isConnected = false
                HealthKitManager.bgLog("WS: Error — \(error.localizedDescription)")
                if self.shouldReconnect { self._scheduleReconnect() }
            }
        }
    }

    func onWSOpened() async {
        self.isConnected = true
        self.reconnectDelay = 1.0
        HealthKitManager.bgLog("WS: Connected")
        if let ws = wsTask {
            _startHeartbeat(ws)
        }
        await _flush()
    }

    func onHTTPTaskSucceeded(task: URLSessionTask) {
        queue.async { [weak self] in
            guard let self else { return }
            Task { @MainActor in
                guard let count = self.pendingHTTPTasks.removeValue(forKey: task.taskIdentifier) else { return }
                PendingStore.shared.pop(count: count)
                HealthKitManager.shared.markOldestAsSynced(count: count)
                HealthKitManager.bgLog("HTTP: Success (\(count) records)")
                self.httpRetryDelay = 2.0
                if PendingStore.shared.count > 0 { await self._flush() }
            }
        }
    }

    func onHTTPTaskFailed(task: URLSessionTask, error: Error) {
        queue.async { [weak self] in
            guard let self else { return }
            Task { @MainActor in
                // Remove from pending tracking but do NOT pop from PendingStore.
                // The data remains in PendingStore so the next flush cycle retries it.
                if let count = self.pendingHTTPTasks.removeValue(forKey: task.taskIdentifier) {
                    HealthKitManager.bgLog("HTTP: Task failed (\(count) records kept for retry) — \(error.localizedDescription)")
                } else {
                    HealthKitManager.bgLog("HTTP: Task failed — \(error.localizedDescription)")
                }
                // Symmetric to the success path: re-kick the drain, otherwise the
                // initial-install backfill freezes on the first failed chunk until
                // the user force-kills and relaunches the app. Backoff avoids
                // hammering a struggling server.
                guard PendingStore.shared.count > 0 else { return }
                let delay = self.httpRetryDelay
                self.httpRetryDelay = min(self.httpRetryDelay * 2, self.maxHttpRetryDelay)
                try? await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000))
                await self._flush()
            }
        }
    }

    // MARK: - WS Lifecycle

    private func _openWS() {
        guard wsTask == nil else { return }

        guard var components = URLComponents(string: serverURL) else { return }
        // Ensure path ends with /ws
        let path = components.path.hasSuffix("/") ? components.path.dropLast() : components.path[...]
        if !path.hasSuffix("/ws") {
            components.path = String(path) + "/ws"
        }
        // WebSocket clients can't set HTTP headers, so pass the auth
        // token as a query parameter for BearerAuthMiddleware.
        let token = ServerConfig.authToken
        if !token.isEmpty {
            var items = components.queryItems ?? []
            items.append(URLQueryItem(name: "token", value: token))
            components.queryItems = items
        }
        guard let url = components.url else { return }
        
        shouldReconnect = true
        reconnectDelay = 1.0
        
        let ws = fgSession.webSocketTask(with: url)
        wsTask = ws
        ws.resume()
        
        ws.sendPing { _ in }
        _receiveLoop(ws)
    }

    private func _receiveLoop(_ ws: URLSessionWebSocketTask) {
        ws.receive { [weak self] result in
            guard let self else { return }
            self.queue.async {
                Task { @MainActor in
                    switch result {
                    case .success:
                        self._receiveLoop(ws)
                    case .failure:
                        guard self.wsTask === ws else { return }
                        self._stopHeartbeat()
                        self.wsTask = nil
                        self.isConnected = false
                        if self.shouldReconnect { self._scheduleReconnect() }
                    }
                }
            }
        }
    }

    // MARK: - Heartbeat (dead-connection detection)

    private func _startHeartbeat(_ ws: URLSessionWebSocketTask) {
        _stopHeartbeat()
        let timer = DispatchSource.makeTimerSource(queue: queue)
        timer.schedule(deadline: .now() + heartbeatInterval, repeating: heartbeatInterval)
        timer.setEventHandler { [weak self, weak ws] in
            guard let self, let ws else { return }
            ws.sendPing { [weak self] error in
                guard let self else { return }
                if let error = error {
                    // Ping failed — connection is dead
                    self.queue.async {
                        Task { @MainActor in
                            HealthKitManager.bgLog("WS: Heartbeat ping failed — \(error.localizedDescription)")
                            guard self.wsTask === ws else { return }
                            self._stopHeartbeat()
                            self.wsTask?.cancel(with: .abnormalClosure, reason: nil)
                            self.wsTask = nil
                            self.isConnected = false
                            if self.shouldReconnect { self._scheduleReconnect() }
                        }
                    }
                }
                // Ping succeeded — connection is alive, nothing to do
            }
        }
        timer.resume()
        heartbeatTimer = timer
    }

    private func _stopHeartbeat() {
        heartbeatTimer?.cancel()
        heartbeatTimer = nil
    }

    private func _scheduleReconnect() {
        let delay = reconnectDelay
        reconnectDelay = min(reconnectDelay * 2, maxReconnectDelay)
        queue.asyncAfter(deadline: .now() + delay) { [weak self] in
            guard let self else { return }
            Task { @MainActor in
                guard self.shouldReconnect else { return }
                self._openWS()
            }
        }
    }

    // MARK: - Background Session Management
    
    private var backgroundCompletionHandlers: [String: () -> Void] = [:]
    
    func addBackgroundCompletionHandler(identifier: String, completion: @escaping () -> Void) {
        backgroundCompletionHandlers[identifier] = completion
    }
    
    func executeBackgroundCompletionHandler(for identifier: String) {
        if let completion = backgroundCompletionHandlers.removeValue(forKey: identifier) {
            completion()
        }
    }
}
