//
//  ChatViewModel.swift
//  hime
//
//  Drives the in-app conversation: posts messages to `/api/agent/chat`,
//  consumes the live agent event stream for replies, loads persisted
//  history, fetches fact-verification evidence, and clears the conversation.
//
//  The server transcript (`chat_history`) is the source of truth; the live
//  stream is only a low-latency hint. `reconcile()` merges the two after every
//  (re)connect, send and foreground so a dropped socket can never lose or
//  duplicate a message.
//

import Combine
import Foundation
import SwiftUI

@MainActor
final class ChatViewModel: ObservableObject {
    @Published var messages: [ChatMessage] = [] {
        didSet { rows = Self.makeRows(messages) }
    }
    /// `messages` with the per-row avatar flag precomputed (what the list renders).
    @Published private(set) var rows: [ChatRow] = []
    @Published private(set) var activity: AgentActivity = .idle
    @Published var agentStarting = false
    /// Short, self-clearing error shown above the composer.
    @Published var errorBanner: String?
    /// True once a history fetch has succeeded (drives the initial jump-to-bottom).
    @Published private(set) var didLoadHistory = false

    private let stream = ChatStreamClient()
    /// Accumulates the model's streamed reasoning/narration for the live
    /// preview shown in the status pill (it is NOT the user-facing reply —
    /// that arrives whole as `chat_reply`).
    private var thinkingBuffer = ""
    /// Ids of user messages the server didn't accept because the agent wasn't
    /// running; re-posted once it starts (`agent_started` / reconnect / watchdog).
    private var pendingResend: [String] = []
    private var resendTask: Task<Void, Never>?
    private var bannerTask: Task<Void, Never>?
    private var isReconciling = false
    private var needsAnotherReconcile = false

    /// Drives the show/hide of the status pill (kept coarse so per-token
    /// preview updates don't re-trigger the container's spring animation).
    var isBusy: Bool { activity != .idle }

    private var apiBase: String { ServerConfig.load().apiBaseURL }

    // MARK: - Lifecycle

    func onAppear() {
        stream.onEvent = { [weak self] event in self?.handle(event) }
        stream.onConnected = { [weak self] in self?.streamConnected() }
        stream.connect()
        if !didLoadHistory { Task { await reconcile() } }
    }

    func connectStream() { stream.connect() }
    func disconnectStream() { stream.disconnect() }

    /// App returned to the foreground: make sure the socket is alive and pull
    /// anything (e.g. an APNs-delivered report) that landed while away.
    func foregrounded() {
        stream.connect()
        Task { await reconcile() }
    }

    private func streamConnected() {
        Task { await reconcile() }
        if !pendingResend.isEmpty { Task { await flushPending() } }
    }

    private func setActivity(_ new: AgentActivity) {
        if activity != new { activity = new }
    }

