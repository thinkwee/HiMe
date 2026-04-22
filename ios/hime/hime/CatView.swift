import SwiftUI
import UIKit

// MARK: - Main Cat Screen

struct CatMainView: View {
    @StateObject private var vm = CatViewModel()

    var body: some View {
        let _ = vm.animFrame

        ZStack {
            GeometryReader { geo in
                // ── Two fixed points, computed from real geometry ──
                let ps = geo.size.width / 64
                let wallEndPx = geo.size.height * 0.25
                let floorTopPx = wallEndPx + 2 * ps   // 2-row baseboard
                let bottomUIPx: CGFloat = 340 + geo.safeAreaInsets.bottom
                let floorCenterPx = (floorTopPx + (geo.size.height - bottomUIPx)) / 2
                let homeRow = Int(floorCenterPx / ps)
                let bedRow = homeRow + 18

                ZStack(alignment: .top) {
                    // ── Canvas fills entire visible area ──
                    ZStack {
                        CatRoomCanvas(
                            catState: vm.catState,
                            isConnected: vm.isConnected,
                            bedX: 50, bedY: bedRow,
                            cx: vm.catX.i, cy: vm.catY.i,
                            hx: vm.headX.i, hy: vm.headY.i,
                            bodySquash: vm.bodySquash.f,
                            rollAngle: vm.rollAngle.f,
                            lpY: vm.leftPawY.i, rpY: vm.rightPawY.i,
                            earPerk: vm.earPerk.f,
                            eyeShape: vm.eyeShape,
                            eyeX: vm.eyeX.i,
                            eyeOpen: vm.eyeOpenness.f,
                            mouthShape: vm.mouthShape,
                            blush: vm.blushIntensity.f,
                            tailSegments: vm.tailChain.angles,
                            props: vm.props,
                            particles: vm.particles,
                            costume: vm.costume,
                            catScale: vm.catScale.f,
                            catScaleX: vm.catScaleX.f,
                            catPose: vm.catPose
                        )
                        .frame(width: geo.size.width, height: geo.size.height)

                        if vm.showNya {
                            PixelChatBubble(text: "NYA~!").offset(x: 90, y: -100)
                                .transition(.scale(scale: 0.5).combined(with: .opacity))
                        }
                        if vm.showPurrBubble {
                            PixelChatBubble(text: "purrrr~").offset(x: 90, y: -90)
                                .transition(.scale(scale: 0.5).combined(with: .opacity))
                        }
                    }
                    .scaleEffect(vm.breathingScale)
                    .overlay(alignment: .top) {
                        // State badge at fixed position above cat's home row
                        let catScreenY = CGFloat(homeRow - 16) * ps
                        HStack(spacing: 5) {
                            Text(vm.catState.emoji).font(.system(size: 11))
                            Text(vm.catState.rawValue.capitalized)
                                .font(.system(size: 11, weight: .bold, design: .monospaced))
                                .foregroundColor(.white)
                        }
                        .padding(.horizontal, 10).padding(.vertical, 4)
                        .background(
                            Capsule()
                                .fill(vm.catState.color.opacity(0.75))
                                .shadow(color: vm.catState.color.opacity(0.3), radius: 3, y: 1)
                        )
                        .animation(.easeInOut, value: vm.catState)
                        .offset(y: catScreenY)
                    }
                    .gesture(TapGesture(count: 1).onEnded { vm.playRandomTapAnimation() })
                    .simultaneousGesture(LongPressGesture(minimumDuration: 0.6).onEnded { _ in vm.triggerQuickAnalysis() })

                    // ── Overlay UI ──
                    VStack(spacing: 0) {
                        Spacer()

                        // ── Below-cat controls area ──
                        VStack(spacing: 12) {
                            // Message
                            VStack(spacing: 4) {
                                Text(vm.catMessage)
                                    .font(.system(size: 12, weight: .medium, design: .monospaced))
                                    .multilineTextAlignment(.center)
                                    .foregroundColor(Color(red: 0.35, green: 0.30, blue: 0.25).opacity(0.8))
                                    .lineLimit(4)
                                    .padding(.horizontal, 20)
                                    .animation(.easeInOut(duration: 0.5), value: vm.catMessage)

                                if vm.isAnalyzing {
                                    ProgressView().scaleEffect(0.7).tint(vm.catState.color)
                                }
                            }

                            // Row 1: Chat (label resolved server-side via /chat-info)
                            Button { vm.openChat() } label: {
                                HStack(spacing: 6) {
                                    Image(systemName: vm.chatPlatform == "feishu" ? "bubble.left.and.bubble.right.fill" : "paperplane.fill")
                                        .font(.system(size: 13))
                                    Text(vm.chatLabel)
                                        .font(.system(size: 13, weight: .semibold))
                                }
                                .foregroundColor(.blue)
                                .padding(.horizontal, 22).padding(.vertical, 10)
                                .background(Capsule().fill(.ultraThinMaterial))
                                .overlay(Capsule().stroke(Color.blue.opacity(0.2), lineWidth: 1))
                                .shadow(color: .blue.opacity(0.08), radius: 4, y: 2)
                            }
                            .onAppear { vm.refreshChatInfo() }

                            // Row 2: Sync + Agent sliding toggles
                            HStack(spacing: 14) {
                                SlidingToggle(
                                    isOn: vm.isConnected,
                                    onLabel: "Sync",
                                    offLabel: "Sync",
                                    onColor: .green,
                                    isLoading: false
                                ) { vm.toggleConnect() }

                                SlidingToggle(
                                    isOn: vm.agentRunning,
                                    onLabel: "Agent",
                                    offLabel: "Agent",
                                    onColor: Color(red: 0.55, green: 0.40, blue: 0.90),
                                    isLoading: vm.isTogglingAgent
                                ) { Task { await vm.toggleAgent() } }
                            }
                        }
                        .padding(.horizontal, 16)
                        .padding(.vertical, 14)
                        .background(
                            RoundedRectangle(cornerRadius: 16, style: .continuous)
                                .fill(.ultraThinMaterial.opacity(0.85))
                                .shadow(color: .black.opacity(0.06), radius: 8, y: -2)
                        )
                        .animation(.spring(response: 0.4, dampingFraction: 0.8), value: vm.catMessage)
                        .animation(.spring(response: 0.4, dampingFraction: 0.8), value: vm.isAnalyzing)
                        .padding(.horizontal, 12)
                        .padding(.bottom, geo.safeAreaInsets.bottom + 6 * ps + 2 * ps + 16)
                    }
                }
                .onAppear { vm.configure(home: (32, homeRow), bed: (50, bedRow - 12)) }
            }
        }
        .ignoresSafeArea()
        .background(Color(red: 0.82, green: 0.78, blue: 0.72).ignoresSafeArea())
        .alert("Chat Not Configured", isPresented: $vm.showNoChatAlert) {
            Button("Setup Guide") {
                if let url = URL(string: "https://github.com/thinkwee/HiMe") {
                    UIApplication.shared.open(url)
                }
            }
            Button("OK", role: .cancel) {}
        } message: {
            Text("No messaging gateway is enabled on your server. Configure Telegram or Feishu by following the setup guide on GitHub.")
        }
    }
}

