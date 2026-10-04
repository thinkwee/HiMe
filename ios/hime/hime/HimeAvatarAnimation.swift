//
//  HimeAvatarAnimation.swift
//  hime
//
//  What the agent is doing, as the little pixel cat shows it: a small
//  activity enum, a pure resolver from the chat state, and the animated
//  `HimeAvatar` body. Reuses `CatHeadView`'s pixel art (the cat stays the
//  cat); motion is a handful of quantized, subtle loops — blink, bob, ear
//  flick, eye scan, mouth chatter — plus tiny pixel props (thought dots,
//  sparkle, zzz, "!") drawn on the same 40-cell grid.
//

import SwiftUI

// MARK: - Activity

/// The agent's visible state, mapped from the chat view model's live state.
enum AvatarActivity: Equatable {
    case idle        // nothing going on
    case thinking    // reasoning / waiting for the LLM
    case working     // running a tool or sub-analysis
    case replying    // streaming the answer
    case waiting     // agent starting up / reconnecting
    case sleeping    // idle late at night
    case error       // a send / server error is showing

    /// Mood string understood by `CatHeadView`.
    var catMood: String {
        switch self {
        case .idle, .replying: return "relaxed"
        case .thinking, .working: return "focused"
        case .waiting: return "tired"
        case .sleeping: return "sleepy"
        case .error: return "stressed"
        }
    }
}

extension LiveState {
    /// Activity of the in-flight run, derived from the live turn's state.
    var avatarActivity: AvatarActivity {
        guard active else { return .idle }
        if tool == "reply_user" || replying { return .replying }
        if let tool, !tool.isEmpty { return .working }
        return .thinking
    }
}

enum AvatarResolver {
    /// Layers the out-of-band conditions over the run's own activity:
    /// error > waiting > run activity > sleeping (idle at night) > idle.
    static func resolve(run: AvatarActivity, agentStarting: Bool,
                        reconnecting: Bool, hasError: Bool,
                        now: Date = Date()) -> AvatarActivity {
        if hasError { return .error }
        if agentStarting || reconnecting { return .waiting }
        if run != .idle { return run }
        let hour = Calendar.current.component(.hour, from: now)
        return (hour >= 23 || hour < 6) ? .sleeping : .idle
    }
}

/// Header avatar for the chat screen. Observes only `LiveState`, so token-rate
/// updates re-render this 30pt view and nothing else.
struct ChatNavAvatar: View {
    @ObservedObject var live: LiveState
    var agentStarting: Bool
    var reconnecting: Bool
    var hasError: Bool

    var body: some View {
        HimeAvatar(size: 30,
                   activity: AvatarResolver.resolve(run: live.avatarActivity,
                                                    agentStarting: agentStarting,
                                                    reconnecting: reconnecting,
                                                    hasError: hasError))
    }
}

// MARK: - Frame

/// One animation frame: overrides for `CatHeadView` plus body offset (in
/// head pixels) and which prop step to show.
struct AvatarFrame {
    var blink = false
    var mouthOpen = false
    var earLift = 0
    var lookX = 0
    var dx = 0
    var dy = 0
    var propPhase = 0

    /// True for `width` seconds at the start of every `period`.
    private static func pulse(_ t: Double, _ period: Double, _ width: Double) -> Bool {
        t.truncatingRemainder(dividingBy: period) < width
    }

    private static func bob(_ t: Double, _ period: Double, _ amp: Double) -> Int {
        Int((sin(t * 2 * Double.pi / period) * amp).rounded())
    }

    static func make(_ activity: AvatarActivity, t: Double) -> AvatarFrame {
        var f = AvatarFrame()
        let scan = [0, -1, 0, 1]
        switch activity {
        case .idle:
            f.blink = pulse(t, 3.7, 0.14)
            f.earLift = pulse(t + 1.3, 6.1, 0.18) ? 2 : 0
            f.dy = bob(t, 3.2, 0.7)
        case .thinking:
            f.blink = pulse(t, 4.3, 0.14)
            f.lookX = scan[Int(t / 0.9) % 4]
            f.dy = bob(t, 3.0, 0.6)
            f.propPhase = Int(t / 0.45) % 4
        case .working:
            f.lookX = scan[Int(t / 0.3) % 4]
            f.dy = bob(t, 0.8, 0.8)
            f.earLift = pulse(t, 1.6, 0.2) ? 1 : 0
            f.propPhase = Int(t / 0.25) % 2
        case .replying:
            f.blink = pulse(t, 3.4, 0.14)
            f.mouthOpen = (Int(t / 0.16) % 4) < 2
            f.dy = bob(t, 1.2, 0.8)
        case .waiting:
            f.blink = pulse(t, 5.0, 0.5)
            f.dy = bob(t, 4.0, 0.5)
            f.propPhase = Int(t / 0.6) % 3
        case .sleeping:
            f.dy = bob(t, 4.5, 0.7)
            f.propPhase = Int(t / 0.8) % 4
        case .error:
            if pulse(t, 3.0, 0.45) { f.dx = (Int(t / 0.06) % 2 == 0) ? 1 : -1 }
        }
        return f
    }

