//
//  ChatImageView.swift
//  hime
//
//  Chat image affordances: an inline thumbnail that opens a full-screen,
//  pinch-zoomable viewer on tap and offers "Save Image" on long-press, plus
//  the viewer itself. Used for both user-attached photos and agent-sent charts.
//

import SwiftUI

/// Inline image bubble: tap to view full-screen, long-press for a Save menu.
struct ChatImageThumbnail: View {
    let image: UIImage
    var maxWidth: CGFloat = 240
    var maxHeight: CGFloat = 240

    @State private var showFullScreen = false

    var body: some View {
        Image(uiImage: image)
            .resizable()
            .scaledToFit()
            .frame(maxWidth: maxWidth, maxHeight: maxHeight)
            .clipShape(RoundedRectangle(cornerRadius: HimeRadius.card))
            .contentShape(RoundedRectangle(cornerRadius: HimeRadius.card))
            .onTapGesture { showFullScreen = true }
            .contextMenu {
                Button {
                    ImageSaver.shared.save(image)
                } label: {
                    Label("Save Image", systemImage: "square.and.arrow.down")
                }
            }
            .fullScreenCover(isPresented: $showFullScreen) {
                FullScreenImageViewer(image: image)
            }
            .accessibilityAddTraits(.isButton)
            .accessibilityLabel("Photo. Double-tap to enlarge.")
    }
}

/// Full-screen, pinch-to-zoom + pan image viewer with a Save button.
struct FullScreenImageViewer: View {
    let image: UIImage
    @Environment(\.dismiss) private var dismiss

    @State private var scale: CGFloat = 1
    @State private var lastScale: CGFloat = 1
    @State private var offset: CGSize = .zero
    @State private var lastOffset: CGSize = .zero
    @State private var savedToast = false

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()

            Image(uiImage: image)
                .resizable()
                .scaledToFit()
                .scaleEffect(scale)
                .offset(offset)
                .gesture(magnification)
                .simultaneousGesture(scale > 1 ? panGesture : nil)
                .onTapGesture(count: 2) { toggleZoom() }

            VStack {
                HStack {
                    Spacer()
                    Button { dismiss() } label: {
                        Image(systemName: "xmark.circle.fill")
                            .font(.system(size: 30))
                            .foregroundStyle(.white.opacity(0.85))
                            .padding(8)
                    }
                }
                Spacer()
                if savedToast {
                    Label("Saved to Photos", systemImage: "checkmark.circle.fill")
                        .font(.subheadline)
                        .padding(.horizontal, 14).padding(.vertical, 9)
                        .background(.ultraThinMaterial, in: Capsule())
                        .foregroundStyle(.white)
                        .transition(.opacity)
                        .padding(.bottom, 8)
                }
                Button {
                    ImageSaver.shared.save(image)
                    withAnimation { savedToast = true }
                    DispatchQueue.main.asyncAfter(deadline: .now() + 1.6) {
                        withAnimation { savedToast = false }
                    }
                } label: {
                    Label("Save Image", systemImage: "square.and.arrow.down")
                        .font(.callout.weight(.medium))
                        .padding(.horizontal, 18).padding(.vertical, 11)
                        .background(.ultraThinMaterial, in: Capsule())
                        .foregroundStyle(.white)
                }
                .padding(.bottom, 24)
            }
        }
        .statusBarHidden()
    }

    private var magnification: some Gesture {
        MagnificationGesture()
            .onChanged { value in
                scale = min(max(lastScale * value, 1), 5)
            }
            .onEnded { _ in
                lastScale = scale
                if scale <= 1 { withAnimation { resetTransform() } }
            }
    }

    private var panGesture: some Gesture {
        DragGesture()
            .onChanged { value in
                offset = CGSize(width: lastOffset.width + value.translation.width,
                                height: lastOffset.height + value.translation.height)
            }
            .onEnded { _ in lastOffset = offset }
    }

    private func toggleZoom() {
        withAnimation(.easeInOut(duration: 0.25)) {
            if scale > 1 {
                resetTransform()
            } else {
                scale = 2.5
                lastScale = 2.5
            }
        }
    }

    private func resetTransform() {
        scale = 1; lastScale = 1
        offset = .zero; lastOffset = .zero
    }
}

/// Writes a `UIImage` to the user's photo library. Needs a strong delegate
/// target, so it's a tiny shared singleton rather than a free function.
final class ImageSaver: NSObject {
    static let shared = ImageSaver()

    func save(_ image: UIImage) {
        UIImageWriteToSavedPhotosAlbum(image, self,
            #selector(didFinish(_:didFinishSavingWithError:contextInfo:)), nil)
    }

    @objc private func didFinish(_ image: UIImage,
                                 didFinishSavingWithError error: Error?,
                                 contextInfo: UnsafeRawPointer) {
        // Best-effort; a denied Photos permission surfaces the system prompt.
    }
}

// MARK: - Decoded-image cache

/// Process-wide cache of decoded chat images. Rows in a `LazyVStack` are
/// recycled constantly while scrolling; without this every re-appearance
/// re-downloaded / re-decoded its image (and flashed a spinner).
enum ChatImageCache {
    private static let cache: NSCache<NSString, UIImage> = {
        let c = NSCache<NSString, UIImage>()
        c.countLimit = 80
        c.totalCostLimit = 128 * 1024 * 1024
        return c
    }()

    static func image(for key: String) -> UIImage? {
        cache.object(forKey: key as NSString)
    }

    static func store(_ image: UIImage, for key: String) {
        let cost = Int(image.size.width * image.size.height * image.scale * image.scale * 4)
        cache.setObject(image, forKey: key as NSString, cost: cost)
    }

    /// Decode (and pre-render) image bytes off the main actor.
    static func decode(_ data: Data) async -> UIImage? {
        await Task.detached(priority: .userInitiated) { () -> UIImage? in
            guard let ui = UIImage(data: data) else { return nil }
            return ui.preparingForDisplay() ?? ui
        }.value
    }

    /// Decode a `data:<mime>;base64,<payload>` URI off the main actor.
    static func decodeDataURI(_ src: String) async -> UIImage? {
        await Task.detached(priority: .userInitiated) { () -> UIImage? in
            guard let comma = src.firstIndex(of: ","),
                  let data = Data(base64Encoded: String(src[src.index(after: comma)...])),
                  let ui = UIImage(data: data) else { return nil }
            return ui.preparingForDisplay() ?? ui
        }.value
    }

    /// Whether `url` points at the configured API server. The bearer token must
    /// only ever be sent there — agent-written markdown can reference any URL.
    static func isAPIHost(_ url: URL) -> Bool {
        guard let base = URL(string: ServerConfig.load().apiBaseURL),
              let host = url.host?.lowercased(), let baseHost = base.host?.lowercased()
        else { return false }
        return host == baseHost && url.port == base.port
    }

    /// Download + decode `url`, attaching the bearer token only when `authed`.
    /// Returns nil on any network error, non-2xx status, or undecodable body.
    static func fetch(_ url: URL, authed: Bool) async -> UIImage? {
        let req = authed ? APIClient.request(url) : URLRequest(url: url)
        guard let (data, resp) = try? await URLSession.shared.data(for: req),
              let http = resp as? HTTPURLResponse, (200..<300).contains(http.statusCode)
        else { return nil }
        return await decode(data)
    }
}