// MARK: - Sliding Toggle

struct SlidingToggle: View {
    let isOn: Bool
    let onLabel: String
    let offLabel: String
    let onColor: Color
    let isLoading: Bool
    let action: () -> Void

    private let toggleWidth: CGFloat = 120
    private let toggleHeight: CGFloat = 36
    private let knobSize: CGFloat = 28

    var body: some View {
        Button(action: action) {
            ZStack {
                // Track
                Capsule()
                    .fill(isOn ? onColor.opacity(0.2) : Color(white: 0.88))
                    .overlay(Capsule().stroke(isOn ? onColor.opacity(0.3) : Color(white: 0.78), lineWidth: 1))

                // Sliding knob
                HStack {
                    if isOn { Spacer() }
                    ZStack {
                        Capsule()
                            .fill(isOn ? onColor : Color(white: 0.55))
                            .shadow(color: (isOn ? onColor : .black).opacity(0.2), radius: 3, y: 1)
                        if isLoading {
                            ProgressView().scaleEffect(0.5).tint(.white)
                        } else {
                            Text(isOn ? onLabel : offLabel)
                                .font(.system(size: 11, weight: .bold))
                                .foregroundColor(.white)
                        }
                    }
                    .frame(width: toggleWidth * 0.52, height: knobSize)
                    if !isOn { Spacer() }
                }
                .padding(.horizontal, 3)

                // Off-side label
                HStack {
                    if !isOn { Spacer() }
                    Text(isOn ? "On" : "Off")
                        .font(.system(size: 11, weight: .medium))
                        .foregroundColor(isOn ? onColor.opacity(0.6) : Color(white: 0.45))
                        .padding(.horizontal, 12)
                    if isOn { Spacer() }
                }
            }
            .frame(width: toggleWidth, height: toggleHeight)
            .animation(.spring(response: 0.35, dampingFraction: 0.7), value: isOn)
        }
        .buttonStyle(.plain)
    }
}

// MARK: - Room Canvas (64 wide × gridH tall)

struct CatRoomCanvas: View {
    let catState: CatState
    let isConnected: Bool
    let bedX: Int, bedY: Int
    let cx: Int, cy: Int
    let hx: Int, hy: Int
    let bodySquash: CGFloat
    let rollAngle: Double
    let lpY: Int, rpY: Int
    let earPerk: CGFloat
    let eyeShape: EyeShape
    let eyeX: Int
    let eyeOpen: CGFloat
    let mouthShape: MouthShape
    let blush: CGFloat
    let tailSegments: [CGFloat]
    let props: [PropInstance]
    let particles: [PixelParticle]
    var costume: CatCostume = .none
    var catScale: CGFloat = 1.0
    var catScaleX: CGFloat = 1.0
    var catPose: CatPose = .frontSitting

    // Cat palette (lightened outline for softer look)
    private let cK  = Color(red: 0.42, green: 0.35, blue: 0.28)
    private let cO  = Color(red: 0.95, green: 0.70, blue: 0.35)
    private let cOL = Color(red: 0.98, green: 0.82, blue: 0.52)
    private let cD  = Color(red: 0.78, green: 0.50, blue: 0.20)
    private let cCr = Color(red: 1.00, green: 0.95, blue: 0.86)
    private let cPE = Color(red: 1.00, green: 0.74, blue: 0.80)
    private let cPk = Color(red: 1.00, green: 0.78, blue: 0.83)
    private let cN  = Color(red: 0.88, green: 0.50, blue: 0.54)
    private let cEy = Color(red: 0.14, green: 0.12, blue: 0.10)
    private let cW  = Color(red: 1.00, green: 1.00, blue: 0.98)

