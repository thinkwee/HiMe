//
//  ChatView.swift
//  hime
//
//  Native in-app conversation with the agent. Replaces the old "Chat on
//  Telegram/Feishu" deep-link: text + image both directions, streaming
//  replies, proactive reports, and the fact-verification "Show Evidence"
//  affordance — all in-app, authenticated by the existing bearer token.
//

import SwiftUI
import PhotosUI

struct ChatView: View {
    @StateObject private var vm = ChatViewModel()
    @State private var photoItem: PhotosPickerItem?

    var body: some View {
        VStack(spacing: 0) {
            if vm.agentStarting {
                Label("Waking up Hime…", systemImage: "moon.zzz")
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .padding(.vertical, 6)
            }

            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(spacing: 14) {
                        ForEach(Array(vm.messages.enumerated()), id: \.element.id) { idx, msg in
                            ChatBubble(
                                message: msg, vm: vm,
                                showAvatar: idx == 0 || vm.messages[idx - 1].role != msg.role
                            )
                            .id(msg.id)
                            .transition(.opacity.combined(with: .move(edge: .bottom)))
                        }
                        if vm.isBusy {
                            AgentActivityView(activity: vm.activity)
                                .padding(.leading, 36)  // align under the avatar gutter
                                .id("activity")
                                .transition(.opacity.combined(with: .scale(scale: 0.92, anchor: .leading)))
                        }
                    }
                    .padding(.horizontal, 14)
                    .padding(.vertical, 14)
                    .animation(.spring(response: 0.35, dampingFraction: 0.8), value: vm.isBusy)
                }
                // A grouped backdrop so the (white) assistant cards have contrast
                // and read as distinct messages rather than blending into the page.
                .background(Color(.systemGroupedBackground))
                .onChange(of: vm.messages.count) { _, _ in scrollToBottom(proxy) }
                .onChange(of: vm.isBusy) { _, _ in scrollToBottom(proxy) }
                .overlay {
                    if vm.messages.isEmpty && !vm.isBusy { ChatEmptyState() }
                }
            }

            composer
        }
        .navigationTitle("Chat")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .navigationBarTrailing) {
                Menu {
                    Button(role: .destructive) { vm.clearConversation() } label: {
                        Label("Clear conversation", systemImage: "trash")
                    }
                } label: {
                    Image(systemName: "ellipsis.circle")
                }
            }
        }
        .onAppear { vm.onAppear() }
        .onDisappear { vm.disconnectStream() }
        .onReceive(NotificationCenter.default.publisher(
            for: UIApplication.didBecomeActiveNotification)) { _ in
            vm.connectStream()
            // The live socket won't replay anything that arrived while we were
            // backgrounded (e.g. a proactive report pushed via APNs), so pull
            // history and append the missed tail.
            Task { await vm.reconcile() }
        }
        .onReceive(NotificationCenter.default.publisher(
            for: UIApplication.didEnterBackgroundNotification)) { _ in vm.disconnectStream() }
    }

    private func scrollToBottom(_ proxy: ScrollViewProxy) {
        withAnimation(.easeOut(duration: 0.2)) {
            if vm.isBusy {
                proxy.scrollTo("activity", anchor: .bottom)
            } else if let last = vm.messages.last {
                proxy.scrollTo(last.id, anchor: .bottom)
            }
        }
    }

    private var composer: some View {
        VStack(spacing: 6) {
            if let data = vm.pendingImage, let ui = UIImage(data: data) {
                HStack {
                    Image(uiImage: ui)
                        .resizable().scaledToFill()
                        .frame(width: 44, height: 44)
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                    Button { vm.pendingImage = nil } label: {
                        Image(systemName: "xmark.circle.fill").foregroundColor(.secondary)
                    }
                    Spacer()
                }
                .padding(.horizontal, 12)
            }
            HStack(spacing: 8) {
                PhotosPicker(selection: $photoItem, matching: .images) {
                    Image(systemName: "photo.on.rectangle")
                        .font(.system(size: 22))
                        .foregroundColor(.secondary)
                }
                .onChange(of: photoItem) { _, item in
                    guard let item else { return }
                    Task {
                        if let data = try? await item.loadTransferable(type: Data.self) {
                            vm.pendingImage = downscaleJPEG(data)
                        }
                        photoItem = nil
                    }
                }

                TextField("Message Hime", text: $vm.inputText, axis: .vertical)
                    .textFieldStyle(.plain)
                    .lineLimit(1...5)
                    .padding(.horizontal, 12)
                    .padding(.vertical, 8)
                    .background(Color(.systemGray6))
                    .clipShape(RoundedRectangle(cornerRadius: 18))

                Button(action: vm.send) {
                    Image(systemName: "arrow.up.circle.fill")
                        .font(.system(size: 28))
                        .foregroundColor(canSend ? Color.himeAccent : .gray)
                }
                .disabled(!canSend)
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
        }
        .background(.bar)
        .overlay(alignment: .top) {
            // Hairline that separates the composer from the conversation.
            Rectangle()
                .fill(Color.primary.opacity(0.08))
                .frame(height: 0.5)
        }
    }

    private var canSend: Bool {
        !vm.inputText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || vm.pendingImage != nil
    }
}

