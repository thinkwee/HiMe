//
//  ChatHub.swift
//  hime
//
//  App-wide owner of the in-app chat plumbing, so that many conversations
//  ("threads") can share ONE event socket:
//
//    * owns the single `ChatStreamClient` (self-healing reconnect, heartbeat)
//      and decides when it should be connected (any chat screen visible and
//      the app foregrounded — the backend uses that presence to choose live
//      delivery vs. APNs);
//    * routes every decoded agent event to the right per-thread
//      `ChatViewModel` by its `thread_id` (events without one belong to the
//      permanent "main" thread — that is also what old servers send);
//    * keeps the thread list (active + archived), applies create / rename /
//      archive / delete, and live-updates it from `chat_thread_updated` /
//      `chat_thread_deleted` events and from new messages;
//    * tracks unread state locally.
//
//  Old servers answer 404 to `GET /api/agent/chat/threads`; `threadsSupported`
//  then becomes false and the UI hides every thread affordance, leaving the
//  single main chat exactly as before.
//

import Combine
import Foundation
import UIKit

/// Whether the "Reconnecting…" marker should show. Separate from `ChatHub` so
/// a thread-list change never re-renders the conversation that observes this.
@MainActor
final class ChatConnectionState: ObservableObject {
    @Published var showReconnecting = false
}

@MainActor
final class ChatHub: ObservableObject {
    static let shared = ChatHub()

    /// nil until the first thread-list response (or a failure that says nothing).
    @Published private(set) var threadsSupported: Bool?
    /// Active (non-archived) threads, main first.
    @Published private(set) var threads: [ChatThread] = [.main]
    /// Archived threads; filled when the list's "Archived" section is opened.
    @Published private(set) var archived: [ChatThread] = []
    /// Last time each thread was looked at (drives unread dots). Persisted.
    @Published private(set) var seen: [String: Date] = [:]
    /// Set when a thread action failed; the list shows it in an alert.
    @Published var actionError: String?

    let connection = ChatConnectionState()
    /// Fires with a thread id when that thread was deleted (here or elsewhere).
    let removedThread = PassthroughSubject<String, Never>()

    /// Thread currently on screen (cleared by `threadClosed`).
    private(set) var openThreadId: String?

    private let stream = ChatStreamClient()
    private var viewModels: [String: ChatViewModel] = [:]
    private var activeScreens = 0
    private var appActive = true
    private var reconnectIndicatorTask: Task<Void, Never>?
    private var disconnectTask: Task<Void, Never>?
    private var isLoadingThreads = false
    private var cancellables = Set<AnyCancellable>()
    private let baseline: Date

    private static let seenKey = "chat.threadSeen"
    private static let baselineKey = "chat.unreadBaseline"
    private static let maxCachedModels = 8

    private var apiBase: String { ServerConfig.load().apiBaseURL }
    private var wantStream: Bool { activeScreens > 0 && appActive }

    private init() {
        let defaults = UserDefaults.standard
        if let t = defaults.object(forKey: Self.baselineKey) as? Double {
            baseline = Date(timeIntervalSince1970: t)
        } else {
            baseline = Date()
            defaults.set(baseline.timeIntervalSince1970, forKey: Self.baselineKey)
        }
        if let raw = defaults.dictionary(forKey: Self.seenKey) as? [String: Double] {
            seen = raw.mapValues { Date(timeIntervalSince1970: $0) }
        }

        stream.onEvent = { [weak self] event in self?.handle(event) }
        stream.onConnected = { [weak self] in self?.streamConnected() }
        stream.onLiveChange = { [weak self] isLive in self?.streamLiveChanged(isLive) }

        NotificationCenter.default.publisher(for: UIApplication.didEnterBackgroundNotification)
            .sink { [weak self] _ in Task { @MainActor in self?.appDidEnterBackground() } }
            .store(in: &cancellables)
        NotificationCenter.default.publisher(for: UIApplication.didBecomeActiveNotification)
            .sink { [weak self] _ in Task { @MainActor in self?.appDidBecomeActive() } }
            .store(in: &cancellables)
    }

