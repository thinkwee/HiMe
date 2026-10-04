//
//  MarkdownView.swift
//  hime
//
//  Block-level Markdown renderer for chat bubbles. SwiftUI's
//  `AttributedString(markdown:)` only does *inline* styling (bold/italic/code/
//  links) and collapses all block structure onto one line, so headings, lists,
//  code fences, quotes and rules render as plain text. Here we parse those
//  block constructs ourselves and render each natively, while still delegating
//  inline styling to AttributedString per line.
//

import SwiftUI

struct MarkdownView: View {
    let text: String
    var foreground: Color = .primary

    var body: some View {
        // Parsed once per distinct text (see `MarkdownParser.cachedParse`), not
        // on every render — body re-runs on any ancestor invalidation.
        let blocks = MarkdownParser.cachedParse(text)
        VStack(alignment: .leading, spacing: 6) {
            ForEach(blocks.indices, id: \.self) { i in
                blocks[i].view(foreground: foreground)
            }
        }
    }
}

// MARK: - Blocks

enum MarkdownBlock {
    case heading(level: Int, text: String)
    case paragraph(String)
    case bullets([String])
    case ordered([(marker: String, text: String)])
    case code(String)
    case quote([String])
    case table(headers: [String], rows: [[String]])
    case image(alt: String, src: String)
    case rule