// MARK: - Empty state

/// Shown before the first message — a calm, single-accent invitation.
private struct ChatEmptyState: View {
    var body: some View {
        VStack(spacing: 10) {
            Image(systemName: "bubble.left.and.bubble.right")
                .font(.system(size: 34, weight: .light))
                .foregroundStyle(Color.himeAccent.opacity(0.75))
            Text("Chat with Hime")
                .font(.callout.weight(.medium))
                .foregroundColor(.primary)
            Text("Ask about your sleep, activity, or how you're recovering.")
                .font(.footnote)
                .foregroundColor(.secondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 40)
        }
        .padding(.bottom, 40)
    }
}

// MARK: - Hime avatar

/// A small, quiet identity mark beside Hime's messages. Monochrome on a soft
/// accent disc — detail without a second colour.
private struct HimeAvatar: View {
    var body: some View {
        Image(systemName: "pawprint.fill")
            .font(.system(size: 13, weight: .semibold))
            .foregroundColor(Color.himeAccent)
            .frame(width: 28, height: 28)
            .background(Circle().fill(Color.himeAccent.opacity(0.12)))
            .overlay(Circle().stroke(Color.himeAccent.opacity(0.18), lineWidth: 0.5))
    }
}

// MARK: - Bubble

private struct ChatBubble: View {
    let message: ChatMessage
    @ObservedObject var vm: ChatViewModel
    /// Only the first Hime message in a consecutive run shows the avatar; the
    /// rest reserve the same gutter so their bubbles stay left-aligned under it.
    var showAvatar: Bool = true

    @State private var evidence: String?
    @State private var showEvidence = false
    @State private var loadingEvidence = false

    private var isUser: Bool { message.role == .user }