    /// Reduce Motion / frozen frame: eyes open, a representative prop step.
    static func still(_ activity: AvatarActivity) -> AvatarFrame {
        var f = AvatarFrame()
        switch activity {
        case .thinking: f.propPhase = 3
        case .sleeping: f.propPhase = 2
        default: break
        }
        return f
    }
}

// MARK: - Props

/// Tiny pixel props in the avatar's top-right (sweat drop top-left), drawn on
/// the same 40-cell grid as the cat head so they scale with it.
private struct AvatarProps: View {
    let activity: AvatarActivity
    let phase: Int

    private let cK = Color(red: 0.42, green: 0.35, blue: 0.28)
    private let cSpark = Color(red: 0.98, green: 0.72, blue: 0.20)
    private let cZ = Color(red: 0.50, green: 0.62, blue: 0.85)
    private let cErr = Color(red: 0.90, green: 0.30, blue: 0.30)
    private let cDrop = Color(red: 0.50, green: 0.72, blue: 0.96)

    var body: some View {
        Canvas { ctx, size in
            let ps = size.width / 40
            func px(_ c: Int, _ r: Int, _ w: Int, _ h: Int, _ col: Color) {
                ctx.fill(Path(CGRect(x: CGFloat(c) * ps, y: CGFloat(r) * ps,
                                     width: CGFloat(w) * ps + 0.5, height: CGFloat(h) * ps + 0.5)),
                         with: .color(col))
            }
            func zed(_ c: Int, _ r: Int, _ w: Int) {
                px(c, r, w, 2, cZ)
                px(c + w / 2 - 1, r + 2, 2, 2, cZ)
                px(c, r + 4, w, 2, cZ)
            }

            switch activity {
            case .thinking:
                let spots: [(Int, Int)] = [(31, 9), (34, 5), (37, 1)]
                for i in 0..<min(phase, spots.count) {
                    px(spots[i].0, spots[i].1, 3, 3, cK)
                }
            case .working:
                px(35, 4, 2, 2, cSpark)
                if phase == 0 {
                    px(35, 2, 2, 2, cSpark); px(35, 6, 2, 2, cSpark)
                    px(33, 4, 2, 2, cSpark); px(37, 4, 2, 2, cSpark)
                }
            case .waiting:
                for i in 0..<3 {
                    px(31 + i * 3, 8, 2, 2, cK.opacity(i == phase ? 0.9 : 0.35))
                }
            case .sleeping:
                if phase == 1 || phase == 2 { zed(33, 9, 5) }
                if phase >= 2 { zed(34, 1, 6) }
            case .error:
                px(36, 1, 3, 6, cErr)
                px(36, 8, 3, 3, cErr)
                px(4, 8, 2, 2, cDrop)
                px(3, 10, 4, 3, cDrop)
            case .idle, .replying:
                break
            }
        }
    }
}

// MARK: - Avatar

/// The agent's pixel-cat avatar. With `activity == nil` it is a static head
/// showing the `state` mood (older bubbles, list rows). Pass an activity to
/// animate; only a couple of these should be on screen at once.
struct HimeAvatar: View {
    var size: CGFloat = 28
    var state: String = "relaxed"
    var activity: AvatarActivity?

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.scenePhase) private var scenePhase

    /// `CatHeadView` draws a ~19x21 head inside a 40x40 grid; zoom in so the
    /// head fills the frame (leaving the corners free for props).
    private static let headScale: CGFloat = 1.4
    /// Grid rows to move the zoomed head down so it sits centred.
    private static let headShift: CGFloat = 3.2

    var body: some View {
        Group {
            if let activity {
                if reduceMotion {
                    art(mood: activity.catMood, frame: AvatarFrame.still(activity), props: activity)
                } else {
                    TimelineView(.animation(minimumInterval: 1.0 / 12.0,
                                            paused: scenePhase != .active)) { ctx in
                        art(mood: activity.catMood,
                            frame: AvatarFrame.make(activity,
                                                    t: ctx.date.timeIntervalSinceReferenceDate),
                            props: activity)
                    }
                }
            } else {
                art(mood: state, frame: AvatarFrame(), props: nil)
            }
        }
        .frame(width: size, height: size)
        .accessibilityHidden(true)
    }

    private func art(mood: String, frame: AvatarFrame, props: AvatarActivity?) -> some View {
        let unit = size / 40
        return ZStack {
            CatHeadView(catState: mood, blink: frame.blink, mouthOpen: frame.mouthOpen,
                        earLift: frame.earLift, lookX: frame.lookX)
                .frame(width: size, height: size)
                .scaleEffect(Self.headScale)
                .offset(x: CGFloat(frame.dx) * unit * Self.headScale,
                        y: (Self.headShift + CGFloat(frame.dy) * Self.headScale) * unit)
            if let props {
                AvatarProps(activity: props, phase: frame.propPhase)
                    .frame(width: size, height: size)
            }
        }
        .frame(width: size, height: size)
    }
}
