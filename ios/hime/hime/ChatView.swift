//
//  ChatView.swift
//  hime
//
//  Native in-app conversation with the agent. Replaces the old "Chat on
//  Telegram/Feishu" deep-link: text + image both directions, streaming
//  replies, proactive reports, and the fact-verification "Show Evidence"
//  affordance — all in-app, authenticated by the existing bearer token.
//
//  Performance notes: the draft text lives in `ChatComposer`'s own state and
//  bubbles are plain value views (`Equatable`, no view-model reference), so
//  typing or a streamed token never re-renders the message list.
//

import SwiftUI
import PhotosUI

struct ChatView: View {
    @StateObject private var vm = ChatViewModel()
    @FocusState private var inputFocused: Bool
    /// Whether the viewport is at (or near) the newest message — gates whether
    /// incoming messages pull the list down.
    @State private var nearBottom = true

    private static let bottomID = "chat-bottom"

    var body: some View {
        VStack(spacing: 0) {
            if vm.agentStarting {
                Label("Waking up Hime…", systemImage: "moon.zzz")
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .padding(.vertical, 6)
            }
            if let error = vm.errorBanner {
                Label(error, systemImage: "exclamationmark.triangle.fill")
                    .font(.caption)
                    .foregroundColor(.red)
                    .padding(.horizontal, 12)
                    .padding(.vertical, 6)
                    .transition(.opacity)
            }

            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(spacing: 14) {
                        ForEach(vm.rows) { row in
                            ChatBubble(
                                message: row.message,
                                showAvatar: row.showAvatar,
                                loadEvidence: { msg in await vm.evidence(for: msg) },
                                onRetry: { id in vm.retry(messageId: id) }
                            )
                            .equatable()
                        }
                        if vm.isBusy {
                            AgentActivityView(activity: vm.activity)
                                .padding(.leading, 36)  // align under the avatar gutter
                                .transition(.opacity.combined(with: .scale(scale: 0.92, anchor: .leading)))
                        }
                        Color.clear.frame(height: 1).id(Self.bottomID)
                    }
                    .padding(.horizontal, 14)
                    .padding(.vertical, 14)
                    .animation(.spring(response: 0.35, dampingFraction: 0.8), value: vm.isBusy)
                }
                // Open (and stay) pinned to the newest message.
                .defaultScrollAnchor(.bottom)
                .scrollDismissesKeyboard(.interactively)
                // Tap the list to dismiss the keyboard (attached before the composer
                // inset below, so taps on the text field itself are unaffected).
                .simultaneousGesture(TapGesture().onEnded { inputFocused = false })
                .onScrollGeometryChange(for: ScrollMetrics.self) { geo in
                    ScrollMetrics(visibleMaxY: geo.visibleRect.maxY,
                                  content: geo.contentSize.height,
                                  container: geo.containerSize.height)
                } action: { old, new in
                    // Content grew (image finished loading, tokens streamed) or the
                    // viewport shrank (keyboard) while the user was at the bottom:
                    // keep the newest message in view.
                    let resized = new.content > old.content + 0.5
                        || abs(new.container - old.container) > 0.5
                    if resized && old.isNearBottom {
                        scrollToBottom(proxy, animated: false)
                        if !nearBottom { nearBottom = true }
                    } else if nearBottom != new.isNearBottom {
                        nearBottom = new.isNearBottom
                    }
                }
                // The composer lives in a bottom safe-area inset so the scroll
                // content, the keyboard and the input bar are laid out together.
                .safeAreaInset(edge: .bottom, spacing: 0) {
                    ChatComposer(focus: $inputFocused) { text, image in
                        vm.send(text: text, image: image)
                        scrollToBottom(proxy, animated: true)
                    }
                }
                // A grouped backdrop so the (white) assistant cards have contrast
                // and read as distinct messages rather than blending into the page.
                .background(Color(.systemGroupedBackground))
                .onChange(of: vm.didLoadHistory) { _, loaded in
                    guard loaded else { return }
                    jumpToBottom(proxy)
                }
                .onChange(of: vm.rows.last?.id) { _, _ in
                    guard let last = vm.rows.last?.message else { return }
                    // Always follow your own sends; follow replies unless the
                    // user scrolled up to read history.
                    if last.role == .user || nearBottom {
                        scrollToBottom(proxy, animated: vm.didLoadHistory)
                    }
                }
                .onChange(of: vm.isBusy) { _, _ in
                    if nearBottom { scrollToBottom(proxy, animated: true) }
                }
                .onChange(of: inputFocused) { _, focused in
                    guard focused else { return }
                    // Wait for the keyboard animation, then settle on the newest message.
                    Task {
                        try? await Task.sleep(nanoseconds: 300_000_000)
                        scrollToBottom(proxy, animated: false)
                    }
                }
                .onReceive(NotificationCenter.default.publisher(
                    for: UIResponder.keyboardDidShowNotification)) { _ in
                    if inputFocused { scrollToBottom(proxy, animated: false) }
                }
                .onAppear { if vm.didLoadHistory { jumpToBottom(proxy) } }
                .overlay {
                    if vm.rows.isEmpty && !vm.isBusy { ChatEmptyState() }
                }
            }
        }
        .animation(.easeInOut(duration: 0.2), value: vm.errorBanner)
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
            // The live socket won't replay anything that arrived while we were
            // backgrounded (e.g. a proactive report pushed via APNs): reconnect
            // and pull history to append the missed tail.
            vm.foregrounded()
        }
        .onReceive(NotificationCenter.default.publisher(
            for: UIApplication.didEnterBackgroundNotification)) { _ in vm.disconnectStream() }
    }

    private func scrollToBottom(_ proxy: ScrollViewProxy, animated: Bool) {
        if animated {
            withAnimation(.easeOut(duration: 0.2)) {
                proxy.scrollTo(Self.bottomID, anchor: .bottom)
            }
        } else {
            proxy.scrollTo(Self.bottomID, anchor: .bottom)
        }
    }

    /// Land on the newest message after the initial load. Lazy rows only settle
    /// their real heights after the first layout pass, so re-anchor once more.
    private func jumpToBottom(_ proxy: ScrollViewProxy) {
        nearBottom = true
        Task {
            scrollToBottom(proxy, animated: false)
            try? await Task.sleep(nanoseconds: 250_000_000)
            scrollToBottom(proxy, animated: false)
        }
    }
}

