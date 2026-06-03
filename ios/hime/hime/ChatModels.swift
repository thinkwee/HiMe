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
    let timestamp: Date

    init(id: String = UUID().uuidString,
         role: Role,
         text: String = "",
         imagePath: String? = nil,
         localImage: Data? = nil,
         messageHash: String? = nil,
         reportId: Int? = nil,
         isStreaming: Bool = false,
         timestamp: Date = Date()) {
        self.id = id
        self.role = role
        self.text = text
        self.imagePath = imagePath
        self.localImage = localImage
        self.messageHash = messageHash
        self.reportId = reportId
        self.isStreaming = isStreaming
        self.timestamp = timestamp
    }
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
    let role: String
    let content: String
    let message_hash: String?
    let report_id: Int?
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
