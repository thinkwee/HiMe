//
//  GoalSurveyView.swift
//  hime
//
//  Adaptive onboarding goal survey — no LLM, just records. A short branching
//  questionnaire: the primary-focus answer decides which follow-up question
//  appears next, so different users are profiled along different axes. The
//  captured goals + structured answers are POSTed to the backend, where a
//  deferred agent run later turns them into a personalised plan.
//

import SwiftUI
import Combine   // ObservableObject / @Published — required explicitly under the
                 // MemberImportVisibility upcoming feature (SwiftUI no longer re-exports it).

private let kAccent = Color(red: 0.95, green: 0.70, blue: 0.35)

// MARK: - Question model

struct SurveyOption: Identifiable {
    let id: String              // stable key, persisted in answers
    let english: String         // sent to the backend (the agent reasons in English)
    let label: LocalizedStringKey
    let icon: String
}

struct SurveyQuestion: Identifiable {
    let id: String              // answer key, e.g. "focus"
    let englishTitle: String    // English question text for the backend payload
    let title: LocalizedStringKey
    let multi: Bool             // multi-select (optional) vs single-select (required)
    let options: [SurveyOption]
}

// MARK: - Survey state machine

@MainActor
final class SurveyModel: ObservableObject {
    /// Index into the (dynamically computed) `flow`.
    @Published var step = 0
    /// answerKey -> selected option ids (a single-select question holds one).
    @Published var answers: [String: Set<String>] = [:]

    // -- Catalogue --------------------------------------------------------

    private let focusQuestion = SurveyQuestion(
        id: "focus", englishTitle: "Primary focus", title: "What would you like to focus on first?",
        multi: false, options: [
            .init(id: "sleep",     english: "Improve sleep",                    label: "Improve sleep",            icon: "moon.zzz.fill"),
            .init(id: "fitness",   english: "Get fitter and move more",         label: "Get fitter & move more",   icon: "figure.run"),
            .init(id: "stress",    english: "Manage stress and mood",           label: "Manage stress & mood",     icon: "leaf.fill"),
            .init(id: "energy",    english: "Have more energy",                 label: "Have more energy",         icon: "bolt.fill"),
            .init(id: "weight",    english: "Manage weight and body composition", label: "Weight & body shape",    icon: "scalemass.fill"),
            .init(id: "longevity", english: "Heart and long-term health",       label: "Heart & long-term health", icon: "heart.fill"),
        ])

    private lazy var branchQuestions: [String: SurveyQuestion] = [
        "sleep": SurveyQuestion(
            id: "sleep_issue", englishTitle: "Biggest sleep struggle", title: "What's your biggest sleep struggle?",
            multi: false, options: [
                .init(id: "onset",       english: "Hard to fall asleep",        label: "Hard to fall asleep",      icon: "hourglass"),
                .init(id: "maintenance", english: "Waking up during the night", label: "Waking up at night",       icon: "eye"),
                .init(id: "duration",    english: "Not getting enough sleep",   label: "Not enough sleep",         icon: "clock"),
                .init(id: "schedule",    english: "Irregular sleep schedule",   label: "Irregular schedule",       icon: "calendar.badge.clock"),
                .init(id: "daytime",     english: "Still tired during the day", label: "Tired during the day",     icon: "zzz"),
            ]),
        "fitness": SurveyQuestion(
            id: "fitness_level", englishTitle: "Current activity level", title: "How active are you right now?",
            multi: false, options: [
                .init(id: "sedentary",  english: "Rarely active",               label: "Rarely active",           icon: "tortoise.fill"),
                .init(id: "occasional", english: "Occasionally active",         label: "Occasionally active",      icon: "figure.walk"),
                .init(id: "regular",    english: "Regular, want to level up",   label: "Regular, want to progress",icon: "figure.run"),
                .init(id: "athlete",    english: "High-intensity or training",  label: "High-intensity / training",icon: "trophy.fill"),
            ]),
        "stress": SurveyQuestion(
            id: "stress_when", englishTitle: "When stress is hardest", title: "When is stress hardest for you?",
            multi: false, options: [
                .init(id: "work",          english: "During work or study",     label: "During work / study",      icon: "desktopcomputer"),
                .init(id: "evening",       english: "Can't unwind at night",    label: "Can't unwind at night",    icon: "moon.fill"),
                .init(id: "constant",      english: "It's fairly constant",     label: "Fairly constant",          icon: "infinity"),
                .init(id: "affects_sleep", english: "It affects my sleep",      label: "It affects my sleep",      icon: "bed.double.fill"),
            ]),
        "energy": SurveyQuestion(
            id: "energy_dip", englishTitle: "When energy dips", title: "When do you feel most drained?",
            multi: false, options: [
                .init(id: "morning",   english: "Mornings are hard",            label: "Mornings are hard",        icon: "sunrise.fill"),
                .init(id: "afternoon", english: "Afternoon slump",              label: "Afternoon slump",          icon: "sun.max.fill"),
                .init(id: "evening",   english: "Burned out by evening",        label: "Burned out by evening",    icon: "sunset.fill"),
                .init(id: "allday",    english: "Low energy all day",           label: "Low all day",              icon: "battery.25"),
            ]),
        "weight": SurveyQuestion(
            id: "weight_goal", englishTitle: "Body goal", title: "What's your body goal?",
            multi: false, options: [
                .init(id: "lose",     english: "Lose fat",                      label: "Lose fat",                 icon: "arrow.down.circle.fill"),
                .init(id: "gain",     english: "Build muscle",                  label: "Build muscle",             icon: "dumbbell.fill"),
                .init(id: "maintain", english: "Maintain and tone",             label: "Maintain & tone",          icon: "equal.circle.fill"),
            ]),
        "longevity": SurveyQuestion(
            id: "longevity_focus", englishTitle: "Most important signal", title: "Which signal matters most to you?",
            multi: false, options: [
                .init(id: "resting_hr", english: "Resting heart rate",          label: "Resting heart rate",       icon: "heart.fill"),
                .init(id: "recovery",   english: "HRV and recovery",            label: "HRV & recovery",           icon: "waveform.path.ecg"),
                .init(id: "endurance",  english: "Endurance and VO2max",        label: "Endurance / VO₂max",       icon: "lungs.fill"),
                .init(id: "monitoring", english: "Family history — keep watch", label: "Family history — monitor", icon: "shield.lefthalf.filled"),
            ]),
    ]

