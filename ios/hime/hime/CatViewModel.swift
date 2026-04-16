import SwiftUI
import UIKit
import Combine
import WidgetKit

// MARK: - Widget snapshot (App Group bridge to HimeWidgets extension)
//
// JSON-compatible with the `HimeSnapshot` struct duplicated inside the
// HimeWidgets target. They're separate compile units; only the wire
// format must match.

struct HimeWidgetSnapshot: Codable {
    var catStateRaw: String
    var catMessage: String
    var agentRunning: Bool
    var metrics: [HimeWidgetMetric]
    var latestReportTitle: String?
    var latestReportPreview: String?
    var latestReportLevel: String?
}

struct HimeWidgetMetric: Codable {
    let name: String
    let value: String
    let unit: String
}

enum HimeWidgetStore {
    static let appGroup: String = {
        guard let id = Bundle.main.bundleIdentifier,
              let range = id.range(of: ".hime", options: .backwards) else { return "" }
        return "group.\(id[id.startIndex..<range.upperBound])"
    }()
    static let fileName = "widget_snapshot.json"

    static var fileURL: URL? {
        FileManager.default
            .containerURL(forSecurityApplicationGroupIdentifier: appGroup)?
            .appendingPathComponent(fileName)
    }

    static func read() -> HimeWidgetSnapshot {
        guard let url = fileURL,
              let data = try? Data(contentsOf: url),
              let snap = try? JSONDecoder().decode(HimeWidgetSnapshot.self, from: data) else {
            return HimeWidgetSnapshot(
                catStateRaw: "relaxed",
                catMessage: "Tap himeow for a check-in",
                agentRunning: false,
                metrics: []
            )
        }
        return snap
    }

    static func write(_ snap: HimeWidgetSnapshot) {
        guard let url = fileURL else { return }
        guard let data = try? JSONEncoder().encode(snap) else { return }
        try? data.write(to: url, options: .atomic)
        WidgetCenter.shared.reloadAllTimelines()
    }

    static func update(_ mutate: (inout HimeWidgetSnapshot) -> Void) {
        var s = read()
        mutate(&s)
        write(s)
    }
}

// MARK: - Cat State

enum CatState: String, Codable {
    case energetic, tired, stressed, sad, relaxed, curious, happy, focused, sleepy
    case recovering, sick, zen, proud, alert, adventurous

    var emoji: String {
        switch self {
        case .energetic:   return "⚡"
        case .tired:       return "😴"
        case .stressed:    return "😾"
        case .sad:         return "😿"
        case .relaxed:     return "😌"
        case .curious:     return "🧐"
        case .happy:       return "😸"
        case .focused:     return "🎯"
        case .sleepy:      return "💤"
        case .recovering:  return "🌿"
        case .sick:        return "🤒"
        case .zen:         return "🧘"
        case .proud:       return "🏆"
        case .alert:       return "🔔"
        case .adventurous: return "🌍"
        }
    }

    var color: Color {
        switch self {
        case .energetic:   return Color(red: 0.85, green: 0.50, blue: 0.10)
        case .tired:       return Color(red: 0.35, green: 0.30, blue: 0.60)
        case .stressed:    return Color(red: 0.75, green: 0.22, blue: 0.20)
        case .sad:         return Color(red: 0.25, green: 0.40, blue: 0.72)
        case .relaxed:     return Color(red: 0.22, green: 0.55, blue: 0.30)
        case .curious:     return Color(red: 0.15, green: 0.52, blue: 0.58)
        case .happy:       return Color(red: 0.78, green: 0.62, blue: 0.08)
        case .focused:     return Color(red: 0.50, green: 0.28, blue: 0.70)
        case .sleepy:      return Color(red: 0.42, green: 0.38, blue: 0.55)
        case .recovering:  return Color(red: 0.18, green: 0.52, blue: 0.48)
        case .sick:        return Color(red: 0.70, green: 0.32, blue: 0.32)
        case .zen:         return Color(red: 0.62, green: 0.55, blue: 0.25)
        case .proud:       return Color(red: 0.72, green: 0.55, blue: 0.12)
        case .alert:       return Color(red: 0.78, green: 0.48, blue: 0.10)
        case .adventurous: return Color(red: 0.20, green: 0.52, blue: 0.30)
        }
    }

    var propType: PixelPropType {
        switch self {
        case .energetic:   return .dumbbell
        case .tired:       return .pillow
        case .stressed:    return .alarmClock
        case .sad:         return .umbrella
        case .relaxed:     return .teaCup
        case .curious:     return .magnifyingGlass
        case .happy:       return .guitar
        case .focused:     return .laptop
        case .sleepy:      return .blanket
        case .recovering:  return .yogaMat
        case .sick:        return .thermometer
        case .zen:         return .flyingCarpet
        case .proud:       return .rocket
        case .alert:       return .warningSign
        case .adventurous: return .globe
        }
    }

    var revealDuration: Double {
        switch self {
        case .energetic:   return 3.80
        case .tired:       return 4.20
        case .stressed:    return 4.00
        case .sad:         return 4.80
        case .relaxed:     return 4.00
        case .curious:     return 3.80
        case .happy:       return 4.50
        case .focused:     return 3.80
        case .sleepy:      return 4.20
        case .recovering:  return 4.20
        case .sick:        return 4.20
        case .zen:         return 6.80
        case .proud:       return 5.20
        case .alert:       return 3.60
        case .adventurous: return 4.50
        }
    }
}

// MARK: - Cat ViewModel

@MainActor
class CatViewModel: ObservableObject {
    // ── Published state (for UI bindings) ──
    @Published var catState: CatState = .relaxed
    @Published var catMessage: String = "Long press himeow to start quick health analysis"
    @Published var isConnected: Bool = false
    @Published var isAnalyzing: Bool = false
    @Published var agentRunning: Bool = false
    @Published var isTogglingAgent: Bool = false
    @Published var showNya: Bool = false
    @Published var showPurrBubble: Bool = false
    @Published var syncGlow: CGFloat = 0
    @Published var breathingScale: CGFloat = 1.0
    @Published var chatLabel: String = UserDefaults.standard.string(forKey: "chatLabel") ?? "Chat"
    @Published var chatPlatform: String = UserDefaults.standard.string(forKey: "chatPlatform") ?? "none"
    @Published var showNoChatAlert: Bool = false
    @Published var glowPhase: CGFloat = 0.95

    /// Incremented each tick to trigger view re-render
    @Published var animFrame: UInt = 0

    /// Current costume overlay (hat, cape, etc.) for state-specific dress-up
    @Published var costume: CatCostume = .none

    // ── Spring-driven pose (read on re-render) ──
    var catX = SpringValue(current: 22, target: 22, stiffness: 120, damping: 14)
    var catY = SpringValue(current: 34, target: 34, stiffness: 120, damping: 14)
    var headX = SpringValue()
    var headY = SpringValue()
    var bodySquash = SpringValue()
    var leftPawY = SpringValue()
    var rightPawY = SpringValue()
    var earPerk = SpringValue(current: 1, target: 1)
    var eyeX = SpringValue()
    var eyeOpenness = SpringValue(current: 1, target: 1, stiffness: 250, damping: 18)
    var rollAngle = SpringValue()
    var blushIntensity = SpringValue(current: 0.45, target: 0.45, stiffness: 80, damping: 12)
    var catScale = SpringValue(current: 1, target: 1, stiffness: 80, damping: 14)
    /// Horizontal scale — squish to ~0 to simulate a 3D turn, then expand into new pose
    var catScaleX = SpringValue(current: 1, target: 1, stiffness: 300, damping: 14)

    /// Current body pose — switches the renderer to draw a completely different body shape
    var catPose: CatPose = .frontSitting

    // ── Face shapes ──
    var eyeShape: EyeShape = .normal
    var mouthShape: MouthShape = .neutral

    // ── Tail ──
    var tailChain = TailChain()
    var tailSpeed: CGFloat = 3.5
    var tailAmplitude: CGFloat = 1.0

    // ── Visual elements ──
    var props: [PropInstance] = []
    var particles: [PixelParticle] = []

    // ── Animation flags ──
    var isTapAnimating = false
    var isLongPressAnimating = false

    // ── Two fixed points: home (connected) and bed (disconnected) ──
    var homeX = 32; var homeY = 50
    var bedX = 50; var bedY = 68
    private var isConfigured = false

    /// Called ONCE by View on first frame with positions computed from real geometry.
    func configure(home: (Int, Int), bed: (Int, Int)) {
        guard !isConfigured else { return }
        isConfigured = true
        homeX = home.0; homeY = home.1
        bedX = bed.0; bedY = bed.1
        catX.snap(to: CGFloat(isConnected ? homeX : bedX))
        catY.snap(to: CGFloat(isConnected ? homeY : bedY))
    }

    // ── Tap debounce ──
    private var lastTapTime: Date = .distantPast
    private var lastTapAnimIndex: Int = -1

    // ── Internal ──
    private var animTime: Double = 0
    private var idleParticleTimer: CGFloat = 0
    private var animTimer: Timer?
    private var agentStatusTimer: Timer?
    private var revealLoopTask: Task<Void, Never>?
    private var serverConfig: ServerConfig { ServerConfig.load() }

    init() {
        isConnected = !UserDefaults.standard.bool(forKey: "userRequestedDisconnect")
        // Positions will be corrected on first frame by configure()
        syncGlow = isConnected ? 1.0 : 0.0
        catMessage = "Long press himeow to start quick health analysis"
        startAnimations()
        applyIdleState()
        startAgentStatusPolling()
    }

    // ══════════════════════════════════════
    // ── IDLE STATE ──
    // ══════════════════════════════════════

    private func applyIdleState() {
        costume = .none
        catPose = .frontSitting
        catScaleX.snap(to: 1)
        if isConnected {
            eyeShape = .normal; mouthShape = .neutral; earPerk.set(1)
            headX.set(0); headY.set(0); eyeX.set(0); bodySquash.set(0)
            leftPawY.set(0); rightPawY.set(0); rollAngle.set(0)
            tailSpeed = 3.5; tailAmplitude = 1.0
            eyeOpenness.set(1); blushIntensity.set(0.45)
        } else {
            eyeShape = .sleepy; mouthShape = .closed; earPerk.set(-1)
            headX.set(0); headY.set(0); eyeX.set(0); bodySquash.set(0)
            leftPawY.set(0); rightPawY.set(0); rollAngle.set(0)
            tailSpeed = 1.2; tailAmplitude = 0.5
            eyeOpenness.set(0.3); blushIntensity.set(0.3)
        }
    }

    /// Set spring responsiveness (stiffness/damping) for all pose springs
    private func configSprings(stiffness s: CGFloat, damping d: CGFloat) {
        headX.stiffness = s; headX.damping = d
        headY.stiffness = s; headY.damping = d
        bodySquash.stiffness = s; bodySquash.damping = d
        leftPawY.stiffness = s; leftPawY.damping = d
        rightPawY.stiffness = s; rightPawY.damping = d
        earPerk.stiffness = s; earPerk.damping = d
        eyeX.stiffness = s; eyeX.damping = d
    }

    // ══════════════════════════════════════
    // ── ANIMATION TICK (30fps) ──
    // ══════════════════════════════════════

    private func startAnimTimer() {
        animTimer?.invalidate()
        animTimer = Timer.scheduledTimer(withTimeInterval: 1.0 / 60, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated {
                guard let self else { return }
                self.animationTick()
            }
        }
    }

    private func animationTick() {
        let dt: CGFloat = 1.0 / 60

        // Tick all springs
        catX.tick(dt: dt); catY.tick(dt: dt)
        headX.tick(dt: dt); headY.tick(dt: dt)
        bodySquash.tick(dt: dt)
        leftPawY.tick(dt: dt); rightPawY.tick(dt: dt)
        earPerk.tick(dt: dt); eyeX.tick(dt: dt)
        eyeOpenness.tick(dt: dt); rollAngle.tick(dt: dt)
        blushIntensity.tick(dt: dt)
        catScale.tick(dt: dt); catScaleX.tick(dt: dt)

        // Tail: sine-wave base → spring chain follow-through
        animTime += Double(dt)
        let baseAngle = CGFloat(sin(animTime * Double(tailSpeed))) * tailAmplitude
        tailChain.update(baseAngle: baseAngle, dt: dt)

        // Props lifecycle
        for i in props.indices { props[i].lifetime += dt }
        props.removeAll { $0.isDead }

        // Particles physics
        for i in particles.indices {
            particles[i].x += particles[i].vx * dt
            particles[i].y += particles[i].vy * dt
            particles[i].vy += 3 * dt  // gentle gravity (slows upward drift)
            particles[i].life += dt
        }
        particles.removeAll { $0.progress >= 1.0 }
        if particles.count > 40 { particles.removeFirst(particles.count - 40) }

        // Idle particles
        idleParticleTimer += dt
        if !isTapAnimating && !isLongPressAnimating {
            if isConnected && idleParticleTimer > 1.8 {
                idleParticleTimer = 0
                spawnCatParticles(.sparkle, count: 1,
                                  color: Color(red: 1.0, green: 0.90, blue: 0.55), dy: -12)
            } else if !isConnected && idleParticleTimer > 2.5 {
                idleParticleTimer = 0
                spawnCatParticles(.zzz, count: 1,
                                  color: .white.opacity(0.7), dx: 6, dy: -14)
            }
        }

        // Trigger re-render
        animFrame &+= 1
    }

    // ══════════════════════════════════════
    // ── PARTICLE & PROP MANAGEMENT ──
    // ══════════════════════════════════════