    /// Subtly asymmetric corners — a small "tail" on the sender's bottom edge.
    /// A quiet detail that reads as a chat bubble without any ornament.
    private var bubbleShape: UnevenRoundedRectangle {
        let r: CGFloat = 17
        let tail: CGFloat = 5
        return UnevenRoundedRectangle(
            topLeadingRadius: r,
            bottomLeadingRadius: isUser ? r : tail,
            bottomTrailingRadius: isUser ? tail : r,
            topTrailingRadius: r,
            style: .continuous)
    }

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            if isUser {
                Spacer(minLength: 44)
            } else if showAvatar {
                HimeAvatar()
            } else {
                Color.clear.frame(width: 28, height: 1)
            }
            VStack(alignment: isUser ? .trailing : .leading, spacing: 6) {
                if let data = message.localImage, let ui = UIImage(data: data) {
                    ChatImageThumbnail(image: ui, maxWidth: 220, maxHeight: 220)
                }
                if let path = message.imagePath {
                    AuthedAsyncImage(path: path)
                }
                if !message.text.isEmpty {
                    bubbleText
                        .padding(.horizontal, 14)
                        .padding(.vertical, 10)
                        .background(
                            // Assistant: a white card with a soft shadow + hairline,
                            // lifted off the grouped backdrop. User: flat accent.
                            // The shadow sits on the shape only, so text stays crisp.
                            bubbleShape
                                .fill(isUser ? Color.himeAccent : Color(.secondarySystemGroupedBackground))
                                .shadow(color: .black.opacity(isUser ? 0.08 : 0.05),
                                        radius: 2, x: 0, y: 1)
                        )
                        .overlay {
                            if !isUser {
                                bubbleShape.stroke(Color.primary.opacity(0.05), lineWidth: 0.5)
                            }
                        }
                        .textSelection(.enabled)
                }
                if let reportId = message.reportId, !isUser {
                    reportLink(reportId)
                }
                if message.messageHash != nil && !isUser && !message.isStreaming {
                    evidenceControl
                }
                if showEvidence, let evidence {
                    MarkdownView(text: evidence, foreground: Color(.secondaryLabel))
                        .font(.footnote)
                        .padding(10)
                        .background(Color(.systemGray6))
                        .clipShape(RoundedRectangle(cornerRadius: 12))
                }
            }
            if !isUser { Spacer(minLength: 40) }
        }
    }

    /// User messages are echoed back verbatim (plain), assistant replies render
    /// full block-level Markdown.
    @ViewBuilder
    private var bubbleText: some View {
        if isUser {
            Text(message.text)
                .font(.body)
                .foregroundColor(.white)
        } else {
            MarkdownView(text: message.text, foreground: .primary)
        }
    }

    /// A quiet "view full report" affordance shown beneath a proactive report
    /// bubble — taps deep-link to the Reports tab with this report expanded.
    private func reportLink(_ reportId: Int) -> some View {
        Button {
            AppRouter.shared.requestReport(reportId)
        } label: {
            HStack(spacing: 4) {
                Image(systemName: "doc.text.magnifyingglass")
                Text("View full report")
                Image(systemName: "chevron.right")
                    .font(.system(size: 9, weight: .semibold))
            }
            .font(.caption.weight(.medium))
            .foregroundColor(Color.himeAccent)
        }
        .buttonStyle(.plain)
    }

    private var evidenceControl: some View {
        Button {
            if showEvidence { showEvidence = false; return }
            if let evidence, !evidence.isEmpty { showEvidence = true; return }
            loadingEvidence = true
            Task {
                let result = await vm.evidence(for: message)
                evidence = result ?? String(localized: "No evidence recorded.")
                showEvidence = true
                loadingEvidence = false
            }
        } label: {
            HStack(spacing: 4) {
                if loadingEvidence {
                    ProgressView().scaleEffect(0.7)
                } else {
                    Image(systemName: "chart.bar.doc.horizontal")
                }
                Text(showEvidence ? "Hide evidence" : "Show evidence")
            }
            .font(.caption)
            .foregroundColor(.secondary)
        }
    }
}

// MARK: - Authed image loader (AsyncImage can't set the bearer header)

private struct AuthedAsyncImage: View {
    let path: String
    @State private var image: UIImage?

    var body: some View {
        Group {
            if let image {
                ChatImageThumbnail(image: image, maxWidth: 240, maxHeight: 240)
            } else {
                RoundedRectangle(cornerRadius: 14)
                    .fill(Color(.systemGray6))
                    .frame(width: 200, height: 150)
                    .overlay(ProgressView())
            }
        }
        .task(id: path) { await load() }
    }

    private func load() async {
        guard image == nil,
              let url = URL(string: "\(ServerConfig.load().apiBaseURL)\(path)") else { return }
        if let (data, _) = try? await URLSession.shared.data(for: APIClient.request(url)),
           let ui = UIImage(data: data) {
            image = ui
        }
    }
}

// MARK: - Helpers

/// Downscale + JPEG-encode so uploads stay well under the server's size cap.
private func downscaleJPEG(_ data: Data, maxDimension: CGFloat = 1280, quality: CGFloat = 0.7) -> Data {
    guard let ui = UIImage(data: data) else { return data }
    let scale = min(1, maxDimension / max(ui.size.width, ui.size.height))
    if scale >= 1 { return ui.jpegData(compressionQuality: quality) ?? data }
    let newSize = CGSize(width: ui.size.width * scale, height: ui.size.height * scale)
    let renderer = UIGraphicsImageRenderer(size: newSize)
    let resized = renderer.image { _ in ui.draw(in: CGRect(origin: .zero, size: newSize)) }
    return resized.jpegData(compressionQuality: quality) ?? data
}