    private let cadenceQuestion = SurveyQuestion(
        id: "cadence", englishTitle: "Preferred check-in cadence", title: "How often should HiMe check in with you?",
        multi: false, options: [
            .init(id: "daily",           english: "Every day",          label: "Every day",          icon: "sun.max"),
            .init(id: "morning_evening", english: "Morning and evening",label: "Morning & evening",  icon: "clock.arrow.circlepath"),
            .init(id: "weekly",          english: "A weekly summary",   label: "Weekly summary",     icon: "calendar"),
            .init(id: "auto",            english: "Let HiMe decide",    label: "Let HiMe decide",    icon: "sparkles"),
        ])

    private let extrasQuestion = SurveyQuestion(
        id: "extras", englishTitle: "Also watch", title: "Anything else you'd like HiMe to watch? (optional)",
        multi: true, options: [
            .init(id: "diet",      english: "Diet and nutrition", label: "Diet & nutrition", icon: "fork.knife"),
            .init(id: "hydration", english: "Hydration",          label: "Hydration",        icon: "drop.fill"),
            .init(id: "sitting",   english: "Too much sitting",   label: "Too much sitting", icon: "chair.fill"),
            .init(id: "mood",      english: "Mood and wellbeing", label: "Mood & wellbeing", icon: "face.smiling"),
            .init(id: "cycle",     english: "Menstrual health",   label: "Menstrual health", icon: "heart.text.square"),
            .init(id: "breathing", english: "Breathing and mindfulness", label: "Breathing & mindfulness", icon: "wind"),
        ])

    // -- Branching flow ---------------------------------------------------

    /// The ordered questions for the current answers. The follow-up at index 1
    /// depends on the chosen primary focus — that's the branch.
    var flow: [SurveyQuestion] {
        var f = [focusQuestion]
        if let focus = answers["focus"]?.first, let branch = branchQuestions[focus] {
            f.append(branch)
        }
        f.append(cadenceQuestion)
        f.append(extrasQuestion)
        return f
    }

    /// Stable display total (focus + branch + cadence + extras). The branch is
    /// required, so by the time it isn't shown the user simply hasn't picked a
    /// focus yet — showing 4 keeps the progress bar from jumping.
    var displayTotal: Int { 4 }

    var current: SurveyQuestion? { flow.indices.contains(step) ? flow[step] : nil }
    var isLast: Bool { step >= flow.count - 1 }
    var atStart: Bool { step == 0 }

    var canProceed: Bool {
        guard let q = current else { return false }
        if q.multi { return true }                       // optional
        return !(answers[q.id]?.isEmpty ?? true)         // single requires a pick
    }

    var hasAnyAnswer: Bool { answers.values.contains { !$0.isEmpty } }

    func isSelected(_ q: SurveyQuestion, _ optionId: String) -> Bool {
        answers[q.id]?.contains(optionId) ?? false
    }

