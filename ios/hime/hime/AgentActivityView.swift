//
//  AgentActivityView.swift
//  hime
//
//  How the chat shows what Hime is doing, in two pieces:
//
//  * `LiveBubble` — the ONE live-status bubble at the bottom of the
//    conversation. While the agent works it shows the latest thought (muted,
//    italic), the current step in plain words and typing dots; once reply text
//    starts streaming it becomes that reply (with a pulsing caret). It observes
//    only `LiveState`, so token-rate updates never re-render the message list.
//  * `RunCardView` — the collapsible "steps" card left in the timeline, one row
//    per tool call (symbol + plain verb + short object + status glyph).
//

import SwiftUI

// MARK: - Plain-language tool vocabulary

/// Friendly icon + verbs for each agent tool. Unknown tools fall back to a
/// generic "working" wording so new backend tools still look intentional.
enum ChatStepStyle {
    struct Info {
        let icon: String
        /// Present tense, shown while the step runs ("Running an analysis").
        let running: LocalizedStringKey
        /// Past tense, shown once it finished ("Ran an analysis").
        let done: LocalizedStringKey
    }

    static func info(_ tool: String) -> Info {
        switch tool {
        case "reply_user":
            return Info(icon: "paperplane.fill", running: "Writing a reply", done: "Wrote a reply")
        case "finish_chat":
            return Info(icon: "checkmark.bubble", running: "Wrapping up", done: "Wrapped up")
        case "analyze":
            return Info(icon: "chart.xyaxis.line", running: "Looking at your data", done: "Looked at your data")
        case "manage":
            return Info(icon: "slider.horizontal.3", running: "Making an update", done: "Made an update")
        case "sql":
            return Info(icon: "cylinder.split.1x2", running: "Checking your records", done: "Checked your records")
        case "code":
            return Info(icon: "function", running: "Running an analysis", done: "Ran an analysis")
        case "read_skill":
            return Info(icon: "book", running: "Reading a playbook", done: "Read a playbook")
        case "push_report":
            return Info(icon: "doc.text.fill", running: "Writing a report", done: "Wrote a report")
        case "update_md":
            return Info(icon: "note.text", running: "Saving a note", done: "Saved a note")
        case "create_page":
            return Info(icon: "rectangle.on.rectangle.angled", running: "Building a page", done: "Built a page")
        default:
            return Info(icon: "wrench.and.screwdriver.fill", running: "Working on it", done: "Worked on it")
        }
    }
}

/// The asymmetric chat-bubble outline (a small "tail" on the sender's bottom edge).
func chatBubbleShape(isUser: Bool) -> UnevenRoundedRectangle {
    let r = HimeRadius.bubble
    let tail = HimeRadius.bubbleTail
    return UnevenRoundedRectangle(
        topLeadingRadius: r,
        bottomLeadingRadius: isUser ? r : tail,
        bottomTrailingRadius: isUser ? tail : r,
        topTrailingRadius: r,
        style: .continuous)
}

// MARK: - Live bubble

struct LiveBubble: View {
    @ObservedObject var live: LiveState
    /// Show the avatar (false when it directly follows another Hime row).
    let showAvatar: Bool

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            if showAvatar {
                HimeAvatar(size: 28)
            } else {
                Color.clear.frame(width: 28, height: 1)
            }
            content
                .padding(.horizontal, 14)
                .padding(.vertical, 10)
                .background(
                    chatBubbleShape(isUser: false)
                        .fill(HimeColor.assistantBubble)
                        .shadow(color: .black.opacity(0.05), radius: 2, x: 0, y: 1)
                )
                .overlay(chatBubbleShape(isUser: false).stroke(Color.primary.opacity(0.05), lineWidth: 0.5))
            Spacer(minLength: 40)
        }
    }

    @ViewBuilder
    private var content: some View {
        if !live.streamText.isEmpty {
            VStack(alignment: .leading, spacing: 4) {
                MarkdownView(text: live.streamText, foreground: .primary)
                PulsingCaret()
            }
        } else {
            VStack(alignment: .leading, spacing: 6) {
                if !live.thought.isEmpty {
                    Text(live.thought)
                        .font(.footnote)
                        .italic()
                        .foregroundColor(.secondary)
                        .lineLimit(3)
                        .multilineTextAlignment(.leading)
                }
                HStack(spacing: 8) {
                    if let tool = live.tool {
                        let info = ChatStepStyle.info(tool)
                        PulsingIcon(systemName: info.icon)
                        Text(info.running)
                            .font(.callout)
                            .foregroundColor(.secondary)
                    } else {
                        PulsingIcon(systemName: "sparkles")
                        Text("Hime is thinking")
                            .font(.callout)
                            .foregroundColor(.secondary)
                    }
                    TypingDots()
                }
            }
        }
    }
}

// MARK: - Run card

struct RunCardView: View, Equatable {
    let card: RunCard

    /// nil = follow the default (expanded while live, collapsed once done).
    @State private var userExpanded: Bool?
    @State private var openSteps: Set<String> = []

    static func == (lhs: RunCardView, rhs: RunCardView) -> Bool { lhs.card == rhs.card }

    private var expanded: Bool { userExpanded ?? card.isLive }

