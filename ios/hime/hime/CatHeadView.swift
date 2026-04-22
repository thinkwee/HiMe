import SwiftUI

// MARK: - Cat Head for iPhone
//
// Pixel-art cat head identical to watchOS WatchCatHead, used in
// onboarding and other places where a compact cat avatar is needed.
// Duplicated from himewatch/WatchCatHead.swift because each target
// needs its own copy (same pattern as HimeWatchWidgets).

// MARK: - Expression types

private enum CatHeadEyeShape {
    case normal, happy, heart, sleepy, wide, sad
}

private enum CatHeadMouthShape {
    case neutral, smile, open, heart, closed, frown
}

// MARK: - State to expression mapping

private struct CatHeadExpression {
    let eye: CatHeadEyeShape
    let mouth: CatHeadMouthShape
    let blush: CGFloat
    let earPerk: Int

    static func from(state: String) -> CatHeadExpression {
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

// MARK: - CatHeadView

struct CatHeadView: View {
    let catState: String

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
        let expr = CatHeadExpression.from(state: catState)
        let gs = gridSize

        Canvas { ctx, size in
            let ps = size.width / CGFloat(gs)
            let cx = gs / 2
            let cy = gs / 2
            let ep = expr.earPerk

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

            func fillR(_ c0: Int, _ c1: Int, _ r0: Int, _ r1: Int, _ col: Color) {
                let s0 = max(0,c0); let e0 = min(gs-1,c1)
                let s1 = max(0,r0); let e1 = min(gs-1,r1)
                guard s0 <= e0, s1 <= e1 else { return }
                ctx.fill(Path(CGRect(x: CGFloat(s0)*ps, y: CGFloat(s1)*ps,
                                     width: CGFloat(e0-s0+1)*ps+0.5, height: CGFloat(e1-s1+1)*ps+0.5)),
                         with: .color(col))
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

        }
        .aspectRatio(1, contentMode: .fit)
    }
}