    func spawnCatParticles(_ type: PixelParticleType, count: Int, color: Color,
                           dx: CGFloat = 0, dy: CGFloat = -8,
                           vyRange: ClosedRange<CGFloat>? = nil) {
        let cx = CGFloat(catX.i) + dx
        let cy = CGFloat(catY.i) + dy
        for _ in 0..<count {
            particles.append(PixelParticle(
                type: type,
                x: cx + .random(in: -5...5), y: cy + .random(in: -4...4),
                vx: .random(in: -8...8), vy: .random(in: vyRange ?? ((-14)...(-4))),
                life: 0, maxLife: .random(in: 1.3...2.5),
                scale: .random(in: 0.7...1.2), color: color
            ))
        }
    }

    private func spawnPropParticles(_ type: PixelParticleType, count: Int, color: Color) {
        guard let prop = props.first else {
            spawnCatParticles(type, count: count, color: color); return
        }
        let cx = CGFloat(catX.i) + prop.relX
        let cy = CGFloat(catY.i) + prop.relY
        for _ in 0..<count {
            particles.append(PixelParticle(
                type: type,
                x: cx + .random(in: -3...3), y: cy + .random(in: -3...3),
                vx: .random(in: -12...12), vy: .random(in: (-18)...(-5)),
                life: 0, maxLife: .random(in: 1.0...2.0), color: color
            ))
        }
    }

    private func spawnPropIfNeeded(_ type: PixelPropType, anchor: PropAnchor? = nil,
                                    dx: CGFloat? = nil, dy: CGFloat? = nil) {
        guard !props.contains(where: { $0.type == type && $0.removeAt == nil }) else { return }
        let off = type.defaultOffset
        props.append(PropInstance(
            type: type,
            anchor: anchor ?? type.defaultAnchor,
            relX: dx ?? off.dx, relY: dy ?? off.dy
        ))
    }

    /// Mutate an active prop in-place (change anchor, offset, etc.)
    private func updateProp(_ type: PixelPropType, _ update: (inout PropInstance) -> Void) {
        if let idx = props.firstIndex(where: { $0.type == type && $0.removeAt == nil }) {
            update(&props[idx])
        }
    }

    private func dismissProps() {
        for i in props.indices {
            if props[i].removeAt == nil { props[i].removeAt = props[i].lifetime }
        }
    }

    /// Spawn a prop unconditionally (allows duplicates, e.g. two rockets)
    private func spawnPropForce(_ type: PixelPropType, anchor: PropAnchor? = nil,
                                 dx: CGFloat? = nil, dy: CGFloat? = nil) {
        let off = type.defaultOffset
        props.append(PropInstance(
            type: type,
            anchor: anchor ?? type.defaultAnchor,
            relX: dx ?? off.dx, relY: dy ?? off.dy
        ))
    }

    // ══════════════════════════════════════
    // ── WALK (spring-driven bouncy walk) ──
    // ══════════════════════════════════════

    private func walkTo(destX: Int, destY: Int, steps: Int = 12, completion: @escaping () -> Void) {
        isTapAnimating = true
        let startX = catX.f; let startY = catY.f
        let stepTime = 0.06
        let prevSpeed = tailSpeed

        // Phase 1: Anticipation squat
        bodySquash.set(1.0, stiffness: 300, damping: 12)
        tailSpeed = 7.0; tailAmplitude = 1.5
        after(0.08) { self.bodySquash.set(-0.8, stiffness: 280, damping: 10) }

        // Phase 2: Bouncy walk
        for i in 1...steps {
            after(0.14 + stepTime * Double(i)) {
                let t = CGFloat(i) / CGFloat(steps)
                let arc = sin(t * .pi) * 3.0
                let bounce = abs(sin(t * .pi * CGFloat(steps) * 0.5)) * 1.2
                self.catX.snap(to: startX + (CGFloat(destX) - startX) * t)
                self.catY.snap(to: startY + (CGFloat(destY) - startY) * t - arc - bounce)
                if i % 2 == 1 {
                    self.leftPawY.set(-2); self.rightPawY.set(0)
                    self.headY.set(-1); self.headX.set(-1); self.bodySquash.set(0.4)
                } else {
                    self.leftPawY.set(0); self.rightPawY.set(-2)
                    self.headY.set(0); self.headX.set(1); self.bodySquash.set(-0.3)
                }
            }
        }

        // Phase 3: Landing settle (squash → bounce → settle)
        let landT = 0.14 + stepTime * Double(steps)
        after(landT) {
            self.bodySquash.set(1.2, stiffness: 350, damping: 12)
            self.leftPawY.set(0); self.rightPawY.set(0); self.headY.set(2)
        }
        after(landT + 0.08) { self.bodySquash.set(-0.7); self.headY.set(-2) }
        after(landT + 0.16) { self.bodySquash.set(0.3); self.headY.set(1) }
        after(landT + 0.24) { self.bodySquash.set(-0.15); self.headY.set(0) }
        after(landT + 0.32) {
            self.bodySquash.set(0); self.headX.set(0)
            self.isTapAnimating = false; self.tailSpeed = prevSpeed
            completion()
        }
    }

    // ══════════════════════════════════════
    // ── RANDOM TAP MICRO-ANIMATIONS ──
    // ══════════════════════════════════════

    func playRandomTapAnimation() {
        guard !isTapAnimating && !isLongPressAnimating else { return }

        // Debounce: ignore taps within 500ms
        let now = Date()
        guard now.timeIntervalSince(lastTapTime) > 0.5 else { return }
        lastTapTime = now

        UIImpactFeedbackGenerator(style: .light).impactOccurred()

        // Pick a random animation, avoid repeating the same one
        let count = 8
        var idx = Int.random(in: 0..<count)
        if idx == lastTapAnimIndex { idx = (idx + 1) % count }
        lastTapAnimIndex = idx

        switch idx {
        case 0: tapAnim_headTilt()
        case 1: tapAnim_pawWave()
        case 2: tapAnim_tailSwish()
        case 3: tapAnim_bounce()
        case 4: tapAnim_spin()
        case 5: tapAnim_sneeze()
        case 6: tapAnim_curiousPeek()
        default: tapAnim_bellyFlop()
        }
    }

    // ── Head tilt + ear twitch ──
    private func tapAnim_headTilt() {
        isTapAnimating = true
        configSprings(stiffness: 250, damping: 11)
        let dir: CGFloat = Bool.random() ? 1 : -1
        after(0.00) { self.headX.set(4 * dir); self.earPerk.impulse(8) }
        after(0.15) { self.headY.set(-1); self.eyeShape = .happy; self.mouthShape = .smile }
        after(0.35) { self.headX.set(-2 * dir); self.earPerk.impulse(-4) }
        after(0.55) { self.headX.set(0); self.headY.set(0) }
        after(0.70) { self.isTapAnimating = false; self.applyIdleState() }
    }

    // ── Raise one paw and wiggle ──
    private func tapAnim_pawWave() {
        isTapAnimating = true
        configSprings(stiffness: 280, damping: 10)
        let useLeft = Bool.random()
        after(0.00) {
            if useLeft { self.leftPawY.set(-5) } else { self.rightPawY.set(-5) }
            self.eyeShape = .happy; self.mouthShape = .smile
        }
        after(0.15) { if useLeft { self.leftPawY.set(-3) } else { self.rightPawY.set(-3) } }
        after(0.25) { if useLeft { self.leftPawY.set(-5) } else { self.rightPawY.set(-5) } }
        after(0.35) { if useLeft { self.leftPawY.set(-3) } else { self.rightPawY.set(-3) } }
        after(0.50) { if useLeft { self.leftPawY.set(-5) } else { self.rightPawY.set(-5) } }
        after(0.65) { self.leftPawY.set(0); self.rightPawY.set(0) }
        after(0.80) { self.isTapAnimating = false; self.applyIdleState() }
    }

    // ── Big tail amplitude burst ──
    private func tapAnim_tailSwish() {
        isTapAnimating = true
        let prevSpeed = tailSpeed; let prevAmp = tailAmplitude
        after(0.00) { self.tailSpeed = 12.0; self.tailAmplitude = 3.0; self.bodySquash.set(0.3) }
        after(0.15) { self.bodySquash.set(-0.2) }
        after(0.30) { self.bodySquash.set(0.15) }
        after(0.50) { self.tailSpeed = prevSpeed; self.tailAmplitude = prevAmp; self.bodySquash.set(0) }
        after(0.65) { self.isTapAnimating = false; self.applyIdleState() }
    }

    // ── Small hop with squash/stretch ──
    private func tapAnim_bounce() {
        isTapAnimating = true
        configSprings(stiffness: 350, damping: 10)
        after(0.00) { self.bodySquash.set(1.5); self.earPerk.set(-1) }
        after(0.08) { self.bodySquash.set(-1.8); self.catY.set(self.catY.f - 6); self.earPerk.set(3) }
        after(0.22) { self.bodySquash.set(1.0); self.catY.set(self.catY.f + 6) }
        after(0.32) { self.bodySquash.set(-0.5) }
        after(0.42) { self.bodySquash.set(0.2) }
        after(0.52) { self.bodySquash.set(0) }
        after(0.65) { self.isTapAnimating = false; self.applyIdleState() }
    }

    // ── Quick 360 spin ──
    private func tapAnim_spin() {
        isTapAnimating = true
        configSprings(stiffness: 200, damping: 12)
        let dir: CGFloat = Bool.random() ? 1 : -1
        after(0.00) { self.rollAngle.set(120 * dir, stiffness: 300, damping: 8); self.eyeShape = .wide }
        after(0.15) { self.rollAngle.set(270 * dir, stiffness: 300, damping: 8) }
        after(0.30) { self.rollAngle.set(360 * dir, stiffness: 250, damping: 14) }
        after(0.45) { self.rollAngle.snap(to: 0); self.eyeShape = .happy; self.mouthShape = .smile }
        after(0.50) { self.spawnCatParticles(.sparkle, count: 3, color: .yellow, dy: -14) }
        after(0.70) { self.isTapAnimating = false; self.applyIdleState() }
    }

    // ── Sneeze with puff particles ──
    private func tapAnim_sneeze() {
        isTapAnimating = true
        configSprings(stiffness: 300, damping: 10)
        after(0.00) { self.headY.set(-2); self.eyeOpenness.set(0.2); self.bodySquash.set(-0.5) }
        after(0.20) { self.headY.set(1) }
        after(0.35) {
            self.headY.set(3); self.bodySquash.set(1.5); self.eyeOpenness.set(1)
            self.eyeShape = .wide; self.mouthShape = .open
            self.spawnCatParticles(.puff, count: 4, color: .white.opacity(0.8), dy: -6, vyRange: (-16)...(-4))
        }
        after(0.50) { self.bodySquash.set(-0.3); self.headY.set(0); self.mouthShape = .neutral }
        after(0.65) { self.bodySquash.set(0) }
        after(0.80) { self.isTapAnimating = false; self.applyIdleState() }
    }

    // ── Curious peek: head extends up, eyes go wide ──
    private func tapAnim_curiousPeek() {
        isTapAnimating = true
        configSprings(stiffness: 200, damping: 12)
        after(0.00) { self.headY.set(-5); self.eyeShape = .wide; self.earPerk.set(4); self.eyeOpenness.set(1) }
        after(0.10) { self.eyeX.set(3) }
        after(0.25) { self.eyeX.set(-3) }
        after(0.40) { self.eyeX.set(0); self.headY.set(-3) }
        after(0.55) { self.headY.set(0); self.eyeShape = .normal }
        after(0.70) { self.isTapAnimating = false; self.applyIdleState() }
    }

    // ── Belly flop: squat down hard ──
    private func tapAnim_bellyFlop() {
        isTapAnimating = true
        configSprings(stiffness: 280, damping: 10)
        after(0.00) { self.bodySquash.set(-1.0); self.earPerk.set(3) }
        after(0.12) { self.bodySquash.set(3.0); self.earPerk.set(-2); self.leftPawY.set(2); self.rightPawY.set(2) }
        after(0.30) { self.bodySquash.set(-0.5); self.spawnCatParticles(.puff, count: 3, color: Color(white: 0.8), dy: 2, vyRange: (-8)...(-2)) }
        after(0.45) { self.bodySquash.set(0.3) }
        after(0.55) { self.bodySquash.set(0); self.leftPawY.set(0); self.rightPawY.set(0) }
        after(0.75) { self.isTapAnimating = false; self.applyIdleState() }
    }

    // ══════════════════════════════════════
    // ── TOGGLE CONNECT ──
    // ══════════════════════════════════════

    func toggleConnect() {
        guard !isTapAnimating && !isLongPressAnimating else { return }

        // Cancel any in-flight reveal that could snap catX/catY
        revealLoopTask?.cancel()
        dismissProps()
        catPose = .frontSitting
        catScaleX.snap(to: 1)

        isConnected.toggle()
        UserDefaults.standard.set(!isConnected, forKey: "userRequestedDisconnect")
        UIImpactFeedbackGenerator(style: .medium).impactOccurred()

        if isConnected {
            WebSocketClient.shared.connect()
            catMessage = "Long press himeow to start quick health analysis"
            withAnimation(.easeInOut(duration: 0.6)) { syncGlow = 1.0 }
            eyeShape = .normal; earPerk.set(0)
            after(0.2) {
                // Ensure walk starts from bed position (guards against any drift)
                self.catX.snap(to: CGFloat(self.bedX))
                self.catY.snap(to: CGFloat(self.bedY))
                self.walkTo(destX: self.homeX, destY: self.homeY) { self.playWakeUpDance() }
            }
        } else {
            WebSocketClient.shared.disconnect(userInitiated: true)
            catMessage = "Long press himeow to start quick health analysis"
            withAnimation(.easeInOut(duration: 0.8)) { syncGlow = 0.0 }
            // Ensure walk starts from home position
            catX.snap(to: CGFloat(homeX))
            catY.snap(to: CGFloat(homeY))
            walkTo(destX: bedX, destY: bedY) { self.playSleepySettle() }
        }
        // Server notification is fire-and-forget — never silently revert user's toggle.
        // The old catch block reverted isConnected on server error, creating a state
        // mismatch (cat visually at bed but logically "connected") that caused the
        // next tap to walk bed→bed (jump in place).
        let newState = isConnected
        Task { try? await notifyServerSyncControl(enabled: newState) }
    }