    private var headerText: String {
        let n = card.steps.count
        if card.isLive {
            switch n {
            case 0: return String(localized: "Hime is working")
            case 1: return String(localized: "Hime is working · \(n) step")
            default: return String(localized: "Hime is working · \(n) steps")
            }
        }
        return n == 1 ? String(localized: "\(n) step") : String(localized: "\(n) steps")
    }

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            Color.clear.frame(width: 28, height: 1)
            VStack(alignment: .leading, spacing: 0) {
                header
                if expanded {
                    Divider().padding(.horizontal, 12)
                    VStack(alignment: .leading, spacing: 2) {
                        ForEach(card.steps) { step in
                            stepRow(step)
                        }
                    }
                    .padding(.horizontal, 12)
                    .padding(.vertical, 8)
                }
            }
            .background(
                RoundedRectangle(cornerRadius: HimeRadius.card, style: .continuous)
                    .fill(HimeColor.card)
            )
            .overlay(
                RoundedRectangle(cornerRadius: HimeRadius.card, style: .continuous)
                    .stroke(HimeColor.line, lineWidth: 0.5)
            )
            Spacer(minLength: 40)
        }
        .animation(.easeInOut(duration: 0.2), value: expanded)
    }

    private var header: some View {
        Button {
            userExpanded = !expanded
        } label: {
            HStack(spacing: 8) {
                if card.isLive {
                    PulsingIcon(systemName: "sparkles")
                } else {
                    Image(systemName: "checklist")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundColor(HimeColor.ink2)
                }
                Text(headerText)
                    .font(.footnote.weight(.medium))
                    .foregroundColor(HimeColor.ink2)
                Spacer(minLength: 8)
                Image(systemName: "chevron.right")
                    .font(.system(size: 10, weight: .semibold))
                    .foregroundColor(HimeColor.ink2.opacity(0.7))
                    .rotationEffect(.degrees(expanded ? 90 : 0))
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 9)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    private func stepRow(_ step: RunStep) -> some View {
        let info = ChatStepStyle.info(step.tool)
        let isOpen = openSteps.contains(step.id)
        let tappable = !(step.preview ?? "").isEmpty
        return Button {
            guard tappable else { return }
            if isOpen { openSteps.remove(step.id) } else { openSteps.insert(step.id) }
        } label: {
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 8) {
                    statusGlyph(step.status)
                    Image(systemName: info.icon)
                        .font(.system(size: 12))
                        .foregroundColor(HimeColor.ink2)
                        .frame(width: 16)
                    Text(step.status == .running ? info.running : info.done)
                        .font(.footnote)
                        .foregroundColor(HimeColor.ink)
                    if let detail = step.detail, !detail.isEmpty {
                        Text(detail)
                            .font(.caption)
                            .foregroundColor(HimeColor.ink2)
                            .lineLimit(1)
                            .truncationMode(.tail)
                    }
                    Spacer(minLength: 0)
                }
                if isOpen, let preview = step.preview {
                    Text(preview)
                        .font(.caption)
                        .foregroundColor(HimeColor.ink2)
                        .lineLimit(8)
                        .multilineTextAlignment(.leading)
                        .padding(.leading, 24)
                }
            }
            .padding(.leading, step.nested ? 16 : 0)
            .padding(.vertical, 3)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    @ViewBuilder
    private func statusGlyph(_ status: RunStep.Status) -> some View {
        switch status {
        case .running:
            ProgressView().scaleEffect(0.55).frame(width: 14, height: 14)
        case .ok:
            Image(systemName: "checkmark.circle.fill")
                .font(.system(size: 13)).foregroundColor(HimeColor.ok).frame(width: 14, height: 14)
        case .failed:
            Image(systemName: "exclamationmark.circle.fill")
                .font(.system(size: 13)).foregroundColor(HimeColor.bad).frame(width: 14, height: 14)
        case .stopped:
            Image(systemName: "stop.circle.fill")
                .font(.system(size: 13)).foregroundColor(HimeColor.ink2).frame(width: 14, height: 14)
        }
    }
}

// MARK: - Pulsing icon

private struct PulsingIcon: View {
    let systemName: String
    @State private var animate = false

    var body: some View {
        Image(systemName: systemName)
            .font(.system(size: 15, weight: .semibold))
            .foregroundStyle(HimeColor.accent)
            .scaleEffect(animate ? 1.12 : 0.9)
            .opacity(animate ? 1.0 : 0.65)
            .animation(.easeInOut(duration: 0.7).repeatForever(autoreverses: true), value: animate)
            .onAppear { animate = true }
            // Re-trigger the pulse cleanly when the icon swaps (tool → tool).
            .id(systemName)
    }
}

// MARK: - Streaming caret

private struct PulsingCaret: View {
    @State private var on = false

    var body: some View {
        Capsule()
            .fill(HimeColor.accent)
            .frame(width: 3, height: 14)
            .opacity(on ? 1 : 0.2)
            .animation(.easeInOut(duration: 0.6).repeatForever(autoreverses: true), value: on)
            .onAppear { on = true }
    }
}

// MARK: - Typing dots

private struct TypingDots: View {
    var body: some View {
        TimelineView(.animation(minimumInterval: 0.1, paused: false)) { context in
            let t = context.date.timeIntervalSinceReferenceDate
            HStack(spacing: 4) {
                ForEach(0..<3, id: \.self) { i in
                    Circle()
                        .fill(HimeColor.accent.opacity(0.85))
                        .frame(width: 5, height: 5)
                        .scaleEffect(scale(t, i))
                        .opacity(opacity(t, i))
                }
            }
        }
    }

    private func wave(_ t: TimeInterval, _ i: Int) -> Double {
        let phase = t * 2.2 - Double(i) * 0.45
        return 0.5 + 0.5 * sin(phase * .pi)
    }

    private func opacity(_ t: TimeInterval, _ i: Int) -> Double {
        0.3 + 0.7 * wave(t, i)
    }

    private func scale(_ t: TimeInterval, _ i: Int) -> Double {
        0.7 + 0.5 * wave(t, i)
    }
}