    func toggle(_ q: SurveyQuestion, _ optionId: String) {
        var sel = answers[q.id] ?? []
        if q.multi {
            if sel.contains(optionId) { sel.remove(optionId) } else { sel.insert(optionId) }
        } else {
            sel = [optionId]                              // single-select replaces
        }
        answers[q.id] = sel
    }

    func back() { if step > 0 { step -= 1 } }
    func next() { if !isLast { step += 1 } }

    /// Build the backend payload: `goals` is the headline (focus + extras),
    /// `answers` is the full structured profile keyed by English question text.
    func payload() -> (goals: [String], answers: [String: Any]) {
        var answerDict: [String: Any] = [:]
        var goals: [String] = []
        for q in flow {
            let sel = answers[q.id] ?? []
            let chosen = q.options.filter { sel.contains($0.id) }
            if q.multi {
                answerDict[q.englishTitle] = chosen.map { $0.english }
            } else {
                answerDict[q.englishTitle] = chosen.first?.english ?? ""
            }
            if q.id == "focus", let f = chosen.first { goals.append(f.english) }
            if q.id == "extras" { goals.append(contentsOf: chosen.map { $0.english }) }
        }
        return (goals, answerDict)
    }
}

// MARK: - View

struct GoalSurveyView: View {
    @ObservedObject var model: SurveyModel

    var body: some View {
        VStack(spacing: 16) {
            // Header: back affordance + progress
            HStack {
                if !model.atStart {
                    Button {
                        withAnimation(.easeInOut(duration: 0.2)) { model.back() }
                    } label: {
                        Image(systemName: "chevron.left")
                            .font(.system(size: 15, weight: .semibold))
                            .foregroundColor(kAccent)
                    }
                } else {
                    Color.clear.frame(width: 16, height: 16)
                }
                Spacer()
                Text("\(min(model.step + 1, model.displayTotal)) / \(model.displayTotal)")
                    .font(.caption.weight(.medium))
                    .foregroundColor(.secondary)
            }
            .padding(.horizontal, 24)
            .padding(.top, 8)

            // Slim progress bar
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    Capsule().fill(Color.gray.opacity(0.15))
                    Capsule().fill(kAccent)
                        .frame(width: geo.size.width * CGFloat(model.step + 1) / CGFloat(model.displayTotal))
                }
            }
            .frame(height: 4)
            .padding(.horizontal, 24)

            if let q = model.current {
                Text(q.title)
                    .font(.title3.weight(.bold))
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, 28)
                    .padding(.top, 4)
                if q.multi {
                    Text("Choose any that apply — or skip.")
                        .font(.footnote)
                        .foregroundColor(.secondary)
                }

                ScrollView {
                    VStack(spacing: 10) {
                        ForEach(q.options) { opt in
                            optionRow(q, opt)
                        }
                    }
                    .padding(.horizontal, 24)
                    .padding(.top, 4)
                    .padding(.bottom, 12)
                    // Re-key the list so a branch swap animates as fresh content
                    // rather than reshuffling the previous question's rows.
                    .id(q.id)
                }
            }
        }
        .padding(.top, 12)
        .animation(.easeInOut(duration: 0.2), value: model.step)
    }

    private func optionRow(_ q: SurveyQuestion, _ opt: SurveyOption) -> some View {
        let selected = model.isSelected(q, opt.id)
        return Button {
            withAnimation(.easeInOut(duration: 0.15)) { model.toggle(q, opt.id) }
        } label: {
            HStack(spacing: 12) {
                Image(systemName: opt.icon)
                    .font(.system(size: 16))
                    .foregroundColor(selected ? .white : kAccent)
                    .frame(width: 30, height: 30)
                    .background(Circle().fill(selected ? kAccent : kAccent.opacity(0.12)))
                Text(opt.label)
                    .font(.subheadline.weight(.medium))
                    .foregroundColor(.primary)
                    .multilineTextAlignment(.leading)
                Spacer()
                Image(systemName: selectionGlyph(q, selected))
                    .font(.system(size: 18))
                    .foregroundColor(selected ? kAccent : .secondary.opacity(0.4))
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 12)
            .background(Color(.secondarySystemGroupedBackground))
            .overlay(
                RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .stroke(selected ? kAccent.opacity(0.5) : Color.clear, lineWidth: 1.5)
            )
            .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
        }
        .buttonStyle(.plain)
    }

    /// Multi-select reads as checkboxes, single-select as radio dots.
    private func selectionGlyph(_ q: SurveyQuestion, _ selected: Bool) -> String {
        if q.multi { return selected ? "checkmark.square.fill" : "square" }
        return selected ? "checkmark.circle.fill" : "circle"
    }
}
