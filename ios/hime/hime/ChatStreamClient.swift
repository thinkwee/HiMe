//
//  ChatStreamClient.swift
//  hime
//
//  Live downlink for in-app chat. Connects to the backend's existing
//  per-user agent event stream (`/api/stream/agent`) — a *separate* socket
//  from `WebSocketClient` (which is the HealthKit uplink on port 8765). The
//  `?client=ios` marker lets the backend treat this connection as iOS
//  presence so it knows whether to deliver replies live (online) or via APNs
//  (offline). The app closes this socket when it backgrounds, so presence is
//  an accurate online signal.
//
//  The client owns its own liveness: while a connection is wanted (between
//  `connect()` and `disconnect()`) it reconnects with exponential backoff on
//  any receive/ping failure, server close, server `error` frame, heartbeat
//  silence, or network-path change. Heartbeat contract with the backend: every
//  20s we send a text frame `{"type":"ping"}` and the server answers
//  `{"type":"pong"}`; if nothing at all arrives for 45s the socket is dead.
//

import Foundation
import Network

@MainActor
final class ChatStreamClient: NSObject {
    private var task: URLSessionWebSocketTask?
    private var session: URLSession?
    private var tickTimer: Timer?
    private var reconnectTask: Task<Void, Never>?
    private var pathMonitor: NWPathMonitor?
    private var lastPathKey: String?

    /// True between `connect()` and `disconnect()` — i.e. the view wants a live socket.
    private var wantsConnection = false
    /// True once the current socket has delivered a real (non-error) message.
    private var isOpen = false {
        didSet { if isOpen != oldValue { onLiveChange?(isOpen) } }
    }
    /// Bumped whenever a socket is created or dropped, so callbacks from a
    /// stale socket (late receive failure, old ping completion) are ignored.
    private var generation = 0
    private var backoff: TimeInterval = ChatStreamClient.minBackoff
    private var lastReceive = Date()
    private var lastPing = Date.distantPast

    private static let minBackoff: TimeInterval = 1
    private static let maxBackoff: TimeInterval = 30
    private static let pingInterval: TimeInterval = 20
    private static let silenceLimit: TimeInterval = 45
    private static let tickInterval: TimeInterval = 5

    /// Called on the main actor for every decoded agent event.
    var onEvent: (([String: Any]) -> Void)?
    /// Called on the main actor each time a (re)connection is confirmed live.
    var onConnected: (() -> Void)?
    /// Called on the main actor whenever the socket flips between live and down
    /// (drives the "Reconnecting…" indicator).
    var onLiveChange: ((Bool) -> Void)?
    /// True once the current socket has delivered a real message.
    var isLive: Bool { isOpen }

    /// Map the API base URL (http→ws, https→wss) and append the stream path.
    private func streamURL() -> URL? {
        let base = ServerConfig.load().apiBaseURL
        var wsBase = base
        if base.hasPrefix("https://") {
            wsBase = "wss://" + base.dropFirst("https://".count)
        } else if base.hasPrefix("http://") {
            wsBase = "ws://" + base.dropFirst("http://".count)
        }
        // Single-user backend: the agent monitor stream is keyed by user id
        // in the path (always "LiveUser" here). ?client=ios marks this socket
        // as the app's presence connection (drives the WS-vs-APNs decision).
        var comps = URLComponents(string: "\(wsBase)/api/stream/agent/LiveUser")
        comps?.queryItems = [URLQueryItem(name: "client", value: "ios")]
        return comps?.url
    }

    // MARK: - Public lifecycle

    func connect() {
        wantsConnection = true
        startPathMonitor()
        if task != nil {
            // Already connected or connecting. A socket that went silent while
            // the app was suspended is replaced instead of trusted.
            if Date().timeIntervalSince(lastReceive) > Self.silenceLimit { reconnectNow() }
            return
        }
        reconnectTask?.cancel()
        reconnectTask = nil
        open()
    }

    func disconnect() {
        wantsConnection = false
        reconnectTask?.cancel()
        reconnectTask = nil
        pathMonitor?.cancel()
        pathMonitor = nil
        lastPathKey = nil
        isOpen = false
        backoff = Self.minBackoff
        dropSocket()
    }

    // MARK: - Socket management

