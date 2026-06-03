//
//  ChatViewModel.swift
//  hime
//
//  Drives the in-app conversation: posts messages to `/api/agent/chat`,
//  consumes the live agent event stream for replies, loads persisted
//  history, fetches fact-verification evidence, and clears the conversation.
//

import Combine
import Foundation
import SwiftUI

@MainActor
final class ChatViewModel: ObservableObject {
    @Published var messages: [ChatMessage] = []
    @Published var inputText: String = ""
    @Published var pendingImage: Data?
    @Published var activity: AgentActivity = .idle
    @Published var agentStarting = false

    private let stream = ChatStreamClient()
    /// Accumulates the model's streamed reasoning/narration for the live
    /// preview shown in the status pill (it is NOT the user-facing reply —
    /// that arrives whole as `chat_reply`).
    private var thinkingBuffer = ""
    /// Held when the agent wasn't running so we can resend once it starts.
    private var pendingResend: (text: String, image: Data?)?
    private var didLoadHistory = false

    /// Drives the show/hide of the status pill (kept coarse so per-token
    /// preview updates don't re-trigger the container's spring animation).
    var isBusy: Bool { activity != .idle }

    private var apiBase: String { ServerConfig.load().apiBaseURL }

    // MARK: - Lifecycle

    func onAppear() {
        stream.onEvent = { [weak self] event in self?.handle(event) }
        stream.connect()
        if !didLoadHistory {
            didLoadHistory = true
            Task { await loadHistory() }
        }
    }

    func connectStream() { stream.connect() }
    func disconnectStream() { stream.disconnect() }

    // MARK: - Sending

    func send() {
        let text = inputText.trimmingCharacters(in: .whitespacesAndNewlines)
        let image = pendingImage
        guard !text.isEmpty || image != nil else { return }
        messages.append(ChatMessage(role: .user, text: text, localImage: image))
        inputText = ""
        pendingImage = nil
        thinkingBuffer = ""
        activity = .thinking("")
        Task { await post(text: text, image: image) }
    }

    private func post(text: String, image: Data?) async {
        guard let url = URL(string: "\(apiBase)/api/agent/chat") else { return }
        var req = APIClient.request(url, method: "POST")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        var body: [String: Any] = ["text": text, "client_msg_id": UUID().uuidString]
        if let image {
            body["image_base64"] = image.base64EncodedString()
            body["image_mime"] = "image/jpeg"
        }
        req.httpBody = try? JSONSerialization.data(withJSONObject: body)
        do {
            let (data, _) = try await URLSession.shared.data(for: req)
            if let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               (obj["status"] as? String) == "starting" {
                // Agent is waking up; resend once it's ready (message wasn't queued).
                pendingResend = (text, image)
                agentStarting = true
            }
        } catch {
            activity = .idle
        }
    }

    // MARK: - History / evidence / clear

    func loadHistory() async {
        guard let url = URL(string: "\(apiBase)/api/agent/chat-history?limit=100") else { return }
        do {
            let (data, _) = try await URLSession.shared.data(for: APIClient.request(url))
            let resp = try JSONDecoder().decode(ChatHistoryResponse.self, from: data)
            let loaded = resp.messages.map(Self.message(from:))
            if !loaded.isEmpty, messages.isEmpty { messages = loaded }
        } catch { /* no history yet — fine */ }
    }

    /// Catch up on messages that landed while the chat socket was disconnected.
    /// The live agent stream is push-only — it does NOT replay events missed
    /// while we were backgrounded, so a proactive report delivered via APNs is
    /// persisted server-side but never reaches the reconnecting socket. On every
    /// foreground we re-fetch history and append anything not already on screen
    /// (matched by evidence hash, falling back to text). Without this the new
    /// message only shows after leaving and re-entering Chat (a fresh view model
    /// that reloads history from scratch).
    func reconcile() async {
        guard let url = URL(string: "\(apiBase)/api/agent/chat-history?limit=100") else { return }
        do {
            let (data, _) = try await URLSession.shared.data(for: APIClient.request(url))
            let resp = try JSONDecoder().decode(ChatHistoryResponse.self, from: data)
            if messages.isEmpty {
                messages = resp.messages.map(Self.message(from:))
                return
            }
            // Only assistant turns can arrive while the app is closed (the user
            // can't send from a backgrounded app), so we only append those.
            let shownHashes = Set(messages.compactMap { $0.messageHash })
            let shownAssistantText = Set(messages.filter { $0.role == .assistant }.map { $0.text })
            let missing = resp.messages.filter { row in
                guard row.role == "assistant" else { return false }
                if let hash = row.message_hash { return !shownHashes.contains(hash) }
                return !shownAssistantText.contains(row.content)
            }.map(Self.message(from:))
            if !missing.isEmpty { messages.append(contentsOf: missing) }
        } catch { /* offline — fine */ }
    }

    private static func message(from row: ChatHistoryRow) -> ChatMessage {
        ChatMessage(role: row.role == "assistant" ? .assistant : .user,
                    text: row.content, messageHash: row.message_hash,
                    reportId: row.report_id)
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

    func clearConversation() {
        messages.removeAll()
        thinkingBuffer = ""
        activity = .idle
        Task { await post(text: "/clear", image: nil) }
    }

    // MARK: - Event handling

    private func handle(_ event: [String: Any]) {
        guard let type = event["type"] as? String else { return }
        switch type {
        case "agent_started":
            agentStarting = false
            if let pending = pendingResend {
                pendingResend = nil
                Task { await post(text: pending.text, image: pending.image) }
            }
        case "chat_thinking":
            appendThinking((event["content"] as? String) ?? "")
        case "chat_tool_call":
            thinkingBuffer = ""
            if let tool = event["tool"] as? String, !tool.isEmpty {
                activity = .tool(tool)
            } else {
                activity = .thinking("")
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
            activity = .idle
            thinkingBuffer = ""
            messages.append(ChatMessage(role: .assistant,
                                        text: (event["caption"] as? String) ?? "",
                                        imagePath: event["url"] as? String,
                                        messageHash: event["message_hash"] as? String))
        case "chat_cleared":
            messages.removeAll()
            thinkingBuffer = ""
            activity = .idle
        default:
            break
        }
    }

    private func appendThinking(_ delta: String) {
        guard !delta.isEmpty else { return }
        thinkingBuffer += delta
        activity = .thinking(Self.firstSentence(thinkingBuffer))
    }

    private func finalizeReply(text: String, hash: String?, reportId: Int? = nil) {
        activity = .idle
        thinkingBuffer = ""
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