    func triggerQuickAnalysis() {
        guard !isAnalyzing && !isLongPressAnimating else { return }
        isAnalyzing = true
        catMessage = "Purrrr~ analyzing..."
        UIImpactFeedbackGenerator(style: .heavy).impactOccurred()
        playLongPressAnimation()
        Task { await runQuickAnalysis(); isAnalyzing = false }
    }

    // ══════════════════════════════════════
    // ── WAKE-UP DANCE ──
    // ══════════════════════════════════════

    private func playWakeUpDance() {
        guard !isTapAnimating else { return }
        isTapAnimating = true; showNya = true
        configSprings(stiffness: 280, damping: 10)
        eyeShape = .normal; mouthShape = .neutral; earPerk.set(0)
        tailSpeed = 5.0; tailAmplitude = 1.3

        // Big stretch
        after(0.08) {
            self.bodySquash.set(-1.2); self.leftPawY.set(-4); self.headY.set(-3)
            self.earPerk.set(3); self.mouthShape = .open; self.eyeShape = .wide
        }
        // Settle from stretch
        after(0.25) {
            self.bodySquash.set(0.5); self.leftPawY.set(0); self.headY.set(0)
            self.mouthShape = .smile
        }
        // Happy bounce sequence
        after(0.35) {
            self.bodySquash.set(-0.8); self.eyeShape = .happy; self.mouthShape = .smile
            self.spawnCatParticles(.note, count: 2, color: .orange, dy: -14)
        }
        after(0.45) {
            self.bodySquash.set(0.4); self.leftPawY.set(-3); self.rightPawY.set(-1)
            self.headX.set(-2)
        }
        after(0.55) {
            self.bodySquash.set(-0.4); self.leftPawY.set(-1); self.rightPawY.set(-3)
            self.headX.set(2)
            self.spawnCatParticles(.note, count: 2, color: .orange, dy: -14)
        }
        after(0.65) {
            self.bodySquash.set(0.3); self.leftPawY.set(-3); self.headY.set(-1)
        }
        after(0.75) {
            self.bodySquash.set(-0.2); self.leftPawY.set(-1); self.rightPawY.set(-3)
            self.headX.set(-1)
            self.spawnCatParticles(.sparkle, count: 3, color: .yellow, dy: -14)
        }
        after(0.85) {
            self.bodySquash.set(0); self.leftPawY.set(0); self.rightPawY.set(0)
            self.headX.set(0); self.headY.set(0)
        }
        after(1.05) {
            self.showNya = false
            self.isTapAnimating = false; self.applyIdleState()
        }
    }

    // ══════════════════════════════════════
    // ── SLEEPY SETTLE ──
    // ══════════════════════════════════════

    private func playSleepySettle() {
        guard !isTapAnimating else { return }
        isTapAnimating = true
        configSprings(stiffness: 100, damping: 16)
        tailSpeed = 2.0; tailAmplitude = 0.8

        // Yawn
        after(0.08) {
            self.mouthShape = .open; self.headY.set(-2); self.bodySquash.set(-0.5)
            self.eyeShape = .normal; self.eyeOpenness.set(0.5)
        }
        after(0.35) { self.bodySquash.set(0.2); self.mouthShape = .closed }
        // Eyes drooping
        after(0.50) {
            self.eyeShape = .sleepy; self.eyeOpenness.set(0.4); self.headY.set(0)
            self.earPerk.set(-1)
        }
        // Settling
        after(0.65) { self.bodySquash.set(0.5); self.headY.set(1) }
        // Curl up
        after(0.80) { self.leftPawY.set(-1); self.rightPawY.set(-1) }
        after(0.90) { self.leftPawY.set(0); self.rightPawY.set(0) }
        // Final close
        after(1.00) {
            self.eyeOpenness.set(0.15); self.mouthShape = .closed
            self.bodySquash.set(0.2); self.tailSpeed = 1.2; self.tailAmplitude = 0.5
        }
        after(1.15) { self.bodySquash.set(0); self.isTapAnimating = false; self.applyIdleState() }
    }

    // ══════════════════════════════════════
    // ── KNEADING (long press) ──
    // ══════════════════════════════════════

    private func playLongPressAnimation() {
        guard !isLongPressAnimating else { return }
        isLongPressAnimating = true; showPurrBubble = true
        configSprings(stiffness: 250, damping: 12)
        eyeShape = .happy; mouthShape = .heart; earPerk.set(1)
        tailSpeed = 4.0; tailAmplitude = 0.8
        spawnCatParticles(.heart, count: 1, color: .pink, dy: -14)

        // Rhythmic kneading
        let knead: [(Double, CGFloat, CGFloat, CGFloat, CGFloat)] = [
            (0.00, -3, 0,  0.4, -1), (0.12, -1, -3, -0.3, 1),
            (0.24, -3, -1, 0.4, -1), (0.36, -1, -3, -0.3, 1),
            (0.48, -3, -1, 0.4, -1), (0.60, -1, -3, -0.3, 1),
            (0.72, -3, -1, 0.4,  0), (0.84, -1, -1, 0, 0),
        ]
        for (t, lp, rp, sq, hx) in knead {
            after(t) {
                self.leftPawY.set(lp); self.rightPawY.set(rp)
                self.bodySquash.set(sq); self.headX.set(hx)
            }
            if Int(t * 100) % 24 == 0 {
                after(t) { self.spawnCatParticles(.heart, count: 1, color: .pink, dy: -14) }
            }
        }
        after(0.96) { self.leftPawY.set(0); self.rightPawY.set(0); self.bodySquash.set(0) }
        after(1.20) {
            self.showPurrBubble = false
            self.isLongPressAnimating = false; self.applyIdleState()
        }
    }

    // ══════════════════════════════════════
    // ── STATE REVEALS ──
    // ══════════════════════════════════════