/// Viewport / content measurements used to decide when to keep the list pinned
/// to the newest message.
private struct ScrollMetrics: Equatable {
    var visibleMaxY: CGFloat
    var content: CGFloat
    var container: CGFloat

    var isNearBottom: Bool { content - visibleMaxY < 100 }
}

// MARK: - Composer

/// The input bar. Owns the draft text and attached image as local state so that
/// typing never invalidates the (potentially very long) message list.
private struct ChatComposer: View {
    var focus: FocusState<Bool>.Binding
    let onSend: (String, Data?) -> Void

    @State private var text = ""
    @State private var pendingImage: Data?
    @State private var pendingPreview: UIImage?
    @State private var photoItem: PhotosPickerItem?

    var body: some View {
        VStack(spacing: 6) {
            if let ui = pendingPreview {
                HStack {
                    Image(uiImage: ui)
                        .resizable().scaledToFill()
                        .frame(width: 44, height: 44)
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                    Button {
                        pendingImage = nil
                        pendingPreview = nil
                    } label: {
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
                            // Decoding + redrawing + re-encoding a modern 48MP
                            // capture takes hundreds of ms; keep it off the main
                            // actor so the picker dismissal doesn't hitch.
                            let scaled = await Task.detached(priority: .userInitiated) {
                                downscaleJPEG(data)
                            }.value
                            pendingImage = scaled
                            pendingPreview = scaled.flatMap { UIImage(data: $0) }
                        }
                        photoItem = nil
                    }
                }

                TextField("Message Hime", text: $text, axis: .vertical)
                    .textFieldStyle(.plain)
                    .focused(focus)
                    .lineLimit(1...5)
                    .padding(.horizontal, 12)
                    .padding(.vertical, 8)
                    .background(Color(.systemGray6))
                    .clipShape(RoundedRectangle(cornerRadius: 18))

                Button(action: send) {
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
        !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || pendingImage != nil
    }

    private func send() {
        guard canSend else { return }
        onSend(text, pendingImage)
        text = ""
        pendingImage = nil
        pendingPreview = nil
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
        .allowsHitTesting(false)
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

/// A pure value view: it holds the message plus closures, never the view
/// model, and is `Equatable` so SwiftUI skips re-rendering rows whose message
/// didn't change (the closures are stable calls into the view model).
private struct ChatBubble: View, Equatable {
    let message: ChatMessage
    /// Only the first Hime message in a consecutive run shows the avatar; the
    /// rest reserve the same gutter so their bubbles stay left-aligned under it.
    let showAvatar: Bool
    let loadEvidence: (ChatMessage) async -> String?
    let onRetry: (String) -> Void

    @State private var evidence: String?
    @State private var showEvidence = false
    @State private var loadingEvidence = false

    static func == (lhs: ChatBubble, rhs: ChatBubble) -> Bool {
        lhs.message == rhs.message && lhs.showAvatar == rhs.showAvatar
    }

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
                if let data = message.localImage {
                    LocalChatImage(key: "local:\(message.id)", data: data)
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
                        .opacity(isUser && message.delivery == .sending ? 0.75 : 1)
                        .textSelection(.enabled)
                }
                if isUser && message.delivery == .failed {
                    retryControl
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

    /// Shown beneath a user message the server never accepted.
    private var retryControl: some View {
        Button {
            onRetry(message.id)
        } label: {
            HStack(spacing: 4) {
                Image(systemName: "exclamationmark.circle.fill")
                Text("Not sent. Tap to retry")
                Image(systemName: "arrow.clockwise")
                    .font(.system(size: 10, weight: .semibold))
            }
            .font(.caption)
            .foregroundColor(.red)
        }
        .buttonStyle(.plain)
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
                let result = await loadEvidence(message)
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

// MARK: - Image loaders

/// The user's own attached photo. Decoded once off the main actor and cached,
/// instead of `UIImage(data:)` inside `body` on every render.
private struct LocalChatImage: View {
    let key: String
    let data: Data
    @State private var image: UIImage?

    init(key: String, data: Data) {
        self.key = key
        self.data = data
        _image = State(initialValue: ChatImageCache.image(for: key))
    }

    var body: some View {
        Group {
            if let image {
                ChatImageThumbnail(image: image, maxWidth: 220, maxHeight: 220)
            } else {
                RoundedRectangle(cornerRadius: 14)
                    .fill(Color(.systemGray6))
                    .frame(width: 120, height: 120)
            }
        }
        .task(id: key) {
            guard image == nil else { return }
            if let ui = await ChatImageCache.decode(data) {
                ChatImageCache.store(ui, for: key)
                image = ui
            }
        }
    }
}

/// Authed image loader (AsyncImage can't set the bearer header). Results are
/// cached so a recycled row shows its image immediately instead of re-fetching.
private struct AuthedAsyncImage: View {
    let path: String
    @State private var image: UIImage?
    @State private var failed = false

    init(path: String) {
        self.path = path
        _image = State(initialValue: ChatImageCache.image(for: "path:\(path)"))
    }

    var body: some View {
        Group {
            if let image {
                ChatImageThumbnail(image: image, maxWidth: 240, maxHeight: 240)
            } else {
                RoundedRectangle(cornerRadius: 14)
                    .fill(Color(.systemGray6))
                    .frame(width: 200, height: 150)
                    .overlay {
                        if failed {
                            VStack(spacing: 6) {
                                Image(systemName: "photo.badge.exclamationmark")
                                    .font(.system(size: 22))
                                Text("Image unavailable")
                                    .font(.caption)
                            }
                            .foregroundColor(.secondary)
                        } else {
                            ProgressView()
                        }
                    }
            }
        }
        .task(id: path) { await load() }
    }

    private func load() async {
        guard image == nil,
              let url = URL(string: "\(ServerConfig.load().apiBaseURL)\(path)") else { return }
        failed = false
        // Without the status check an expired image (the server TTLs them out and
        // answers 404) decodes to nil and the ProgressView spins forever.
        guard let ui = await ChatImageCache.fetch(url, authed: true) else {
            failed = true
            return
        }
        ChatImageCache.store(ui, for: "path:\(path)")
        image = ui
    }
}

// MARK: - Helpers

/// Downscale + JPEG-encode so uploads stay well under the server's size cap.
///
/// Returns nil when the bytes can't be decoded or re-encoded. Callers must NOT
/// fall back to the original data: the upload is tagged `image/jpeg`, so handing
/// back untouched HEIC/PNG bytes would mislabel them.
///
/// `nonisolated` so it can run off the main actor — see the PhotosPicker callback.
private nonisolated func downscaleJPEG(_ data: Data, maxDimension: CGFloat = 1280, quality: CGFloat = 0.7) -> Data? {
    guard let ui = UIImage(data: data) else { return nil }
    let scale = min(1, maxDimension / max(ui.size.width, ui.size.height))
    if scale >= 1 { return ui.jpegData(compressionQuality: quality) }
    let newSize = CGSize(width: ui.size.width * scale, height: ui.size.height * scale)
    // Explicit scale 1 — the default reads the main screen's scale (a main-actor
    // lookup) and would also emit a 2-3x larger bitmap than maxDimension asks for.
    let format = UIGraphicsImageRendererFormat()
    format.scale = 1
    let renderer = UIGraphicsImageRenderer(size: newSize, format: format)
    let resized = renderer.image { _ in ui.draw(in: CGRect(origin: .zero, size: newSize)) }
    return resized.jpegData(compressionQuality: quality)
}
