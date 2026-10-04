//
//  ChatModels.swift
//  hime
//
//  In-app chat with the agent (replaces the external IM gateways).
//

import Combine
import Foundation
import SwiftUI

/// One message in the in-app conversation.
struct ChatMessage: Identifiable, Equatable {
    enum Role: String { case user, assistant }

    /// Delivery state of a user message (assistant rows are always `.sent`).
    enum Delivery: Equatable { case sent, sending, failed }

    let id: String
    var role: Role
    var text: String
    /// Server path for an agent-sent image, e.g. `/api/agent/chat-image/<id>`.
    var imagePath: String?
    /// Local image the user attached (shown optimistically before upload echo).
    var localImage: Data?
    /// Fact-verification hash; when present the "Show Evidence" affordance appears.
    var messageHash: String?
    /// When this bubble is a proactive report push, the report's DB id — drives
    /// the "view full report" deep-link beneath the bubble. nil for chat replies.
    var reportId: Int?
    /// True while assistant tokens are still streaming in.
    var isStreaming: Bool
    /// Row id in the server's `chat_history` once this message is known to be
    /// persisted there — the primary key for de-duplicating history merges.
    var serverId: Int?
    /// Id of the `TurnState` this assistant message was delivered in (nil for
    /// user messages and proactive pushes). Survives history merges, so a reply
    /// keeps rendering inside the same turn block for the whole session.
    var turnId: String?
    /// Client-generated id sent with the POST; the server echoes it into history
    /// so an optimistic local user bubble can be matched to its persisted row.
    var clientMsgId: String?
    var delivery: Delivery
    let timestamp: Date

    init(id: String = UUID().uuidString,
         role: Role,
         text: String = "",
         imagePath: String? = nil,
         localImage: Data? = nil,
         messageHash: String? = nil,
         reportId: Int? = nil,
         isStreaming: Bool = false,
         serverId: Int? = nil,
         turnId: String? = nil,
         clientMsgId: String? = nil,
         delivery: Delivery = .sent,
         timestamp: Date = Date()) {
        self.id = id
        self.role = role
        self.text = text
        self.imagePath = imagePath
        self.localImage = localImage
        self.messageHash = messageHash
        self.reportId = reportId
        self.isStreaming = isStreaming
        self.serverId = serverId
        self.turnId = turnId
        self.clientMsgId = clientMsgId
        self.delivery = delivery
        self.timestamp = timestamp
    }

    /// Cheap equality: the synthesized one would byte-compare `localImage` on
    /// every diff of a large message list. Image bytes never change for a given
    /// id, so their length is a sufficient change signal.
    static func == (lhs: ChatMessage, rhs: ChatMessage) -> Bool {
        lhs.id == rhs.id
            && lhs.role == rhs.role
            && lhs.text == rhs.text
            && lhs.imagePath == rhs.imagePath
            && lhs.localImage?.count == rhs.localImage?.count
            && lhs.messageHash == rhs.messageHash
            && lhs.reportId == rhs.reportId
            && lhs.isStreaming == rhs.isStreaming
            && lhs.serverId == rhs.serverId
            && lhs.turnId == rhs.turnId
            && lhs.delivery == rhs.delivery
    }
}

/// One entry of a turn's activity timeline: a tool call, a stretch of model
/// narration (reasoning / draft text), or a notice (e.g. a held-back draft).
struct RunStep: Identifiable, Equatable {
    enum Kind: Equatable { case tool, narration, notice }
    enum Status: Equatable { case running, ok, failed, stopped }

    let id: String
    let kind: Kind
    /// Backend tool name (`analyze`, `sql`, `code`, ...) for `.tool` steps.
    /// Mapped to a plain verb by `ChatStepStyle`.
    let tool: String
    /// Orchestrator tool (`analyze` / `manage`) a sub-agent step ran under;
    /// nil for top-level steps.
    let parent: String?
    /// Narration text, or the headline of a notice.
    var text: String
    /// Short object of a tool step (the goal / query), or the reason of a notice.
    var detail: String?
    var status: Status
    /// Short, truncated result preview (tool) or the held-back draft (notice).
    var preview: String?
    /// Server correlation id of a tool call (pairs call with result).
    var callId: String?

    var nested: Bool { parent != nil }