    @ViewBuilder
    func view(foreground: Color) -> some View {
        switch self {
        case let .heading(level, text):
            inlineMarkdown(text)
                .font(headingFont(level))
                .foregroundColor(foreground)
                .fixedSize(horizontal: false, vertical: true)

        case let .paragraph(text):
            inlineMarkdown(text)
                .font(.body)
                .foregroundColor(foreground)
                .fixedSize(horizontal: false, vertical: true)

        case let .bullets(items):
            VStack(alignment: .leading, spacing: 4) {
                ForEach(items.indices, id: \.self) { i in
                    let item = items[i]
                    HStack(alignment: .firstTextBaseline, spacing: 7) {
                        Text("•").foregroundColor(foreground.opacity(0.55))
                        inlineMarkdown(item)
                            .foregroundColor(foreground)
                            .fixedSize(horizontal: false, vertical: true)
                        Spacer(minLength: 0)
                    }
                }
            }
            .font(.body)

        case let .ordered(items):
            VStack(alignment: .leading, spacing: 4) {
                ForEach(items.indices, id: \.self) { i in
                    let pair = items[i]
                    HStack(alignment: .firstTextBaseline, spacing: 7) {
                        Text("\(pair.marker).")
                            .monospacedDigit()
                            .foregroundColor(foreground.opacity(0.55))
                        inlineMarkdown(pair.text)
                            .foregroundColor(foreground)
                            .fixedSize(horizontal: false, vertical: true)
                        Spacer(minLength: 0)
                    }
                }
            }
            .font(.body)

        case let .code(code):
            ScrollView(.horizontal, showsIndicators: false) {
                Text(code)
                    .font(.system(.callout, design: .monospaced))
                    .foregroundColor(foreground)
                    .padding(10)
            }
            .background(Color.primary.opacity(0.06))
            .clipShape(RoundedRectangle(cornerRadius: 10))

        case let .quote(lines):
            HStack(spacing: 8) {
                RoundedRectangle(cornerRadius: 2)
                    .fill(HimeColor.accent.opacity(0.6))
                    .frame(width: 3)
                inlineMarkdown(lines.joined(separator: "\n"))
                    .font(.body)
                    .foregroundColor(foreground.opacity(0.85))
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 0)
            }

        case let .table(headers, rows):
            ScrollView(.horizontal, showsIndicators: false) {
                Grid(alignment: .leading, horizontalSpacing: 14, verticalSpacing: 6) {
                    GridRow {
                        ForEach(headers.indices, id: \.self) { i in
                            let h = headers[i]
                            inlineMarkdown(h)
                                .font(.footnote.weight(.semibold))
                                .foregroundColor(foreground)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    Divider().gridCellColumns(max(headers.count, 1))
                    ForEach(rows.indices, id: \.self) { r in
                        let row = rows[r]
                        GridRow {
                            ForEach(row.indices, id: \.self) { c in
                                let cell = row[c]
                                inlineMarkdown(cell)
                                    .font(.footnote)
                                    .foregroundColor(foreground.opacity(0.9))
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                        }
                    }
                }
                .padding(10)
            }
            .background(Color.primary.opacity(0.04))
            .clipShape(RoundedRectangle(cornerRadius: 10))

        case let .image(alt, src):
            MarkdownImageView(alt: alt, src: src)

        case .rule:
            Divider().padding(.vertical, 2)
        }
    }

    private func headingFont(_ level: Int) -> Font {
        switch level {
        case 1: return .title2.weight(.bold)
        case 2: return .title3.weight(.bold)
        case 3: return .headline
        default: return .subheadline.weight(.semibold)
        }
    }
}

/// Render one line/paragraph of inline Markdown (bold, italic, `code`, links).
/// SwiftUI doesn't reliably apply a monospaced font to inline-code runs, so we
/// post-process those runs ourselves.
func inlineMarkdown(_ s: String) -> Text {
    guard var attr = try? AttributedString(
        markdown: s,
        options: .init(
            allowsExtendedAttributes: true,
            interpretedSyntax: .inlineOnlyPreservingWhitespace,
            failurePolicy: .returnPartiallyParsedIfPossible)
    ) else {
        return Text(s)
    }
    let codeRanges = attr.runs.compactMap { run -> Range<AttributedString.Index>? in
        if let intent = run.inlinePresentationIntent, intent.contains(.code) {
            return run.range
        }
        return nil
    }
    for range in codeRanges {
        attr[range].font = .system(.body, design: .monospaced)
    }
    return Text(attr)
}

// MARK: - Parser

enum MarkdownParser {
    /// Reference box so parsed blocks (an enum array) can live in an NSCache.
    private final class Parsed {
        let blocks: [MarkdownBlock]
        init(_ blocks: [MarkdownBlock]) { self.blocks = blocks }
    }

    private static let cache: NSCache<NSString, Parsed> = {
        let c = NSCache<NSString, Parsed>()
        c.countLimit = 300
        return c
    }()

    /// `parse` memoised by the exact source text. A streamed message that grows
    /// simply misses the cache for each new text; finished messages hit it.
    static func cachedParse(_ text: String) -> [MarkdownBlock] {
        let key = text as NSString
        if let hit = cache.object(forKey: key) { return hit.blocks }
        let blocks = parse(text)
        cache.setObject(Parsed(blocks), forKey: key)
        return blocks
    }

    static func parse(_ text: String) -> [MarkdownBlock] {
        var blocks: [MarkdownBlock] = []
        let lines = text.components(separatedBy: "\n")
        var paragraph: [String] = []
        var i = 0

        func flushParagraph() {
            if !paragraph.isEmpty {
                blocks.append(.paragraph(paragraph.joined(separator: "\n")))
                paragraph.removeAll()
            }
        }

        while i < lines.count {
            let trimmed = lines[i].trimmingCharacters(in: .whitespaces)

            // Fenced code block — consume until the closing fence (or EOF, so a
            // half-streamed block still renders).
            if trimmed.hasPrefix("```") {
                flushParagraph()
                var code: [String] = []
                i += 1
                while i < lines.count,
                      !lines[i].trimmingCharacters(in: .whitespaces).hasPrefix("```") {
                    code.append(lines[i])
                    i += 1
                }
                i += 1 // skip closing fence
                blocks.append(.code(code.joined(separator: "\n")))
                continue
            }

            if trimmed.isEmpty {
                flushParagraph()
                i += 1
                continue
            }

            if trimmed == "---" || trimmed == "***" || trimmed == "___" {
                flushParagraph()
                blocks.append(.rule)
                i += 1
                continue
            }

            if let heading = headingMatch(trimmed) {
                flushParagraph()
                blocks.append(.heading(level: heading.level, text: heading.text))
                i += 1
                continue
            }

            // Standalone image line: `![alt](src)` on its own (charts in reports).
            if let img = imageMatch(trimmed) {
                flushParagraph()
                blocks.append(.image(alt: img.alt, src: img.src))
                i += 1
                continue
            }

            // GFM table: a header row containing `|` immediately followed by a
            // separator row like `|---|:--:|---|`.
            if trimmed.contains("|"), i + 1 < lines.count,
               isTableSeparator(lines[i + 1].trimmingCharacters(in: .whitespaces)) {
                flushParagraph()
                let headers = parseTableRow(trimmed)
                i += 2 // consume header + separator
                var rows: [[String]] = []
                while i < lines.count {
                    let t = lines[i].trimmingCharacters(in: .whitespaces)
                    guard !t.isEmpty, t.contains("|") else { break }
                    var cells = parseTableRow(t)
                    // Pad/truncate so every row matches the header column count.
                    if cells.count < headers.count {
                        cells.append(contentsOf:
                            Array(repeating: "", count: headers.count - cells.count))
                    } else if cells.count > headers.count {
                        cells = Array(cells.prefix(headers.count))
                    }
                    rows.append(cells)
                    i += 1
                }
                blocks.append(.table(headers: headers, rows: rows))
                continue
            }

            if trimmed.hasPrefix(">") {
                flushParagraph()
                var quoted: [String] = []
                while i < lines.count {
                    let t = lines[i].trimmingCharacters(in: .whitespaces)
                    guard t.hasPrefix(">") else { break }
                    quoted.append(String(t.dropFirst()).trimmingCharacters(in: .whitespaces))
                    i += 1
                }
                blocks.append(.quote(quoted))
                continue
            }

            if isBullet(trimmed) {
                flushParagraph()
                var items: [String] = []
                while i < lines.count {
                    let t = lines[i].trimmingCharacters(in: .whitespaces)
                    guard isBullet(t) else { break }
                    items.append(String(t.dropFirst(2)).trimmingCharacters(in: .whitespaces))
                    i += 1
                }
                blocks.append(.bullets(items))
                continue
            }

            if orderedMatch(trimmed) != nil {
                flushParagraph()
                var items: [(marker: String, text: String)] = []
                while i < lines.count {
                    let t = lines[i].trimmingCharacters(in: .whitespaces)
                    guard let match = orderedMatch(t) else { break }
                    items.append(match)
                    i += 1
                }
                blocks.append(.ordered(items))
                continue
            }

            paragraph.append(trimmed)
            i += 1
        }
        flushParagraph()
        return blocks
    }

    /// Parse a line that is exactly a Markdown image: `![alt](src)`.
    private static func imageMatch(_ s: String) -> (alt: String, src: String)? {
        guard s.hasPrefix("!["), s.hasSuffix(")"),
              let altEnd = s.range(of: "]("), altEnd.upperBound < s.endIndex else { return nil }
        let alt = String(s[s.index(s.startIndex, offsetBy: 2)..<altEnd.lowerBound])
        let src = String(s[altEnd.upperBound..<s.index(before: s.endIndex)])
            .trimmingCharacters(in: .whitespaces)
        guard !src.isEmpty else { return nil }
        return (alt, src)
    }

    private static func headingMatch(_ s: String) -> (level: Int, text: String)? {
        var level = 0
        var idx = s.startIndex
        while idx < s.endIndex, s[idx] == "#", level < 6 {
            level += 1
            idx = s.index(after: idx)
        }
        guard level > 0, idx < s.endIndex, s[idx] == " " else { return nil }
        return (level, String(s[idx...]).trimmingCharacters(in: .whitespaces))
    }

    /// A GFM table separator row: every cell is made of `-`/`:`/spaces and
    /// contains at least one `-` (e.g. `|---|:--:|`).
    private static func isTableSeparator(_ s: String) -> Bool {
        guard s.contains("-"), s.contains("|") else { return false }
        let cells = splitTableCells(s)
        guard !cells.isEmpty else { return false }
        for cell in cells {
            let t = cell.trimmingCharacters(in: .whitespaces)
            guard !t.isEmpty, t.contains("-") else { return false }
            if t.contains(where: { $0 != "-" && $0 != ":" }) { return false }
        }
        return true
    }

    private static func parseTableRow(_ s: String) -> [String] {
        splitTableCells(s).map { $0.trimmingCharacters(in: .whitespaces) }
    }

    private static func splitTableCells(_ s: String) -> [String] {
        var t = s.trimmingCharacters(in: .whitespaces)
        if t.hasPrefix("|") { t.removeFirst() }
        if t.hasSuffix("|") { t.removeLast() }
        return t.components(separatedBy: "|")
    }

    private static func isBullet(_ s: String) -> Bool {
        s.hasPrefix("- ") || s.hasPrefix("* ") || s.hasPrefix("+ ")
    }

    private static func orderedMatch(_ s: String) -> (marker: String, text: String)? {
        var digits = ""
        var idx = s.startIndex
        while idx < s.endIndex, s[idx].isNumber {
            digits.append(s[idx])
            idx = s.index(after: idx)
        }
        guard !digits.isEmpty, idx < s.endIndex else { return nil }
        guard s[idx] == "." || s[idx] == ")" else { return nil }
        idx = s.index(after: idx)
        guard idx < s.endIndex, s[idx] == " " else { return nil }
        return (digits, String(s[idx...]).trimmingCharacters(in: .whitespaces))
    }
}

// MARK: - Image block

/// Renders a Markdown image. Charts embedded in reports arrive as self-contained
/// `data:` URIs (decoded locally); a server path falls back to an authed fetch.
/// Absolute http(s) URLs are loaded WITHOUT credentials unless they point at the
/// configured API host. Decoded images are cached so row recycling is free.
private struct MarkdownImageView: View {
    let alt: String
    let src: String
    @State private var image: UIImage?
    @State private var failed = false
    @State private var showFullScreen = false

    init(alt: String, src: String) {
        self.alt = alt
        self.src = src
        _image = State(initialValue: ChatImageCache.image(for: Self.cacheKey(src)))
    }

    private static func cacheKey(_ src: String) -> String {
        src.hasPrefix("data:") ? "md-data:\(src.count):\(src.hashValue)" : "md:\(src)"
    }

    var body: some View {
        Group {
            if let image {
                VStack(alignment: .leading, spacing: 4) {
                    Image(uiImage: image)
                        .resizable()
                        .scaledToFit()
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
                        // Charts embedded in reports/chat are often dense — let
                        // the user tap to open a full-screen, pinch-zoomable view.
                        .contentShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
                        .onTapGesture { showFullScreen = true }
                        .fullScreenCover(isPresented: $showFullScreen) {
                            FullScreenImageViewer(image: image)
                        }
                        .accessibilityAddTraits(.isButton)
                        .accessibilityLabel(alt.isEmpty ? "Chart. Double-tap to enlarge."
                                                         : "\(alt). Double-tap to enlarge.")
                    if !alt.isEmpty {
                        Text(alt)
                            .font(.caption2)
                            .foregroundColor(.secondary)
                    }
                }
            } else if failed {
                EmptyView()
            } else {
                RoundedRectangle(cornerRadius: 10, style: .continuous)
                    .fill(Color.primary.opacity(0.05))
                    .frame(height: 160)
                    .overlay(ProgressView())
            }
        }
        .task(id: src) { await load() }
    }

    private func load() async {
        let key = Self.cacheKey(src)
        if let cached = ChatImageCache.image(for: key) {
            image = cached
            return
        }
        let loaded: UIImage?
        if src.hasPrefix("data:") {
            loaded = await ChatImageCache.decodeDataURI(src)
        } else {
            let urlString = src.hasPrefix("http") ? src : "\(ServerConfig.load().apiBaseURL)\(src)"
            guard let url = URL(string: urlString) else { failed = true; return }
            // Only the API server may see the bearer token; any other absolute
            // URL (agent-authored markdown) is fetched anonymously.
            loaded = await ChatImageCache.fetch(url, authed: ChatImageCache.isAPIHost(url))
        }
        if let loaded {
            ChatImageCache.store(loaded, for: key)
            image = loaded
        } else {
            failed = true
        }
    }
}