    private func showBanner(_ text: String) {
        errorBanner = text
        bannerTask?.cancel()
        bannerTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 4_000_000_000)
            guard !Task.isCancelled, let self else { return }
            self.errorBanner = nil
        }
    }

    // MARK: - Sending

    func send(text rawText: String, image: Data?) {
        let text = rawText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty || image != nil else { return }
        let msg = ChatMessage(role: .user, text: text, localImage: image,
                              clientMsgId: UUID().uuidString, delivery: .sending)
        messages.append(msg)
        thinkingBuffer = ""
        setActivity(.thinking(""))
        Task { await deliver(messageId: msg.id) }
    }

    /// Tap-to-retry for a user message that failed to send.
    func retry(messageId: String) {
        guard let i = messages.firstIndex(where: { $0.id == messageId }),
              messages[i].delivery == .failed else { return }
        messages[i].delivery = .sending
        thinkingBuffer = ""
        setActivity(.thinking(""))
        Task { await deliver(messageId: messageId) }
    }

    private enum PostOutcome {
        case queued
        case starting
        case failed(String)
    }

    /// POST one message and reflect the outcome on its bubble.
    private func deliver(messageId: String) async {
        guard let msg = messages.first(where: { $0.id == messageId }) else { return }
        let outcome = await post(text: msg.text, image: msg.localImage,
                                 clientMsgId: msg.clientMsgId ?? UUID().uuidString)
        switch outcome {
        case .queued:
            setDelivery(messageId, .sent)
            pendingResend.removeAll { $0 == messageId }
            if pendingResend.isEmpty { agentStarting = false }
            // Pull the persisted row (binds serverId / clientMsgId) and any
            // reply that already landed.
            await reconcile()
        case .starting:
            // Agent is waking up; the message wasn't queued — resend once it's ready.
            if !pendingResend.contains(messageId) { pendingResend.append(messageId) }
            agentStarting = true
            armResendWatchdog()
        case .failed(let reason):
            // The request errored client-side but a reconcile already found the
            // message persisted on the server: it did go through.
            if messages.first(where: { $0.id == messageId })?.serverId != nil { return }
            setDelivery(messageId, .failed)
            pendingResend.removeAll { $0 == messageId }
            if pendingResend.isEmpty { agentStarting = false }
            if !messages.contains(where: { $0.delivery == .sending }) { setActivity(.idle) }
            showBanner(reason)
        }
    }

    private func setDelivery(_ id: String, _ state: ChatMessage.Delivery) {
        guard let i = messages.firstIndex(where: { $0.id == id }),
              messages[i].delivery != state else { return }
        messages[i].delivery = state
    }

    private func post(text: String, image: Data?, clientMsgId: String) async -> PostOutcome {
        guard let url = URL(string: "\(apiBase)/api/agent/chat") else {
            return .failed(String(localized: "Server address is invalid."))
        }
        var req = APIClient.request(url, method: "POST")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        var body: [String: Any] = ["text": text, "client_msg_id": clientMsgId]
        if let image {
            body["image_base64"] = image.base64EncodedString()
            body["image_mime"] = "image/jpeg"
        }
        req.httpBody = try? JSONSerialization.data(withJSONObject: body)
        do {
            let (data, resp) = try await URLSession.shared.data(for: req)
            guard let http = resp as? HTTPURLResponse else {
                return .failed(String(localized: "Couldn't send. Tap the message to retry."))
            }
            if http.statusCode == 429 {
                return .failed(String(localized: "You're sending too fast. Wait a moment, then tap to retry."))
            }
            guard (200..<300).contains(http.statusCode) else {
                return .failed(String(localized: "Couldn't send (error \(http.statusCode)). Tap the message to retry."))
            }
            if let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               (obj["queued"] as? Bool) == false || (obj["status"] as? String) == "starting" {
                return .starting
            }
            return .queued
        } catch {
            return .failed(String(localized: "Couldn't reach Hime. Tap the message to retry."))
        }
    }

    /// Re-post everything that was held back while the agent was starting.
    private func flushPending() async {
        let ids = pendingResend
        pendingResend = []
        for id in ids { await deliver(messageId: id) }
    }

    /// Backstop for a missed `agent_started` (e.g. it fired while the socket
    /// was reconnecting): periodically retry the held messages, then give up
    /// and mark them failed so the user can retry by hand.
    private func armResendWatchdog() {
        guard resendTask == nil else { return }
        resendTask = Task { [weak self] in
            var tries = 0
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 15_000_000_000)
                guard !Task.isCancelled, let self else { return }
                if self.pendingResend.isEmpty { break }
                tries += 1
                if tries > 6 {
                    let ids = self.pendingResend
                    self.pendingResend = []
                    for id in ids { self.setDelivery(id, .failed) }
                    self.agentStarting = false
                    self.setActivity(.idle)
                    self.showBanner(String(localized: "Hime didn't wake up. Tap the message to retry."))
                    break
                }
                await self.flushPending()
            }
            self?.resendTask = nil
        }
    }

    // MARK: - History / evidence / clear

    /// Fetch the recent server transcript. Returns nil on any failure.
    private func fetchHistory() async -> [ChatHistoryRow]? {
        guard let url = URL(string: "\(apiBase)/api/agent/chat-history?limit=100") else { return nil }
        do {
            let (data, resp) = try await URLSession.shared.data(for: APIClient.request(url))
            if let http = resp as? HTTPURLResponse, !(200..<300).contains(http.statusCode) { return nil }
            return try JSONDecoder().decode(ChatHistoryResponse.self, from: data).messages
        } catch { return nil }
    }

    /// Fetch recent history and merge it into the on-screen list. Used for the
    /// initial load and after every (re)connect / send / foreground: the live
    /// agent stream is push-only and replays nothing, so anything delivered
    /// while the socket was down (or an APNs-only proactive report) is picked
    /// up here. Safe to call at any time and from several places at once.
    func reconcile() async {
        if isReconciling { needsAnotherReconcile = true; return }
        isReconciling = true
        defer { isReconciling = false }
        repeat {
            needsAnotherReconcile = false
            guard let history = await fetchHistory() else { return }  // offline — fine
            merge(history)
            didLoadHistory = true
        } while needsAnotherReconcile
    }

    /// Merge server rows into `messages` without discarding anything live and
    /// without ever double-rendering. Server rows are authoritative for what
    /// they contain; each is matched to an existing local bubble by (in order)
    /// server id, client message id, evidence hash, then role+text — the last
    /// two only against bubbles not yet bound to a server row, so two
    /// identical-text turns are never conflated. Unmatched local bubbles
    /// (in-flight / failed sends, replies not yet persisted) are kept.
    private func merge(_ history: [ChatHistoryRow]) {
        guard !history.isEmpty else { return }
        let locals = messages
        var byServerId: [Int: Int] = [:]
        var byClientId: [String: Int] = [:]
        var unsyncedByHash: [String: [Int]] = [:]
        var unsyncedByText: [String: [Int]] = [:]
        for (i, m) in locals.enumerated() {
            if let sid = m.serverId { byServerId[sid] = i }
            if let cid = m.clientMsgId { byClientId[cid] = i }
            if m.serverId == nil {
                if let h = m.messageHash { unsyncedByHash[h, default: []].append(i) }
                unsyncedByText[Self.textKey(role: m.role, text: m.text, hasImage: m.imagePath != nil),
                               default: []].append(i)
            }
        }

        var used = Set<Int>()
        var merged: [ChatMessage] = []
        var addedAssistant = false
        for row in history {
            let role: ChatMessage.Role = row.role == "assistant" ? .assistant : .user
            var match: Int?
            if let sid = row.id, let i = byServerId[sid], !used.contains(i) {
                match = i
            } else if let cid = row.client_msg_id, let i = byClientId[cid], !used.contains(i) {
                match = i
            } else if let h = row.message_hash,
                      let i = unsyncedByHash[h]?.first(where: { !used.contains($0) && locals[$0].role == role }) {
                match = i
            } else {
                let key = Self.textKey(role: role, text: row.content, hasImage: row.image_id != nil)
                match = unsyncedByText[key]?.first(where: { !used.contains($0) })
            }
            if let i = match {
                used.insert(i)
                var m = locals[i]
                m.serverId = row.id ?? m.serverId
                if m.messageHash == nil { m.messageHash = row.message_hash }
                if m.reportId == nil { m.reportId = row.report_id }
                if m.imagePath == nil, let img = row.image_id { m.imagePath = Self.imagePath(img) }
                if m.clientMsgId == nil { m.clientMsgId = row.client_msg_id }
                if m.role == .user { m.delivery = .sent }
                merged.append(m)
            } else {
                if role == .assistant { addedAssistant = true }
                merged.append(Self.message(from: row))
            }
        }

        // Keep unmatched local bubbles: unsynced ones (not persisted yet) and
        // synced ones outside this snapshot's id window (older, or newer than a
        // stale response). Synced bubbles inside the window that the server no
        // longer has were deleted elsewhere and are dropped.
        let ids = history.compactMap { $0.id }
        let lo = ids.min(), hi = ids.max()
        var prefix: [ChatMessage] = []
        var tail: [ChatMessage] = []
        for (i, m) in locals.enumerated() where !used.contains(i) {
            if let sid = m.serverId {
                if let lo, sid < lo { prefix.append(m) }
                else if let hi, sid > hi { tail.append(m) }
            } else {
                tail.append(m)
            }
        }
        let result = prefix + merged + tail
        if result != messages { messages = result }

        if addedAssistant, result.last?.role == .assistant {
            thinkingBuffer = ""
            setActivity(.idle)
        }
    }

    private static func textKey(role: ChatMessage.Role, text: String, hasImage: Bool) -> String {
        "\(role.rawValue)|\(hasImage ? 1 : 0)|\(text.trimmingCharacters(in: .whitespacesAndNewlines))"
    }

    private static func imagePath(_ imageId: String) -> String { "/api/agent/chat-image/\(imageId)" }

    private static func message(from row: ChatHistoryRow) -> ChatMessage {
        ChatMessage(role: row.role == "assistant" ? .assistant : .user,
                    text: row.content,
                    imagePath: row.image_id.map { Self.imagePath($0) },
                    messageHash: row.message_hash,
                    reportId: row.report_id,
                    serverId: row.id,
                    clientMsgId: row.client_msg_id,
                    timestamp: row.date ?? Date())
    }

    private static func makeRows(_ messages: [ChatMessage]) -> [ChatRow] {
        var out: [ChatRow] = []
        out.reserveCapacity(messages.count)
        var prevRole: ChatMessage.Role?
        for m in messages {
            out.append(ChatRow(message: m, showAvatar: prevRole != m.role))
            prevRole = m.role
        }
        return out
    }

    func evidence(for message: ChatMessage) async -> String? {
        guard let hash = message.messageHash,
              let url = URL(string: "\(apiBase)/api/agent/evidence/\(hash)") else { return nil }
        do {
            let (data, _) = try await URLSession.shared.data(for: APIClient.request(url))
            let resp = try JSONDecoder().decode(EvidenceResponse.self, from: data)
            return resp.found ? resp.formatted : nil
        } catch { return nil }
    }

    /// Ask the server to clear the transcript; the UI is only wiped once the
    /// server accepted the request (a failure leaves the conversation intact).
    func clearConversation() {
        Task {
            switch await post(text: "/clear", image: nil, clientMsgId: UUID().uuidString) {
            case .queued:
                clearLocal()
            case .starting:
                showBanner(String(localized: "Hime is waking up. Try clearing again in a moment."))
            case .failed:
                showBanner(String(localized: "Couldn't clear the conversation. Please try again."))
            }
        }
    }

    private func clearLocal() {
        messages.removeAll()
        pendingResend.removeAll()
        thinkingBuffer = ""
        agentStarting = false
        setActivity(.idle)
    }

    // MARK: - Event handling

    private func handle(_ event: [String: Any]) {
        guard let type = event["type"] as? String else { return }
        switch type {
        case "status_update", "pong", "monitor_connected":
            break
        case "agent_waiting":
            // Agent not running yet; the server keeps the socket open and
            // streams `agent_started` when it is. Hold the "waking up" state.
            if !pendingResend.isEmpty || isBusy { agentStarting = true }
        case "agent_started":
            agentStarting = false
            if !pendingResend.isEmpty { Task { await flushPending() } }
        case "chat_thinking":
            appendThinking((event["content"] as? String) ?? "")
        case "chat_tool_call":
            thinkingBuffer = ""
            if let tool = event["tool"] as? String, !tool.isEmpty {
                setActivity(.tool(tool))
            } else {
                setActivity(.thinking(""))
            }
        case "chat_content":
            // In the chat loop this is the model's intermediate reasoning /
            // narration, NOT the user-facing reply (that arrives whole as
            // `chat_reply`). Surface it as a live preview in the status pill
            // rather than letting it fill the message bubble.
            appendThinking((event["content"] as? String) ?? "")
        case "chat_reply":
            finalizeReply(text: (event["content"] as? String) ?? "",
                          hash: event["message_hash"] as? String,
                          reportId: event["report_id"] as? Int)
        case "chat_image":
            setActivity(.idle)
            thinkingBuffer = ""
            let path = event["url"] as? String
            if let path, messages.contains(where: { $0.imagePath == path }) { break }
            messages.append(ChatMessage(role: .assistant,
                                        text: (event["caption"] as? String) ?? "",
                                        imagePath: path,
                                        messageHash: event["message_hash"] as? String))
        case "chat_cleared":
            clearLocal()
        default:
            break
        }
    }

    private func appendThinking(_ delta: String) {
        guard !delta.isEmpty else { return }
        thinkingBuffer += delta
        setActivity(.thinking(Self.firstSentence(thinkingBuffer)))
    }

    private func finalizeReply(text: String, hash: String?, reportId: Int? = nil) {
        setActivity(.idle)
        thinkingBuffer = ""
        // A replayed / duplicated event (or one already merged from history)
        // must not render twice: same evidence hash + text, or an identical
        // assistant message arriving within seconds of the previous one.
        if let last = messages.last(where: { $0.role == .assistant }) {
            if let hash, last.messageHash == hash, last.text == text { return }
            if hash == nil, last.text == text, Date().timeIntervalSince(last.timestamp) < 5,
               messages.last?.role == .assistant { return }
        }
        if let hash, messages.contains(where: {
            $0.role == .assistant && $0.messageHash == hash && $0.text == text
        }) { return }
        messages.append(ChatMessage(role: .assistant, text: text,
                                    messageHash: hash, reportId: reportId))
    }

    /// The first sentence of the streamed reasoning, followed by an ellipsis —
    /// a compact, stable preview for the status pill (it stops growing once the
    /// first sentence terminator arrives).
    private static func firstSentence(_ s: String) -> String {
        let trimmed = s.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return "" }
        // CJK terminators always end a sentence. A newline always does too.
        let hardStops: Set<Character> = ["。", "！", "？", "…", "\n"]
        // ASCII .!? only end a sentence when followed by whitespace or end —
        // so decimals ("36.5"), versions ("v2.0") and "etc." mid-clause don't
        // cause a premature cut.
        let asciiStops: Set<Character> = [".", "!", "?"]
        let chars = Array(trimmed)
        for (i, c) in chars.enumerated() {
            let isHard = hardStops.contains(c)
            let isAscii = asciiStops.contains(c) && {
                let next = i + 1 < chars.count ? chars[i + 1] : " "
                return next == " " || next == "\n" || next == "\t" || i + 1 == chars.count
            }()
            if isHard || isAscii {
                let sentence = String(chars[..<i]).trimmingCharacters(in: .whitespaces)
                return sentence.isEmpty ? "…" : sentence + "…"
            }
        }
        return trimmed + "…"
    }
}