    init(kind: Kind, tool: String = "", parent: String? = nil, text: String = "",
         detail: String? = nil, status: Status = .ok, preview: String? = nil,
         callId: String? = nil) {
        self.id = UUID().uuidString
        self.kind = kind
        self.tool = tool
        self.parent = parent
        self.text = text
        self.detail = detail
        self.status = status
        self.preview = preview
        self.callId = callId
    }
}

/// One assistant turn: the whole of Hime's response to one user message, as a
/// single block with stable identity from the first event of the run until
/// long after it finished. Layout: avatar, a collapsible activity section
/// (narration + tool steps), then the reply bubble(s).
///
/// A separate observable per turn so token-rate updates re-render only the
/// live turn's view; finished turns never change and are never invalidated.
@MainActor
final class TurnState: ObservableObject {
    enum Phase: Equatable { case live, done, stopped }

    let id = UUID().uuidString
    /// Message this turn renders directly after (the user message that started it).
    let anchorId: String?
    let startedAt = Date()
    /// Server run id, bound on the first event that carries one.
    var runId: String?
    /// True once the server said the run is over (`chat_run_done` / finish_chat):
    /// late events must not reopen the turn.
    var closedByServer = false
    /// Latest `chat_verification` detail, used to label a held-back draft.
    var lastVerificationDetail: String?

    @Published private(set) var phase: Phase = .live
    @Published private(set) var steps: [RunStep] = []
    @Published private(set) var endedAt: Date?
    /// Reply text streaming in (full text so far). Rendered in the reply body.
    @Published var streamText = ""
    /// Delivered reply messages of this turn (mirrored from the message list).
    @Published var replies: [ChatMessage] = []
    /// reply_user is being called (its arguments are streaming / it is sending).
    @Published var writingReply = false
    /// A draft was held back by the fact check and is being redone.
    @Published var rechecking = false
    @Published var errorText: String?

    private var openNarrationId: String?
    private var narrationThinking = false
    private var noticeAt: Date?
    private static let narrationCap = 4000

    init(anchorId: String?) {
        self.anchorId = anchorId
    }

    // MARK: Derived

    /// Innermost tool step still running.
    var runningTool: String? {
        steps.last(where: { $0.kind == .tool && $0.status == .running })?.tool
    }

    var stepCount: Int { steps.filter { $0.kind == .tool }.count }

    /// True once there is anything to show in the body or activity section.
    var hasContent: Bool { !steps.isEmpty || !streamText.isEmpty || !replies.isEmpty }

    var avatarActivity: AvatarActivity {
        guard phase == .live else { return .idle }
        if writingReply || !streamText.isEmpty { return .replying }
        if runningTool != nil { return .working }
        return .thinking
    }

    /// One-line status for the activity header.
    var statusText: String {
        let n = stepCount
        var suffix = ""
        if n == 1 {
            suffix = " · " + String(localized: "1 step")
        } else if n > 1 {
            suffix = " · " + String(localized: "\(n) steps")
        }
        switch phase {
        case .live:
            let base: String
            if writingReply || !streamText.isEmpty {
                base = String(localized: "Writing the reply…")
            } else if let t = runningTool {
                base = Self.liveVerb(t)
            } else if rechecking {
                base = String(localized: "Re-checking facts…")
            } else {
                base = String(localized: "Thinking…")
            }
            return base + suffix
        case .done:
            return String(localized: "Thought for \(durationText)") + suffix
        case .stopped:
            return String(localized: "Stopped") + suffix
        }
    }

    private var durationText: String {
        let s = max(1, Int((endedAt ?? Date()).timeIntervalSince(startedAt).rounded()))
        return s < 60 ? "\(s)s" : "\(s / 60)m \(s % 60)s"
    }

    private static func liveVerb(_ tool: String) -> String {
        switch tool {
        case "analyze", "sql", "code", "read_skill": return String(localized: "Analyzing data")
        case "manage", "update_md", "create_page": return String(localized: "Making an update")
        default: return String(localized: "Working")
        }
    }

    // MARK: Activity