    private func open() {
        guard wantsConnection, task == nil, let url = streamURL() else { return }
        generation += 1
        let gen = generation
        let cfg = URLSessionConfiguration.default
        cfg.timeoutIntervalForRequest = 60
        let session = URLSession(configuration: cfg)
        // Bearer header rather than `?token=`: query strings are recorded verbatim
        // in reverse-proxy / tunnel access logs. `webSocketTask(with: URLRequest)`
        // carries custom headers through the HTTP upgrade, and the backend's
        // `_ws_token_ok` (backend/api/stream_routes.py) reads either form.
        var req = URLRequest(url: url)
        let token = ServerConfig.authToken
        if !token.isEmpty {
            req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        let task = session.webSocketTask(with: req)
        self.session = session
        self.task = task
        isOpen = false
        lastReceive = Date()
        lastPing = .distantPast
        task.resume()
        receiveLoop(gen)
        startTimer(gen)
        sendPing(gen)
    }

    /// Tear down the current socket, timer and session. Bumps `generation` so
    /// any in-flight callback from the old socket is ignored.
    private func dropSocket() {
        generation += 1
        tickTimer?.invalidate()
        tickTimer = nil
        task?.cancel(with: .goingAway, reason: nil)
        task = nil
        session?.invalidateAndCancel()
        session = nil
    }

    /// The current socket is unusable — drop it and retry with backoff.
    private func socketFailed(_ gen: Int) {
        guard gen == generation else { return }
        isOpen = false
        dropSocket()
        scheduleReconnect()
    }

    private func scheduleReconnect() {
        guard wantsConnection, reconnectTask == nil else { return }
        let delay = backoff
        backoff = min(backoff * 2, Self.maxBackoff)
        reconnectTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000))
            guard !Task.isCancelled, let self else { return }
            self.reconnectTask = nil
            self.open()
        }
    }

    /// Replace the socket immediately (network change / stale after resume).
    private func reconnectNow() {
        guard wantsConnection else { return }
        reconnectTask?.cancel()
        reconnectTask = nil
        isOpen = false
        backoff = Self.minBackoff
        dropSocket()
        open()
    }

    private func markOpen(_ gen: Int) {
        guard gen == generation, !isOpen else { return }
        isOpen = true
        backoff = Self.minBackoff
        onConnected?()
    }

    // MARK: - Heartbeat

    private func startTimer(_ gen: Int) {
        tickTimer?.invalidate()
        tickTimer = Timer.scheduledTimer(withTimeInterval: Self.tickInterval, repeats: true) { [weak self] _ in
            guard let self else { return }
            Task { @MainActor in self.tick(gen) }
        }
    }

    private func tick(_ gen: Int) {
        guard gen == generation else { return }
        let now = Date()
        if now.timeIntervalSince(lastReceive) > Self.silenceLimit {
            socketFailed(gen)
            return
        }
        if now.timeIntervalSince(lastPing) >= Self.pingInterval { sendPing(gen) }
    }

    /// Text-frame ping (answered by the server's `pong`) plus a protocol-level
    /// ping; a failure of either means the socket is gone.
    private func sendPing(_ gen: Int) {
        guard gen == generation, let task else { return }
        lastPing = Date()
        task.send(.string("{\"type\":\"ping\"}")) { [weak self] error in
            guard error != nil, let self else { return }
            Task { @MainActor in self.socketFailed(gen) }
        }
        task.sendPing { [weak self] error in
            guard error != nil, let self else { return }
            Task { @MainActor in self.socketFailed(gen) }
        }
    }

    // MARK: - Receive

    private func receiveLoop(_ gen: Int) {
        guard gen == generation, let task else { return }
        task.receive { [weak self] result in
            guard let self else { return }
            Task { @MainActor in
                guard gen == self.generation else { return }
                switch result {
                case .failure:
                    self.socketFailed(gen)
                case .success(let message):
                    self.lastReceive = Date()
                    var text: String?
                    switch message {
                    case .string(let s): text = s
                    case .data(let d): text = String(data: d, encoding: .utf8)
                    @unknown default: break
                    }
                    if let text, self.handle(text) { self.markOpen(gen) }
                    // After a server `error` frame the socket was failed and
                    // `generation` bumped, so this is a no-op.
                    self.receiveLoop(gen)
                }
            }
        }
    }

    /// Decode one frame and forward it. Returns false when the frame was a
    /// server `error` (the socket is failed and a reconnect scheduled).
    private func handle(_ text: String) -> Bool {
        // Cheap prefilter: pong / periodic status snapshots carry nothing the
        // chat UI needs, so skip the JSON decode for them.
        let head = text.prefix(48)
        if head.contains("\"pong\"") || head.contains("\"status_update\"") { return true }
        guard let data = text.data(using: .utf8),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { return true }
        // Only an `error` before the socket is confirmed live is a connection
        // failure (e.g. an old server rejecting the stream). Once open, `error`
        // also carries ordinary LLM/agent errors, which must not drop the socket.
        if (obj["type"] as? String) == "error", !isOpen {
            socketFailed(generation)
            return false
        }
        onEvent?(obj)
        return true
    }

    // MARK: - Network path

    /// Force a fresh socket when the network changes (Wi-Fi ↔ cellular, coming
    /// back online) — a TCP connection on the old path never reports failure.
    private func startPathMonitor() {
        guard pathMonitor == nil else { return }
        let monitor = NWPathMonitor()
        monitor.pathUpdateHandler = { [weak self] path in
            let satisfied = path.status == .satisfied
            let key = "\(satisfied)-" + path.availableInterfaces.map { "\($0.type)" }.joined(separator: ",")
            Task { @MainActor [weak self] in
                guard let self else { return }
                let changed = self.lastPathKey != nil && self.lastPathKey != key
                self.lastPathKey = key
                if changed && satisfied { self.reconnectNow() }
            }
        }
        monitor.start(queue: DispatchQueue(label: "hime.chat.path"))
        pathMonitor = monitor
    }
}
