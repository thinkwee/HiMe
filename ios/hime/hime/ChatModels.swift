//
//  ChatModels.swift
//  hime
//
//  In-app chat with the agent (replaces the external IM gateways).
//

import Foundation

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
            && lhs.delivery == rhs.delivery
    }
}

/// A message plus its precomputed layout flag, so the list never has to
/// rebuild `Array(enumerated())` or index neighbours during render.
struct ChatRow: Identifiable, Equatable {
    let message: ChatMessage
    /// Only the first Hime message in a consecutive run shows the avatar.
    let showAvatar: Bool
    var id: String { message.id }
}

/// What the agent is doing right now — drives the lively status pill under the
/// conversation (Claude-Code-style "thinking" / "using a tool" indicator).
enum AgentActivity: Equatable {
    case idle
    /// Reasoning in progress; the associated value is a short live preview
    /// (first sentence of the streamed thinking, possibly empty).
    case thinking(String)
    case tool(String)
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