    private func playStateReveal() {
        switch catState {

        // ══════════════════════════════════════════════════════
        // ── ENERGETIC: Boxing Champion ──
        // Boxing gloves on, rapid combos, uppercut launches cat skyward
        // ══════════════════════════════════════════════════════
        case .energetic:
            configSprings(stiffness: 320, damping: 9)
            costume = .boxingGloves
            tailSpeed = 8.0; tailAmplitude = 2.0

            // Fighter stance — bouncing on feet
            after(0.00) { self.bodySquash.set(-0.5); self.eyeShape = .wide; self.mouthShape = .closed; self.earPerk.set(3) }
            after(0.12) { self.bodySquash.set(0.3) }
            after(0.22) { self.bodySquash.set(-0.3) }
            after(0.32) { self.bodySquash.set(0.3) }
            // LEFT JAB!
            after(0.45) {
                self.leftPawY.set(-5); self.headX.set(-2); self.bodySquash.set(-0.8)
                self.spawnCatParticles(.starBurst, count: 1, color: .red, dx: -8, dy: -8)
            }
            // RIGHT CROSS!
            after(0.65) {
                self.leftPawY.set(0); self.rightPawY.set(-5); self.headX.set(2)
                self.spawnCatParticles(.starBurst, count: 1, color: .orange, dx: 8, dy: -8)
            }
            // RAPID COMBO!
            after(0.80) { self.rightPawY.set(0); self.leftPawY.set(-6); self.headX.set(-3) }
            after(0.90) { self.leftPawY.set(0); self.rightPawY.set(-6); self.headX.set(3) }
            after(1.00) { self.rightPawY.set(0); self.leftPawY.set(-6); self.headX.set(-3) }
            after(1.10) { self.leftPawY.set(0); self.rightPawY.set(-6); self.headX.set(3) }
            // Power CROUCH for uppercut
            after(1.25) {
                self.leftPawY.set(0); self.rightPawY.set(0); self.headX.set(0)
                self.bodySquash.set(1.5, stiffness: 380, damping: 8)
                self.headY.set(2); self.earPerk.set(-1); self.mouthShape = .open
            }
            // UPPERCUT!! Cat LAUNCHES OFF THE TOP OF SCREEN!
            after(1.50) {
                self.bodySquash.set(-2.5, stiffness: 400, damping: 7)
                self.catY.set(self.catY.f - 30, stiffness: 180, damping: 8)
                self.headY.set(-3); self.earPerk.set(5)
                self.rightPawY.set(-6); self.mouthShape = .open
                self.spawnCatParticles(.starBurst, count: 3, color: .yellow, dy: -16)
                self.spawnCatParticles(.sparkle, count: 4, color: .orange, dy: -14)
            }
            // Cat is WAY above screen — hang time
            after(2.00) {
                self.bodySquash.set(-2.0)
                self.leftPawY.set(-4); self.rightPawY.set(-4)
            }
            // CRASHING DOWN!
            after(2.30) {
                self.catY.set(self.catY.f + 30, stiffness: 120, damping: 12)
                self.bodySquash.set(0.5)
                self.leftPawY.set(0); self.rightPawY.set(0)
            }
            // MASSIVE IMPACT!
            after(2.70) {
                self.bodySquash.set(2.5, stiffness: 350, damping: 10)
                self.headY.set(2)
                for _ in 0..<5 { self.spawnCatParticles(.puff, count: 1, color: .white, dx: .random(in: -8...8), dy: .random(in: -2...4)) }
                self.spawnCatParticles(.starBurst, count: 2, color: .orange, dy: -2)
            }
            // Bounce recover
            after(2.90) { self.bodySquash.set(-1.0); self.headY.set(-1); self.eyeShape = .happy; self.mouthShape = .smile }
            // Victory flex
            after(3.10) {
                self.leftPawY.set(-6); self.bodySquash.set(-1.5)
                self.spawnCatParticles(.sparkle, count: 3, color: .yellow, dy: -14)
            }
            after(3.40) {
                self.bodySquash.set(0); self.headY.set(0); self.headX.set(0)
                self.leftPawY.set(0); self.rightPawY.set(0); self.earPerk.set(1)
            }

        // ══════════════════════════════════════════════════════
        // ── TIRED: Total Collapse ──
        // Cat yawns, wobbles, topples sideways onto pillow — truly lies down
        // ══════════════════════════════════════════════════════
        case .tired:
            configSprings(stiffness: 50, damping: 22)
            tailSpeed = 0.8; tailAmplitude = 0.2

            after(0.00) { self.eyeShape = .sleepy; self.eyeOpenness.set(0.3); self.mouthShape = .closed; self.earPerk.set(-2); self.headY.set(1) }
            // Big yawn
            after(0.40) {
                self.mouthShape = .open; self.bodySquash.set(-1.0, stiffness: 60, damping: 16)
                self.headY.set(-2); self.eyeOpenness.set(0.15)
                self.spawnCatParticles(.drop, count: 1, color: Color(red: 0.6, green: 0.75, blue: 0.95), dx: 4, dy: -6)
            }
            after(0.80) { self.mouthShape = .closed; self.bodySquash.set(0.5); self.headY.set(2) }
            // Pillow appears LEFT
            after(1.10) { self.spawnPropIfNeeded(.pillow, dx: -14, dy: 6) }
            // Starting to tip LEFT
            after(1.40) { self.headX.set(-3, stiffness: 30, damping: 20); self.bodySquash.set(1.0, stiffness: 35, damping: 22); self.eyeOpenness.set(0.1) }
            // Brief fight — jerk upright
            after(1.70) { self.headX.set(-1); self.headY.set(-1); self.eyeOpenness.set(0.3) }
            // Tipping further
            after(2.00) {
                self.headX.set(-5, stiffness: 25, damping: 22); self.bodySquash.set(1.8, stiffness: 30, damping: 24)
                self.headY.set(3); self.earPerk.set(-3); self.eyeOpenness.set(0.05)
            }
            // TOPPLE! Cat falls sideways onto pillow
            after(2.40) {
                self.headX.set(-10, stiffness: 40, damping: 18); self.bodySquash.set(2.5, stiffness: 25, damping: 26)
                self.headY.set(5); self.earPerk.set(-4); self.leftPawY.set(1); self.rightPawY.set(1)
                self.spawnCatParticles(.puff, count: 1, color: .white.opacity(0.3), dx: -6, dy: 2)
            }
            // Fully collapsed — lying on side
            after(2.80) {
                self.bodySquash.set(3.0, stiffness: 20, damping: 28); self.headX.set(-10); self.headY.set(6)
                self.spawnCatParticles(.zzz, count: 1, color: .white.opacity(0.6), dx: -4, dy: -6)
            }
            // Gentle breathing
            after(3.20) { self.bodySquash.set(2.8) }
            after(3.40) { self.bodySquash.set(3.0); self.spawnCatParticles(.zzz, count: 1, color: .white.opacity(0.5), dx: -3, dy: -4) }
            after(3.60) { self.bodySquash.set(2.8) }
            after(3.80) {
                self.bodySquash.set(0); self.headY.set(0); self.headX.set(0)
                self.earPerk.set(-1); self.eyeOpenness.set(1); self.leftPawY.set(0); self.rightPawY.set(0)
            }

        // ══════════════════════════════════════════════════════
        // ── STRESSED: Scaredy Arch ──
        // Cat startles → horizontally squishes (like a card flip
        // turn) → pose swaps at thinnest point → expands into
        // side-view arched cat → hops → turns back same way.
        // ══════════════════════════════════════════════════════
        case .stressed:
            let sx = catX.f, sy = catY.f
            configSprings(stiffness: 300, damping: 9)
            earPerk.set(3); tailSpeed = 10.0; tailAmplitude = 2.5
            eyeShape = .wide; mouthShape = .open

            // Something startles the cat!
            after(0.00) {
                self.spawnCatParticles(.starBurst, count: 2, color: .yellow, dy: -14)
            }
            // Tiny flinch (still front-sitting)
            after(0.15) {
                self.catY.snap(to: sy - 2); self.earPerk.set(5)
                self.spawnCatParticles(.drop, count: 2, color: Color(red: 0.65, green: 0.80, blue: 0.95), dx: 3, dy: -10)
            }
            after(0.22) { self.catY.snap(to: sy) }

            // ── TURN: Squish horizontally (card-flip effect) ──
            after(0.30) {
                self.catScaleX.set(0.05, stiffness: 350, damping: 10)  // squish to thin line
                self.earPerk.set(5)
            }

            // At thinnest point → swap pose → expand into side view
            after(0.48) {
                self.catPose = .sideArchedBack
                self.catScaleX.snap(to: 0.05)                          // ensure flat
                self.catScaleX.set(1.0, stiffness: 250, damping: 12)   // expand into side view
                self.bodySquash.snap(to: 0)
                self.tailSpeed = 16.0; self.tailAmplitude = 3.5
                self.eyeShape = .wide; self.mouthShape = .open
                self.spawnCatParticles(.puff, count: 2, color: .white, dy: 0)
            }

            // Hold arched pose — let user see the bristled side cat
            after(0.70) {
                self.spawnCatParticles(.drop, count: 1, color: Color(red: 0.65, green: 0.80, blue: 0.95), dx: -3, dy: -12)
            }

            // ── HOP 1: BIG jump left! ──
            after(0.95) {
                self.catY.snap(to: sy - 10)
                self.catX.snap(to: sx - 8)
                self.spawnCatParticles(.puff, count: 2, color: .white, dy: 4)
            }
            after(1.15) {
                self.catY.snap(to: sy)
                self.spawnCatParticles(.puff, count: 2, color: .white, dy: 2)
            }

            // ── HOP 2: Jump right! ──
            after(1.30) {
                self.catY.snap(to: sy - 8)
                self.catX.snap(to: sx + 6)
                self.spawnCatParticles(.drop, count: 1, color: Color(red: 0.65, green: 0.80, blue: 0.95), dx: 2, dy: -10)
            }
            after(1.48) {
                self.catY.snap(to: sy)
                self.spawnCatParticles(.puff, count: 2, color: .white, dy: 2)
            }

            // ── HOP 3: Smaller jump left ──
            after(1.60) {
                self.catY.snap(to: sy - 6)
                self.catX.snap(to: sx - 4)
            }
            after(1.75) {
                self.catY.snap(to: sy)
                self.spawnCatParticles(.puff, count: 1, color: .white, dy: 2)
            }

            // ── HOP 4: Small hop back to center ──
            after(1.88) {
                self.catY.snap(to: sy - 3)
                self.catX.snap(to: sx)
            }
            after(2.00) {
                self.catY.snap(to: sy)
                self.spawnCatParticles(.puff, count: 1, color: .white, dy: 2)
            }

            // Still arched, panting
            after(2.20) {
                self.mouthShape = .open
                self.spawnCatParticles(.drop, count: 1, color: Color(red: 0.65, green: 0.80, blue: 0.95), dx: -2, dy: -10)
            }
            after(2.50) {
                self.tailSpeed = 6.0; self.tailAmplitude = 1.5; self.earPerk.set(2)
            }

            // ── TURN BACK: Squish → swap → expand ──
            after(2.80) {
                self.catScaleX.set(0.05, stiffness: 350, damping: 10)
            }
            after(2.98) {
                self.catPose = .frontSitting
                self.catScaleX.snap(to: 0.05)
                self.catScaleX.set(1.0, stiffness: 200, damping: 14)
                self.bodySquash.snap(to: 0)
                self.mouthShape = .frown; self.earPerk.set(0); self.eyeOpenness.set(0.8)
                self.spawnCatParticles(.puff, count: 2, color: .white, dy: -4)
            }
            after(3.20) { self.mouthShape = .frown; self.tailSpeed = 3.0 }
            after(3.60) {
                self.bodySquash.set(0); self.earPerk.set(1); self.eyeX.set(0)
                self.eyeOpenness.set(1); self.mouthShape = .closed; self.tailAmplitude = 1.0
                self.catScaleX.snap(to: 1)
            }

        // ══════════════════════════════════════════════════════
        // ── SAD: Rainy Day ──
        // Rain falls, cat walks LEFT in retreat, big umbrella appears, peeks back
        // ══════════════════════════════════════════════════════
        case .sad:
            configSprings(stiffness: 60, damping: 20)
            eyeShape = .sad; tailSpeed = 0.8; tailAmplitude = 0.2

            // Rain starts — drops FALL DOWNWARD!
            after(0.00) {
                for _ in 0..<6 {
                    self.spawnCatParticles(.drop, count: 1, color: Color(red: 0.5, green: 0.72, blue: 0.96),
                        dx: .random(in: -20...10), dy: -25, vyRange: 8...18)
                }
                self.earPerk.set(-2); self.mouthShape = .frown
            }
            after(0.20) {
                for _ in 0..<5 {
                    self.spawnCatParticles(.drop, count: 1, color: Color(red: 0.5, green: 0.72, blue: 0.96),
                        dx: .random(in: -20...10), dy: -25, vyRange: 8...18)
                }
            }
            // Flinch
            after(0.35) { self.headY.set(1); self.bodySquash.set(0.5); self.eyeOpenness.set(0.6) }
            // More rain
            after(0.50) {
                for _ in 0..<6 {
                    self.spawnCatParticles(.drop, count: 1, color: Color(red: 0.5, green: 0.72, blue: 0.96),
                        dx: .random(in: -20...10), dy: -25, vyRange: 8...18)
                }
            }
            // Walk LEFT
            after(0.70) { self.catX.set(self.catX.f - 4, stiffness: 30, damping: 18); self.headX.set(-2); self.bodySquash.set(0.3) }
            after(0.85) { self.leftPawY.set(-1); self.bodySquash.set(0.5) }
            // Rain continues
            after(0.90) {
                for _ in 0..<5 {
                    self.spawnCatParticles(.drop, count: 1, color: Color(red: 0.5, green: 0.72, blue: 0.96),
                        dx: .random(in: -20...10), dy: -25, vyRange: 8...18)
                }
            }
            after(1.00) { self.leftPawY.set(0); self.rightPawY.set(-1); self.catX.set(self.catX.f - 4, stiffness: 30, damping: 18) }
            after(1.15) { self.rightPawY.set(0) }
            after(1.30) { self.catX.set(self.catX.f - 4, stiffness: 30, damping: 18) }
            // More rain
            after(1.30) {
                for _ in 0..<7 {
                    self.spawnCatParticles(.drop, count: 1, color: Color(red: 0.5, green: 0.72, blue: 0.96),
                        dx: .random(in: -22...8), dy: -25, vyRange: 8...18)
                }
            }
            // Look up
            after(1.60) { self.headY.set(-2); self.eyeOpenness.set(0.5); self.bodySquash.set(-0.3) }
            // Rain wave
            after(1.70) {
                for _ in 0..<6 {
                    self.spawnCatParticles(.drop, count: 1, color: Color(red: 0.5, green: 0.72, blue: 0.96),
                        dx: .random(in: -22...8), dy: -25, vyRange: 8...18)
                }
            }
            // BIG umbrella appears
            after(1.90) { self.spawnPropIfNeeded(.umbrella); self.spawnCatParticles(.puff, count: 1, color: .white.opacity(0.4), dy: -18) }
            // Huddle under umbrella — rain AROUND the umbrella edges
            after(2.10) {
                for _ in 0..<4 {
                    self.spawnCatParticles(.drop, count: 1, color: Color(red: 0.5, green: 0.72, blue: 0.96),
                        dx: .random(in: -25...(-10)), dy: -25, vyRange: 8...18)
                }
                for _ in 0..<4 {
                    self.spawnCatParticles(.drop, count: 1, color: Color(red: 0.5, green: 0.72, blue: 0.96),
                        dx: .random(in: 10...20), dy: -25, vyRange: 8...18)
                }
            }
            after(2.20) { self.bodySquash.set(1.0); self.headY.set(1); self.earPerk.set(-3); self.eyeOpenness.set(0.3) }
            // Rain continues on sides
            after(2.50) {
                for _ in 0..<4 {
                    self.spawnCatParticles(.drop, count: 1, color: Color(red: 0.5, green: 0.72, blue: 0.96),
                        dx: .random(in: -25...(-10)), dy: -25, vyRange: 8...18)
                }
                for _ in 0..<3 {
                    self.spawnCatParticles(.drop, count: 1, color: Color(red: 0.5, green: 0.72, blue: 0.96),
                        dx: .random(in: 10...20), dy: -25, vyRange: 8...18)
                }
            }
            // Peek back at camera
            after(2.60) { self.headX.set(3); self.eyeX.set(1); self.eyeOpenness.set(0.4) }
            // More rain
            after(2.80) {
                for _ in 0..<3 {
                    self.spawnCatParticles(.drop, count: 1, color: Color(red: 0.5, green: 0.72, blue: 0.96),
                        dx: .random(in: -22...(-8)), dy: -25, vyRange: 8...18)
                }
            }
            // Sigh
            after(2.90) { self.spawnCatParticles(.puff, count: 1, color: .white.opacity(0.3), dy: -4); self.headX.set(0); self.eyeX.set(0) }
            // Walk back
            after(3.20) { self.catX.set(self.catX.f + 4, stiffness: 25, damping: 20); self.bodySquash.set(0.5) }
            after(3.40) { self.leftPawY.set(-1) }
            after(3.55) { self.leftPawY.set(0); self.catX.set(self.catX.f + 4, stiffness: 25, damping: 20) }
            after(3.70) { self.catX.set(self.catX.f + 4, stiffness: 25, damping: 20) }
            after(4.20) {
                self.bodySquash.set(0); self.headY.set(0); self.headX.set(0)
                self.rightPawY.set(0); self.leftPawY.set(0); self.earPerk.set(-1)
                self.eyeOpenness.set(1); self.eyeX.set(0)
            }

        // ══════════════════════════════════════════════════════
        // ── RELAXED: Belly Up ──
        // Cat rolls onto back, paws in air, lazily sips tea while lying down
        // ══════════════════════════════════════════════════════
        case .relaxed:
            let sx = catX.f, sy = catY.f
            configSprings(stiffness: 80, damping: 16)
            costume = .sunglasses
            eyeShape = .happy; mouthShape = .smile; tailSpeed = 2.0; tailAmplitude = 0.6

            after(0.00) { self.bodySquash.set(-0.5); self.headY.set(-1); self.leftPawY.set(-2); self.rightPawY.set(-2) }
            after(0.30) {
                self.spawnPropIfNeeded(.teaCup)
                self.bodySquash.set(0.2); self.headY.set(0); self.leftPawY.set(0); self.rightPawY.set(0)
            }
            after(0.60) { self.bodySquash.set(0.5); self.headY.set(1) }
            after(0.90) {
                self.bodySquash.set(2.0, stiffness: 50, damping: 18)
                self.headX.set(-6, stiffness: 40, damping: 16)
                self.headY.set(4); self.earPerk.set(-2)
            }
            after(1.20) {
                self.bodySquash.set(2.5, stiffness: 40, damping: 20)
                self.headX.set(-8); self.headY.set(5)
                self.leftPawY.set(-5); self.rightPawY.set(-5)
                self.eyeShape = .happy; self.mouthShape = .smile
            }
            after(1.40) { self.leftPawY.set(-3) }
            after(1.50) { self.leftPawY.set(-5) }
            after(1.60) { self.rightPawY.set(-3) }
            after(1.70) { self.rightPawY.set(-5) }
            after(1.90) {
                self.leftPawY.set(-6); self.headX.set(-5)
                self.updateProp(.teaCup) { $0.relX = -6; $0.relY = 4 }
            }
            after(2.20) {
                self.updateProp(.teaCup) { $0.relX = -5; $0.relY = 3 }
                self.mouthShape = .heart
                self.spawnCatParticles(.heart, count: 2, color: .pink, dy: -8)
            }
            after(2.50) {
                self.updateProp(.teaCup) { $0.relX = -15; $0.relY = 4 }
                self.leftPawY.set(-5); self.mouthShape = .smile
            }
            after(2.80) {
                self.bodySquash.set(0.5, stiffness: 60, damping: 16)
                self.headX.set(-2, stiffness: 60, damping: 14)
                self.headY.set(1); self.leftPawY.set(-2); self.rightPawY.set(-2)
                self.earPerk.set(0)
                self.spawnCatParticles(.puff, count: 1, color: .white, dy: 2)
            }
            after(3.10) {
                self.bodySquash.set(-0.5); self.headX.set(0); self.headY.set(-1)
                self.leftPawY.set(-2); self.rightPawY.set(-2)
                self.blushIntensity.set(0.7)
            }
            after(3.40) {
                self.bodySquash.set(0); self.headY.set(0); self.headX.set(0)
                self.leftPawY.set(0); self.rightPawY.set(0)
                self.eyeX.set(0); self.blushIntensity.set(0.45); self.earPerk.set(1)
            }

        // ══════════════════════════════════════════════════════
        // ── CURIOUS: Window Climber ──
        // Cat leaps UP to windowsill, peers through glass, gets startled, falls back
        // ══════════════════════════════════════════════════════
        case .curious:
            let sx = catX.f, sy = catY.f
            configSprings(stiffness: 220, damping: 11)
            costume = .detective
            earPerk.set(4); tailSpeed = 5.0; tailAmplitude = 1.0

            after(0.00) { self.bodySquash.set(-0.8); self.headY.set(-1); self.eyeShape = .wide; self.mouthShape = .open }
            after(0.25) {
                self.spawnPropIfNeeded(.magnifyingGlass)
                self.spawnCatParticles(.sparkle, count: 2, color: .cyan, dx: -14, dy: -2)
            }
            after(0.50) { self.headY.set(-3); self.headX.set(0); self.eyeX.set(0) }
            after(0.70) {
                self.bodySquash.set(1.5, stiffness: 350, damping: 10)
                self.headY.set(2); self.earPerk.set(-1)
            }
            after(0.90) {
                self.catY.set(sy - 40, stiffness: 120, damping: 10)
                self.catX.set(sx - 16, stiffness: 100, damping: 12)
                self.bodySquash.set(-2.0); self.leftPawY.set(-6); self.rightPawY.set(-6)
                self.headY.set(-3); self.mouthShape = .open
                self.spawnCatParticles(.puff, count: 3, color: .white, dy: 4)
            }
            after(1.20) {
                self.bodySquash.set(0.5); self.leftPawY.set(0); self.rightPawY.set(0)
                self.headY.set(0); self.mouthShape = .closed
            }
            after(1.40) { self.headX.set(-3); self.eyeX.set(-1); self.eyeShape = .wide }
            after(1.55) { self.headX.set(3); self.eyeX.set(1) }
            after(1.70) { self.leftPawY.set(-4); self.headX.set(0); self.eyeX.set(0) }
            after(1.80) { self.leftPawY.set(-2) }
            after(1.90) {
                self.leftPawY.set(0); self.eyeShape = .wide; self.eyeOpenness.set(1)
                self.earPerk.impulse(15); self.bodySquash.set(-1.5)
                self.mouthShape = .open
            }
            after(2.10) {
                self.catY.set(sy, stiffness: 100, damping: 10)
                self.catX.set(sx, stiffness: 80, damping: 12)
                self.bodySquash.set(0.5); self.leftPawY.set(-4); self.rightPawY.set(-4)
            }
            after(2.50) {
                self.bodySquash.set(2.0, stiffness: 300, damping: 12)
                self.leftPawY.set(0); self.rightPawY.set(0); self.headY.set(2)
                self.spawnCatParticles(.puff, count: 4, color: .white, dy: 2)
                self.spawnCatParticles(.starBurst, count: 1, color: .cyan, dy: -2)
            }
            after(2.70) { self.bodySquash.set(-0.8); self.headY.set(-1) }
            after(2.80) { self.headX.set(-2) }
            after(2.90) { self.headX.set(2) }
            after(3.00) {
                self.headX.set(0); self.bodySquash.set(-0.5)
                self.eyeShape = .happy; self.mouthShape = .smile
            }
            after(3.40) {
                self.bodySquash.set(0); self.headY.set(0); self.headX.set(0)
                self.leftPawY.set(0); self.rightPawY.set(0); self.eyeX.set(0)
            }

        // ══════════════════════════════════════════════════════
        // ── HAPPY: Peek-a-boo ──
        // Cat runs out, peeks back BIG from screen edge, wiggles ears, enters from right
        // ══════════════════════════════════════════════════════
        case .happy:
            let sx = catX.f, sy = catY.f
            configSprings(stiffness: 280, damping: 9)
            costume = .bowtie
            tailSpeed = 8.0; tailAmplitude = 2.0

            // Excited stance
            after(0.00) {
                self.bodySquash.set(-0.5); self.eyeShape = .happy; self.mouthShape = .smile
                self.earPerk.set(3)
            }
            // Pre-run crouch
            after(0.20) {
                self.bodySquash.set(0.8, stiffness: 350, damping: 10); self.headY.set(1)
            }
            // Start RUNNING LEFT
            after(0.40) {
                self.catX.set(sx - 15, stiffness: 150, damping: 10)
                self.bodySquash.set(-0.5); self.headX.set(-3); self.mouthShape = .open
            }
            after(0.50) { self.leftPawY.set(-3); self.bodySquash.set(0.3) }
            after(0.60) { self.leftPawY.set(0); self.rightPawY.set(-3); self.bodySquash.set(-0.3) }
            after(0.70) { self.leftPawY.set(-3); self.rightPawY.set(0) }
            // EXIT LEFT off screen!
            after(0.85) {
                self.catX.set(sx - 45, stiffness: 200, damping: 9)
                self.leftPawY.set(0); self.rightPawY.set(0)
                self.spawnCatParticles(.puff, count: 2, color: .white, dy: 0)
            }
            // Cat is gone...
            after(1.20) {
                self.headX.set(0); self.bodySquash.set(0)
            }

            // ── PEEK! Cat suddenly appears from bottom-left, BIG and close ──
            after(1.60) {
                // Teleport to bottom-left corner (partially off screen)
                self.catX.snap(to: sx - 22)
                self.catY.snap(to: sy + 20)
                self.catScale.snap(to: 2.2) // instantly BIG
                self.headX.set(4); self.headY.set(-2) // head turned toward viewer
                self.eyeShape = .happy; self.mouthShape = .smile
                self.eyeOpenness.set(1); self.earPerk.set(3)
                self.blushIntensity.set(0.8)
            }
            // Ear wiggle 1
            after(1.85) { self.earPerk.impulse(15) }
            // Ear wiggle 2
            after(2.05) { self.earPerk.impulse(12) }
            // Little head tilt — playful
            after(2.20) { self.headX.set(5); self.headY.set(-1) }
            // Hearts from the big face
            after(2.35) {
                self.spawnCatParticles(.heart, count: 2, color: .pink, dy: -4)
            }
            // Ear wiggle 3
            after(2.50) { self.earPerk.impulse(10) }

            // ── DISAPPEAR from corner ──
            after(2.70) {
                self.catX.set(sx - 35, stiffness: 150, damping: 10)
                self.catY.set(sy + 30, stiffness: 150, damping: 10)
                self.catScale.set(1.5, stiffness: 100, damping: 12)
            }

            // ── ENTER FROM RIGHT at normal size ──
            after(3.00) {
                self.catX.snap(to: sx + 40) // teleport to right side
                self.catY.snap(to: sy) // back at home row
                self.catScale.snap(to: 1.0) // normal size
                self.headX.set(-3); self.bodySquash.set(-0.5)
                self.blushIntensity.set(0.45)
            }
            // Walk left toward center
            after(3.10) {
                self.catX.set(sx, stiffness: 80, damping: 14) // animate to home
            }
            after(3.20) { self.leftPawY.set(-3); self.bodySquash.set(0.3) }
            after(3.30) { self.leftPawY.set(0); self.rightPawY.set(-3); self.bodySquash.set(-0.3) }
            after(3.40) { self.leftPawY.set(-3); self.rightPawY.set(0) }
            after(3.50) { self.leftPawY.set(0); self.rightPawY.set(-3) }
            // Arrive — settle
            after(3.70) {
                self.leftPawY.set(0); self.rightPawY.set(0); self.headX.set(0)
                self.bodySquash.set(0.5)
                self.spawnCatParticles(.puff, count: 2, color: .white, dy: 2)
                self.spawnCatParticles(.heart, count: 2, color: .pink, dy: -12)
            }
            // Happy pose
            after(3.90) {
                self.bodySquash.set(-0.5); self.eyeShape = .happy; self.mouthShape = .smile
            }
            after(4.10) {
                self.bodySquash.set(0); self.headY.set(0); self.headX.set(0)
                self.leftPawY.set(0); self.rightPawY.set(0); self.earPerk.set(1)
            }

        // ══════════════════════════════════════════════════════
        // ── FOCUSED: Turbo Type Explosion ──
        // Furious typing, laptop overheats, EXPLODES, cat blown backward, eureka!
        // ══════════════════════════════════════════════════════
        case .focused:
            let sx = catX.f, sy = catY.f
            configSprings(stiffness: 180, damping: 13)
            costume = .labGoggles
            earPerk.set(2); tailSpeed = 1.5; tailAmplitude = 0.2
            eyeShape = .wide; mouthShape = .closed

            after(0.00) { self.spawnPropIfNeeded(.laptop); self.headY.set(1) }
            after(0.25) { self.leftPawY.set(-3); self.rightPawY.set(-3); self.bodySquash.set(-0.5) }
            after(0.38) { self.leftPawY.set(0); self.rightPawY.set(0); self.bodySquash.set(0.3) }
            after(0.45) { self.leftPawY.set(-3); self.eyeX.set(-1) }
            after(0.52) { self.leftPawY.set(0); self.rightPawY.set(-3); self.eyeX.set(1) }
            after(0.59) { self.rightPawY.set(0); self.leftPawY.set(-4); self.eyeX.set(-1) }
            after(0.66) { self.leftPawY.set(0); self.rightPawY.set(-4); self.eyeX.set(1) }
            after(0.73) { self.rightPawY.set(0); self.leftPawY.set(-4); self.eyeX.set(-1) }
            after(0.80) { self.leftPawY.set(0); self.rightPawY.set(-5); self.eyeX.set(0) }
            after(0.87) { self.rightPawY.set(0); self.leftPawY.set(-5); self.eyeX.set(1) }
            after(0.94) { self.leftPawY.set(0); self.rightPawY.set(-5); self.eyeX.set(-1) }
            after(1.01) { self.rightPawY.set(0); self.leftPawY.set(-5); self.eyeX.set(1) }
            after(1.08) { self.leftPawY.set(0); self.rightPawY.set(-5); self.eyeX.set(0) }
            after(1.15) {
                self.leftPawY.set(0); self.rightPawY.set(0); self.eyeX.set(0)
                self.spawnCatParticles(.sparkle, count: 2, color: .cyan, dy: 12)
            }
            after(1.30) {
                self.headY.set(2); self.bodySquash.set(0.3)
                self.leftPawY.set(-4)
            }
            after(1.38) { self.leftPawY.set(0); self.rightPawY.set(-4) }
            after(1.46) { self.rightPawY.set(0); self.leftPawY.set(-5) }
            after(1.50) {
                self.leftPawY.set(0)
                self.spawnCatParticles(.puff, count: 2, color: Color(red: 0.6, green: 0.6, blue: 0.65), dy: 10)
            }
            after(1.65) {
                self.spawnCatParticles(.puff, count: 2, color: Color(red: 0.5, green: 0.5, blue: 0.55), dy: 8)
                self.spawnCatParticles(.sparkle, count: 1, color: .orange, dy: 10)
            }
            after(1.80) {
                self.catY.set(sy - 10, stiffness: 200, damping: 8)
                self.catX.set(sx + 5, stiffness: 200, damping: 8)
                self.bodySquash.set(2.0, stiffness: 400, damping: 8)
                self.headY.set(-2); self.earPerk.set(-2)
                self.eyeShape = .wide; self.mouthShape = .open
                self.leftPawY.set(-4); self.rightPawY.set(-4)
                for _ in 0..<4 {
                    self.spawnCatParticles(.puff, count: 1, color: .white, dx: .random(in: -8...8), dy: .random(in: 4...12))
                }
                self.spawnCatParticles(.starBurst, count: 2, color: .orange, dy: 6)
                for c in [Color.red, .orange, .yellow] {
                    self.spawnCatParticles(.confetti, count: 2, color: c, dy: 8)
                }
                self.updateProp(.laptop) { $0.relX = 8; $0.relY = 20 }
            }
            after(2.10) { self.bodySquash.set(0.5); self.headX.set(-2) }
            after(2.20) { self.headX.set(2) }
            after(2.30) {
                self.catY.set(sy, stiffness: 150, damping: 12)
                self.catX.set(sx, stiffness: 150, damping: 12)
                self.bodySquash.set(1.5, stiffness: 300, damping: 12)
                self.headY.set(2); self.leftPawY.set(0); self.rightPawY.set(0)
                self.spawnCatParticles(.puff, count: 3, color: .white, dy: 2)
            }
            after(2.50) { self.headX.set(-2); self.bodySquash.set(-0.3); self.headY.set(0) }
            after(2.60) { self.headX.set(2) }
            after(2.70) { self.headX.set(-1) }
            after(2.80) { self.headX.set(0); self.eyeShape = .wide; self.eyeOpenness.set(1) }
            after(3.00) {
                self.bodySquash.set(-1.5, stiffness: 300, damping: 8)
                self.headY.set(-2); self.leftPawY.set(-6); self.rightPawY.set(-6)
                self.eyeShape = .happy; self.mouthShape = .open; self.earPerk.set(4)
                self.spawnCatParticles(.starBurst, count: 2, color: .yellow, dy: -14)
                self.spawnCatParticles(.sparkle, count: 3, color: .cyan, dy: -12)
            }
            after(3.30) {
                self.bodySquash.set(-0.5); self.headY.set(-1)
                self.leftPawY.set(-3); self.rightPawY.set(-3)
                self.mouthShape = .smile
            }
            after(3.60) {
                self.bodySquash.set(0); self.headY.set(0); self.headX.set(0)
                self.leftPawY.set(0); self.rightPawY.set(0); self.eyeX.set(0); self.earPerk.set(1)
            }

        // ══════════════════════════════════════════════════════
        // ── SLEEPY: Drifting Off ──
        // Cat drifts LEFT while fighting sleep, eventually curls up where it stops
        // ══════════════════════════════════════════════════════
        case .sleepy:
            configSprings(stiffness: 50, damping: 22)
            costume = .sleepCap
            tailSpeed = 0.8; tailAmplitude = 0.2

            // Cat sits, eyes half-closed
            after(0.00) {
                self.eyeShape = .sleepy; self.eyeOpenness.set(0.5)
                self.mouthShape = .closed; self.earPerk.set(-1)
            }
            // Head starts to nod...
            after(0.40) {
                self.headY.set(2, stiffness: 30, damping: 20)
                self.eyeOpenness.set(0.2)
            }
            // JERKS awake!
            after(0.80) {
                self.headY.set(-2, stiffness: 200, damping: 10)
                self.eyeShape = .wide; self.eyeOpenness.set(0.8)
                self.earPerk.set(2)
            }
            // Settles... starts DRIFTING LEFT unconsciously
            after(1.10) {
                self.headY.set(0); self.eyeShape = .sleepy; self.eyeOpenness.set(0.4)
                self.earPerk.set(-1)
                self.catX.set(self.catX.f - 3, stiffness: 15, damping: 20)
            }
            // Head nods again — drifts more LEFT
            after(1.50) {
                self.headY.set(3, stiffness: 25, damping: 22)
                self.eyeOpenness.set(0.1)
                self.catX.set(self.catX.f - 3, stiffness: 15, damping: 20)
                self.spawnCatParticles(.zzz, count: 1, color: .white.opacity(0.6), dx: 5, dy: -10)
            }
            // Weaker jerk — barely awake
            after(1.90) {
                self.headY.set(-1, stiffness: 120, damping: 14)
                self.eyeOpenness.set(0.25)
            }
            // Gives up fighting... drifts more LEFT
            after(2.20) {
                self.headY.set(2); self.eyeOpenness.set(0.1)
                self.catX.set(self.catX.f - 3, stiffness: 12, damping: 22)
            }
            // Blanket appears where cat stopped
            after(2.50) {
                self.spawnPropIfNeeded(.blanket)
                self.headY.set(1)
            }
            // Curls into blanket at current position
            after(2.80) {
                self.bodySquash.set(1.5, stiffness: 25, damping: 26)
                self.headY.set(3); self.eyeOpenness.set(0.05)
                self.earPerk.set(-3)
                self.spawnCatParticles(.zzz, count: 1, color: .white.opacity(0.7), dx: 5, dy: -8)
            }
            // Deep sleep
            after(3.20) {
                self.bodySquash.set(2.0)
                self.spawnCatParticles(.zzz, count: 1, color: .white.opacity(0.5), dx: 6, dy: -6)
            }
            // Gentle breathing
            after(3.50) { self.bodySquash.set(1.8) }
            after(3.70) { self.bodySquash.set(2.0) }
            after(3.80) {
                self.catX.set(self.catX.f + 9, stiffness: 40, damping: 20)
                self.bodySquash.set(0); self.headY.set(0); self.earPerk.set(-1)
                self.eyeOpenness.set(1)
            }

        // ══════════════════════════════════════════════════════
        // ── RECOVERING: Rising Lotus ──
        // Cat starts as compressed ball, progressively unfolds upward like a flower
        // ══════════════════════════════════════════════════════
        case .recovering:
            configSprings(stiffness: 120, damping: 14)
            costume = .flowerGarland
            eyeShape = .happy; mouthShape = .smile; tailSpeed = 2.5; tailAmplitude = 0.6

            // Cat starts VERY compressed — like a ball on the ground
            after(0.00) {
                self.bodySquash.set(2.5, stiffness: 40, damping: 24)
                self.headY.set(5); self.earPerk.set(-4)
                self.eyeOpenness.set(0.1)
                self.spawnPropIfNeeded(.yogaMat)
            }
            // First unfold — slight rise
            after(0.50) {
                self.bodySquash.set(2.0, stiffness: 50, damping: 22)
                self.headY.set(4); self.eyeOpenness.set(0.2)
            }
            // Rising more — arms start to show
            after(1.00) {
                self.bodySquash.set(1.2, stiffness: 70, damping: 18)
                self.headY.set(2); self.earPerk.set(-2)
                self.eyeOpenness.set(0.4)
                self.spawnCatParticles(.sparkle, count: 1, color: .teal, dy: -4)
            }
            // Half risen — eyes opening
            after(1.40) {
                self.bodySquash.set(0.5, stiffness: 100, damping: 16)
                self.headY.set(1); self.earPerk.set(0)
                self.eyeOpenness.set(0.7)
            }
            // STANDING! Cat is upright
            after(1.80) {
                self.bodySquash.set(0, stiffness: 120, damping: 14)
                self.headY.set(0); self.earPerk.set(1)
                self.eyeOpenness.set(1)
                self.spawnCatParticles(.sparkle, count: 2, color: .teal, dy: -10)
            }
            // First stretch — left paw reaches UP
            after(2.10) {
                self.leftPawY.set(-6); self.bodySquash.set(-0.8)
                self.headY.set(-1); self.earPerk.set(2)
                self.spawnCatParticles(.sparkle, count: 1, color: .teal, dy: -14)
            }
            // Switch — right paw UP
            after(2.40) {
                self.leftPawY.set(0); self.rightPawY.set(-6)
                self.spawnCatParticles(.sparkle, count: 1, color: .teal, dy: -14)
            }
            // TALL BLOOM! Both paws up, reaching for sky
            after(2.70) {
                self.leftPawY.set(-6); self.rightPawY.set(-6)
                self.bodySquash.set(-1.8, stiffness: 200, damping: 10)
                self.catY.set(self.catY.f - 3)
                self.headY.set(-2); self.earPerk.set(4)
                self.spawnCatParticles(.heart, count: 2, color: .teal, dy: -16)
                self.spawnCatParticles(.sparkle, count: 2, color: .green, dy: -14)
            }
            // Hold the bloom
            after(3.10) {
                self.bodySquash.set(-1.5)
            }
            // Namaste — return to earth
            after(3.40) {
                self.catY.set(self.catY.f + 3, stiffness: 80, damping: 16)
                self.bodySquash.set(-0.3); self.headY.set(-1)
                self.leftPawY.set(-2); self.rightPawY.set(-2)
                self.mouthShape = .heart
            }
            after(3.80) {
                self.bodySquash.set(0); self.headY.set(0)
                self.leftPawY.set(0); self.rightPawY.set(0); self.earPerk.set(1)
            }

        // ══════════════════════════════════════════════════════
        // ── SICK: Fever Sneeze ──
        // Blanket-wrapped cat shivers with thermometer, builds to HUGE sneeze
        // ══════════════════════════════════════════════════════
        case .sick:
            configSprings(stiffness: 100, damping: 16)
            costume = .faceMask
            eyeShape = .sleepy; mouthShape = .frown; earPerk.set(-1)
            tailSpeed = 0.5; tailAmplitude = 0.15

            after(0.00) { self.headY.set(2); self.bodySquash.set(0.5); self.eyeOpenness.set(0.4); self.blushIntensity.set(0.9) }
            // Shivers
            after(0.25) { self.headX.set(-1); self.bodySquash.set(0.7) }
            after(0.35) { self.headX.set(1); self.bodySquash.set(0.3) }
            after(0.45) { self.headX.set(-1); self.bodySquash.set(0.7) }
            after(0.55) { self.headX.set(1); self.bodySquash.set(0.3) }
            after(0.65) { self.headX.set(0); self.bodySquash.set(0.5) }
            // Thermometer appears
            after(0.80) { self.spawnPropIfNeeded(.thermometer); self.headX.set(-1) }
            // More shivers
            after(1.00) { self.headX.set(1); self.bodySquash.set(0.3) }
            after(1.10) { self.headX.set(-1); self.bodySquash.set(0.7) }
            after(1.20) { self.headX.set(0); self.bodySquash.set(0.5) }
            // Nose tickle
            after(1.40) { self.headY.set(0); self.earPerk.impulse(5); self.eyeOpenness.set(0.5); self.mouthShape = .open }
            // Inhale 1
            after(1.55) { self.bodySquash.set(-0.8, stiffness: 120, damping: 12); self.headY.set(-1); self.earPerk.set(1); self.eyeOpenness.set(0.3) }
            // Inhale 2 — BIGGER
            after(1.75) {
                self.bodySquash.set(-1.5, stiffness: 150, damping: 10); self.catY.set(self.catY.f - 2)
                self.headY.set(-2); self.earPerk.set(3); self.eyeOpenness.set(0.1); self.eyeShape = .wide
                self.leftPawY.set(-4); self.rightPawY.set(-4)
            }
            // ACHOO!!! Cat LAUNCHED LEFTWARD OFF SCREEN!
            after(2.00) {
                self.bodySquash.set(2.0, stiffness: 400, damping: 8)
                self.catX.set(self.catX.f - 30, stiffness: 200, damping: 8)
                self.catY.set(self.catY.f + 2) // restore catY
                self.headY.set(3); self.earPerk.set(-3); self.mouthShape = .open
                self.leftPawY.set(0); self.rightPawY.set(0); self.eyeOpenness.set(0.05)
                for _ in 0..<6 {
                    self.spawnCatParticles(.puff, count: 1, color: .white, dx: .random(in: -10...10), dy: .random(in: -8...4))
                }
                self.spawnCatParticles(.starBurst, count: 1, color: Color(red: 0.90, green: 0.55, blue: 0.55), dy: 4)
            }
            // Cat stumbles back from left
            after(2.50) {
                self.catX.set(self.catX.f + 30, stiffness: 60, damping: 16)
                self.bodySquash.set(0.5); self.headY.set(0)
            }
            // Dazed landing
            after(2.90) { self.bodySquash.set(0.3); self.eyeShape = .wide; self.eyeOpenness.set(0.8); self.headX.set(-1) }
            // Wobble
            after(3.05) { self.headX.set(1) }
            after(3.20) { self.headX.set(-1) }
            after(3.35) { self.headX.set(0) }
            // Weak smile
            after(3.50) {
                self.mouthShape = .smile; self.eyeShape = .sleepy; self.eyeOpenness.set(0.5)
                self.bodySquash.set(0); self.blushIntensity.set(0.7)
                self.spawnCatParticles(.puff, count: 1, color: Color(red: 0.85, green: 0.85, blue: 0.90), dy: -4)
            }
            after(3.80) {
                self.bodySquash.set(0); self.headY.set(0); self.headX.set(0)
                self.earPerk.set(-1); self.eyeOpenness.set(1); self.blushIntensity.set(0.45)
            }

        // ══════════════════════════════════════════════════════
        // ── ZEN: Transcendence Flight ──
        // Cat meditates on carpet, ASCENDS through the ceiling, returns enlightened
        // ══════════════════════════════════════════════════════
        case .zen:
            configSprings(stiffness: 35, damping: 26)
            costume = .witchHat
            tailSpeed = 0.5; tailAmplitude = 0.3

            // Flying carpet appears below
            after(0.00) { self.spawnPropIfNeeded(.flyingCarpet) }
            // Cat sits on carpet, crosses paws
            after(0.30) {
                self.leftPawY.set(-1); self.rightPawY.set(-1)
                self.bodySquash.set(-0.3)
            }
            // Eyes close — meditation begins, tail nearly stops
            after(0.70) {
                self.eyeShape = .happy; self.eyeOpenness.set(0.2)
                self.mouthShape = .neutral
                self.tailSpeed = 0.2; self.tailAmplitude = 0.1
            }
            // Carpet + cat begin rising slowly
            after(1.20) {
                self.catY.set(self.catY.f - 5, stiffness: 15, damping: 18)
                self.bodySquash.set(-0.5)
                self.spawnCatParticles(.sparkle, count: 2, color: Color(red: 0.95, green: 0.85, blue: 0.40), dy: 4)
            }
            // Higher — golden aura forms
            after(1.80) {
                self.catY.set(self.catY.f - 6, stiffness: 12, damping: 20)
                for i in 0..<6 {
                    let angle = CGFloat(i) * .pi / 3
                    self.spawnCatParticles(.sparkle, count: 1,
                        color: Color(red: 0.95, green: 0.85, blue: 0.40),
                        dx: cos(angle) * 8, dy: sin(angle) * 5 - 6)
                }
            }
            // ASCENDS THROUGH CEILING — nearly off screen!
            after(2.50) {
                self.catY.set(self.catY.f - 10, stiffness: 8, damping: 24)
                self.spawnCatParticles(.sparkle, count: 2, color: Color(red: 0.95, green: 0.85, blue: 0.40), dy: -4)
            }
            // Very high — inhale
            after(3.20) { self.bodySquash.set(-0.6) }
            // Breathing while floating high — radiant aura
            after(3.60) {
                self.bodySquash.set(-0.3)
                for i in 0..<6 {
                    let angle = CGFloat(i) * .pi / 3
                    self.spawnCatParticles(.sparkle, count: 1,
                        color: Color(red: 0.95, green: 0.85, blue: 0.40),
                        dx: cos(angle) * 7, dy: sin(angle) * 4 - 6)
                }
            }
            after(4.00) { self.bodySquash.set(-0.5) }
            after(4.40) { self.bodySquash.set(-0.3) }
            // Begin slow descent — drifting back to earth
            after(4.80) {
                self.catY.set(self.catY.f + 21, stiffness: 8, damping: 26)
                self.bodySquash.set(0)
                self.tailSpeed = 0.5; self.tailAmplitude = 0.3
                self.spawnCatParticles(.sparkle, count: 2, color: Color(red: 0.95, green: 0.85, blue: 0.40), dy: -10)
            }
            // Gentle landing
            after(5.80) { self.bodySquash.set(0.3) }
            after(6.20) {
                self.bodySquash.set(0); self.leftPawY.set(0); self.rightPawY.set(0)
                self.eyeOpenness.set(1)
            }

        // ══════════════════════════════════════════════════════
        // ── PROUD: Rocket Rider ──
        // Cat mounts a rocket LEFT, rides it UPWARD, floats back down
        // ══════════════════════════════════════════════════════
        case .proud:
            configSprings(stiffness: 280, damping: 9)
            costume = .crown
            tailSpeed = 7.0; tailAmplitude = 1.5

            // Regal entrance
            after(0.00) {
                self.bodySquash.set(-0.8)
                self.eyeShape = .happy; self.mouthShape = .smile; self.earPerk.set(3)
            }
            // Big rocket appears to the LEFT
            after(0.30) {
                self.spawnPropForce(.rocket, dx: -15, dy: 2)
                self.spawnCatParticles(.sparkle, count: 2, color: .orange, dx: -15, dy: 4)
            }
            // Cat WALKS to rocket
            after(0.55) {
                self.catX.set(self.catX.f - 8, stiffness: 100, damping: 12)
                self.headX.set(-2)
            }
            after(0.65) { self.leftPawY.set(-2); self.bodySquash.set(0.3) }
            after(0.75) { self.leftPawY.set(0); self.rightPawY.set(-2); self.bodySquash.set(-0.3) }
            // Cat "mounts" the rocket — sits alongside it
            after(0.90) {
                self.bodySquash.set(0.3); self.headX.set(0)
                self.leftPawY.set(-1); self.rightPawY.set(-1)
            }
            // Ignition sparkles!
            after(1.15) {
                self.spawnCatParticles(.sparkle, count: 2, color: .orange, dx: -8, dy: 6)
                self.spawnCatParticles(.sparkle, count: 2, color: .yellow, dx: -8, dy: 8)
                self.earPerk.set(-1); self.headY.set(1)
            }
            // Smoke and fire!
            after(1.40) {
                self.spawnCatParticles(.puff, count: 3, color: .white, dx: -8, dy: 6)
                self.mouthShape = .open
            }
            // LIFTOFF! Cat + rocket rise together!
            after(1.65) {
                self.catY.set(self.catY.f - 6, stiffness: 100, damping: 12)
                self.bodySquash.set(-1.2)
                self.headY.set(-2); self.earPerk.set(4)
                self.spawnCatParticles(.sparkle, count: 2, color: .orange, dx: -6, dy: 8)
            }
            // HIGHER! Confetti explosion
            after(2.00) {
                self.catY.set(self.catY.f - 5, stiffness: 80, damping: 14)
                for c in [Color.yellow, .orange, .red, .pink, .blue, .green] {
                    self.spawnCatParticles(.confetti, count: 1, color: c, dy: -16)
                }
            }
            // PEAK ALTITUDE — victory in the sky!
            after(2.40) {
                self.catY.set(self.catY.f - 4, stiffness: 60, damping: 16)
                self.bodySquash.set(-1.8)
                self.leftPawY.set(-6); self.rightPawY.set(-6)
                self.headY.set(-2)
                self.spawnCatParticles(.starBurst, count: 2, color: .yellow, dy: -14)
            }
            // Cat "dismounts" — rocket continues, cat floats
            after(2.80) {
                self.bodySquash.set(-0.3); self.leftPawY.set(-2); self.rightPawY.set(-2)
                self.mouthShape = .smile
            }
            // Slow dreamy descent — soft springs
            after(3.10) {
                self.catY.set(self.catY.f + 15, stiffness: 15, damping: 22)
                self.catX.set(self.catX.f + 8, stiffness: 15, damping: 22)
                self.bodySquash.set(0)
            }
            // Gentle sway during descent
            after(3.50) { self.headX.set(-2) }
            after(3.80) { self.headX.set(2) }
            after(4.10) { self.headX.set(0) }
            // Landing — gentle squash
            after(4.40) {
                self.bodySquash.set(0.5, stiffness: 200, damping: 14)
                self.leftPawY.set(0); self.rightPawY.set(0)
                self.spawnCatParticles(.puff, count: 2, color: .white, dy: 2)
            }
            // Proud pose
            after(4.70) {
                self.bodySquash.set(-0.5); self.headY.set(-1)
                self.mouthShape = .smile
                self.spawnCatParticles(.heart, count: 2, color: .pink, dy: -12)
            }
            after(4.80) {
                self.bodySquash.set(0); self.headY.set(0)
                self.leftPawY.set(0); self.rightPawY.set(0); self.earPerk.set(1)
                self.headX.set(0)
            }

        // ══════════════════════════════════════════════════════
        // ── ALERT: Warning Patrol ──
        // Safety vest on, warning sign appears, cat dashes LEFT to patrol
        // ══════════════════════════════════════════════════════
        case .alert:
            configSprings(stiffness: 350, damping: 8)
            costume = .safetyVest
            earPerk.set(5); tailSpeed = 8.0; tailAmplitude = 2.0
            eyeShape = .wide

            after(0.00) { self.bodySquash.set(-1.5, stiffness: 380, damping: 7); self.headY.set(-2) }
            // Warning sign
            after(0.20) { self.spawnPropIfNeeded(.warningSign); self.spawnCatParticles(.sparkle, count: 2, color: .yellow, dx: -16, dy: -4) }
            // Point at sign
            after(0.40) { self.leftPawY.set(-5); self.headX.set(-4); self.eyeX.set(-1); self.bodySquash.set(-0.8) }
            // Crouch
            after(0.60) { self.bodySquash.set(0.8, stiffness: 350, damping: 10); self.headY.set(1); self.leftPawY.set(0) }
            // SCAN LEFT
            after(0.80) { self.headX.set(-5); self.eyeX.set(-2); self.bodySquash.set(-0.5) }
            // DETECTED! DASH LEFT OFF SCREEN!
            after(1.00) {
                self.headX.set(-3); self.bodySquash.set(-1.5); self.headY.set(0)
                self.catX.set(self.catX.f - 40, stiffness: 200, damping: 8)
                self.spawnCatParticles(.puff, count: 3, color: .white, dy: 0)
            }
            // Off screen — teleport to RIGHT side
            after(1.50) {
                self.catX.snap(to: self.catX.f + 80) // far right off screen
                self.headX.set(0)
            }
            // ENTER FROM RIGHT running left
            after(1.60) {
                self.catX.set(self.catX.f - 20, stiffness: 180, damping: 10)
                self.headX.set(-2); self.bodySquash.set(-0.8)
            }
            after(1.70) { self.leftPawY.set(-3); self.bodySquash.set(0.3) }
            after(1.80) { self.leftPawY.set(0); self.rightPawY.set(-3); self.bodySquash.set(-0.3) }
            after(1.90) {
                self.catX.set(self.catX.f - 20, stiffness: 150, damping: 12)
                self.leftPawY.set(-3); self.rightPawY.set(0)
            }
            // Arrive at center
            after(2.10) {
                self.leftPawY.set(0); self.rightPawY.set(0); self.headX.set(0); self.eyeX.set(0)
                self.spawnCatParticles(.puff, count: 2, color: .white, dy: 2)
            }
            // ALL CLEAR! Salute!
            after(2.30) {
                self.bodySquash.set(-1.8, stiffness: 400, damping: 7)
                self.leftPawY.set(-6); self.earPerk.set(5)
                self.spawnCatParticles(.starBurst, count: 2, color: .yellow, dy: -14)
                self.spawnCatParticles(.sparkle, count: 2, color: .orange, dy: -12)
            }
            after(2.70) { self.bodySquash.set(-1.0); self.rightPawY.set(-2) }
            after(3.00) {
                self.bodySquash.set(0); self.leftPawY.set(0); self.rightPawY.set(0)
                self.earPerk.set(3); self.headX.set(0); self.eyeX.set(0); self.headY.set(0)
            }

        // ══════════════════════════════════════════════════════
        // ── ADVENTUROUS: Globe Trotter ──
        // Cat examines spinning globe, runs LEFT off-screen, returns with treasure
        // ══════════════════════════════════════════════════════
        case .adventurous:
            configSprings(stiffness: 250, damping: 10)
            costume = .explorerHat
            earPerk.set(3); tailSpeed = 5.0; tailAmplitude = 1.2

            after(0.00) { self.bodySquash.set(-0.8); self.eyeShape = .happy; self.mouthShape = .open; self.headY.set(-1); self.earPerk.set(4) }
            // Globe appears LEFT
            after(0.25) { self.spawnPropIfNeeded(.globe); self.bodySquash.set(0.3); self.headY.set(0) }
            // Examine globe
            after(0.50) { self.headX.set(-3); self.headY.set(1); self.eyeShape = .wide; self.eyeX.set(-1) }
            after(0.75) { self.headX.set(-2); self.headY.set(-1) }
            // POINTS at spot!
            after(1.00) {
                self.leftPawY.set(-5); self.headX.set(-3); self.bodySquash.set(-0.5); self.mouthShape = .open
                self.spawnCatParticles(.sparkle, count: 2, color: .green, dx: -14, dy: 0)
            }
            // Cat looks UP at the window...
            after(1.15) { self.headY.set(-3); self.headX.set(-1); self.leftPawY.set(0); self.eyeX.set(0) }
            // LAUNCHES TOWARD WINDOW! Cat jumps UP and LEFT
            after(1.30) {
                self.catY.set(self.catY.f - 35, stiffness: 150, damping: 10)
                self.catX.set(self.catX.f - 15, stiffness: 100, damping: 12)
                self.bodySquash.set(-2.0); self.leftPawY.set(-6); self.rightPawY.set(-6)
                self.earPerk.set(5); self.mouthShape = .open
                self.spawnCatParticles(.sparkle, count: 3, color: .cyan, dy: -16)
                self.spawnCatParticles(.puff, count: 3, color: .white, dy: 2)
            }
            // Cat reaches window area — disappears!
            after(1.60) {
                self.bodySquash.set(-1.5)
                self.spawnCatParticles(.sparkle, count: 4, color: .yellow, dy: -8)
            }
            // Cat is "gone" through the window
            after(1.90) { self.leftPawY.set(0); self.rightPawY.set(0) }
            // Cat RETURNS through the window! Falling back down
            after(2.30) {
                self.catY.set(self.catY.f + 35, stiffness: 80, damping: 14)
                self.catX.set(self.catX.f + 15, stiffness: 60, damping: 16)
                self.bodySquash.set(0.5)
                self.spawnCatParticles(.sparkle, count: 3, color: .yellow, dy: -14)
                self.spawnCatParticles(.sparkle, count: 3, color: .green, dy: -12)
            }
            // Landing with treasure!
            after(2.80) {
                self.bodySquash.set(1.0, stiffness: 250, damping: 12); self.headY.set(1)
                self.spawnCatParticles(.puff, count: 3, color: .white, dy: 2)
            }
            // TREASURE FOUND! BIG JUMP
            after(3.05) {
                self.bodySquash.set(-2.0, stiffness: 350, damping: 7)
                self.headY.set(-3); self.catY.set(self.catY.f - 5)
                self.leftPawY.set(-6); self.rightPawY.set(-6); self.earPerk.set(5)
                self.eyeShape = .happy; self.mouthShape = .open; self.eyeX.set(0)
                self.spawnCatParticles(.starBurst, count: 2, color: .yellow, dy: -16)
                self.spawnCatParticles(.sparkle, count: 3, color: .green, dy: -14)
            }
            // Landing
            after(3.35) {
                self.catY.set(self.catY.f + 5); self.bodySquash.set(1.0, stiffness: 250, damping: 12); self.headY.set(1)
                for c in [Color.yellow, .green, .orange, .cyan] { self.spawnCatParticles(.confetti, count: 1, color: c, dy: -12) }
                self.spawnCatParticles(.puff, count: 2, color: .white, dy: 2)
            }
            // Victory
            after(3.65) {
                self.bodySquash.set(-0.5); self.headY.set(-1); self.leftPawY.set(-4); self.rightPawY.set(-4)
                self.mouthShape = .smile; self.earPerk.set(3)
            }
            after(4.00) {
                self.bodySquash.set(0); self.headY.set(0); self.headX.set(0)
                self.leftPawY.set(0); self.rightPawY.set(0); self.earPerk.set(2); self.eyeX.set(0)
            }
        }
    }