    /// Append model narration (reasoning or free text) to the current segment.
    /// It lives in the activity section from the start and is never promoted
    /// to the reply body.
    func appendNarration(_ delta: String, thinking: Bool) {
        if let id = openNarrationId, let i = steps.lastIndex(where: { $0.id == id }) {
            var t = steps[i].text
            if thinking != narrationThinking, !t.isEmpty { t += "\n\n" }
            t += delta
            if t.count > Self.narrationCap { t = "…" + String(t.suffix(Self.narrationCap)) }
            steps[i].text = t
        } else {
            let step = RunStep(kind: .narration, text: delta)
            openNarrationId = step.id
            steps.append(step)
        }
        narrationThinking = thinking
    }

    func addTool(callId: String?, tool: String, parent: String?, detail: String?) {
        openNarrationId = nil
        rechecking = false
        steps.append(RunStep(kind: .tool, tool: tool, parent: parent, detail: detail,
                             status: .running, callId: callId))
    }

    func completeTool(callId: String?, tool: String, nested: Bool, success: Bool, preview: String?) {
        var idx: Int?
        if let callId {
            idx = steps.lastIndex(where: { $0.kind == .tool && $0.callId == callId })
        }
        if idx == nil {
            idx = steps.lastIndex(where: {
                $0.kind == .tool && $0.status == .running && $0.tool == tool && $0.nested == nested
            })
        }
        guard let i = idx else { return }
        steps[i].status = success ? .ok : .failed
        steps[i].preview = preview
    }

    /// A reply draft was not delivered (blocked by the fact check, rejected, or
    /// re-streamed after a retry). Instead of vanishing, the draft moves into
    /// the activity section as a notice and the body fades out. The tool result
    /// and the stream reset both report the same hold-back, so notices within a
    /// couple of seconds merge.
    func holdBack(reason: String, detail: String?) {
        let draft = streamText.trimmingCharacters(in: .whitespacesAndNewlines)
        let now = Date()
        let reasonDetail = (detail?.isEmpty == false) ? detail : lastVerificationDetail
        let verification = reason == "verification"
        withAnimation(.easeOut(duration: 0.25)) {
            if let at = noticeAt, now.timeIntervalSince(at) < 2,
               let i = steps.lastIndex(where: { $0.kind == .notice }) {
                if verification {
                    steps[i].text = Self.noticeText(reason)
                    if let reasonDetail, !reasonDetail.isEmpty { steps[i].detail = reasonDetail }
                }
                if steps[i].preview == nil, !draft.isEmpty { steps[i].preview = draft }
            } else if !draft.isEmpty {
                openNarrationId = nil
                steps.append(RunStep(kind: .notice, text: Self.noticeText(reason),
                                     detail: verification ? reasonDetail : nil,
                                     status: .failed, preview: draft))
                noticeAt = now
            }
            if !streamText.isEmpty { streamText = "" }
            if verification { rechecking = true }
        }
    }

    private static func noticeText(_ reason: String) -> String {
        switch reason {
        case "verification": return String(localized: "Draft held back by fact check — re-verifying")
        case "retry": return String(localized: "Draft discarded — retrying")
        default: return String(localized: "Draft set aside — rewriting")
        }
    }

    // MARK: Lifecycle

    func reopen() {
        guard phase == .done else { return }
        phase = .live
        endedAt = nil
    }

    /// Finish the turn. Steps still running settle as done (or stopped on a
    /// user Stop); an undelivered draft is dropped.
    func settle(stopped: Bool = false) {
        guard phase == .live else { return }
        withAnimation(.easeInOut(duration: 0.2)) {
            for i in steps.indices where steps[i].status == .running {
                steps[i].status = stopped ? .stopped : .ok
            }
            openNarrationId = nil
            writingReply = false
            rechecking = false
            if !streamText.isEmpty { streamText = "" }
            endedAt = Date()
            phase = stopped ? .stopped : .done
        }
    }
}

/// A timeline entry: a message bubble or a whole assistant turn, with its
/// precomputed layout flag so the list never has to index neighbours.
struct ChatRow: Identifiable, Equatable {
    enum Item: Equatable {
        case message(ChatMessage)
        case turn(TurnState)

        static func == (lhs: Item, rhs: Item) -> Bool {
            switch (lhs, rhs) {
            case let (.message(a), .message(b)): return a == b
            case let (.turn(a), .turn(b)): return a === b
            default: return false
            }
        }
    }

    let item: Item
    /// Only the first Hime message in a consecutive run shows the avatar.
    let showAvatar: Bool