    var body: some View {
        Canvas { ctx, size in
            let ps = size.width / 64
            let gridH = max(64, Int(size.height / ps))
            let wallRow = max(16, Int(Double(gridH) * 0.25))
            let floorRow = wallRow + 2
            let mR = gridH - 1

            // ── Helpers (row bounds use gridH, column bounds stay 64) ──
            func px(_ c: Int, _ r: Int, _ col: Color) {
                guard c >= 0, c < 64, r >= 0, r < gridH else { return }
                ctx.fill(Path(CGRect(x: CGFloat(c)*ps, y: CGFloat(r)*ps, width: ps+0.5, height: ps+0.5)), with: .color(col))
            }
            func hline(_ c0: Int, _ c1: Int, _ r: Int, _ col: Color) {
                let s = max(0, c0); let e = min(63, c1)
                guard s <= e, r >= 0, r < gridH else { return }
                ctx.fill(Path(CGRect(x: CGFloat(s)*ps, y: CGFloat(r)*ps, width: CGFloat(e-s+1)*ps+0.5, height: ps+0.5)), with: .color(col))
            }
            func vline(_ r0: Int, _ r1: Int, _ c: Int, _ col: Color) {
                let s = max(0, r0); let e = min(mR, r1)
                guard s <= e, c >= 0, c < 64 else { return }
                ctx.fill(Path(CGRect(x: CGFloat(c)*ps, y: CGFloat(s)*ps, width: ps+0.5, height: CGFloat(e-s+1)*ps+0.5)), with: .color(col))
            }
            func fillR(_ c0: Int, _ c1: Int, _ r0: Int, _ r1: Int, _ col: Color) {
                let s0 = max(0,c0); let e0 = min(63,c1); let s1 = max(0,r0); let e1 = min(mR,r1)
                guard s0 <= e0, s1 <= e1 else { return }
                ctx.fill(Path(CGRect(x: CGFloat(s0)*ps, y: CGFloat(s1)*ps, width: CGFloat(e0-s0+1)*ps+0.5, height: CGFloat(e1-s1+1)*ps+0.5)), with: .color(col))
            }
            func fillE(_ ecx: Int, _ ecy: Int, _ rx: Int, _ ry: Int, _ col: Color) {
                guard rx > 0, ry > 0 else { return }
                let rS = max(0, ecy-ry); let rE = min(mR, ecy+ry)
                guard rS <= rE else { return }
                for r in rS...rE {
                    let dy = Double(r-ecy)/Double(ry); if dy*dy > 1 { continue }
                    let sp = Int(Double(rx)*sqrt(1-dy*dy))
                    let cs = max(0,ecx-sp); let ce = min(63,ecx+sp)
                    if cs <= ce { ctx.fill(Path(CGRect(x: CGFloat(cs)*ps, y: CGFloat(r)*ps, width: CGFloat(ce-cs+1)*ps+0.5, height: ps+0.5)), with: .color(col)) }
                }
            }
            func fillTri(_ x1: Int, _ y1: Int, _ x2: Int, _ y2: Int, _ x3: Int, _ y3: Int, _ col: Color) {
                let d = (y2-y3)*(x1-x3)+(x3-x2)*(y1-y3); guard d != 0 else { return }
                let dD = Double(d)
                let rS = max(0,min(y1,min(y2,y3))); let rE = min(mR,max(y1,max(y2,y3)))
                guard rS <= rE else { return }
                let cS = max(0,min(x1,min(x2,x3))); let cE = min(63,max(x1,max(x2,x3)))
                guard cS <= cE else { return }
                for r in rS...rE {
                    var lo = 64; var hi = -1
                    for c in cS...cE {
                        let a = Double((y2-y3)*(c-x3)+(x3-x2)*(r-y3))/dD
                        let b = Double((y3-y1)*(c-x3)+(x1-x3)*(r-y3))/dD
                        if a >= -0.01 && b >= -0.01 && (1-a-b) >= -0.01 { lo = min(lo,c); hi = max(hi,c) }
                    }
                    if lo <= hi { ctx.fill(Path(CGRect(x: CGFloat(lo)*ps, y: CGFloat(r)*ps, width: CGFloat(hi-lo+1)*ps+0.5, height: ps+0.5)), with: .color(col)) }
                }
            }

            // ════════════════════════════════
            // ── ROOM BACKGROUND ──
            // ════════════════════════════════

            // Dim room when disconnected (nighttime feel)
            let d: CGFloat = isConnected ? 1.0 : 0.82
            let wallC   = Color(red: 0.82 * d, green: 0.78 * d, blue: 0.72 * d)  // warm gray-beige wall
            let boardC  = Color(red: 0.72 * d, green: 0.65 * d, blue: 0.55 * d)  // darker baseboard
            let floorC  = Color(red: 0.68 * d, green: 0.52 * d, blue: 0.35 * d)  // medium oak wood
            let plankC  = Color(red: 0.74 * d, green: 0.58 * d, blue: 0.40 * d)  // plank highlight

            // ── Wall / Board / Floor / Grass (bottom 3 rows only) ──
            let grassStart = mR - 5  // exactly 6 rows of outdoor grass
            let grassC1 = Color(red: 0.42 * d, green: 0.68 * d, blue: 0.32 * d)
            let grassC2 = Color(red: 0.52 * d, green: 0.78 * d, blue: 0.38 * d)
            let grassC3 = Color(red: 0.34 * d, green: 0.56 * d, blue: 0.26 * d)

            fillR(0, 63, 0, wallRow - 1, wallC)
            fillR(0, 63, wallRow, wallRow + 1, boardC)
            fillR(0, 63, floorRow, grassStart - 1, floorC)   // wood floor (indoor)
            fillR(0, 63, grassStart, mR, grassC1)              // 3-row grass strip

            // Plank lines on indoor floor
            var pr = floorRow + 6; while pr < grassStart { hline(0, 63, pr, plankC); pr += 6 }
            pr = floorRow; var bi = 0
            while pr < grassStart {
                let endR = min(pr + 5, grassStart - 1)
                if bi % 2 == 0 { vline(pr, endR, 20, plankC); vline(pr, endR, 50, plankC) }
                else { vline(pr, endR, 35, plankC) }
                pr += 6; bi += 1
            }

            // ── Grass detail (6 rows with gradient + sub-pixel detail) ──
            let gColors: [Color] = [
                Color(red: 0.50 * d, green: 0.76 * d, blue: 0.36 * d),
                Color(red: 0.46 * d, green: 0.72 * d, blue: 0.34 * d),
                Color(red: 0.42 * d, green: 0.68 * d, blue: 0.32 * d),
                Color(red: 0.38 * d, green: 0.62 * d, blue: 0.28 * d),
                Color(red: 0.34 * d, green: 0.56 * d, blue: 0.26 * d),
                Color(red: 0.30 * d, green: 0.50 * d, blue: 0.22 * d),
            ]
            for i in 0..<6 { hline(0, 63, grassStart + i, gColors[i]) }
            // Dappled light/shadow within grass
            for col in stride(from: 1, through: 62, by: 3) { px(col, grassStart + 1, grassC2.opacity(0.5)) }
            for col in stride(from: 2, through: 61, by: 4) { px(col, grassStart + 3, grassC3.opacity(0.6)) }
            for col in stride(from: 0, through: 63, by: 5) { px(col, grassStart + 4, grassC2.opacity(0.35)) }

            // ── Grass blades poking UP above grass line ──
            let bladeL = Color(red: 0.48 * d, green: 0.74 * d, blue: 0.35 * d)
            let bladeM = Color(red: 0.40 * d, green: 0.64 * d, blue: 0.30 * d)
            let bladeD = Color(red: 0.32 * d, green: 0.54 * d, blue: 0.24 * d)
            // Short blades (1px up)
            for col in stride(from: 1, through: 62, by: 3) { px(col, grassStart - 1, bladeL) }
            // Medium blades (2px up)
            for col in stride(from: 3, through: 60, by: 5) {
                px(col, grassStart - 2, bladeM); px(col, grassStart - 1, bladeL)
            }
            // Tall blades (3px up, sparser)
            for col in stride(from: 5, through: 58, by: 9) {
                px(col, grassStart - 3, bladeD); px(col, grassStart - 2, bladeM); px(col, grassStart - 1, bladeL)
                px(col + 1, grassStart - 3, bladeD.opacity(0.4)) // leaning tip
            }

            // ── Flowers (detailed multi-pixel) ──
            let stemC  = Color(red: 0.28 * d, green: 0.50 * d, blue: 0.20 * d)
            let stemL  = Color(red: 0.35 * d, green: 0.58 * d, blue: 0.26 * d)

            // 🌸 Pink daisy at col 7
            px(7, grassStart + 1, stemC); px(7, grassStart, stemL); px(7, grassStart - 1, stemC)
            let pk = Color(red: 1.0, green: 0.50, blue: 0.62)
            let pkL = Color(red: 1.0, green: 0.68, blue: 0.76)
            px(7, grassStart - 3, pk)
            px(6, grassStart - 2, pk); px(8, grassStart - 2, pk)
            px(6, grassStart - 3, pkL.opacity(0.5)); px(8, grassStart - 3, pkL.opacity(0.5))
            px(7, grassStart - 2, Color(red: 1.0, green: 0.85, blue: 0.25))

            // 🌼 Sunflower at col 19
            px(19, grassStart + 2, stemC); px(19, grassStart + 1, stemC); px(19, grassStart, stemL); px(19, grassStart - 1, stemC)
            let yel = Color(red: 1.0, green: 0.82, blue: 0.20)
            let yelL = Color(red: 1.0, green: 0.90, blue: 0.45)
            px(19, grassStart - 3, yel); px(18, grassStart - 2, yel); px(20, grassStart - 2, yel)
            px(18, grassStart - 3, yelL.opacity(0.5)); px(20, grassStart - 3, yelL.opacity(0.5))
            px(19, grassStart - 4, yelL.opacity(0.4))
            px(19, grassStart - 2, Color(red: 0.65, green: 0.40, blue: 0.15))

            // 💜 Lavender at col 35
            px(35, grassStart + 1, stemC); px(35, grassStart, stemL); px(35, grassStart - 1, stemC)
            let lav = Color(red: 0.65, green: 0.50, blue: 0.90)
            let lavL = Color(red: 0.78, green: 0.65, blue: 0.95)
            px(35, grassStart - 2, lav); px(35, grassStart - 3, lavL)
            px(34, grassStart - 2, lavL.opacity(0.6)); px(36, grassStart - 2, lavL.opacity(0.6))
            px(35, grassStart - 4, lav.opacity(0.5))
            px(34, grassStart, Color(red: 0.35 * d, green: 0.58 * d, blue: 0.28 * d)) // leaf

            // 🤍 White wildflower at col 48
            px(48, grassStart + 1, stemC); px(48, grassStart, stemL)
            let wh = Color(red: 1.0, green: 0.98, blue: 0.94)
            let whS = Color(red: 0.92, green: 0.90, blue: 0.86)
            px(48, grassStart - 2, wh); px(47, grassStart - 1, wh); px(49, grassStart - 1, wh)
            px(48, grassStart - 1, Color(red: 0.95, green: 0.80, blue: 0.30))
            px(47, grassStart - 2, whS.opacity(0.5)); px(49, grassStart - 2, whS.opacity(0.5))

            // 🌺 Red poppy at col 57
            px(57, grassStart + 2, stemC); px(57, grassStart + 1, stemC); px(57, grassStart, stemL)
            let rd = Color(red: 0.92, green: 0.28, blue: 0.22)
            let rdL = Color(red: 1.0, green: 0.45, blue: 0.38)
            px(57, grassStart - 2, rd); px(56, grassStart - 1, rd); px(58, grassStart - 1, rd)
            px(56, grassStart - 2, rdL.opacity(0.5)); px(58, grassStart - 2, rdL.opacity(0.5))
            px(57, grassStart - 1, Color(red: 0.20, green: 0.18, blue: 0.15))

            // ── Wall decorations scale proportionally to wall height ──
            let ws = CGFloat(wallRow) / 44.0  // scale factor vs original 44-row wall
            func wr(_ r: Int) -> Int { Int(round(CGFloat(r) * ws)) }

            // ── Window ──
            let wFrame = Color(red: 0.45, green: 0.32, blue: 0.20)
            let wGlass = Color(red: 0.78, green: 0.90, blue: 0.98)
            let wLight = Color(red: 0.90, green: 0.96, blue: 1.00)

            fillR(4, 28, wr(3), wr(26), wFrame)
            fillR(6, 26, wr(5), wr(24), wGlass)
            fillR(6, 26, wr(14), wr(15), wFrame)
            fillR(15, 16, wr(5), wr(24), wFrame)
            fillR(3, 29, wr(26), wr(28), wFrame)
            hline(3, 29, wr(26), Color(red: 0.55, green: 0.40, blue: 0.25))

            if isConnected {
                fillR(7, 14, wr(6), wr(13), wLight); fillR(17, 25, wr(6), wr(13), wLight)
                let curtC = Color(red: 0.72, green: 0.42, blue: 0.38)
                let curtL = Color(red: 0.80, green: 0.50, blue: 0.45)
                fillR(4, 6, wr(5), wr(25), curtC); vline(wr(5), wr(25), 5, curtL)
                fillR(26, 28, wr(5), wr(25), curtC); vline(wr(5), wr(25), 27, curtL)
                fillTri(6, wr(28), 2, floorRow, 18, floorRow,
                        Color(red: 1.0, green: 0.95, blue: 0.75).opacity(0.25))
            } else {
                let curtC  = Color(red: 0.62, green: 0.35, blue: 0.32)
                let curtHL = Color(red: 0.70, green: 0.42, blue: 0.38)
                let curtSH = Color(red: 0.52, green: 0.28, blue: 0.25)
                fillR(5, 27, wr(5), wr(25), curtC)
                vline(wr(5), wr(25), 10, curtHL); vline(wr(5), wr(25), 11, curtSH)
                vline(wr(5), wr(25), 16, curtHL); vline(wr(5), wr(25), 17, curtSH)
                vline(wr(5), wr(25), 22, curtHL); vline(wr(5), wr(25), 23, curtSH)
                fillR(4, 28, wr(4), wr(5), curtSH)
            }

            // ── Shelf + Plant (scale to wall) ──
            let shelfC = Color(red: 0.55 * d, green: 0.40 * d, blue: 0.25 * d)
            let shelfL = Color(red: 0.65 * d, green: 0.48 * d, blue: 0.32 * d)
            fillR(44, 60, wr(28), wr(29), shelfC); hline(44, 60, wr(27), shelfL)
            vline(wr(29), wr(31), 46, shelfC); vline(wr(29), wr(31), 58, shelfC)
            let potC = Color(red: 0.78, green: 0.45, blue: 0.28)
            let potD = Color(red: 0.65, green: 0.35, blue: 0.20)
            fillR(49, 55, wr(23), wr(27), potC); fillR(48, 56, wr(22), wr(23), potD)
            let leafC = Color(red: 0.38, green: 0.65, blue: 0.32)
            let leafD = Color(red: 0.28, green: 0.52, blue: 0.24)
            fillE(52, wr(18), 4, 3, leafC); fillE(49, wr(16), 2, 2, leafD); fillE(55, wr(17), 2, 2, leafD)
            px(52, wr(15), leafC); px(51, wr(14), leafD); px(53, wr(14), leafD)
            vline(wr(20), wr(22), 52, Color(red: 0.32, green: 0.48, blue: 0.26))

            // ── Cat Bed ──
            let bedRim  = Color(red: 0.50, green: 0.32, blue: 0.18)
            let bedMid  = Color(red: 0.62, green: 0.44, blue: 0.28)
            let bedIn   = Color(red: 0.92, green: 0.82, blue: 0.78)
            let bedDeep = Color(red: 0.86, green: 0.74, blue: 0.70)
            fillE(bedX, bedY, 10, 5, bedRim); fillE(bedX, bedY, 8, 4, bedMid)
            fillE(bedX, bedY + 1, 6, 2, bedIn); fillE(bedX, bedY + 1, 5, 1, bedDeep)

            // ── Candle (only when disconnected) ──
            if !isConnected {
                let candleX = bedX - 14  // to the left of the bed
                let candleBase = bedY + 2
                let waxC  = Color(red: 0.95, green: 0.92, blue: 0.82)
                let waxD  = Color(red: 0.88, green: 0.84, blue: 0.72)
                let wickC = Color(red: 0.30, green: 0.25, blue: 0.18)
                // Saucer
                fillE(candleX, candleBase + 1, 4, 1, Color(red: 0.70, green: 0.58, blue: 0.42))
                // Wax body (3 wide, 5 tall)
                fillR(candleX - 1, candleX + 1, candleBase - 4, candleBase, waxC)
                vline(candleBase - 4, candleBase, candleX - 1, waxD) // left shadow
                // Wick
                px(candleX, candleBase - 5, wickC)
                // Flame (animated glow)
                let flameC = Color(red: 1.0, green: 0.85, blue: 0.30)
                let flameT = Color(red: 1.0, green: 0.65, blue: 0.15)
                let flameW = Color(red: 1.0, green: 0.95, blue: 0.70)
                px(candleX, candleBase - 6, flameC)
                px(candleX, candleBase - 7, flameT)
                px(candleX - 1, candleBase - 6, flameT.opacity(0.5))
                px(candleX + 1, candleBase - 6, flameT.opacity(0.5))
                px(candleX, candleBase - 8, flameT.opacity(0.3))
                // Warm glow on floor around candle
                fillE(candleX, candleBase + 2, 8, 3,
                      flameW.opacity(0.08))
            }

            // Paw positions for prop anchoring
            let plY = min(lpY, 0)
            let prY = min(rpY, 0)

            // ════════════════════════════════
            // ── CAT + PROPS (scaled layer for approach-camera effect) ──
            // ════════════════════════════════
            ctx.drawLayer { scaledCtx in
                // Apply catScale (uniform) + catScaleX (horizontal squish for turn animation)
                let effSX = catScale * catScaleX
                let effSY = catScale
                if effSX != 1.0 || effSY != 1.0 {
                    let scx = CGFloat(cx) * ps + ps * 0.5
                    let scy = CGFloat(cy) * ps + ps * 0.5
                    scaledCtx.translateBy(x: scx, y: scy)
                    scaledCtx.scaleBy(x: effSX, y: effSY)
                    scaledCtx.translateBy(x: -scx, y: -scy)
                }

            // ── Behind-cat props ──
            for prop in props where prop.type.drawsBehind {
                PixelPropRenderer.draw(prop, ctx: scaledCtx, catX: cx, catY: cy,
                                       headX: hx, headY: hy, lpY: plY, rpY: prY, ps: ps)
            }

            // ── Behind-cat costume (cape) ──
            if costume == .cape || costume == .backpack {
                CatCostumeRenderer.drawBehind(costume, ctx: scaledCtx,
                                               cx: cx, cy: cy, hx: hx, hy: hy,
                                               bodySquash: bodySquash, ps: ps)
            }

            // ── CAT SPRITE ──
            scaledCtx.drawLayer { catCtx in
                let catCenterX = CGFloat(cx) * ps + ps * 0.5
                let catCenterY = CGFloat(cy) * ps + ps * 0.5
                if rollAngle != 0 {
                    catCtx.translateBy(x: catCenterX, y: catCenterY)
                    catCtx.rotate(by: Angle(radians: rollAngle * .pi / 180))
                    catCtx.translateBy(x: -catCenterX, y: -catCenterY)
                }

                func cpx(_ c: Int, _ r: Int, _ col: Color) {
                    guard c >= 0, c < 64, r >= 0, r < gridH else { return }
                    catCtx.fill(Path(CGRect(x: CGFloat(c)*ps, y: CGFloat(r)*ps, width: ps+0.5, height: ps+0.5)), with: .color(col))
                }
                func chline(_ c0: Int, _ c1: Int, _ r: Int, _ col: Color) {
                    let s = max(0, c0); let e = min(63, c1)
                    guard s <= e, r >= 0, r < gridH else { return }
                    catCtx.fill(Path(CGRect(x: CGFloat(s)*ps, y: CGFloat(r)*ps, width: CGFloat(e-s+1)*ps+0.5, height: ps+0.5)), with: .color(col))
                }
                func cvline(_ r0: Int, _ r1: Int, _ c: Int, _ col: Color) {
                    let s = max(0, r0); let e = min(mR, r1)
                    guard s <= e, c >= 0, c < 64 else { return }
                    catCtx.fill(Path(CGRect(x: CGFloat(c)*ps, y: CGFloat(s)*ps, width: ps+0.5, height: CGFloat(e-s+1)*ps+0.5)), with: .color(col))
                }
                func cfillR(_ c0: Int, _ c1: Int, _ r0: Int, _ r1: Int, _ col: Color) {
                    let s0 = max(0,c0); let e0 = min(63,c1); let s1 = max(0,r0); let e1 = min(mR,r1)
                    guard s0 <= e0, s1 <= e1 else { return }
                    catCtx.fill(Path(CGRect(x: CGFloat(s0)*ps, y: CGFloat(s1)*ps, width: CGFloat(e0-s0+1)*ps+0.5, height: CGFloat(e1-s1+1)*ps+0.5)), with: .color(col))
                }
                func cfillE(_ ecx: Int, _ ecy: Int, _ rx: Int, _ ry: Int, _ col: Color) {
                    guard rx > 0, ry > 0 else { return }
                    let rS = max(0, ecy-ry); let rE = min(mR, ecy+ry)
                    guard rS <= rE else { return }
                    for r in rS...rE {
                        let dy = Double(r-ecy)/Double(ry); if dy*dy > 1 { continue }
                        let sp = Int(Double(rx)*sqrt(1-dy*dy))
                        let cs = max(0,ecx-sp); let ce = min(63,ecx+sp)
                        if cs <= ce { catCtx.fill(Path(CGRect(x: CGFloat(cs)*ps, y: CGFloat(r)*ps, width: CGFloat(ce-cs+1)*ps+0.5, height: ps+0.5)), with: .color(col)) }
                    }
                }
                func cfillTri(_ x1: Int, _ y1: Int, _ x2: Int, _ y2: Int, _ x3: Int, _ y3: Int, _ col: Color) {
                    let d = (y2-y3)*(x1-x3)+(x3-x2)*(y1-y3); guard d != 0 else { return }
                    let dD = Double(d)
                    let rS = max(0,min(y1,min(y2,y3))); let rE = min(mR,max(y1,max(y2,y3)))
                    guard rS <= rE else { return }
                    let cS = max(0,min(x1,min(x2,x3))); let cE = min(63,max(x1,max(x2,x3)))
                    guard cS <= cE else { return }
                    for r in rS...rE {
                        var lo = 64; var hi = -1
                        for c in cS...cE {
                            let a = Double((y2-y3)*(c-x3)+(x3-x2)*(r-y3))/dD
                            let b = Double((y3-y1)*(c-x3)+(x1-x3)*(r-y3))/dD
                            if a >= -0.01 && b >= -0.01 && (1-a-b) >= -0.01 { lo = min(lo,c); hi = max(hi,c) }
                        }
                        if lo <= hi { catCtx.fill(Path(CGRect(x: CGFloat(lo)*ps, y: CGFloat(r)*ps, width: CGFloat(hi-lo+1)*ps+0.5, height: ps+0.5)), with: .color(col)) }
                    }
                }

                let ep = Int(round(earPerk))
                let plY = min(lpY, 0)
                let prY = min(rpY, 0)

                if catPose == .sideArchedBack {
                    // ════════════════════════════════════════════════════════
                    // ── SIDE VIEW: ARCHED SCARED CAT (LARGE) ──
                    // Cat faces LEFT. Dramatic arched back, bristled fur spikes,
                    // puffed tail pointing UP, stiff legs, scared face.
                    // Body: ~16px wide, arch 17px tall. Much bigger than front view.
                    // ════════════════════════════════════════════════════════
                    let groundY = cy + 12

                    // ── Shadow ──
                    cfillE(cx + 3, groundY + 1, 10, 1, Color.black.opacity(0.10))

                    // ── Puffed tail (behind body, thick brush going UP) ──
                    // Base at rump (cx+11), 4px wide, narrows to 2px at tip
                    let tBX = cx + 11
                    // Base (widest — 4px)
                    cfillR(tBX - 1, tBX + 2, cy + 4, cy + 5, cK)
                    cfillR(tBX, tBX + 1, cy + 4, cy + 5, cO)
                    // Shaft — 3px wide with wave, going up 10 rows
                    for seg in 0..<min(7, tailSegments.count) {
                        let wave = Int(round(tailSegments[seg] * CGFloat(seg + 1) * 0.10))
                        let row = cy + 3 - seg
                        let w = seg < 4 ? 1 : 0  // narrows toward tip
                        cpx(tBX - 1 + wave - w, row, cK)
                        cpx(tBX + wave, row, cO)
                        cpx(tBX + 1 + wave, row, seg < 4 ? cO : cD)
                        cpx(tBX + 2 + wave + w, row, cK)
                    }
                    // Tip (2px)
                    let tipW = tailSegments.count > 6 ? Int(round(tailSegments[6] * 7 * 0.10)) : 0
                    cpx(tBX + tipW, cy - 4, cK); cpx(tBX + 1 + tipW, cy - 4, cD); cpx(tBX + 2 + tipW, cy - 4, cK)
                    cpx(tBX + tipW, cy - 5, cK); cpx(tBX + 1 + tipW, cy - 5, cK)

                    // ── Body arch (large filled triangle) ──
                    // Peak at (cx+4, cy-8), base from (cx-2, cy+9) to (cx+11, cy+9)
                    // Outline
                    cfillTri(cx - 2, cy + 9,  cx + 4, cy - 8,  cx + 11, cy + 9, cK)
                    // Orange fill (1px inset)
                    cfillTri(cx - 1, cy + 8,  cx + 4, cy - 6,  cx + 10, cy + 8, cO)
                    // Lighter highlight (inner upper)
                    cfillTri(cx + 1, cy + 5,  cx + 4, cy - 3,  cx + 8, cy + 5, cOL)
                    // Belly (cream) — lower portion of body
                    cfillR(cx, cx + 9, cy + 6, cy + 8, cCr)

                    // ── SPIKY FUR along the entire arch (2-3px tall spikes) ──
                    // Left ascending edge spikes
                    cpx(cx - 1, cy + 4, cK); cpx(cx - 1, cy + 3, cK)                     // near base
                    cpx(cx + 0, cy + 1, cK); cpx(cx + 0, cy + 0, cK)
                    cpx(cx + 1, cy - 2, cK); cpx(cx + 1, cy - 3, cK); cpx(cx + 1, cy - 4, cK) // 3px
                    cpx(cx + 2, cy - 5, cK); cpx(cx + 2, cy - 6, cK)
                    cpx(cx + 3, cy - 7, cK); cpx(cx + 3, cy - 8, cK); cpx(cx + 3, cy - 9, cK) // 3px
                    // Peak spikes (tallest!)
                    cpx(cx + 4, cy - 9, cK); cpx(cx + 4, cy - 10, cK); cpx(cx + 4, cy - 11, cK)
                    cpx(cx + 5, cy - 9, cK); cpx(cx + 5, cy - 8, cK)
                    // Right descending edge spikes
                    cpx(cx + 6, cy - 6, cK); cpx(cx + 6, cy - 7, cK); cpx(cx + 6, cy - 8, cK) // 3px
                    cpx(cx + 7, cy - 4, cK); cpx(cx + 7, cy - 5, cK)
                    cpx(cx + 8, cy - 2, cK); cpx(cx + 8, cy - 3, cK); cpx(cx + 8, cy - 4, cK) // 3px
                    cpx(cx + 9, cy + 0, cK); cpx(cx + 9, cy - 1, cK)
                    cpx(cx + 10, cy + 2, cK); cpx(cx + 10, cy + 1, cK); cpx(cx + 10, cy + 0, cK) // 3px

                    // ── Legs (4 stiff legs, side view) ──
                    // Near front leg
                    cvline(cy + 9, groundY, cx, cK)
                    cfillR(cx + 1, cx + 2, cy + 9, groundY, cO)
                    cvline(cy + 9, groundY, cx + 3, cK)
                    chline(cx, cx + 3, groundY, cK)
                    cpx(cx + 1, groundY, cPk); cpx(cx + 2, groundY, cPk)
                    // Far front leg (darker, slightly behind)
                    cvline(cy + 10, groundY, cx - 1, cK)
                    cfillR(cx - 1, cx, cy + 10, groundY - 1, cD)
                    cpx(cx - 1, groundY, cPk)
                    // Near back leg
                    cvline(cy + 9, groundY, cx + 7, cK)
                    cfillR(cx + 8, cx + 9, cy + 9, groundY, cO)
                    cvline(cy + 9, groundY, cx + 10, cK)
                    chline(cx + 7, cx + 10, groundY, cK)
                    cpx(cx + 8, groundY, cPk); cpx(cx + 9, groundY, cPk)
                    // Far back leg (darker)
                    cvline(cy + 10, groundY, cx + 11, cK)
                    cfillR(cx + 10, cx + 11, cy + 10, groundY - 1, cD)
                    cpx(cx + 11, groundY, cPk)

                    // ── Head (round, facing LEFT, larger) ──
                    let sHCX = cx - 5 + hx
                    let sHCY = cy + 3 + hy
                    cfillE(sHCX, sHCY, 6, 6, cK)        // outline
                    cfillE(sHCX, sHCY, 5, 5, cO)         // fill
                    cfillE(sHCX, sHCY + 1, 4, 4, cOL)    // highlight

                    // ── Ears (pointed UP, alert/scared) ──
                    // Near ear (tall, scared)
                    cfillTri(sHCX - 3, sHCY - 10 - ep, sHCX - 6, sHCY - 4, sHCX, sHCY - 4, cK)
                    cfillTri(sHCX - 3, sHCY - 9 - ep, sHCX - 5, sHCY - 4, sHCX - 1, sHCY - 4, cO)
                    cfillTri(sHCX - 3, sHCY - 8 - ep, sHCX - 4, sHCY - 5, sHCX - 2, sHCY - 5, cPE)
                    // Far ear (behind)
                    cfillTri(sHCX + 1, sHCY - 9 - ep, sHCX - 1, sHCY - 4, sHCX + 3, sHCY - 4, cK)
                    cfillTri(sHCX + 1, sHCY - 8 - ep, sHCX, sHCY - 4, sHCX + 2, sHCY - 4, cO)

                    // ── Eye (one visible — wide/scared, larger) ──
                    let sEX = sHCX - 3, sEY = sHCY - 1
                    switch eyeShape {
                    case .wide:
                        // 3x3 big scared eye
                        cfillR(sEX, sEX + 2, sEY - 1, sEY + 1, cEy)
                        cpx(sEX, sEY - 1, cW); cpx(sEX + 1, sEY - 1, cW)
                    case .happy:
                        cpx(sEX - 1, sEY + 1, cEy); cpx(sEX, sEY, cEy)
                        cpx(sEX + 1, sEY, cEy); cpx(sEX + 2, sEY + 1, cEy)
                    default:
                        cfillR(sEX, sEX + 1, sEY, sEY + 1, cEy)
                        cpx(sEX, sEY, cW)
                    }

                    // ── Nose (at snout edge) ──
                    cpx(sHCX - 6, sHCY + 1, cN); cpx(sHCX - 6, sHCY + 2, cN)

                    // ── Mouth ──
                    switch mouthShape {
                    case .open:
                        // Open hissing mouth
                        cpx(sHCX - 5, sHCY + 3, cK)
                        cpx(sHCX - 4, sHCY + 3, cPk); cpx(sHCX - 3, sHCY + 3, cPk)
                        cpx(sHCX - 2, sHCY + 3, cK)
                        cpx(sHCX - 4, sHCY + 4, cK); cpx(sHCX - 3, sHCY + 4, cK)
                    case .frown:
                        cpx(sHCX - 5, sHCY + 3, cK); cpx(sHCX - 4, sHCY + 4, cK)
                    default:
                        cpx(sHCX - 5, sHCY + 3, cK); cpx(sHCX - 4, sHCY + 3, cK)
                    }

                } else {

                // ── Body squash/stretch (continuous, moderate range) ──
                let sqClamped = max(-3.0, min(3.0, bodySquash))
                let bRX = max(3, 6 + Int(round(sqClamped)))
                let bRY = max(1, 3 - Int(round(sqClamped)))
                let bDY = Int(round(sqClamped * 0.7))

                // ── Shadow ──
                let jumpUp = max(0, -hy)
                let shadowY = cy + 13 + jumpUp
                let shadowW = max(3, 5 + (sqClamped > 0.4 ? 1 : 0) - jumpUp)
                let shadowAlpha = max(0.0, 0.14 - Double(jumpUp) * 0.03)
                if shadowAlpha > 0 && shadowY < 64 {
                    cfillE(cx, shadowY, shadowW, 1, Color.black.opacity(shadowAlpha))
                }

                // ── Tail (multi-segment spring chain) ──
                let tailDir = cx > 35 ? -1 : 1
                let tailBaseX = cx + 5 * tailDir
                let tailBaseY = cy + 9
                let tailPathDX = [0, 1, 2, 2, 3, 2, 1]
                let tailPathDY = [0, 0, -1, -2, -2, -3, -4]
                let segCount = min(tailSegments.count, tailPathDX.count)

                for i in 0..<segCount {
                    let wave = Int(round(tailSegments[i] * CGFloat(i + 1) * 0.18))
                    let tpx = tailBaseX + tailPathDX[i] * tailDir
                    let tpy = tailBaseY + tailPathDY[i] + wave
                    cpx(tpx, tpy, i < 4 ? cO : cD)
                    // Outline on outer edge
                    cpx(tpx + tailDir, tpy, cK)
                }
                // Tip outline
                if segCount > 0 {
                    let lastWave = Int(round(tailSegments[segCount-1] * CGFloat(segCount) * 0.18))
                    cpx(tailBaseX + tailPathDX[segCount-1] * tailDir, tailBaseY + tailPathDY[segCount-1] + lastWave - 1, cK)
                }

                // ── Body ──
                cfillE(cx, cy + 9 + bDY, bRX + 1, bRY + 1, cK)
                cfillE(cx, cy + 9 + bDY, bRX, bRY, cO)
                cfillE(cx, cy + 10 + bDY, max(1, bRX - 3), max(1, bRY - 1), cCr)

                // ── Paws ──
                chline(cx-4, cx-2, cy+12+plY, cK)
                chline(cx-4, cx-2, cy+11+plY, cO)
                cpx(cx-4, cy+12+plY, cPk); cpx(cx-2, cy+12+plY, cPk)
                if plY < -1 {
                    cvline(cy+12+plY, cy+11, cx-5, cK); cvline(cy+12+plY, cy+11, cx-1, cK)
                    cfillR(cx-4, cx-2, cy+12+plY, cy+11, cO)
                    chline(cx-4, cx-2, cy+12+plY, cK)
                    cpx(cx-4, cy+12+plY, cPk); cpx(cx-2, cy+12+plY, cPk)
                }
                chline(cx+2, cx+4, cy+12+prY, cK)
                chline(cx+2, cx+4, cy+11+prY, cO)
                cpx(cx+2, cy+12+prY, cPk); cpx(cx+4, cy+12+prY, cPk)
                if prY < -1 {
                    cvline(cy+12+prY, cy+11, cx+1, cK); cvline(cy+12+prY, cy+11, cx+5, cK)
                    cfillR(cx+2, cx+4, cy+12+prY, cy+11, cO)
                    chline(cx+2, cx+4, cy+12+prY, cK)
                    cpx(cx+2, cy+12+prY, cPk); cpx(cx+4, cy+12+prY, cPk)
                }

                // ── Head ──
                cfillE(cx+hx, cy+hy, 8, 7, cK)
                cfillE(cx+hx, cy+hy, 7, 6, cO)
                cfillE(cx+hx, cy+hy+1, 5, 4, cOL)

                // ── Ears ──
                cfillTri(cx+hx-6, cy+hy-9-ep, cx+hx-9, cy+hy-3, cx+hx-3, cy+hy-3, cK)
                cfillTri(cx+hx-6, cy+hy-7-ep, cx+hx-8, cy+hy-3, cx+hx-4, cy+hy-3, cO)
                cfillTri(cx+hx-6, cy+hy-6-ep, cx+hx-7, cy+hy-4, cx+hx-5, cy+hy-4, cPE)
                cfillTri(cx+hx+6, cy+hy-9-ep, cx+hx+3, cy+hy-3, cx+hx+9, cy+hy-3, cK)
                cfillTri(cx+hx+6, cy+hy-7-ep, cx+hx+4, cy+hy-3, cx+hx+8, cy+hy-3, cO)
                cfillTri(cx+hx+6, cy+hy-6-ep, cx+hx+5, cy+hy-4, cx+hx+7, cy+hy-4, cPE)

                // ── Eyes (smooth blink via eyeOpen) ──
                let elx = cx + hx - 4 + eyeX
                let erx = cx + hx + 3 + eyeX
                let eey = cy + hy - 1

                if eyeOpen <= 0.3 {
                    // Fully closed - thin lines
                    for ex in [elx, erx] { chline(ex, ex+1, eey+1, cEy) }
                } else if eyeOpen <= 0.7 {
                    // Half-closed - squished
                    for ex in [elx, erx] { chline(ex, ex+1, eey, cEy); chline(ex, ex+1, eey+1, cEy) }
                } else {
                    // Full eyes - shape depends on eyeShape
                    switch eyeShape {
                    case .normal:
                        for ex in [elx, erx] { cfillR(ex, ex+1, eey, eey+1, cEy); cpx(ex, eey, cW) }
                    case .happy:
                        for ex in [elx, erx] {
                            cpx(ex-1, eey+1, cEy); cpx(ex, eey, cEy); cpx(ex+1, eey, cEy); cpx(ex+2, eey+1, cEy)
                        }
                    case .heart:
                        for ex in [elx, erx] { cfillR(ex, ex+1, eey, eey+1, cPk) }
                    case .sleepy:
                        for ex in [elx, erx] { chline(ex, ex+1, eey+1, cEy) }
                    case .wide:
                        for ex in [elx, erx] {
                            cfillR(ex, ex+1, eey-1, eey+1, cEy); cpx(ex, eey-1, cW); cpx(ex+1, eey-1, cW)
                        }
                    case .sad:
                        let tearC = Color(red: 0.50, green: 0.72, blue: 0.96)
                        for ex in [elx, erx] {
                            cfillR(ex, ex+1, eey, eey+1, cEy); cpx(ex, eey, cW)
                            cpx(ex+2, eey+2, tearC)
                        }
                    }
                }

                // ── Nose ──
                cpx(cx+hx-1, cy+hy+3, cN); cpx(cx+hx, cy+hy+3, cN); cpx(cx+hx+1, cy+hy+3, cN)
                cpx(cx+hx, cy+hy+4, cN)

                // ── Mouth (new!) ──
                let mx = cx + hx
                let my = cy + hy + 5
                switch mouthShape {
                case .neutral:
                    cpx(mx-1, my, cK); cpx(mx+1, my, cK)
                case .smile:
                    cpx(mx-2, my, cK); cpx(mx+2, my, cK)
                    cpx(mx-1, my+1, cK); cpx(mx+1, my+1, cK)
                case .open:
                    cpx(mx-1, my, cK); cpx(mx, my, cPk); cpx(mx+1, my, cK)
                    cpx(mx-1, my+1, cK); cpx(mx, my+1, cPk); cpx(mx+1, my+1, cK)
                case .heart:
                    cpx(mx-1, my, cPk); cpx(mx+1, my, cPk); cpx(mx, my+1, cPk)
                case .closed:
                    cpx(mx-1, my, cK); cpx(mx, my, cK); cpx(mx+1, my, cK)
                case .frown:
                    cpx(mx-1, my, cK); cpx(mx, my+1, cK); cpx(mx+1, my, cK)
                }

                // ── Blush ──
                let ba = blush
                cpx(cx+hx-5, cy+hy+2, cPk.opacity(Double(ba)))
                cpx(cx+hx-4, cy+hy+2, cPk.opacity(Double(ba)))
                cpx(cx+hx+4, cy+hy+2, cPk.opacity(Double(ba)))
                cpx(cx+hx+5, cy+hy+2, cPk.opacity(Double(ba)))
                cpx(cx+hx-5, cy+hy+3, cPk.opacity(Double(ba * 0.65)))
                cpx(cx+hx+5, cy+hy+3, cPk.opacity(Double(ba * 0.65)))

                // ── Costume overlay (hats, goggles, etc.) ──
                if costume != .none && costume != .cape {
                    CatCostumeRenderer.drawFront(costume, ctx: catCtx,
                                                  cx: cx, cy: cy, hx: hx, hy: hy,
                                                  earPerk: earPerk, ps: ps,
                                                  lpY: plY, rpY: prY)
                }
                } // end frontSitting pose
            }

            // ── Front-of-cat props ──
            for prop in props where !prop.type.drawsBehind {
                PixelPropRenderer.draw(prop, ctx: scaledCtx, catX: cx, catY: cy,
                                       headX: hx, headY: hy, lpY: plY, rpY: prY, ps: ps)
            }

            // ── Particles ──
            for particle in particles {
                PixelParticleRenderer.draw(particle, ctx: scaledCtx, ps: ps)
            }

            } // end scaled cat layer

            // ════════════════════════════════
            // ── FOREGROUND (bed front rim) ──
            // ════════════════════════════════

            // Bed front rim — only when cat is near the bed
            let bedFrontStart = bedY + 2
            let bedFrontEnd = min(bedY + 5, mR)
            if cy > bedY - 15 && cx > 38 && bedFrontStart <= bedFrontEnd {
                let bedFrontCenter = bedY + 3
                for r in bedFrontStart...bedFrontEnd {
                    let dy = Double(r - bedFrontCenter) / 3.0
                    if dy * dy <= 1 {
                        let sp = Int(9.0 * sqrt(1 - dy * dy))
                        let cs = max(0, bedX - sp); let ce = min(63, bedX + sp)
                        if cs <= ce {
                            ctx.fill(Path(CGRect(x: CGFloat(cs)*ps, y: CGFloat(r)*ps,
                                                 width: CGFloat(ce-cs+1)*ps+0.5, height: ps+0.5)),
                                     with: .color(bedRim))
                        }
                    }
                }
            }
        }
    }
}

// MARK: - Speech Bubble

struct PixelChatBubble: View {
    let text: String
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(text)
                .font(.system(size: 12, weight: .black, design: .monospaced))
                .foregroundColor(Color(red: 0.12, green: 0.11, blue: 0.14))
                .padding(.horizontal, 8).padding(.vertical, 6)
                .background(Color.white)
                .overlay(Rectangle().stroke(Color(red: 0.12, green: 0.11, blue: 0.14), lineWidth: 2))
            HStack(spacing: 0) {
                Spacer().frame(width: 8)
                Rectangle().fill(Color.white).frame(width: 6, height: 6)
                    .overlay(Rectangle().stroke(Color(red: 0.12, green: 0.11, blue: 0.14), lineWidth: 1.5))
            }
            HStack(spacing: 0) {
                Spacer().frame(width: 3)
                Rectangle().fill(Color.white).frame(width: 5, height: 5)
                    .overlay(Rectangle().stroke(Color(red: 0.12, green: 0.11, blue: 0.14), lineWidth: 1.5))
            }
        }
    }
}

#Preview {
    ZStack {
        Color(red: 0.96, green: 0.95, blue: 0.93).ignoresSafeArea()
        CatMainView()
    }
}
