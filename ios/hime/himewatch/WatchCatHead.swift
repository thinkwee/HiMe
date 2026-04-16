//
//  WatchCatHead.swift
//  himeWatch Watch App
//
//  Pixel art cat head with state-specific accessories, ported from iOS CatView.
//

import SwiftUI

// MARK: - Expression types

private enum WatchEyeShape {
    case normal, happy, heart, sleepy, wide, sad
}

private enum WatchMouthShape {
    case neutral, smile, open, heart, closed, frown
}

// MARK: - State to expression mapping

private struct CatExpression {
    let eye: WatchEyeShape
    let mouth: WatchMouthShape
    let blush: CGFloat
    let earPerk: Int

    static func from(state: String) -> CatExpression {
        switch state {
        case "energetic":   return .init(eye: .wide,   mouth: .open,    blush: 0.0, earPerk: 4)
        case "tired":       return .init(eye: .sleepy, mouth: .closed,  blush: 0.0, earPerk: -2)
        case "stressed":    return .init(eye: .wide,   mouth: .frown,   blush: 0.0, earPerk: 3)
        case "sad":         return .init(eye: .sad,    mouth: .frown,   blush: 0.0, earPerk: -2)
        case "relaxed":     return .init(eye: .normal, mouth: .neutral, blush: 0.5, earPerk: 1)
        case "curious":     return .init(eye: .wide,   mouth: .open,    blush: 0.0, earPerk: 3)
        case "happy":       return .init(eye: .happy,  mouth: .heart,   blush: 0.7, earPerk: 2)
        case "focused":     return .init(eye: .normal, mouth: .closed,  blush: 0.0, earPerk: 1)
        case "sleepy":      return .init(eye: .sleepy, mouth: .closed,  blush: 0.3, earPerk: -3)
        case "recovering":  return .init(eye: .happy,  mouth: .smile,   blush: 0.3, earPerk: 1)
        case "sick":        return .init(eye: .sleepy, mouth: .frown,   blush: 0.0, earPerk: -2)
        case "zen":         return .init(eye: .heart,  mouth: .smile,   blush: 0.6, earPerk: 1)
        case "proud":       return .init(eye: .happy,  mouth: .smile,   blush: 0.4, earPerk: 4)
        case "alert":       return .init(eye: .wide,   mouth: .open,    blush: 0.0, earPerk: 4)
        case "adventurous": return .init(eye: .wide,   mouth: .smile,   blush: 0.2, earPerk: 3)
        default:            return .init(eye: .normal, mouth: .neutral, blush: 0.3, earPerk: 1)
        }
    }
}

// MARK: - State color

func watchStateColor(for state: String) -> Color {
    switch state {
    case "energetic":   return .orange
    case "tired":       return .indigo
    case "stressed":    return .red
    case "sad":         return .blue
    case "relaxed":     return .green
    case "curious":     return .cyan
    case "happy":       return .yellow
    case "focused":     return .purple
    case "sleepy":      return Color(red: 0.55, green: 0.50, blue: 0.65)
    case "recovering":  return .teal
    case "sick":        return Color(red: 0.90, green: 0.55, blue: 0.55)
    case "zen":         return Color(red: 0.85, green: 0.75, blue: 0.45)
    case "proud":       return Color(red: 0.90, green: 0.72, blue: 0.25)
    case "alert":       return Color(red: 0.95, green: 0.65, blue: 0.20)
    case "adventurous": return Color(red: 0.30, green: 0.70, blue: 0.40)
    default:            return .green
    }
}

// MARK: - WatchCatHead View

struct WatchCatHead: View {
    let catState: String

    // Cat palette (same as iOS)
    private let cK  = Color(red: 0.42, green: 0.35, blue: 0.28)
    private let cO  = Color(red: 0.95, green: 0.70, blue: 0.35)
    private let cOL = Color(red: 0.98, green: 0.82, blue: 0.52)
    private let cPE = Color(red: 1.00, green: 0.74, blue: 0.80)
    private let cPk = Color(red: 1.00, green: 0.78, blue: 0.83)
    private let cN  = Color(red: 0.88, green: 0.50, blue: 0.54)
    private let cEy = Color(red: 0.14, green: 0.12, blue: 0.10)
    private let cW  = Color(red: 1.00, green: 1.00, blue: 0.98)

    private let gridSize = 40