    var id: String {
        switch item {
        case .message(let m): return m.id
        case .turn(let t): return "turn-" + t.id
        }
    }

    /// True for rows that visually belong to Hime (assistant bubbles, turns).
    var isAssistantSide: Bool {
        switch item {
        case .message(let m): return m.role == .assistant
        case .turn: return true
        }
    }
}

/// Tiny state the chat header avatar follows (what the oldest live turn is
/// doing). Deliberately a separate observable so it never invalidates the list.
@MainActor
final class LiveState: ObservableObject {
    @Published var active = false
    /// Tool currently running (nil = just thinking).
    @Published var tool: String?
    /// Reply text is streaming in.
    @Published var replying = false

    func reset() {
        if active { active = false }
        if tool != nil { tool = nil }
        if replying { replying = false }
    }
}

/// A row returned by `GET /api/agent/chat-history`.
struct ChatHistoryRow: Decodable {
    let id: Int?
    let created_at: String?
    let role: String
    let content: String
    let message_hash: String?
    let client_msg_id: String?
    let report_id: Int?
    let image_id: String?

    /// `created_at` is a naive UTC `YYYY-MM-DDTHH:MM:SS` string from SQLite.
    var date: Date? {
        guard let created_at else { return nil }
        return Self.utcFormatter.date(from: String(created_at.prefix(19)))
    }

    private static let utcFormatter: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = TimeZone(identifier: "UTC")
        f.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
        return f
    }()
}

struct ChatHistoryResponse: Decodable {
    let success: Bool
    let messages: [ChatHistoryRow]
}

/// Response of `GET /api/agent/evidence/{hash}`.
struct EvidenceResponse: Decodable {
    let success: Bool
    let found: Bool
    let formatted: String
}

// MARK: - Threads

/// Id of the permanent main conversation. It receives every proactive message
/// (reports, reminders, trigger alerts) and cannot be renamed, archived or deleted.
let chatMainThreadId = "main"

/// Last message of a thread, as shown in the thread list preview.
struct ChatThreadPreview: Equatable {
    var role: String
    var content: String
    var date: Date?
}

/// One conversation in `GET /api/agent/chat/threads`.
struct ChatThread: Identifiable, Equatable {
    let id: String
    /// Empty until the server auto-titles it (or the user renames it).
    var title: String
    var createdAt: Date?
    var updatedAt: Date?
    var pinned: Bool
    var archived: Bool
    var last: ChatThreadPreview?

    var isMain: Bool { id == chatMainThreadId }

    /// Placeholder used before the first list fetch lands.
    static let main = ChatThread(id: chatMainThreadId, title: "", createdAt: nil, updatedAt: nil,
                                 pinned: true, archived: false, last: nil)

    /// Decode one thread from a JSON object (REST response or WS event).
    static func from(_ any: Any?) -> ChatThread? {
        guard let d = any as? [String: Any], let id = d["id"] as? String, !id.isEmpty else { return nil }
        var preview: ChatThreadPreview?
        if let lm = d["last_message"] as? [String: Any] {
            preview = ChatThreadPreview(role: (lm["role"] as? String) ?? "",
                                        content: (lm["content"] as? String) ?? "",
                                        date: parseServerDate(lm["created_at"] as? String))
        }
        return ChatThread(id: id,
                          title: (d["title"] as? String) ?? "",
                          createdAt: parseServerDate(d["created_at"] as? String),
                          updatedAt: parseServerDate(d["updated_at"] as? String),
                          pinned: (d["pinned"] as? Bool) ?? (id == chatMainThreadId),
                          archived: (d["archived"] as? Bool) ?? false,
                          last: preview)
    }

    /// Most recent activity, used to order the list.
    var activityDate: Date { last?.date ?? updatedAt ?? createdAt ?? .distantPast }
}

/// Server timestamps are naive UTC `YYYY-MM-DDTHH:MM:SS` strings (SQLite);
/// anything after the seconds (fraction, offset) is ignored.
func parseServerDate(_ s: String?) -> Date? {
    guard let s, s.count >= 19 else { return nil }
    let f = DateFormatter()
    f.locale = Locale(identifier: "en_US_POSIX")
    f.timeZone = TimeZone(identifier: "UTC")
    f.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
    return f.date(from: String(s.replacingOccurrences(of: " ", with: "T").prefix(19)))
}
