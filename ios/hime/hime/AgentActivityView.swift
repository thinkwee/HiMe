//
//  AgentActivityView.swift
//  hime
//
//  The lively "what is Hime doing right now" status pill shown beneath the
//  conversation while the agent works — a pulsing, tool-aware indicator in the
//  spirit of Claude Code's status line. Reflects the live agent event stream:
//  thinking, or running a specific tool (each with its own friendly icon).
//

import SwiftUI

struct AgentActivityView: View {
    let activity: AgentActivity

    var body: some View {
        HStack {
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 8) {
                    PulsingIcon(systemName: presentation.icon)
                    Text(presentation.label)
                        .font(.callout)
                        .foregroundColor(.secondary)
                    TypingDots()
                }
                if let preview = presentation.preview {
                    Text(preview)
                        .font(.caption2)
                        .foregroundColor(Color(.tertiaryLabel))
                        .lineLimit(3)
                        .fixedSize(horizontal: false, vertical: true)
                        .multilineTextAlignment(.leading)
                }
            }
            .padding(.horizontal, 13)
            .padding(.vertical, 9)
            .background(
                RoundedRectangle(cornerRadius: 18, style: .continuous)
                    .fill(Color(.systemGray6))
            )
            .overlay(
                RoundedRectangle(cornerRadius: 18, style: .continuous)
                    .stroke(Color.himeAccent.opacity(0.18), lineWidth: 1)
            )
            Spacer(minLength: 40)
        }
    }

    private var presentation: (icon: String, label: LocalizedStringKey, preview: String?) {
        switch activity {
        case .idle:
            return ("sparkles", "Hime is thinking", nil)
        case let .thinking(preview):
            let trimmed = preview.trimmingCharacters(in: .whitespaces)
            return ("sparkles", "Hime is thinking", trimmed.isEmpty ? nil : trimmed)
        case let .tool(name):
            let p = Self.toolPresentation(name)
            return (p.icon, p.label, nil)
        }
    }

    /// Friendly icon + label for each agent tool. Unknown tools fall back to a
    /// generic "working" pill so new backend tools still look intentional.
    static func toolPresentation(_ name: String) -> (icon: String, label: LocalizedStringKey) {
        switch name {
        case "reply_user":
            return ("paperplane.fill", "Writing a reply")
        case "analyze":
            return ("chart.xyaxis.line", "Looking at your data")
        case "manage":
            return ("slider.horizontal.3", "Tidying things up")
        case "sql":
            return ("cylinder.split.1x2", "Querying your records")
        case "push_report", "report":
            return ("doc.text.fill", "Putting together a report")
        case "search", "recall", "memory", "retrieve":
            return ("magnifyingglass", "Searching memory")
        case "web", "browse", "fetch":
            return ("globe", "Looking it up")
        default:
            return ("wrench.and.screwdriver.fill", "Working on it")
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
            .foregroundStyle(Color.himeAccent)
            .scaleEffect(animate ? 1.12 : 0.9)
            .opacity(animate ? 1.0 : 0.65)
            .animation(.easeInOut(duration: 0.7).repeatForever(autoreverses: true), value: animate)
            .onAppear { animate = true }
            // Re-trigger the pulse cleanly when the icon swaps (tool → tool).
            .id(systemName)
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
                        .fill(Color.himeAccent.opacity(0.85))
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