    private func startRevealLoop() {
        revealLoopTask?.cancel()
        dismissProps()
        particles.removeAll()
        costume = .none

        // Save home position so reveals with relative offsets don't accumulate drift
        let savedX = catX.f
        let savedY = catY.f

        revealLoopTask = Task { @MainActor [weak self] in
            guard let self else { return }
            let totalLoops = 1
            let interval = self.catState.revealDuration

            for i in 0..<totalLoops {
                guard !Task.isCancelled else { return }
                // Reset cat to home position before each reveal
                self.catX.snap(to: savedX)
                self.catY.snap(to: savedY)
                self.rollAngle.snap(to: 0); self.catScale.snap(to: 1); self.catScaleX.snap(to: 1)
                self.catPose = .frontSitting
                self.dismissProps()
                self.costume = .none
                self.configSprings(stiffness: 180, damping: 16)
                self.bodySquash.set(0); self.headX.set(0); self.headY.set(0)
                self.leftPawY.set(0); self.rightPawY.set(0)
                self.earPerk.set(1); self.eyeX.set(0); self.eyeOpenness.set(1)
                self.eyeShape = .normal; self.mouthShape = .neutral
                self.playStateReveal()
                if i < totalLoops - 1 {
                    try? await Task.sleep(nanoseconds: UInt64(interval * 1_000_000_000))
                }
            }
            try? await Task.sleep(nanoseconds: UInt64(interval * 1_000_000_000))
            guard !Task.isCancelled else { return }
            self.catX.snap(to: savedX)
            self.catY.snap(to: savedY)
            self.rollAngle.snap(to: 0)
            self.dismissProps()
            try? await Task.sleep(nanoseconds: 350_000_000)
            guard !Task.isCancelled else { return }
            self.applyIdleState()
        }
    }

