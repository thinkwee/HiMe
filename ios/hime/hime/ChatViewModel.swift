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
        didSet { rows = Self.makeRows(messages, runs: runs) }
    }
    /// Collapsible step cards of agent runs; each renders right after its anchor message.
    @Published private(set) var runs: [RunCard] = [] {
        didSet { rows = Self.makeRows(messages, runs: runs) }
    }
    /// The single timeline (messages + run cards) with the per-row avatar flag
    /// precomputed — what the list renders.
    @Published private(set) var rows: [ChatRow] = []
    /// Live-bubble content. A separate observable so token-rate updates never
    /// invalidate this view model (and with it the message list).
    let live = LiveState()
    /// True while the agent is working on a turn (shows the live bubble + Stop).
    @Published private(set) var isBusy = false
    /// False once the server answered 404 to `/chat/stop` (old server).
    @Published private(set) var stopAvailable = true
    /// True when the event stream has been down for a few seconds.
    @Published private(set) var showReconnecting = false
    @Published var agentStarting = false
    /// Short, self-clearing error shown above the composer.
    @Published var errorBanner: String?
    /// True once a history fetch has succeeded (drives the initial jump-to-bottom).
    @Published private(set) var didLoadHistory = false

    private let stream = ChatStreamClient()
    /// Accumulates the model's streamed reasoning for the live thought line.
    private var thinkingBuffer = ""
    /// Model text output since the last tool call. Shown as streaming text, and
    /// demoted to the thought line when a tool call follows (it was narration).
    private var narrationBuffer = ""
    /// True once `chat_reply_delta` started streaming the real reply.
    private var replyStreaming = false
    private var currentRunId: String?
    private var runAnchorId: String?
    /// Bumped on every new run so delayed fallbacks can tell runs apart.
    private var runSerial = 0
    private var lastEventAt = Date()
    private var watchdogTask: Task<Void, Never>?
    private var wantStream = false
    private var reconnectIndicatorTask: Task<Void, Never>?
    private static let maxRuns = 40
    /// Ids of user messages the server didn't accept because the agent wasn't
    /// running; re-posted once it starts (`agent_started` / reconnect / watchdog).
    private var pendingResend: [String] = []
    private var resendTask: Task<Void, Never>?
    private var bannerTask: Task<Void, Never>?
    private var isReconciling = false
    private var needsAnotherReconcile = false

    private var apiBase: String { ServerConfig.load().apiBaseURL }

    // MARK: - Lifecycle

    func onAppear() {
        stream.onEvent = { [weak self] event in self?.handle(event) }
        stream.onConnected = { [weak self] in self?.streamConnected() }
        stream.onLiveChange = { [weak self] isLive in self?.streamLiveChanged(isLive) }
        startStream()
        if !didLoadHistory { Task { await reconcile() } }
    }

    func connectStream() { startStream() }

    func disconnectStream() {
        wantStream = false
        reconnectIndicatorTask?.cancel()
        reconnectIndicatorTask = nil
        if showReconnecting { showReconnecting = false }
        stream.disconnect()
    }

    private func startStream() {
        wantStream = true
        stream.connect()
        streamLiveChanged(stream.isLive)
    }

    /// Show "Reconnecting…" only when the socket stays down for a few seconds,
    /// so brief blips and the initial connect never flash the indicator.
    private func streamLiveChanged(_ isLive: Bool) {
        reconnectIndicatorTask?.cancel()
        reconnectIndicatorTask = nil
        if isLive {
            if showReconnecting { showReconnecting = false }
            return
        }
        guard wantStream else { return }
        reconnectIndicatorTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 3_000_000_000)
            guard !Task.isCancelled, let self, self.wantStream else { return }
            self.showReconnecting = true
        }
    }

    /// App returned to the foreground: make sure the socket is alive and pull
    /// anything (e.g. an APNs-delivered report) that landed while away.
    func foregrounded() {
        startStream()
        Task { await reconcile() }
    }

    private func streamConnected() {
        Task { await reconcile() }
        if !pendingResend.isEmpty { Task { await flushPending() } }
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
        beginRun(newTurn: true)
        Task { await deliver(messageId: msg.id) }
    }

    /// Tap-to-retry for a user message that failed to send.
    func retry(messageId: String) {
        guard let i = messages.firstIndex(where: { $0.id == messageId }),
              messages[i].delivery == .failed else { return }
        messages[i].delivery = .sending
        beginRun(newTurn: true)
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
            if !messages.contains(where: { $0.delivery == .sending }) { endRun() }
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
                    self.endRun()
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

        if addedAssistant, result.last?.role == .assistant { endRun() }
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

    private static func makeRows(_ messages: [ChatMessage], runs: [RunCard]) -> [ChatRow] {
        var byAnchor: [String: [RunCard]] = [:]
        for r in runs { byAnchor[r.anchorId, default: []].append(r) }
        var out: [ChatRow] = []
        out.reserveCapacity(messages.count + runs.count)
        var prevRole: ChatMessage.Role?
        for m in messages {
            out.append(ChatRow(item: .message(m), showAvatar: prevRole != m.role))
            prevRole = m.role
            if let cards = byAnchor[m.id] {
                for c in cards { out.append(ChatRow(item: .run(c), showAvatar: false)) }
            }
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
        endRun()
        runs.removeAll()
        messages.removeAll()
        pendingResend.removeAll()
        agentStarting = false
    }


    // MARK: - Run lifecycle (live bubble + step cards)

    /// Start (or continue) showing live work. `newTurn` is true when the user
    /// just sent something: later steps get a fresh card anchored below it.
    private func beginRun(newTurn: Bool) {
        if newTurn || !live.active {
            runAnchorId = messages.last?.id
            if currentRunId != nil { closeCards(stopped: false, onlyLiveFlag: true) }
            currentRunId = nil
            thinkingBuffer = ""
            narrationBuffer = ""
            replyStreaming = false
            runSerial += 1
            if newTurn {
                live.thought = ""
                live.streamText = ""
                live.tool = nil
            }
        }
        touch()
        if !live.active { live.active = true }
        if !isBusy { isBusy = true }
        startWatchdog()
    }

    /// Resume the live bubble when an event proves the agent is working again
    /// (e.g. after an acknowledgment reply, or a proactive run).
    private func ensureActive() {
        if !live.active { beginRun(newTurn: false) }
        touch()
    }

    private func touch() { lastEventAt = Date() }

    /// Finish the live bubble. Quiet by design: no message is appended. Steps
    /// still running are marked `stopped` (user Stop) or settled as done.
    private func endRun(stopped: Bool = false) {
        watchdogTask?.cancel()
        watchdogTask = nil
        closeCards(stopped: stopped, onlyLiveFlag: false)
        currentRunId = nil
        thinkingBuffer = ""
        narrationBuffer = ""
        replyStreaming = false
        live.reset()
        if isBusy { isBusy = false }
    }

    /// Mark live cards finished. `onlyLiveFlag` leaves step statuses alone (a
    /// new user turn started while earlier steps may still be reporting back).
    private func closeCards(stopped: Bool, onlyLiveFlag: Bool) {
        var copy = runs
        var changed = false
        for i in copy.indices where copy[i].isLive {
            copy[i].isLive = false
            changed = true
            if onlyLiveFlag { continue }
            for j in copy[i].steps.indices where copy[i].steps[j].status == .running {
                copy[i].steps[j].status = stopped ? .stopped : .ok
            }
        }
        if changed { runs = copy }
    }

    /// If events stop arriving mid-run (a dropped event, a crashed turn), end
    /// the live bubble instead of animating forever.
    private func startWatchdog() {
        guard watchdogTask == nil else { return }
        watchdogTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 15_000_000_000)
                guard !Task.isCancelled, let self else { return }
                if !self.live.active { break }
                if Date().timeIntervalSince(self.lastEventAt) > 150 {
                    self.endRun()
                    return
                }
            }
            self?.watchdogTask = nil
        }
    }

    private func addStep(tool: String, nested: Bool, arguments: [String: Any]?) {
        let step = RunStep(id: UUID().uuidString, tool: tool, nested: nested,
                           detail: Self.detail(tool: tool, arguments: arguments),
                           status: .running, preview: nil)
        if let id = currentRunId, let i = runs.firstIndex(where: { $0.id == id }) {
            runs[i].steps.append(step)
            runs[i].isLive = true
        } else if let anchor = runAnchorId {
            let card = RunCard(id: UUID().uuidString, anchorId: anchor, steps: [step], isLive: true)
            var next = runs
            next.append(card)
            if next.count > Self.maxRuns { next.removeFirst(next.count - Self.maxRuns) }
            runs = next
            currentRunId = card.id
        }
    }

    private func completeStep(tool: String, nested: Bool, success: Bool, preview: String?) {
        for i in runs.indices.reversed() {
            guard let j = runs[i].steps.lastIndex(where: {
                $0.status == .running && $0.tool == tool && $0.nested == nested
            }) else { continue }
            runs[i].steps[j].status = success ? .ok : .failed
            runs[i].steps[j].preview = preview
            return
        }
    }

    /// Tool of the innermost step still running (what the live bubble names).
    private func runningTool() -> String? {
        guard let id = currentRunId, let card = runs.first(where: { $0.id == id }) else { return nil }
        return card.steps.last(where: { $0.status == .running })?.tool
    }

    /// Model text that preceded a tool call was narration, not the reply:
    /// move it to the muted thought line and clear the streaming text.
    private func demoteNarration() {
        let n = narrationBuffer.trimmingCharacters(in: .whitespacesAndNewlines)
        if !n.isEmpty { live.thought = Self.tail(n) }
        narrationBuffer = ""
        thinkingBuffer = ""
        replyStreaming = false
        if !live.streamText.isEmpty { live.streamText = "" }
    }

    // MARK: - Stop

    /// Ask the server to cancel the current run. The UI settles when the server
    /// emits `chat_stopped`; a fallback ends it locally if that event is missed.
    func stop() {
        guard live.active else { return }
        let serial = runSerial
        Task {
            guard let url = URL(string: "\(apiBase)/api/agent/chat/stop") else { return }
            var req = APIClient.request(url, method: "POST")
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = Data("{}".utf8)
            do {
                let (data, resp) = try await URLSession.shared.data(for: req)
                if let http = resp as? HTTPURLResponse {
                    if http.statusCode == 404 || http.statusCode == 405 {
                        stopAvailable = false  // old server: hide Stop for the session
                        return
                    }
                    guard (200..<300).contains(http.statusCode) else {
                        showBanner(String(localized: "Couldn't stop. Please try again."))
                        return
                    }
                }
                let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
                if (obj?["stopped"] as? Bool) == false {
                    // Nothing was running server-side: the indicator was stale.
                    endRun()
                    return
                }
                try? await Task.sleep(nanoseconds: 4_000_000_000)
                if live.active && runSerial == serial { endRun(stopped: true) }
            } catch {
                showBanner(String(localized: "Couldn't stop. Please try again."))
            }
        }
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
            ensureActive()
            let delta = (event["content"] as? String) ?? ""
            guard !delta.isEmpty else { break }
            thinkingBuffer += delta
            let t = Self.tail(thinkingBuffer)
            if live.thought != t { live.thought = t }
        case "chat_content":
            // The model's text output. Usually narration before a tool call
            // (demoted to the thought line when the call arrives); the real
            // reply streams via `chat_reply_delta` / arrives as `chat_reply`.
            ensureActive()
            let delta = (event["content"] as? String) ?? ""
            guard !delta.isEmpty else { break }
            narrationBuffer += delta
            if !replyStreaming {
                let t = narrationBuffer.trimmingCharacters(in: .whitespacesAndNewlines)
                if live.streamText != t { live.streamText = t }
            }
        case "chat_reply_delta":
            ensureActive()
            let text = (event["text"] as? String) ?? ""
            guard !text.isEmpty else { break }
            replyStreaming = true
            if live.streamText != text { live.streamText = text }
        case "chat_tool_call":
            handleToolCall(event, forceNested: false)
        case "chat_tool_result":
            handleToolResult(event, forceNested: false)
        case "chat_reply":
            finalizeReply(text: (event["content"] as? String) ?? "",
                          hash: event["message_hash"] as? String,
                          reportId: event["report_id"] as? Int)
        case "chat_image":
            endRun()
            let path = event["url"] as? String
            if let path, messages.contains(where: { $0.imagePath == path }) { break }
            messages.append(ChatMessage(role: .assistant,
                                        text: (event["caption"] as? String) ?? "",
                                        imagePath: path,
                                        messageHash: event["message_hash"] as? String))
        case "chat_stopped":
            endRun(stopped: true)
        case "chat_cleared":
            clearLocal()
        default:
            // Sub-analysis runs tag their tool events by source
            // (`analysis_tool_call`, `plan_...`, `quick_...`). Fold them into the
            // current run's steps; ignore them when no chat run is live so
            // background cron analyses don't pop a bubble into the chat.
            if live.active {
                if type.hasSuffix("_tool_call") {
                    handleToolCall(event, forceNested: true)
                } else if type.hasSuffix("_tool_result") {
                    handleToolResult(event, forceNested: true)
                }
            }
        }
    }

    private func handleToolCall(_ event: [String: Any], forceNested: Bool) {
        let tool = (event["tool"] as? String) ?? ""
        if tool == "finish_chat" {
            // Turn is over; nothing to show.
            if live.active { endRun() }
            return
        }
        ensureActive()
        demoteNarration()
        guard !tool.isEmpty else {
            live.tool = nil
            return
        }
        live.tool = tool
        // The reply itself is the bubble, not a step.
        if tool == "reply_user" { return }
        // Sub-agent tools (sql / code / ...) carry a `source` tag.
        let nested = forceNested || event["source"] != nil
        addStep(tool: tool, nested: nested, arguments: event["arguments"] as? [String: Any])
    }

    private func handleToolResult(_ event: [String: Any], forceNested: Bool) {
        let tool = (event["tool"] as? String) ?? ""
        // `reply_user`'s result lands after its `chat_reply`: nothing to do.
        guard !tool.isEmpty, tool != "reply_user", tool != "finish_chat" else { return }
        touch()
        let nested = forceNested || event["source"] != nil
        let ok = (event["success"] as? Bool) ?? false
        completeStep(tool: tool, nested: nested, success: ok,
                     preview: Self.preview(of: event["result"]))
        guard live.active else { return }
        // Text produced after a result (sub-agent findings) is not the reply.
        demoteNarration()
        let next = runningTool()
        if live.tool != next { live.tool = next }
    }

    private func finalizeReply(text: String, hash: String?, reportId: Int? = nil) {
        endRun()
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

    // MARK: - Text helpers

    /// The last ~200 characters, for the muted thought line ("latest thinking").
    private static func tail(_ s: String, limit: Int = 200) -> String {
        let t = s.trimmingCharacters(in: .whitespacesAndNewlines)
            .replacingOccurrences(of: "\n", with: " ")
        guard t.count > limit else { return t }
        return "…" + String(t.suffix(limit))
    }

    private static func clip(_ s: String, _ limit: Int) -> String {
        let t = s.trimmingCharacters(in: .whitespacesAndNewlines)
        return t.count > limit ? String(t.prefix(limit)) + "…" : t
    }

    /// Short "object" of a step taken from its call arguments (goal, query, ...).
    private static func detail(tool: String, arguments: [String: Any]?) -> String? {
        guard let args = arguments else { return nil }
        let key: String
        switch tool {
        case "analyze", "manage": key = "goal"
        case "sql": key = "query"
        case "read_skill": key = "name"
        case "create_page": key = "title"
        case "update_md": key = "file"
        default: return nil
        }
        guard let v = args[key] as? String else { return nil }
        let c = clip(v.replacingOccurrences(of: "\n", with: " "), 60)
        return c.isEmpty ? nil : c
    }

    /// A short, human-readable preview of a tool result — never a JSON dump.
    private static func preview(of result: Any?) -> String? {
        if let s = result as? String {
            let c = clip(s, 280)
            return c.isEmpty ? nil : c
        }
        guard let dict = result as? [String: Any] else { return nil }
        for key in ["error", "findings", "result", "message", "summary", "output", "stdout"] {
            if let v = dict[key] as? String {
                let c = clip(v, 280)
                if !c.isEmpty { return c }
            }
        }
        if let data = try? JSONSerialization.data(withJSONObject: dict),
           let json = String(data: data, encoding: .utf8) {
            return clip(json, 160)
        }
        return nil
    }
}