    // MARK: - Screen / app lifecycle

    /// A chat screen (conversation) became visible: make sure the socket is up.
    func screenAppeared() {
        activeScreens += 1
        disconnectTask?.cancel()
        disconnectTask = nil
        connectIfWanted()
        if threadsSupported != false { Task { await loadThreads() } }
    }

    func screenDisappeared() {
        activeScreens = max(0, activeScreens - 1)
        guard activeScreens == 0 else { return }
        // Swapping threads disappears one screen and appears another back to
        // back: wait a beat so that doesn't churn the socket.
        disconnectTask?.cancel()
        disconnectTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 700_000_000)
            guard !Task.isCancelled, let self, self.activeScreens == 0 else { return }
            self.disconnectTask = nil
            self.disconnect()
        }
    }

    private func appDidEnterBackground() {
        appActive = false
        // Closing the socket is what tells the server we're offline (→ APNs).
        disconnect()
    }

    private func appDidBecomeActive() {
        appActive = true
        guard activeScreens > 0 else { return }
        connectIfWanted()
        Task { await loadThreads() }
        // The socket replays nothing: pull whatever landed while away.
        for vm in viewModels.values where vm.isVisible { vm.foregrounded() }
    }

    private func connectIfWanted() {
        guard wantStream else { return }
        stream.connect()
        streamLiveChanged(stream.isLive)
    }

    private func disconnect() {
        reconnectIndicatorTask?.cancel()
        reconnectIndicatorTask = nil
        if connection.showReconnecting { connection.showReconnecting = false }
        stream.disconnect()
    }

    private func streamConnected() {
        Task { await loadThreads() }
        for vm in viewModels.values { vm.streamConnected() }
    }

    /// Show "Reconnecting…" only when the socket stays down for a few seconds,
    /// so brief blips and the initial connect never flash the indicator.
    private func streamLiveChanged(_ isLive: Bool) {
        reconnectIndicatorTask?.cancel()
        reconnectIndicatorTask = nil
        if isLive {
            if connection.showReconnecting { connection.showReconnecting = false }
            return
        }
        guard wantStream else { return }
        reconnectIndicatorTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 3_000_000_000)
            guard !Task.isCancelled, let self, self.wantStream else { return }
            self.connection.showReconnecting = true
        }
    }

    // MARK: - View models

    /// The (cached) view model of a thread. Kept alive while the user hops
    /// between threads so an in-flight run keeps streaming into its bubble.
    func viewModel(for id: String) -> ChatViewModel {
        if let vm = viewModels[id] { return vm }
        if viewModels.count >= Self.maxCachedModels {
            for (key, vm) in viewModels where key != chatMainThreadId && !vm.isVisible && !vm.isBusy {
                viewModels[key] = nil
                if viewModels.count < Self.maxCachedModels { break }
            }
        }
        let vm = ChatViewModel(threadId: id)
        viewModels[id] = vm
        return vm
    }

    func threadOpened(_ id: String) {
        openThreadId = id
        markSeen(id)
    }

    func threadClosed(_ id: String) {
        guard openThreadId == id else { return }
        openThreadId = nil
        markSeen(id)
    }

    // MARK: - Event routing

    private func handle(_ event: [String: Any]) {
        guard let type = event["type"] as? String else { return }
        switch type {
        case "status_update", "pong", "monitor_connected":
            return
        case "chat_thread_updated":
            if let t = ChatThread.from(event["thread"]) { upsert(t) }
        case "chat_thread_deleted":
            if let id = event["thread_id"] as? String { removeLocal(id) }
        case "agent_waiting", "agent_started":
            for vm in viewModels.values { vm.handle(event) }
        default:
            let raw = event["thread_id"] as? String
            let tid = (raw?.isEmpty == false ? raw : nil) ?? chatMainThreadId
            if let vm = viewModels[tid] {
                vm.handle(event)
            } else if type.hasPrefix("chat_") {
                viewModel(for: tid).handle(event)
            }
            noteActivity(type: type, threadId: tid, event: event)
        }
    }

    /// New message in a thread: move it to the top and refresh its preview.
    private func noteActivity(type: String, threadId: String, event: [String: Any]) {
        switch type {
        case "chat_reply":
            bump(threadId, role: "assistant", content: (event["content"] as? String) ?? "")
        case "chat_image":
            let caption = (event["caption"] as? String) ?? ""
            bump(threadId, role: "assistant",
                 content: caption.isEmpty ? String(localized: "Image") : caption)
        default:
            break
        }
    }

    /// Reflect a message on the thread row. Unknown thread (created on another
    /// device) → refetch the list.
    func bump(_ id: String, role: String, content: String) {
        guard threadsSupported == true else { return }
        guard let i = threads.firstIndex(where: { $0.id == id }) else {
            if !archived.contains(where: { $0.id == id }) { Task { await loadThreads() } }
            return
        }
        var t = threads[i]
        let now = Date()
        t.last = ChatThreadPreview(role: role, content: content, date: now)
        t.updatedAt = now
        threads[i] = t
        threads = Self.sorted(threads)
        if id == openThreadId { markSeen(id) }
    }

    /// Called by a view model when the user sends, so the row moves up at once.
    func noteOutgoing(threadId: String, text: String) {
        bump(threadId, role: "user", content: text)
    }

    // MARK: - Unread

    func isUnread(_ t: ChatThread) -> Bool {
        guard t.id != openThreadId, let last = t.last, last.role == "assistant" else { return false }
        let date = last.date ?? t.updatedAt ?? .distantPast
        return date > (seen[t.id] ?? baseline)
    }

    var hasUnread: Bool { threads.contains(where: isUnread) }

    func markSeen(_ id: String) {
        let preview = (threads + archived).first(where: { $0.id == id })?.last?.date
        seen[id] = max(Date(), preview ?? .distantPast)
        let raw = seen.mapValues { $0.timeIntervalSince1970 }
        UserDefaults.standard.set(raw, forKey: Self.seenKey)
    }

    // MARK: - Local list maintenance

    private static func sorted(_ list: [ChatThread]) -> [ChatThread] {
        list.sorted { a, b in
            if a.isMain != b.isMain { return a.isMain }
            if a.pinned != b.pinned { return a.pinned }
            return a.activityDate > b.activityDate
        }
    }

    private func upsert(_ incoming: ChatThread) {
        var t = incoming
        let existing = (threads + archived).first(where: { $0.id == t.id })
        if t.last == nil { t.last = existing?.last }
        if t.isMain { t.archived = false }
        threads.removeAll { $0.id == t.id }
        archived.removeAll { $0.id == t.id }
        if t.archived {
            archived.append(t)
            archived.sort { $0.activityDate > $1.activityDate }
        } else {
            threads = Self.sorted(threads + [t])
        }
        if threadsSupported == nil { threadsSupported = true }
    }

    private func removeLocal(_ id: String) {
        guard id != chatMainThreadId else { return }
        threads.removeAll { $0.id == id }
        archived.removeAll { $0.id == id }
        viewModels[id] = nil
        seen[id] = nil
        removedThread.send(id)
    }

    /// Title for the nav bar / list rows.
    func title(for id: String) -> String {
        if id == chatMainThreadId { return String(localized: "Hime") }
        let t = (threads + archived).first(where: { $0.id == id })?.title ?? ""
        return t.isEmpty ? String(localized: "New chat") : t
    }

    // MARK: - REST

    private func endpoint(_ path: String) -> URL? { URL(string: apiBase + path) }

    /// Fetch the active thread list. A 404 means an old server: hide thread UI.
    func loadThreads() async {
        guard !isLoadingThreads, let url = endpoint("/api/agent/chat/threads?include_archived=false") else { return }
        isLoadingThreads = true
        defer { isLoadingThreads = false }
        guard let list = await fetchThreadList(url) else { return }
        var active = list.filter { !$0.archived }
        if !active.contains(where: { $0.isMain }) { active.append(.main) }
        threads = Self.sorted(active)
    }

    /// Fetch archived threads (when the list's "Archived" section is opened).
    func loadArchived() async {
        guard let url = endpoint("/api/agent/chat/threads?include_archived=true"),
              let list = await fetchThreadList(url) else { return }
        archived = list.filter { $0.archived && !$0.isMain }
            .sorted { $0.activityDate > $1.activityDate }
    }

    private func fetchThreadList(_ url: URL) async -> [ChatThread]? {
        do {
            let (data, resp) = try await URLSession.shared.data(for: APIClient.request(url))
            if let http = resp as? HTTPURLResponse {
                if http.statusCode == 404 {
                    if threadsSupported != false { threadsSupported = false }
                    return nil
                }
                guard (200..<300).contains(http.statusCode) else { return nil }
            }
            guard let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let raw = obj["threads"] as? [Any] else { return nil }
            if threadsSupported != true { threadsSupported = true }
            return raw.compactMap { ChatThread.from($0) }
        } catch {
            return nil
        }
    }

    /// Send a JSON request and return the decoded body on a 2xx.
    private func send(_ method: String, _ path: String, body: [String: Any]?) async -> [String: Any]? {
        guard let url = endpoint(path) else { return nil }
        var req = APIClient.request(url, method: method)
        if let body {
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = try? JSONSerialization.data(withJSONObject: body)
        }
        do {
            let (data, resp) = try await URLSession.shared.data(for: req)
            guard let http = resp as? HTTPURLResponse, (200..<300).contains(http.statusCode) else { return nil }
            return (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] ?? [:]
        } catch {
            return nil
        }
    }

    /// Create a thread (optionally titled) and return it.
    func createThread(title: String? = nil) async -> ChatThread? {
        var body: [String: Any] = [:]
        if let title, !title.isEmpty { body["title"] = title }
        guard let obj = await send("POST", "/api/agent/chat/threads", body: body),
              let t = ChatThread.from(obj["thread"]) else {
            actionError = String(localized: "Couldn't create a new chat. Please try again.")
            return nil
        }
        upsert(t)
        return t
    }

    func rename(_ id: String, to title: String) async {
        let clean = title.trimmingCharacters(in: .whitespacesAndNewlines)
        guard id != chatMainThreadId, !clean.isEmpty else { return }
        applyLocal(id) { $0.title = clean }
        guard let obj = await send("PATCH", "/api/agent/chat/threads/\(id)", body: ["title": clean]) else {
            actionError = String(localized: "Couldn't rename this chat. Please try again.")
            await loadThreads()
            return
        }
        if let t = ChatThread.from(obj["thread"]) { upsert(t) }
    }

    func setArchived(_ id: String, _ value: Bool) async {
        guard id != chatMainThreadId else { return }
        if let t = (threads + archived).first(where: { $0.id == id }) {
            var moved = t
            moved.archived = value
            upsert(moved)
        }
        guard let obj = await send("PATCH", "/api/agent/chat/threads/\(id)", body: ["archived": value]) else {
            actionError = String(localized: "Couldn't update this chat. Please try again.")
            await loadThreads()
            if value == false || archived.contains(where: { $0.id == id }) { await loadArchived() }
            return
        }
        if let t = ChatThread.from(obj["thread"]) { upsert(t) }
    }

    func delete(_ id: String) async {
        guard id != chatMainThreadId else { return }
        guard await send("DELETE", "/api/agent/chat/threads/\(id)", body: nil) != nil else {
            actionError = String(localized: "Couldn't delete this chat. Please try again.")
            return
        }
        removeLocal(id)
    }

    private func applyLocal(_ id: String, _ change: (inout ChatThread) -> Void) {
        if let i = threads.firstIndex(where: { $0.id == id }) { change(&threads[i]) }
        if let i = archived.firstIndex(where: { $0.id == id }) { change(&archived[i]) }
    }
}