    // ══════════════════════════════════════
    // ── IDLE BEHAVIORS ──
    // ══════════════════════════════════════

    private func scheduleIdleBehavior() {
        let delay = isConnected ? Double.random(in: 3.5...7) : Double.random(in: 7...14)
        after(delay) {
            guard !self.isTapAnimating, !self.isLongPressAnimating else {
                self.scheduleIdleBehavior(); return
            }
            self.playRandomIdle()
            self.scheduleIdleBehavior()
        }
    }

    private func playRandomIdle() {
        if isConnected {
            switch Int.random(in: 0...6) {
            case 0: idleLookAround()
            case 1: idleEarWiggle()
            case 2: idleTailFlick()
            case 3: idlePawTuck()
            case 4: idleHeadTilt()
            case 5: idleStretch()
            default: idlePawKnead()
            }
        } else {
            switch Int.random(in: 0...2) {
            case 0: idleSleepTwitch()
            case 1: idleDeepSigh()
            default: idleSleepEarFlick()
            }
        }
    }

    private func idleLookAround() {
        eyeX.set(-1); headX.set(-1)
        idleAfter(0.4) { self.eyeX.set(0) }
        idleAfter(0.7) { self.eyeX.set(1); self.headX.set(1) }
        idleAfter(1.1) { self.eyeX.set(0); self.headX.set(0) }
    }
    private func idleEarWiggle() {
        earPerk.set(3)
        idleAfter(0.2) { self.earPerk.set(0) }
        idleAfter(0.4) { self.earPerk.set(3) }
        idleAfter(0.6) { self.earPerk.set(1) }
    }
    private func idleTailFlick() {
        let prev = (tailSpeed, tailAmplitude)
        tailSpeed = 9.0; tailAmplitude = 2.0
        idleAfter(0.6) { self.tailSpeed = prev.0; self.tailAmplitude = prev.1 }
    }
    private func idlePawTuck() {
        leftPawY.set(-1)
        idleAfter(0.15) { self.leftPawY.set(-2) }
        idleAfter(0.35) { self.leftPawY.set(-1) }
        idleAfter(0.5) { self.leftPawY.set(0) }
    }
    private func idleHeadTilt() {
        let dir: CGFloat = Bool.random() ? -2 : 2
        headX.set(dir); earPerk.set(3)
        idleAfter(0.8) { self.headX.set(0); self.earPerk.set(1) }
    }
    private func idleStretch() {
        bodySquash.set(-1.0); leftPawY.set(-3); rightPawY.set(-3)
        headY.set(-2); earPerk.set(0); mouthShape = .open
        idleAfter(0.5) { self.bodySquash.set(-0.5) }
        idleAfter(0.8) { self.bodySquash.set(0.5); self.headY.set(0); self.mouthShape = .neutral }
        idleAfter(1.0) {
            self.bodySquash.set(0); self.leftPawY.set(0); self.rightPawY.set(0)
            self.earPerk.set(1); self.mouthShape = .neutral
        }
    }
    private func idlePawKnead() {
        leftPawY.set(-1); bodySquash.set(0.25)
        idleAfter(0.12) { self.leftPawY.set(0); self.rightPawY.set(-1); self.bodySquash.set(-0.15) }
        idleAfter(0.24) { self.rightPawY.set(0); self.leftPawY.set(-1); self.bodySquash.set(0.25) }
        idleAfter(0.36) { self.leftPawY.set(0); self.bodySquash.set(0) }
    }
    private func idleSleepTwitch() {
        bodySquash.set(0.4)
        idleAfter(0.1) { self.bodySquash.set(-0.25) }
        idleAfter(0.2) { self.bodySquash.set(0) }
    }
    private func idleDeepSigh() {
        bodySquash.set(-0.6)
        idleAfter(0.5) { self.bodySquash.set(0.3) }
        idleAfter(0.8) { self.bodySquash.set(0) }
    }
    private func idleSleepEarFlick() {
        earPerk.impulse(8)
        idleAfter(0.25) { self.earPerk.impulse(5) }
    }