    var body: some View {
        let expr = CatExpression.from(state: catState)
        let sc = watchStateColor(for: catState)
        let gs = gridSize

        Canvas { ctx, size in
            let ps = size.width / CGFloat(gs)
            let cx = gs / 2   // 20
            let cy = gs / 2   // 20
            let ep = expr.earPerk

            // ── Helper functions ──

            func px(_ c: Int, _ r: Int, _ col: Color) {
                guard c >= 0, c < gs, r >= 0, r < gs else { return }
                ctx.fill(Path(CGRect(x: CGFloat(c)*ps, y: CGFloat(r)*ps,
                                     width: ps+0.5, height: ps+0.5)),
                         with: .color(col))
            }

            func hline(_ c0: Int, _ c1: Int, _ r: Int, _ col: Color) {
                let s = max(0, c0); let e = min(gs-1, c1)
                guard s <= e, r >= 0, r < gs else { return }
                ctx.fill(Path(CGRect(x: CGFloat(s)*ps, y: CGFloat(r)*ps,
                                     width: CGFloat(e-s+1)*ps+0.5, height: ps+0.5)),
                         with: .color(col))
            }

            func fillR(_ c0: Int, _ c1: Int, _ r0: Int, _ r1: Int, _ col: Color) {
                let s0 = max(0,c0); let e0 = min(gs-1,c1)
                let s1 = max(0,r0); let e1 = min(gs-1,r1)
                guard s0 <= e0, s1 <= e1 else { return }
                ctx.fill(Path(CGRect(x: CGFloat(s0)*ps, y: CGFloat(s1)*ps,
                                     width: CGFloat(e0-s0+1)*ps+0.5, height: CGFloat(e1-s1+1)*ps+0.5)),
                         with: .color(col))
            }

            func fillE(_ ecx: Int, _ ecy: Int, _ rx: Int, _ ry: Int, _ col: Color) {
                guard rx > 0, ry > 0 else { return }
                for r in max(0, ecy-ry)...min(gs-1, ecy+ry) {
                    let dy = Double(r-ecy)/Double(ry)
                    if dy*dy > 1 { continue }
                    let sp = Int(Double(rx)*sqrt(1-dy*dy))
                    let cs = max(0, ecx-sp); let ce = min(gs-1, ecx+sp)
                    if cs <= ce {
                        ctx.fill(Path(CGRect(x: CGFloat(cs)*ps, y: CGFloat(r)*ps,
                                             width: CGFloat(ce-cs+1)*ps+0.5, height: ps+0.5)),
                                 with: .color(col))
                    }
                }
            }

            func fillTri(_ x1: Int, _ y1: Int, _ x2: Int, _ y2: Int, _ x3: Int, _ y3: Int, _ col: Color) {
                let d = (y2-y3)*(x1-x3)+(x3-x2)*(y1-y3)
                guard d != 0 else { return }
                let dD = Double(d)
                let rS = max(0, min(y1,min(y2,y3)))
                let rE = min(gs-1, max(y1,max(y2,y3)))
                let cS = max(0, min(x1,min(x2,x3)))
                let cE = min(gs-1, max(x1,max(x2,x3)))
                guard rS <= rE, cS <= cE else { return }
                for r in rS...rE {
                    for c in cS...cE {
                        let w1 = Double((y2-y3)*(c-x3)+(x3-x2)*(r-y3))/dD
                        let w2 = Double((y3-y1)*(c-x3)+(x1-x3)*(r-y3))/dD
                        let w3 = 1.0-w1-w2
                        if w1 >= -0.01, w2 >= -0.01, w3 >= -0.01 {
                            px(c, r, col)
                        }
                    }
                }
            }

            // ── Head ──
            fillE(cx, cy, 8, 7, cK)
            fillE(cx, cy, 7, 6, cO)
            fillE(cx, cy+1, 5, 4, cOL)

            // ── Ears ──
            fillTri(cx-6, cy-9-ep, cx-9, cy-3, cx-3, cy-3, cK)
            fillTri(cx-6, cy-7-ep, cx-8, cy-3, cx-4, cy-3, cO)
            fillTri(cx-6, cy-6-ep, cx-7, cy-4, cx-5, cy-4, cPE)
            fillTri(cx+6, cy-9-ep, cx+3, cy-3, cx+9, cy-3, cK)
            fillTri(cx+6, cy-7-ep, cx+4, cy-3, cx+8, cy-3, cO)
            fillTri(cx+6, cy-6-ep, cx+5, cy-4, cx+7, cy-4, cPE)

            // ── Eyes ──
            let elx = cx - 4
            let erx = cx + 3
            let eey = cy - 1

            switch expr.eye {
            case .normal:
                for ex in [elx, erx] { fillR(ex, ex+1, eey, eey+1, cEy); px(ex, eey, cW) }
            case .happy:
                for ex in [elx, erx] {
                    px(ex-1, eey+1, cEy); px(ex, eey, cEy); px(ex+1, eey, cEy); px(ex+2, eey+1, cEy)
                }
            case .heart:
                for ex in [elx, erx] {
                    px(ex, eey, cPk); px(ex+1, eey, cPk)
                    px(ex-1, eey+1, cPk); px(ex, eey+1, cPk); px(ex+1, eey+1, cPk); px(ex+2, eey+1, cPk)
                    px(ex, eey+2, cPk); px(ex+1, eey+2, cPk)
                }
            case .sleepy:
                for ex in [elx, erx] { hline(ex, ex+1, eey+1, cEy) }
            case .wide:
                for ex in [elx, erx] {
                    fillR(ex, ex+1, eey-1, eey+1, cEy); px(ex, eey-1, cW); px(ex+1, eey-1, cW)
                }
            case .sad:
                let tearC = Color(red: 0.50, green: 0.72, blue: 0.96)
                for ex in [elx, erx] {
                    fillR(ex, ex+1, eey, eey+1, cEy); px(ex, eey, cW)
                    px(ex+1, eey+2, tearC); px(ex+1, eey+3, tearC)
                }
            }

            // ── Nose ──
            px(cx-1, cy+3, cN); px(cx, cy+3, cN); px(cx+1, cy+3, cN)
            px(cx, cy+4, cN)

            // ── Mouth ──
            let mx = cx
            let my = cy + 5
            switch expr.mouth {
            case .neutral:
                px(mx-1, my, cK); px(mx+1, my, cK)
            case .smile:
                px(mx-2, my, cK); px(mx+2, my, cK)
                px(mx-1, my+1, cK); px(mx+1, my+1, cK)
            case .open:
                px(mx-1, my, cK); px(mx, my, cPk); px(mx+1, my, cK)
                px(mx-1, my+1, cK); px(mx, my+1, cPk); px(mx+1, my+1, cK)
            case .heart:
                px(mx-1, my, cPk); px(mx+1, my, cPk); px(mx, my+1, cPk)
            case .closed:
                px(mx-1, my, cK); px(mx, my, cK); px(mx+1, my, cK)
            case .frown:
                px(mx-1, my, cK); px(mx, my+1, cK); px(mx+1, my, cK)
            }

            // ── Blush ──
            let ba = expr.blush
            if ba > 0 {
                px(cx-5, cy+2, cPk.opacity(ba))
                px(cx-4, cy+2, cPk.opacity(ba))
                px(cx+4, cy+2, cPk.opacity(ba))
                px(cx+5, cy+2, cPk.opacity(ba))
                px(cx-5, cy+3, cPk.opacity(ba * 0.65))
                px(cx+5, cy+3, cPk.opacity(ba * 0.65))
            }

            // ══════════════════════════════════════
            // ── State-specific accessories ──
            // ══════════════════════════════════════

            switch catState {
            case "energetic":
                // Headband (red band across forehead)
                let hbC = Color(red: 0.90, green: 0.25, blue: 0.20)
                let hbL = Color(red: 1.00, green: 0.40, blue: 0.35)
                hline(cx-6, cx+6, cy-4, hbC)
                hline(cx-5, cx+5, cy-5, hbL)
                // Sweat drops
                let sw = Color(red: 0.55, green: 0.78, blue: 1.00)
                px(cx+10, cy-2, sw); px(cx+10, cy-1, sw); px(cx+10, cy, sw.opacity(0.5))

            case "tired":
                // Zzz floating above
                let zC = Color(white: 0.7)
                // Big Z
                hline(cx+7, cx+9, cy-8, zC); px(cx+9, cy-7, zC); px(cx+8, cy-6, zC); px(cx+7, cy-5, zC); hline(cx+7, cx+9, cy-5, zC)
                // Small z
                px(cx+10, cy-4, zC.opacity(0.6)); px(cx+11, cy-4, zC.opacity(0.6))
                px(cx+11, cy-3, zC.opacity(0.6)); px(cx+10, cy-2, zC.opacity(0.6)); px(cx+11, cy-2, zC.opacity(0.6))

            case "stressed":
                // Sweat drops on both sides + stress lines above
                let sw = Color(red: 0.55, green: 0.78, blue: 1.00)
                px(cx-9, cy-1, sw); px(cx-9, cy, sw); px(cx-9, cy+1, sw.opacity(0.4))
                px(cx+9, cy-1, sw); px(cx+9, cy, sw); px(cx+9, cy+1, sw.opacity(0.4))
                // Anger/stress cross mark
                let stC = Color(red: 0.90, green: 0.30, blue: 0.25)
                px(cx+6, cy-7, stC); px(cx+8, cy-7, stC)
                px(cx+7, cy-6, stC)
                px(cx+6, cy-5, stC); px(cx+8, cy-5, stC)

            case "sad":
                // Rain drops falling around head
                let rainC = Color(red: 0.50, green: 0.65, blue: 0.90)
                px(cx-8, cy-6, rainC); px(cx-8, cy-5, rainC.opacity(0.5))
                px(cx-5, cy-8, rainC); px(cx-5, cy-7, rainC.opacity(0.5))
                px(cx+5, cy-7, rainC); px(cx+5, cy-6, rainC.opacity(0.5))
                px(cx+8, cy-5, rainC); px(cx+8, cy-4, rainC.opacity(0.5))
                px(cx-3, cy-9, rainC.opacity(0.6))
                px(cx+3, cy-9, rainC.opacity(0.6))
                px(cx+7, cy-8, rainC.opacity(0.4))

            case "relaxed":
                // Leaf on head + steam wisps
                let leafG = Color(red: 0.40, green: 0.72, blue: 0.35)
                let leafD = Color(red: 0.30, green: 0.55, blue: 0.28)
                let stem  = Color(red: 0.45, green: 0.35, blue: 0.22)
                px(cx+2, cy-8, stem)
                px(cx+1, cy-7, leafG); px(cx+2, cy-7, leafG); px(cx+3, cy-7, leafG)
                px(cx+2, cy-6, leafD)
                // Tea steam
                let st = Color.white.opacity(0.4)
                px(cx-10, cy+2, st); px(cx-9, cy+1, st); px(cx-10, cy, st)

            case "curious":
                // Question mark floating
                let qC = Color(red: 0.30, green: 0.75, blue: 0.85)
                px(cx+8, cy-9, qC); px(cx+9, cy-9, qC); px(cx+10, cy-9, qC)
                px(cx+10, cy-8, qC)
                px(cx+9, cy-7, qC)
                px(cx+9, cy-5, qC)
                // Magnifying glass near right
                let fr = Color(red: 0.50, green: 0.65, blue: 0.80)
                let gl = Color(red: 0.82, green: 0.90, blue: 0.98)
                let hn = Color(red: 0.45, green: 0.32, blue: 0.20)
                px(cx-10, cy+1, fr); px(cx-9, cy+1, fr)
                px(cx-11, cy+2, fr); px(cx-10, cy+2, gl); px(cx-9, cy+2, gl); px(cx-8, cy+2, fr)
                px(cx-11, cy+3, fr); px(cx-10, cy+3, gl); px(cx-9, cy+3, gl); px(cx-8, cy+3, fr)
                px(cx-10, cy+4, fr); px(cx-9, cy+4, fr)
                px(cx-8, cy+5, hn); px(cx-7, cy+6, hn)

            case "happy":
                // Musical notes + sparkles
                let noteC = Color(red: 1.00, green: 0.85, blue: 0.30)
                // Note 1
                px(cx+9, cy-8, noteC); px(cx+9, cy-7, noteC); px(cx+9, cy-6, noteC)
                px(cx+8, cy-6, noteC); px(cx+8, cy-5, noteC)
                // Note 2
                px(cx-9, cy-6, noteC.opacity(0.7)); px(cx-9, cy-5, noteC.opacity(0.7))
                px(cx-10, cy-5, noteC.opacity(0.7)); px(cx-10, cy-4, noteC.opacity(0.7))
                // Sparkles
                let spC = Color(red: 1.00, green: 0.95, blue: 0.60)
                px(cx+11, cy-3, spC); px(cx-11, cy-1, spC)
                px(cx+10, cy+3, spC.opacity(0.6))

            case "focused":
                // Glasses on face
                let glC = Color(red: 0.35, green: 0.35, blue: 0.42)
                let lensC = Color(red: 0.72, green: 0.82, blue: 0.95)
                // Left lens
                px(cx-5, cy-2, glC); px(cx-4, cy-2, glC); px(cx-3, cy-2, glC)
                px(cx-5, cy-1, glC); px(cx-4, cy-1, lensC); px(cx-3, cy-1, lensC); px(cx-2, cy-1, glC)
                px(cx-5, cy,   glC); px(cx-4, cy,   lensC); px(cx-3, cy,   lensC); px(cx-2, cy,   glC)
                px(cx-5, cy+1, glC); px(cx-4, cy+1, glC); px(cx-3, cy+1, glC)
                // Bridge
                px(cx-2, cy-1, glC); px(cx-1, cy-1, glC); px(cx, cy-1, glC); px(cx+1, cy-1, glC)
                // Right lens
                px(cx+2, cy-2, glC); px(cx+3, cy-2, glC); px(cx+4, cy-2, glC)
                px(cx+1, cy-1, glC); px(cx+2, cy-1, glC); px(cx+3, cy-1, lensC); px(cx+4, cy-1, lensC); px(cx+5, cy-1, glC)
                px(cx+1, cy,   glC); px(cx+2, cy,   glC); px(cx+3, cy,   lensC); px(cx+4, cy,   lensC); px(cx+5, cy,   glC)
                px(cx+2, cy+1, glC); px(cx+3, cy+1, glC); px(cx+4, cy+1, glC)
                // Temples
                px(cx-6, cy-1, glC); px(cx-7, cy-1, glC)
                px(cx+6, cy-1, glC); px(cx+7, cy-1, glC)

            case "sleepy":
                // Nightcap on head
                let capC = Color(red: 0.45, green: 0.40, blue: 0.65)
                let capL = Color(red: 0.60, green: 0.55, blue: 0.78)
                fillTri(cx, cy-12, cx-5, cy-4, cx+5, cy-4, capC)
                fillTri(cx, cy-10, cx-4, cy-4, cx+4, cy-4, capL)
                // Pom-pom
                let pomC = Color.white
                px(cx, cy-13, pomC); px(cx-1, cy-12, pomC); px(cx+1, cy-12, pomC)
                // Zzz
                let zC = Color.white.opacity(0.5)
                px(cx+9, cy-5, zC); px(cx+10, cy-5, zC); px(cx+10, cy-4, zC); px(cx+9, cy-3, zC); px(cx+10, cy-3, zC)

            case "recovering":
                // Bandage on head + leaf
                let bandC = Color.white
                let crossC = Color(red: 0.90, green: 0.30, blue: 0.25)
                px(cx+3, cy-5, bandC); px(cx+4, cy-5, bandC); px(cx+5, cy-5, bandC)
                px(cx+3, cy-4, bandC); px(cx+4, cy-4, crossC); px(cx+5, cy-4, bandC)
                px(cx+3, cy-3, bandC); px(cx+4, cy-3, bandC); px(cx+5, cy-3, bandC)
                // Small leaf
                let leafG = Color(red: 0.40, green: 0.75, blue: 0.40)
                px(cx-8, cy-3, leafG); px(cx-7, cy-3, leafG); px(cx-7, cy-4, leafG)

            case "sick":
                // Thermometer in mouth area + ice pack on head
                let thR = Color(red: 0.85, green: 0.25, blue: 0.20)
                let thW = Color.white
                let thG = Color(red: 0.55, green: 0.55, blue: 0.60)
                // Thermometer sticking out
                px(cx+3, cy+5, thG); px(cx+4, cy+4, thG); px(cx+5, cy+3, thG)
                px(cx+6, cy+2, thW); px(cx+7, cy+1, thW)
                px(cx+8, cy, thR)
                // Ice pack on head
                let iceC = Color(red: 0.55, green: 0.78, blue: 0.95)
                let iceD = Color(red: 0.40, green: 0.60, blue: 0.82)
                hline(cx-3, cx+3, cy-7, iceD)
                hline(cx-3, cx+3, cy-6, iceC)
                hline(cx-3, cx+3, cy-5, iceC)
                hline(cx-3, cx+3, cy-4, iceD)

            case "zen":
                // Halo/glow ring above head
                let haloC = Color(red: 1.00, green: 0.92, blue: 0.50)
                let haloL = Color(red: 1.00, green: 0.95, blue: 0.70)
                px(cx-3, cy-10, haloC); px(cx-2, cy-11, haloC); px(cx-1, cy-11, haloL)
                px(cx, cy-12, haloL); px(cx+1, cy-11, haloL); px(cx+2, cy-11, haloC)
                px(cx+3, cy-10, haloC)
                // Lotus petals below
                let lotP = Color(red: 0.85, green: 0.55, blue: 0.72)
                let lotL = Color(red: 0.40, green: 0.70, blue: 0.45)
                px(cx-1, cy+8, lotP); px(cx, cy+7, lotP); px(cx+1, cy+8, lotP)
                px(cx-2, cy+8, lotL); px(cx+2, cy+8, lotL)

            case "proud":
                // Crown on head
                let crG = Color(red: 1.00, green: 0.85, blue: 0.20)
                let crD = Color(red: 0.85, green: 0.65, blue: 0.10)
                let gemR = Color(red: 0.90, green: 0.25, blue: 0.25)
                let gemB = Color(red: 0.30, green: 0.45, blue: 0.90)
                // Crown base
                hline(cx-5, cx+5, cy-5, crD)
                hline(cx-5, cx+5, cy-6, crG)
                // Crown points
                px(cx-4, cy-7, crG); px(cx, cy-8, crG); px(cx+4, cy-7, crG)
                px(cx-4, cy-8, crG); px(cx, cy-9, crG); px(cx+4, cy-8, crG)
                // Gems
                px(cx-2, cy-6, gemR); px(cx, cy-6, gemB); px(cx+2, cy-6, gemR)
                // Star sparkle
                let spC = Color(red: 1.00, green: 0.95, blue: 0.60)
                px(cx+9, cy-8, spC); px(cx+8, cy-9, spC); px(cx+10, cy-9, spC); px(cx+9, cy-10, spC)

            case "alert":
                // Exclamation marks + lightning bolt
                let exC = Color(red: 0.95, green: 0.25, blue: 0.20)
                // Left !
                px(cx-9, cy-8, exC); px(cx-9, cy-7, exC); px(cx-9, cy-6, exC)
                px(cx-9, cy-4, exC)
                // Right !
                px(cx+9, cy-8, exC); px(cx+9, cy-7, exC); px(cx+9, cy-6, exC)
                px(cx+9, cy-4, exC)
                // Lightning bolt
                let ltC = Color(red: 1.00, green: 0.85, blue: 0.20)
                px(cx+11, cy-2, ltC); px(cx+10, cy-1, ltC); px(cx+9, cy, ltC)
                px(cx+10, cy, ltC); px(cx+11, cy, ltC)
                px(cx+10, cy+1, ltC); px(cx+9, cy+2, ltC)

            case "adventurous":
                // Explorer hat
                let hatB = Color(red: 0.55, green: 0.40, blue: 0.22)
                let hatL = Color(red: 0.70, green: 0.55, blue: 0.32)
                // Brim
                hline(cx-8, cx+8, cy-5, hatB)
                hline(cx-7, cx+7, cy-4, hatB)
                // Dome
                hline(cx-4, cx+4, cy-6, hatL)
                hline(cx-3, cx+3, cy-7, hatL)
                hline(cx-2, cx+2, cy-8, hatB)
                // Hat band
                hline(cx-4, cx+4, cy-5, Color(red: 0.85, green: 0.30, blue: 0.25))
                // Compass at side
                let compC = Color(red: 0.60, green: 0.60, blue: 0.65)
                let compR = Color(red: 0.90, green: 0.30, blue: 0.25)
                px(cx-10, cy+1, compC); px(cx-9, cy+1, compC)
                px(cx-10, cy+2, compC); px(cx-9, cy+2, compC)
                px(cx-10, cy+1, compR)

            default:
                break
            }
        }
        .aspectRatio(1, contentMode: .fit)
        .background(
            Circle()
                .fill(sc.opacity(0.15))
        )
    }
}