    // ══════════════════════════════════════
    // ── BLINK & EAR SCHEDULING ──
    // ══════════════════════════════════════

    private func scheduleBlink() {
        let delay = isConnected ? Double.random(in: 3...6) : Double.random(in: 5...9)
        Task { [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000))
            guard let self else { return }
            guard !self.isTapAnimating, !self.isLongPressAnimating else {
                self.scheduleBlink(); return
            }
            // Close
            self.eyeOpenness.set(0.05, stiffness: 400, damping: 20)
            let closedDur = self.isConnected ? 0.08 : 0.25
            try? await Task.sleep(nanoseconds: UInt64(closedDur * 1_000_000_000))
            // Open
            self.eyeOpenness.set(1, stiffness: 300, damping: 16)
            // 15% double blink
            if Double.random(in: 0...1) < 0.15 {
                try? await Task.sleep(nanoseconds: 150_000_000)
                self.eyeOpenness.set(0.05, stiffness: 450, damping: 22)
                try? await Task.sleep(nanoseconds: UInt64(closedDur * 0.5 * 1_000_000_000))
                self.eyeOpenness.set(1, stiffness: 300, damping: 16)
            }
            self.scheduleBlink()
        }
    }

    private func scheduleEarTwitch() {
        let delay = isConnected ? Double.random(in: 3...6) : Double.random(in: 8...14)
        Task { [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000))
            guard let self else { return }
            guard !self.isTapAnimating, !self.isLongPressAnimating else {
                self.scheduleEarTwitch(); return
            }
            // Quick impulse-based ear twitch (springy!)
            self.earPerk.impulse(12)
            try? await Task.sleep(nanoseconds: 200_000_000)
            self.earPerk.impulse(-6)
            self.scheduleEarTwitch()
        }
    }

    // ══════════════════════════════════════
    // ── ANIMATION LIFECYCLE ──
    // ══════════════════════════════════════

    private func startAnimations() {
        withAnimation(.easeInOut(duration: 3.0).repeatForever(autoreverses: true)) { breathingScale = 1.025 }
        withAnimation(.easeInOut(duration: 2.8).repeatForever(autoreverses: true)) { glowPhase = 1.06 }
        startAnimTimer()
        scheduleBlink(); scheduleEarTwitch(); scheduleIdleBehavior()
    }

    // ══════════════════════════════════════
    // ── DEBUG: Preview a specific animation ──
    // ══════════════════════════════════════

    /// All cat states for debug preview listing
    static let allStates: [CatState] = [
        .energetic, .tired, .stressed, .sad, .relaxed,
        .curious, .happy, .focused, .sleepy, .recovering,
        .sick, .zen, .proud, .alert, .adventurous
    ]

    /// Trigger a specific state's reveal animation for preview
    func debugPlayState(_ state: CatState) {
        catState = state
        catMessage = "Preview: \(state.rawValue)"
        publishWidgetSnapshot()
        UIImpactFeedbackGenerator(style: .medium).impactOccurred()
        startRevealLoop()
    }

    // ══════════════════════════════════════
    // ── QUICK ANALYSIS ──
    // ══════════════════════════════════════

    private func runQuickAnalysis() async {
        guard let url = URL(string: "\(serverConfig.apiBaseURL)/api/agent/quick-analysis") else {
            catMessage = "Invalid server URL"
            return
        }
        var req = APIClient.request(url, method: "POST")
        req.timeoutInterval = 35
        do {
            let (data, _) = try await URLSession.shared.data(for: req)
            if let j = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               let s = j["state"] as? String, let m = j["message"] as? String {
                catState = CatState(rawValue: s) ?? .relaxed
                catMessage = m
                PhoneConnectivityManager.shared.sendCatState(catState.rawValue, message: catMessage)
                publishWidgetSnapshot()
                startRevealLoop()
            } else { catMessage = "Hmm, something went wrong..." }
        } catch {
            catMessage = "Can't reach server right now"
        }
    }

    // ══════════════════════════════════════
    // ── AGENT & NETWORK ──
    // ══════════════════════════════════════

    func checkAgentStatus() async {
        guard let url = URL(string: "\(serverConfig.apiBaseURL)/api/agent/status?user_id=LiveUser") else { return }
        do {
            let (data, _) = try await URLSession.shared.data(for: APIClient.request(url))
            let json = try JSONSerialization.jsonObject(with: data) as? [String: Any]
            agentRunning = json?["running"] as? Bool ?? false
        } catch {}
    }

    func toggleAgent() async {
        isTogglingAgent = true
        defer { isTogglingAgent = false }
        UIImpactFeedbackGenerator(style: .medium).impactOccurred()

        if agentRunning {
            guard let url = URL(string: "\(serverConfig.apiBaseURL)/api/agent/stop?user_id=LiveUser") else { return }
            var req = APIClient.request(url, method: "POST")
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            let _ = try? await URLSession.shared.data(for: req)
        } else {
            guard let url = URL(string: "\(serverConfig.apiBaseURL)/api/agent/start") else { return }
            var req = APIClient.request(url, method: "POST")
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = try? JSONSerialization.data(withJSONObject: ["user_id": "LiveUser"])
            let _ = try? await URLSession.shared.data(for: req)
        }
        try? await Task.sleep(nanoseconds: 1_000_000_000)
        await checkAgentStatus()
        publishWidgetSnapshot()
    }

    private func startAgentStatusPolling() {
        Task { await checkAgentStatus() }
        agentStatusTimer = Timer.scheduledTimer(withTimeInterval: 15, repeats: true) { [weak self] _ in
            Task { @MainActor in
                guard let self else { return }
                await self.checkAgentStatus()
            }
        }
    }

    /// Open the configured chat platform (Telegram or Feishu, depending on
    /// which gateway the backend has enabled).  Always re-fetches
    /// ``/api/agent/chat-info`` so that flipping platforms server-side
    /// takes effect on the next tap without an iOS rebuild.
    func openChat() {
        Task {
            let base = ServerConfig.load().apiBaseURL
            guard let url = URL(string: "\(base)/api/agent/chat-info") else { return }
            do {
                let (data, _) = try await URLSession.shared.data(for: APIClient.request(url))
                guard let j = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return }
                let platform = j["platform"] as? String ?? "none"
                let label = j["label"] as? String ?? "Chat"
                let primaryURL = j["url"] as? String ?? ""
                UserDefaults.standard.set(label, forKey: "chatLabel")
                UserDefaults.standard.set(platform, forKey: "chatPlatform")
                await MainActor.run {
                    self.chatLabel = label
                    self.chatPlatform = platform
                }
                await MainActor.run {
                    switch platform {
                    case "telegram":
                        let groupLink = j["group_link"] as? String ?? ""
                        let chatId = j["chat_id"] as? String ?? ""
                        let botUsername = j["bot_username"] as? String ?? ""
                        if !groupLink.isEmpty { _openURL(groupLink) }
                        else if !chatId.isEmpty { _openTelegramGroup(chatId: chatId, botUsername: botUsername) }
                        else if !botUsername.isEmpty { _openTelegramBotFallback(bot: botUsername) }
                        else if !primaryURL.isEmpty { _openURL(primaryURL) }
                    case "feishu":
                        if !primaryURL.isEmpty { _openURL(primaryURL) }
                    default:
                        self.showNoChatAlert = true
                    }
                }
            } catch {}
        }
    }

    /// Refresh the chat label/platform from the backend without opening
    /// anything.  Called on view appear so the button reflects the active
    /// gateway before the user taps it.
    func refreshChatInfo() {
        Task {
            let base = ServerConfig.load().apiBaseURL
            guard let url = URL(string: "\(base)/api/agent/chat-info") else { return }
            do {
                let (data, _) = try await URLSession.shared.data(for: APIClient.request(url))
                guard let j = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return }
                let label = j["label"] as? String ?? "Chat"
                let platform = j["platform"] as? String ?? "none"
                UserDefaults.standard.set(label, forKey: "chatLabel")
                UserDefaults.standard.set(platform, forKey: "chatPlatform")
                await MainActor.run {
                    self.chatLabel = label
                    self.chatPlatform = platform
                }
            } catch {}
        }
    }

    private func _openURL(_ link: String) {
        guard let u = URL(string: link) else { return }
        UIApplication.shared.open(u)
    }

    private func _openTelegramGroup(chatId: String, botUsername: String) {
        let numericId: String
        if chatId.hasPrefix("-100") { numericId = String(chatId.dropFirst(4)) }
        else if chatId.hasPrefix("-") { numericId = String(chatId.dropFirst(1)) }
        else { numericId = chatId }
        if let u = URL(string: "tg://openmessage?chat_id=\(numericId)"),
           UIApplication.shared.canOpenURL(u) {
            UIApplication.shared.open(u); return
        }
        _openTelegramBotFallback(bot: botUsername)
    }

    private func _openTelegramBotFallback(bot: String) {
        guard !bot.isEmpty else { return }
        if let u = URL(string: "tg://resolve?domain=\(bot)"), UIApplication.shared.canOpenURL(u) {
            UIApplication.shared.open(u); return
        }
        if let u = URL(string: "https://t.me/\(bot)") { UIApplication.shared.open(u) }
    }

    private func notifyServerSyncControl(enabled: Bool) async throws {
        guard let url = URL(string: "\(serverConfig.watchHTTPBaseURL)/sync-control") else {
            throw URLError(.badURL)
        }
        var req = APIClient.request(url, method: "POST")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONSerialization.data(withJSONObject: ["enabled": enabled])
        let (_, response) = try await URLSession.shared.data(for: req)
        if let http = response as? HTTPURLResponse, !(200...299).contains(http.statusCode) {
            throw URLError(.badServerResponse)
        }
    }

    // ══════════════════════════════════════
    // ── HELPERS ──
    // ══════════════════════════════════════

    private func after(_ d: Double, _ action: @escaping @MainActor () -> Void) {
        Task { @MainActor [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(d * 1_000_000_000))
            guard self != nil, !Task.isCancelled else { return }
            action()
        }
    }

    private func idleAfter(_ d: Double, _ action: @escaping @MainActor () -> Void) {
        after(d) { [weak self] in
            guard let self, !self.isTapAnimating, !self.isLongPressAnimating else { return }
            action()
        }
    }

    deinit { animTimer?.invalidate(); agentStatusTimer?.invalidate(); revealLoopTask?.cancel() }

    /// Push the current cat state into the App Group snapshot so the
    /// home / lock-screen widgets can read it. Metric and report
    /// fields are preserved — DashboardViewModel updates those.
    func publishWidgetSnapshot() {
        let state = catState.rawValue
        let message = catMessage
        let running = agentRunning
        HimeWidgetStore.update { snap in
            snap.catStateRaw = state
            snap.catMessage = message
            snap.agentRunning = running
        }
    }
}
