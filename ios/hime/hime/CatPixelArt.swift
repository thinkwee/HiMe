import SwiftUI

// MARK: - Prop Anchor

/// Determines how a prop tracks the cat's body parts.
enum PropAnchor {
    case world       // fixed offset from cat center (ground items)
    case leftPaw     // follows left paw position
    case rightPaw    // follows right paw position
    case aboveHead   // tracks above head
    case onHead      // sits on top of head
}

// MARK: - Pixel Prop Types

enum PixelPropType {
    case dumbbell        // energetic: cat lifts weights
    case pillow          // tired: cat flops onto it
    case yarnBall        // stressed: cat bats at tangled yarn
    case umbrella        // sad: cat holds umbrella in rain
    case teaCup          // relaxed: cat sips tea
    case magnifyingGlass // curious: cat peers through it
    case guitar          // happy: cat strums a tune
    case laptop          // focused: cat types furiously
    case blanket         // sleepy: cat wraps up in it
    case yogaMat         // recovering: cat stretches on mat
    case hotWaterBottle  // sick: cat clutches it
    case incense         // zen: smoke curls up
    case flag            // proud: cat plants flag
    case binoculars      // alert: cat scans the horizon
    case map             // adventurous: cat studies treasure map
    case flyingCarpet    // zen: ornate flying carpet for levitation
    case rocket          // proud: flanking rockets for liftoff
    case alarmClock      // stressed: ringing alarm clock
    case warningSign     // alert: yellow triangle warning
    case globe           // adventurous: spinning globe
    case thermometer     // sick: fever thermometer

    var defaultAnchor: PropAnchor {
        switch self {
        case .dumbbell:       return .world       // to the side of cat
        case .pillow:         return .world       // below cat
        case .yarnBall:       return .world       // to the side
        case .umbrella:       return .aboveHead   // above cat's head
        case .teaCup:         return .world       // to the side
        case .magnifyingGlass:return .world       // to the side
        case .guitar:         return .world        // to the side
        case .laptop:         return .world       // below cat
        case .blanket:        return .world       // wraps around cat (ok to overlap)
        case .yogaMat:        return .world       // below cat
        case .hotWaterBottle: return .world       // to the side
        case .incense:        return .world       // to the side
        case .flag:           return .world        // to the side
        case .binoculars:     return .world       // to the side
        case .map:            return .world       // below cat
        case .flyingCarpet:   return .world       // below cat
        case .rocket:         return .world       // to the side
        case .alarmClock:     return .world       // to the left
        case .warningSign:    return .world       // to the left
        case .globe:          return .world       // to the left
        case .thermometer:    return .world       // near mouth
        }
    }

    var defaultOffset: (dx: CGFloat, dy: CGFloat) {
        switch self {
        case .dumbbell:       return (-15, -2)    // to the left, clear of cat
        case .pillow:         return (0, 14)      // below cat
        case .yarnBall:       return (-15, 4)     // to the left, clear of cat
        case .umbrella:       return (0, -6)      // above head via aboveHead anchor
        case .teaCup:         return (-15, 4)     // to the left, clear of cat
        case .magnifyingGlass:return (-15, -2)    // to the left, clear of cat
        case .guitar:         return (15, 0)      // to the right, clear of cat
        case .laptop:         return (0, 15)      // below cat
        case .blanket:        return (0, 6)       // around lower body (ok to overlap)
        case .yogaMat:        return (0, 14)      // below cat
        case .hotWaterBottle: return (-14, 0)     // to the left, clear of cat
        case .incense:        return (15, 2)      // to the right
        case .flag:           return (15, -4)     // to the right, clear of cat
        case .binoculars:     return (-15, -2)    // to the left, clear of cat
        case .map:            return (0, 15)      // below cat
        case .flyingCarpet:   return (0, 8)       // below cat (carpet under cat)
        case .rocket:         return (15, 0)      // to the side
        case .alarmClock:     return (-15, 2)     // to the left
        case .warningSign:    return (-16, -4)    // to the left, slightly above
        case .globe:          return (-15, 2)     // to the left
        case .thermometer:    return (-5, -4)     // near cat's mouth area
        }
    }

    /// Scale multiplier for prop pixel art rendering. Larger = bigger prop.
    var sizeScale: CGFloat {
        switch self {
        case .dumbbell:       return 1.5
        case .pillow:         return 1.5
        case .yarnBall:       return 1.8
        case .umbrella:       return 2.2
        case .teaCup:         return 1.8
        case .magnifyingGlass:return 1.8
        case .guitar:         return 1.5
        case .laptop:         return 1.8
        case .blanket:        return 1.2
        case .yogaMat:        return 1.2
        case .hotWaterBottle: return 1.5
        case .incense:        return 1.2
        case .flag:           return 1.5
        case .binoculars:     return 1.5
        case .map:            return 2.0
        case .flyingCarpet:   return 2.2
        case .rocket:         return 1.5
        case .alarmClock:     return 1.8
        case .warningSign:    return 2.0
        case .globe:          return 2.0
        case .thermometer:    return 1.5
        }
    }

    var debugName: String {
        switch self {
        case .dumbbell:       return "dumbbell"
        case .pillow:         return "pillow"
        case .yarnBall:       return "yarn"
        case .umbrella:       return "umbrella"
        case .teaCup:         return "tea"
        case .magnifyingGlass:return "glass"
        case .guitar:         return "guitar"
        case .laptop:         return "laptop"
        case .blanket:        return "blanket"
        case .yogaMat:        return "yoga"
        case .hotWaterBottle: return "bottle"
        case .incense:        return "incense"
        case .flag:           return "flag"
        case .binoculars:     return "binoculars"
        case .map:            return "map"
        case .flyingCarpet:   return "carpet"
        case .rocket:         return "rocket"
        case .alarmClock:     return "alarm"
        case .warningSign:    return "warning"
        case .globe:          return "globe"
        case .thermometer:    return "thermo"
        }
    }

    var drawsBehind: Bool {
        switch self {
        case .pillow, .yogaMat, .blanket, .flyingCarpet: return true
        default: return false
        }
    }
}

// MARK: - Prop Instance

struct PropInstance: Identifiable {
    let id = UUID()
    let type: PixelPropType
    var anchor: PropAnchor
    var relX: CGFloat
    var relY: CGFloat
    var lifetime: CGFloat = 0
    var removeAt: CGFloat? = nil

    var scale: CGFloat {
        let popIn = easeOutElastic(min(1, lifetime * 3.5))
        if let removeAt, lifetime > removeAt {
            return popIn * max(0, 1 - (lifetime - removeAt) * 4)
        }
        return popIn
    }

    var isDead: Bool {
        if let removeAt { return lifetime > removeAt + 0.3 }
        return false
    }

    var bobOffset: CGFloat { sin(lifetime * 3.0) * 0.6 }
}

// MARK: - Pixel Particle Types

enum PixelParticleType {
    case sparkle, heart, note, zzz, drop, confetti, starBurst, puff
}

struct PixelParticle: Identifiable {
    let id = UUID()
    let type: PixelParticleType
    var x: CGFloat, y: CGFloat
    var vx: CGFloat, vy: CGFloat
    var life: CGFloat
    let maxLife: CGFloat
    var scale: CGFloat = 1
    var color: Color = .yellow

    var progress: CGFloat { min(1, life / maxLife) }
    var alpha: CGFloat {
        let fadeIn = min(1, progress * 5)
        let fadeOut = max(0, 1 - (progress - 0.6) / 0.4)
        return fadeIn * fadeOut
    }
}

// MARK: - Prop Renderer

struct PixelPropRenderer {
    /// Draw a prop with anchor-based positioning that tracks cat body parts.
    static func draw(_ prop: PropInstance, ctx: GraphicsContext,
                     catX: Int, catY: Int,
                     headX: Int, headY: Int,
                     lpY: Int, rpY: Int,
                     ps: CGFloat) {
        let sc = prop.scale
        let ssf = prop.type.sizeScale
        guard sc > 0.01 else { return }

        // Resolve anchor to base position
        let (bx, by): (CGFloat, CGFloat) = {
            switch prop.anchor {
            case .world:     return (CGFloat(catX), CGFloat(catY))
            case .leftPaw:   return (CGFloat(catX - 3), CGFloat(catY + 11 + lpY))
            case .rightPaw:  return (CGFloat(catX + 3), CGFloat(catY + 11 + rpY))
            case .aboveHead: return (CGFloat(catX + headX), CGFloat(catY + headY - 10))
            case .onHead:    return (CGFloat(catX + headX), CGFloat(catY + headY - 7))
            }
        }()

        let centerX = (bx + prop.relX) * ps
        let centerY = (by + prop.relY + prop.bobOffset) * ps

        func dpx(_ dx: Int, _ dy: Int, _ color: Color) {
            let sz = ps * sc * ssf
            let px = centerX + CGFloat(dx) * sz - sz * 0.5
            let py = centerY + CGFloat(dy) * sz - sz * 0.5
            ctx.fill(Path(CGRect(x: px, y: py, width: sz + 0.5, height: sz + 0.5)),
                     with: .color(color))
        }

        switch prop.type {
        case .dumbbell:
            // 7x3 barbell: gray weight plates, brown bar
            let gr = Color(red: 0.55, green: 0.55, blue: 0.60)
            let dg = Color(red: 0.40, green: 0.40, blue: 0.45)
            let br = Color(red: 0.50, green: 0.35, blue: 0.22)
            // Top row
            dpx(-3,-1,gr); dpx(-2,-1,gr); dpx(0,-1,br); dpx(2,-1,gr); dpx(3,-1,gr)
            // Middle row
            dpx(-3,0,dg); dpx(-2,0,gr); dpx(-1,0,br); dpx(0,0,br); dpx(1,0,br); dpx(2,0,gr); dpx(3,0,dg)
            // Bottom row
            dpx(-3,1,gr); dpx(-2,1,gr); dpx(0,1,br); dpx(2,1,gr); dpx(3,1,gr)

        case .pillow:
            let p1 = Color(red: 0.78, green: 0.72, blue: 0.88)
            let p2 = Color(red: 0.88, green: 0.84, blue: 0.95)
            let p3 = Color(red: 0.68, green: 0.62, blue: 0.78)
            dpx(1,0,p3); dpx(2,0,p3); dpx(3,0,p3); dpx(4,0,p3)
            dpx(0,1,p3); dpx(1,1,p2); dpx(2,1,p2); dpx(3,1,p2); dpx(4,1,p1); dpx(5,1,p3)
            dpx(0,2,p3); dpx(1,2,p1); dpx(2,2,p2); dpx(3,2,p2); dpx(4,2,p1); dpx(5,2,p3)
            dpx(1,3,p3); dpx(2,3,p3); dpx(3,3,p3); dpx(4,3,p3)

        case .yarnBall:
            // 5x5 classic white yarn ball with black thread lines
            let wh = Color(red: 0.95, green: 0.93, blue: 0.90) // white yarn
            let lt = Color(red: 0.88, green: 0.85, blue: 0.82) // light shadow
            let bk = Color(red: 0.25, green: 0.22, blue: 0.20) // black thread
            // Ball shape
            dpx(0,-2,wh); dpx(1,-2,lt); dpx(2,-2,wh)
            dpx(-1,-1,wh); dpx(0,-1,bk); dpx(1,-1,wh); dpx(2,-1,bk); dpx(3,-1,wh)
            dpx(-1,0,lt); dpx(0,0,wh); dpx(1,0,bk); dpx(2,0,wh); dpx(3,0,lt)
            dpx(-1,1,wh); dpx(0,1,bk); dpx(1,1,wh); dpx(2,1,bk); dpx(3,1,wh)
            dpx(0,2,wh); dpx(1,2,lt); dpx(2,2,wh)
            // Trailing thread
            dpx(3,2,bk); dpx(4,3,bk); dpx(5,3,bk); dpx(6,4,bk)

        case .umbrella:
            // Very wide canopy (13px) + handle — big enough to shelter the cat
            let bl = Color(red: 0.40, green: 0.55, blue: 0.85)
            let lb = Color(red: 0.55, green: 0.70, blue: 0.95)
            let hl = Color(red: 0.65, green: 0.80, blue: 0.98)
            let br = Color(red: 0.50, green: 0.35, blue: 0.22)
            // Top dome (3 rows)
            dpx(-3, -5, bl); dpx(-2, -5, bl); dpx(-1, -5, bl); dpx(0, -5, bl); dpx(1, -5, bl); dpx(2, -5, bl); dpx(3, -5, bl)
            // Wide canopy
            dpx(-6, -4, bl); dpx(-5, -4, bl); dpx(-4, -4, lb); dpx(-3, -4, lb); dpx(-2, -4, hl); dpx(-1, -4, lb)
            dpx(0, -4, hl); dpx(1, -4, lb); dpx(2, -4, hl); dpx(3, -4, lb); dpx(4, -4, lb); dpx(5, -4, bl); dpx(6, -4, bl)
            // Second canopy row
            dpx(-6, -3, bl); dpx(-5, -3, lb); dpx(-4, -3, hl); dpx(-3, -3, lb); dpx(-2, -3, lb); dpx(-1, -3, lb)
            dpx(0, -3, lb); dpx(1, -3, lb); dpx(2, -3, lb); dpx(3, -3, lb); dpx(4, -3, hl); dpx(5, -3, lb); dpx(6, -3, bl)
            // Scalloped edge
            dpx(-5, -2, bl); dpx(-3, -2, bl); dpx(-1, -2, bl); dpx(1, -2, bl); dpx(3, -2, bl); dpx(5, -2, bl)
            // Handle
            dpx(0, -1, br); dpx(0, 0, br); dpx(0, 1, br)
            dpx(-1, 2, br) // curved handle end

        case .teaCup:
            let br = Color(red: 0.55, green: 0.38, blue: 0.22)
            let cr = Color(red: 0.95, green: 0.88, blue: 0.72)
            let st = Color.white.opacity(0.6)
            let steamPhase = prop.lifetime.truncatingRemainder(dividingBy: 1.2)
            if steamPhase < 0.6 { dpx(1,-2,st); dpx(3,-1,st) }
            else { dpx(2,-2,st); dpx(0,-1,st) }
            dpx(0,0,br); dpx(1,0,cr); dpx(2,0,cr); dpx(3,0,cr); dpx(4,0,br)
            dpx(0,1,br); dpx(1,1,cr); dpx(2,1,cr); dpx(3,1,cr); dpx(4,1,br)
            dpx(1,2,br); dpx(2,2,br); dpx(3,2,br)
            dpx(5,0,br); dpx(5,1,br) // handle

        case .magnifyingGlass:
            let fr = Color(red: 0.50, green: 0.65, blue: 0.80)
            let gl = Color(red: 0.82, green: 0.90, blue: 0.98)
            let hn = Color(red: 0.45, green: 0.32, blue: 0.20)
            dpx(1,0,fr); dpx(2,0,fr)
            dpx(0,1,fr); dpx(1,1,gl); dpx(2,1,gl); dpx(3,1,fr)
            dpx(0,2,fr); dpx(1,2,gl); dpx(2,2,gl); dpx(3,2,fr)
            dpx(1,3,fr); dpx(2,3,fr)
            dpx(3,3,hn); dpx(4,4,hn); dpx(5,5,hn) // handle

        case .guitar:
            // 4x8 guitar: headstock, neck, body
            let dbr = Color(red: 0.35, green: 0.22, blue: 0.12)
            let lbr = Color(red: 0.65, green: 0.45, blue: 0.25)
            let og  = Color(red: 0.85, green: 0.55, blue: 0.25)
            let dk  = Color(red: 0.30, green: 0.18, blue: 0.10)
            let gr  = Color(red: 0.72, green: 0.72, blue: 0.75)
            // Headstock
            dpx(0,-4,dbr); dpx(1,-4,dbr)
            dpx(-1,-3,gr); dpx(0,-3,dbr); dpx(1,-3,dbr); dpx(2,-3,gr)
            // Neck
            dpx(0,-2,lbr); dpx(1,-2,lbr)
            dpx(0,-1,lbr); dpx(1,-1,lbr)
            // Body
            dpx(-1,0,og); dpx(0,0,og); dpx(1,0,og); dpx(2,0,og)
            dpx(-1,1,og); dpx(0,1,dk); dpx(1,1,dk); dpx(2,1,og)
            dpx(-1,2,og); dpx(0,2,og); dpx(1,2,og); dpx(2,2,og)
            dpx(0,3,og); dpx(1,3,og)

        case .laptop:
            // 6x5 open laptop with screen glow
            let dk = Color(red: 0.28, green: 0.28, blue: 0.32)
            let lb = Color(red: 0.65, green: 0.80, blue: 0.95)
            let gr = Color(red: 0.55, green: 0.55, blue: 0.58)
            let lg = Color(red: 0.68, green: 0.68, blue: 0.72)
            // Screen
            dpx(-1,-2,dk); dpx(0,-2,dk); dpx(1,-2,dk); dpx(2,-2,dk); dpx(3,-2,dk)
            dpx(-1,-1,dk); dpx(0,-1,lb); dpx(1,-1,lb); dpx(2,-1,lb); dpx(3,-1,dk)
            dpx(-1,0,dk); dpx(0,0,lb); dpx(1,0,lb); dpx(2,0,lb); dpx(3,0,dk)
            // Keyboard
            dpx(-2,1,gr); dpx(-1,1,lg); dpx(0,1,lg); dpx(1,1,lg); dpx(2,1,lg); dpx(3,1,gr)
            dpx(-2,2,dk); dpx(-1,2,gr); dpx(0,2,gr); dpx(1,2,gr); dpx(2,2,gr); dpx(3,2,dk)

        case .blanket:
            // 7x4 checkered blanket
            let wr = Color(red: 0.78, green: 0.42, blue: 0.35)
            let cr = Color(red: 0.95, green: 0.85, blue: 0.72)
            dpx(-2,-1,wr); dpx(-1,-1,cr); dpx(0,-1,wr); dpx(1,-1,cr); dpx(2,-1,wr); dpx(3,-1,cr); dpx(4,-1,wr)
            dpx(-3,0,cr); dpx(-2,0,wr); dpx(-1,0,cr); dpx(0,0,wr); dpx(1,0,cr); dpx(2,0,wr); dpx(3,0,cr)
            dpx(-3,1,wr); dpx(-2,1,cr); dpx(-1,1,wr); dpx(0,1,cr); dpx(1,1,wr); dpx(2,1,cr); dpx(3,1,wr)
            dpx(-2,2,cr); dpx(-1,2,wr); dpx(0,2,cr); dpx(1,2,wr); dpx(2,2,cr); dpx(3,2,wr); dpx(4,2,cr)

        case .yogaMat:
            // 7x2 rolled mat
            let tl = Color(red: 0.35, green: 0.65, blue: 0.60)
            let lt = Color(red: 0.45, green: 0.75, blue: 0.70)
            let dt = Color(red: 0.28, green: 0.52, blue: 0.48)
            dpx(-3,0,tl); dpx(-2,0,tl); dpx(-1,0,lt); dpx(0,0,tl); dpx(1,0,lt); dpx(2,0,tl); dpx(3,0,tl)
            dpx(-3,1,dt); dpx(-2,1,tl); dpx(-1,1,tl); dpx(0,1,tl); dpx(1,1,tl); dpx(2,1,tl); dpx(3,1,dt)

        case .hotWaterBottle:
            // 4x6 round bottle
            let rd = Color(red: 0.85, green: 0.35, blue: 0.30)
            let lr = Color(red: 0.92, green: 0.50, blue: 0.45)
            let br = Color(red: 0.55, green: 0.38, blue: 0.25)
            dpx(0,-2,br); dpx(1,-2,br)  // neck cap
            dpx(-1,-1,rd); dpx(0,-1,rd); dpx(1,-1,rd); dpx(2,-1,rd)
            dpx(-1,0,rd); dpx(0,0,lr); dpx(1,0,lr); dpx(2,0,rd)
            dpx(-1,1,rd); dpx(0,1,lr); dpx(1,1,lr); dpx(2,1,rd)
            dpx(-1,2,rd); dpx(0,2,rd); dpx(1,2,rd); dpx(2,2,rd)
            dpx(0,3,rd); dpx(1,3,rd)

        case .incense:
            // 3x7 incense with animated smoke
            let og = Color(red: 0.95, green: 0.65, blue: 0.20)
            let br = Color(red: 0.50, green: 0.35, blue: 0.22)
            let gr = Color(red: 0.55, green: 0.55, blue: 0.58)
            let sm = Color.white.opacity(0.5)
            // Smoke (animated)
            let sp = prop.lifetime.truncatingRemainder(dividingBy: 1.0)
            if sp < 0.33 { dpx(0,-4,sm); dpx(1,-5,sm) }
            else if sp < 0.66 { dpx(-1,-4,sm); dpx(0,-5,sm) }
            else { dpx(1,-4,sm); dpx(-1,-5,sm) }
            // Ember tip
            dpx(0,-3,og)
            // Stick
            dpx(0,-2,br); dpx(0,-1,br); dpx(0,0,br); dpx(0,1,br)
            // Holder
            dpx(-1,2,gr); dpx(0,2,gr); dpx(1,2,gr)

        case .flag:
            // 5x7 flag on pole
            let gd = Color(red: 0.92, green: 0.78, blue: 0.28)
            let rd = Color(red: 0.88, green: 0.35, blue: 0.30)
            let br = Color(red: 0.50, green: 0.35, blue: 0.22)
            let gr = Color(red: 0.55, green: 0.55, blue: 0.58)
            // Flag
            dpx(0,-4,gd); dpx(1,-4,gd); dpx(2,-4,gd); dpx(3,-4,gd); dpx(4,-4,gd)
            dpx(0,-3,gd); dpx(1,-3,rd); dpx(2,-3,rd); dpx(3,-3,gd); dpx(4,-3,gd)
            dpx(0,-2,gd); dpx(1,-2,gd); dpx(2,-2,gd); dpx(3,-2,gd)
            // Pole
            dpx(0,-1,br); dpx(0,0,br); dpx(0,1,br); dpx(0,2,br); dpx(0,3,br)
            // Base
            dpx(-1,3,gr); dpx(1,3,gr)

        case .binoculars:
            // 5x4 binoculars
            let dk = Color(red: 0.25, green: 0.25, blue: 0.28)
            let gr = Color(red: 0.50, green: 0.50, blue: 0.55)
            let gl = Color(red: 0.65, green: 0.78, blue: 0.92)
            dpx(0,-1,dk); dpx(1,-1,dk); dpx(3,-1,dk); dpx(4,-1,dk)
            dpx(0,0,gr); dpx(1,0,gl); dpx(2,0,gr); dpx(3,0,gl); dpx(4,0,gr)
            dpx(0,1,gr); dpx(1,1,gr); dpx(2,1,gr); dpx(3,1,gr); dpx(4,1,gr)
            dpx(1,2,dk); dpx(2,2,dk); dpx(3,2,dk)

        case .map:
            // 6x5 treasure map with X
            let br = Color(red: 0.55, green: 0.40, blue: 0.22)
            let cr = Color(red: 0.92, green: 0.85, blue: 0.68)
            let rd = Color(red: 0.88, green: 0.30, blue: 0.25)
            // Top edge
            dpx(-1,-2,br); dpx(0,-2,br); dpx(1,-2,br); dpx(2,-2,br); dpx(3,-2,br)
            // Paper
            dpx(-1,-1,cr); dpx(0,-1,cr); dpx(1,-1,cr); dpx(2,-1,cr); dpx(3,-1,cr)
            dpx(-1,0,cr); dpx(0,0,cr); dpx(1,0,cr); dpx(2,0,rd); dpx(3,0,cr) // X
            dpx(-1,1,cr); dpx(0,1,cr); dpx(1,1,rd); dpx(2,1,cr); dpx(3,1,cr) // X
            // Bottom edge
            dpx(-1,2,br); dpx(0,2,br); dpx(1,2,br); dpx(2,2,br); dpx(3,2,br)

        case .flyingCarpet:
            // 9x5 ornate flying carpet with tassels
            let cR = Color(red: 0.78, green: 0.18, blue: 0.22)
            let cG = Color(red: 0.90, green: 0.72, blue: 0.20)
            let cP = Color(red: 0.55, green: 0.22, blue: 0.50)
            let cL = Color(red: 0.92, green: 0.82, blue: 0.55)
            // Tassels top
            dpx(-4,-2,cG); dpx(4,-2,cG)
            // Border top
            dpx(-4,-1,cG); dpx(-3,-1,cR); dpx(-2,-1,cG); dpx(-1,-1,cR); dpx(0,-1,cG)
            dpx(1,-1,cR); dpx(2,-1,cG); dpx(3,-1,cR); dpx(4,-1,cG)
            // Center rows with pattern
            dpx(-4,0,cR); dpx(-3,0,cP); dpx(-2,0,cL); dpx(-1,0,cP); dpx(0,0,cG)
            dpx(1,0,cP); dpx(2,0,cL); dpx(3,0,cP); dpx(4,0,cR)
            dpx(-4,1,cG); dpx(-3,1,cR); dpx(-2,1,cP); dpx(-1,1,cG); dpx(0,1,cR)
            dpx(1,1,cG); dpx(2,1,cP); dpx(3,1,cR); dpx(4,1,cG)
            // Border bottom
            dpx(-4,2,cG); dpx(-3,2,cR); dpx(-2,2,cG); dpx(-1,2,cR); dpx(0,2,cG)
            dpx(1,2,cR); dpx(2,2,cG); dpx(3,2,cR); dpx(4,2,cG)
            // Tassels bottom
            dpx(-4,3,cG); dpx(-3,3,cR); dpx(3,3,cR); dpx(4,3,cG)

        case .rocket:
            // 3x8 rocket with flame
            let wh = Color.white
            let rd = Color(red: 0.90, green: 0.25, blue: 0.20)
            let gr = Color(red: 0.60, green: 0.60, blue: 0.65)
            let og = Color(red: 0.95, green: 0.65, blue: 0.15)
            let yw = Color(red: 1.00, green: 0.90, blue: 0.30)
            // Nose cone
            dpx(0,-4,rd)
            dpx(-1,-3,rd); dpx(0,-3,wh); dpx(1,-3,rd)
            // Body
            dpx(-1,-2,wh); dpx(0,-2,gr); dpx(1,-2,wh)
            dpx(-1,-1,wh); dpx(0,-1,rd); dpx(1,-1,wh)
            dpx(-1,0,wh); dpx(0,0,gr); dpx(1,0,wh)
            // Fins
            dpx(-2,1,rd); dpx(-1,1,gr); dpx(0,1,gr); dpx(1,1,gr); dpx(2,1,rd)
            // Exhaust
            dpx(-1,2,og); dpx(0,2,yw); dpx(1,2,og)
            // Flame particles (animated)
            let fp = prop.lifetime.truncatingRemainder(dividingBy: 0.4)
            if fp < 0.2 { dpx(0,3,yw); dpx(-1,4,og) }
            else { dpx(0,3,og); dpx(1,4,yw) }

        case .alarmClock:
            let mt = Color(red: 0.55, green: 0.55, blue: 0.60) // metal
            let fc = Color(red: 0.95, green: 0.92, blue: 0.78) // face
            let dk = Color(red: 0.35, green: 0.35, blue: 0.38) // dark
            let rd = Color(red: 0.85, green: 0.20, blue: 0.20) // red accent
            // Animated bell shake
            let shake = Int(round(sin(prop.lifetime * 16) * 1.2))
            // Bells
            dpx(-2 + shake, -3, dk); dpx(2 + shake, -3, dk)
            dpx(-1 + shake, -3, mt); dpx(1 + shake, -3, mt)
            // Clock body
            dpx(-2, -2, mt); dpx(-1, -2, mt); dpx(0, -2, mt); dpx(1, -2, mt); dpx(2, -2, mt)
            dpx(-2, -1, mt); dpx(-1, -1, fc); dpx(0, -1, fc); dpx(1, -1, fc); dpx(2, -1, mt)
            dpx(-2, 0, mt); dpx(-1, 0, fc); dpx(0, 0, dk); dpx(1, 0, fc); dpx(2, 0, mt)
            dpx(-2, 1, mt); dpx(-1, 1, fc); dpx(0, 1, fc); dpx(1, 1, fc); dpx(2, 1, mt)
            dpx(-2, 2, mt); dpx(-1, 2, mt); dpx(0, 2, mt); dpx(1, 2, mt); dpx(2, 2, mt)
            // Clock hands
            dpx(0, -1, dk); dpx(1, -1, rd)
            // Feet
            dpx(-2, 3, dk); dpx(2, 3, dk)

        case .warningSign:
            let yw = Color(red: 0.95, green: 0.85, blue: 0.10)
            let ywD = Color(red: 0.80, green: 0.70, blue: 0.08)
            let bk = Color(red: 0.12, green: 0.12, blue: 0.12)
            // Triangle shape with ! inside
            dpx(0, -3, yw)
            dpx(-1, -2, yw); dpx(0, -2, yw); dpx(1, -2, yw)
            dpx(-2, -1, yw); dpx(-1, -1, yw); dpx(0, -1, bk); dpx(1, -1, yw); dpx(2, -1, yw)
            dpx(-3, 0, yw); dpx(-2, 0, yw); dpx(-1, 0, yw); dpx(0, 0, bk); dpx(1, 0, yw); dpx(2, 0, yw); dpx(3, 0, yw)
            dpx(-4, 1, yw); dpx(-3, 1, yw); dpx(-2, 1, yw); dpx(-1, 1, yw); dpx(0, 1, yw); dpx(1, 1, yw); dpx(2, 1, yw); dpx(3, 1, yw); dpx(4, 1, yw)
            dpx(-4, 2, ywD); dpx(-3, 2, ywD); dpx(-2, 2, ywD); dpx(-1, 2, ywD); dpx(0, 2, bk); dpx(1, 2, ywD); dpx(2, 2, ywD); dpx(3, 2, ywD); dpx(4, 2, ywD)

        case .globe:
            let oc = Color(red: 0.25, green: 0.50, blue: 0.85) // ocean blue
            let ld = Color(red: 0.30, green: 0.70, blue: 0.35) // land green
            let _ = Color(red: 0.18, green: 0.38, blue: 0.68) // ocean dark (reserved)
            let ax = Color(red: 0.55, green: 0.55, blue: 0.60) // axis
            // Axis pole
            dpx(0, -4, ax); dpx(0, 4, ax)
            // Spinning animation - alternate between two frames
            let frame = Int(prop.lifetime * 2) % 2
            // Globe body (round, 5x5 core)
            if frame == 0 {
                dpx(-1, -2, oc); dpx(0, -2, ld); dpx(1, -2, oc)
                dpx(-2, -1, oc); dpx(-1, -1, ld); dpx(0, -1, oc); dpx(1, -1, oc); dpx(2, -1, oc)
                dpx(-2, 0, ld); dpx(-1, 0, oc); dpx(0, 0, oc); dpx(1, 0, ld); dpx(2, 0, oc)
                dpx(-2, 1, oc); dpx(-1, 1, oc); dpx(0, 1, ld); dpx(1, 1, ld); dpx(2, 1, oc)
                dpx(-1, 2, oc); dpx(0, 2, oc); dpx(1, 2, ld)
            } else {
                dpx(-1, -2, oc); dpx(0, -2, oc); dpx(1, -2, ld)
                dpx(-2, -1, oc); dpx(-1, -1, oc); dpx(0, -1, ld); dpx(1, -1, oc); dpx(2, -1, ld)
                dpx(-2, 0, oc); dpx(-1, 0, ld); dpx(0, 0, ld); dpx(1, 0, oc); dpx(2, 0, oc)
                dpx(-2, 1, ld); dpx(-1, 1, oc); dpx(0, 1, oc); dpx(1, 1, oc); dpx(2, 1, oc)
                dpx(-1, 2, ld); dpx(0, 2, oc); dpx(1, 2, oc)
            }
            // Stand
            dpx(-1, 3, ax); dpx(0, 3, ax); dpx(1, 3, ax)

        case .thermometer:
            let gl = Color(red: 0.88, green: 0.90, blue: 0.92) // glass
            let rd = Color(red: 0.90, green: 0.22, blue: 0.18) // mercury red
            let tp = Color(red: 0.72, green: 0.74, blue: 0.78) // top cap
            // Top cap
            dpx(0, -4, tp)
            // Glass tube
            dpx(0, -3, gl); dpx(0, -2, gl)
            // Mercury level
            dpx(0, -1, rd); dpx(0, 0, rd)
            // Bulb
            dpx(-1, 1, rd); dpx(0, 1, rd); dpx(1, 1, rd)
            dpx(0, 2, rd)
        }
    }
}

// MARK: - Particle Renderer

struct PixelParticleRenderer {
    static func draw(_ p: PixelParticle, ctx: GraphicsContext, ps: CGFloat) {
        let a = p.alpha
        guard a > 0.01 else { return }
        let cx = p.x * ps, cy = p.y * ps, sz = ps * p.scale

        func dpx(_ dx: Int, _ dy: Int, _ color: Color) {
            ctx.fill(Path(CGRect(x: cx + CGFloat(dx) * sz, y: cy + CGFloat(dy) * sz,
                                 width: sz + 0.5, height: sz + 0.5)),
                     with: .color(color.opacity(Double(a))))
        }

        switch p.type {
        case .sparkle:
            dpx(0,-1,p.color); dpx(-1,0,p.color); dpx(0,0,.white); dpx(1,0,p.color); dpx(0,1,p.color)
        case .heart:
            let pk = Color(red: 1.0, green: 0.45, blue: 0.55)
            dpx(-1,0,pk); dpx(1,0,pk)
            dpx(-2,1,pk); dpx(-1,1,pk); dpx(0,1,pk); dpx(1,1,pk); dpx(2,1,pk)
            dpx(-1,2,pk); dpx(0,2,pk); dpx(1,2,pk); dpx(0,3,pk)
        case .note:
            dpx(1,0,p.color); dpx(2,0,p.color); dpx(2,1,p.color); dpx(2,2,p.color)
            dpx(0,3,p.color); dpx(1,3,p.color); dpx(2,3,p.color); dpx(0,4,p.color)
        case .zzz:
            dpx(0,0,p.color); dpx(1,0,p.color); dpx(2,0,p.color)
            dpx(1,1,p.color)
            dpx(0,2,p.color); dpx(1,2,p.color); dpx(2,2,p.color)
        case .drop:
            dpx(0,0,p.color); dpx(-1,1,p.color); dpx(0,1,p.color); dpx(1,1,p.color); dpx(0,2,p.color)
        case .confetti:
            dpx(0,0,p.color)
        case .starBurst:
            dpx(0,-2,p.color); dpx(-1,-1,p.color); dpx(1,-1,p.color)
            dpx(-2,0,p.color); dpx(0,0,.white); dpx(2,0,p.color)
            dpx(-1,1,p.color); dpx(1,1,p.color); dpx(0,2,p.color)
        case .puff:
            dpx(0,0,p.color); dpx(1,0,p.color)
            dpx(-1,1,p.color); dpx(0,1,p.color); dpx(1,1,p.color); dpx(2,1,p.color)
        }
    }
}

// MARK: - Cat Costumes

/// Costumes overlay the cat sprite for state-specific dress-up.
/// Inspired by Kirby's copy abilities — the cat visually transforms.
enum CatCostume: Int {
    case none = 0
    case headband       // red sporty bandana (energetic/happy)
    case sleepCap       // purple nightcap with pom-pom (tired/sleepy)
    case explorerHat    // wide-brim adventure hat (adventurous)
    case crown          // golden triple-peak crown (proud)
    case detective      // deerstalker hat (curious)
    case cape           // flowing red hero cape (proud) — drawn BEHIND cat
    case labGoggles     // safety goggles on forehead (focused)
    case witchHat       // pointy hat for zen/magic
    case rainHood       // yellow raincoat hood (sad)
    case nurseHat       // white cross hat (recovering)
    case antennaHelmet  // radar dish helmet (alert)
    case scarf          // warm scarf wrapped at neck (sick)
    case bowtie         // party bowtie at chest (happy)
    case bandages       // head bandage wrap (sick alternative)
    case boxingGloves   // red boxing gloves on paws (energetic)
    case safetyVest     // orange high-vis vest on body (alert)
    case blanketShawl   // blanket wrapped around body (sick)
    case backpack       // small backpack on back (adventurous)
    case sunglasses     // dark shades at eye level (relaxed)
    case looseTie       // askew necktie (stressed)
    case flowerGarland  // pink/white flowers around neck (recovering)
    case faceMask       // white medical mask over mouth (sick)
}

struct CatCostumeRenderer {

    /// Draw costume elements that render BEHIND the cat (cape).
    static func drawBehind(_ costume: CatCostume, ctx: GraphicsContext,
                           cx: Int, cy: Int, hx: Int, hy: Int,
                           bodySquash: CGFloat, ps: CGFloat) {
        guard costume == .cape || costume == .backpack else { return }

        func cpx(_ c: Int, _ r: Int, _ col: Color) {
            guard c >= 0, c < 64, r >= 0 else { return }
            ctx.fill(Path(CGRect(x: CGFloat(c)*ps, y: CGFloat(r)*ps,
                                 width: ps+0.5, height: ps+0.5)), with: .color(col))
        }

        let bx = cx + hx, by = cy + hy
        let capeR = Color(red: 0.88, green: 0.18, blue: 0.18)
        let capeD = Color(red: 0.72, green: 0.12, blue: 0.12)
        let capeL = Color(red: 0.95, green: 0.30, blue: 0.28)

        // Cape flows from shoulders, widens downward
        let waveOff = Int(round(sin(bodySquash * 2) * 1.5))
        for dy in 0...8 {
            let w = 4 + dy + abs(waveOff)
            let row = by + 4 + dy
            let c = (dy % 2 == 0) ? capeR : capeD
            for dx in -w...w { cpx(bx + dx + waveOff, row, c) }
            // Highlight center stripe
            if dy > 1 && dy < 7 { cpx(bx + waveOff, row, capeL) }
        }
        // Scalloped bottom edge
        for dx in stride(from: -10, through: 10, by: 3) {
            cpx(bx + dx + waveOff, by + 13, capeD)
        }

        if costume == .backpack {
            let bpM = Color(red: 0.45, green: 0.55, blue: 0.30) // olive green
            let bpD = Color(red: 0.35, green: 0.42, blue: 0.22) // dark green
            let bpB = Color(red: 0.55, green: 0.38, blue: 0.22) // brown buckle
            let bpL = Color(red: 0.55, green: 0.65, blue: 0.38) // light green

            let bx2 = cx + hx, by2 = cy + hy
            // Main body (behind cat, offset to right a bit)
            for dx in 2...7 { cpx(bx2 + dx, by2 + 2, bpM) }
            for dx in 2...7 { cpx(bx2 + dx, by2 + 3, bpL) }
            for dx in 2...7 { cpx(bx2 + dx, by2 + 4, bpM) }
            for dx in 2...7 { cpx(bx2 + dx, by2 + 5, bpD) }
            for dx in 3...6 { cpx(bx2 + dx, by2 + 6, bpD) }
            // Flap
            for dx in 3...6 { cpx(bx2 + dx, by2 + 1, bpD) }
            // Buckle
            cpx(bx2 + 4, by2 + 2, bpB); cpx(bx2 + 5, by2 + 2, bpB)
            // Straps (going to shoulders)
            cpx(bx2 + 3, by2, bpD); cpx(bx2 + 3, by2 - 1, bpD)
            cpx(bx2 + 6, by2, bpD); cpx(bx2 + 6, by2 - 1, bpD)
        }
    }

    /// Draw costume elements that render ON TOP of the cat (hats, accessories).
    static func drawFront(_ costume: CatCostume, ctx: GraphicsContext,
                          cx: Int, cy: Int, hx: Int, hy: Int,
                          earPerk: CGFloat, ps: CGFloat,
                          lpY: Int = 0, rpY: Int = 0) {
        guard costume != .none && costume != .cape else { return }

        let bx = cx + hx, by = cy + hy
        let ep = Int(round(earPerk))

        func cpx(_ c: Int, _ r: Int, _ col: Color) {
            guard c >= 0, c < 64, r >= 0 else { return }
            ctx.fill(Path(CGRect(x: CGFloat(c)*ps, y: CGFloat(r)*ps,
                                 width: ps+0.5, height: ps+0.5)), with: .color(col))
        }

        switch costume {
        case .headband:
            let hbR = Color(red: 0.92, green: 0.22, blue: 0.18)
            let hbD = Color(red: 0.78, green: 0.15, blue: 0.12)
            // Band across forehead
            for dx in -7...7 { cpx(bx + dx, by - 4, hbR) }
            for dx in -6...6 { cpx(bx + dx, by - 3, hbD) }
            // Trailing knot tails (right side, flowing)
            cpx(bx + 8, by - 5, hbR); cpx(bx + 9, by - 6, hbR); cpx(bx + 10, by - 7, hbD)
            cpx(bx + 8, by - 3, hbR); cpx(bx + 9, by - 2, hbR); cpx(bx + 10, by - 1, hbD)
            // Knot center
            cpx(bx + 7, by - 4, hbD); cpx(bx + 8, by - 4, hbD)

        case .sleepCap:
            let capB = Color(red: 0.52, green: 0.45, blue: 0.72)
            let capL = Color(red: 0.68, green: 0.62, blue: 0.88)
            let pomW = Color.white
            // Cap base sits on head
            for dx in -6...4 { cpx(bx + dx, by - 5 - ep, capB) }
            for dx in -5...3 { cpx(bx + dx, by - 6 - ep, capL) }
            for dx in -4...2 { cpx(bx + dx, by - 7 - ep, capB) }
            for dx in -3...1 { cpx(bx + dx, by - 8 - ep, capL) }
            // Droops to the right
            cpx(bx + 5, by - 4 - ep, capB)
            cpx(bx + 6, by - 3 - ep, capL); cpx(bx + 7, by - 2 - ep, capB)
            cpx(bx + 8, by - 1 - ep, capL)
            // Pom-pom at tip
            cpx(bx + 8, by - 2 - ep, pomW); cpx(bx + 9, by - 1 - ep, pomW)
            cpx(bx + 9, by - ep, pomW); cpx(bx + 8, by - ep, pomW)

        case .explorerHat:
            let hatB = Color(red: 0.52, green: 0.36, blue: 0.20)
            let hatL = Color(red: 0.65, green: 0.46, blue: 0.26)
            let band = Color(red: 0.38, green: 0.26, blue: 0.14)
            // Wide brim
            for dx in -9...9 { cpx(bx + dx, by - 5 - ep, hatB) }
            for dx in -8...8 { cpx(bx + dx, by - 4 - ep, hatB) }
            // Crown
            for dx in -5...5 { cpx(bx + dx, by - 6 - ep, hatL) }
            for dx in -4...4 { cpx(bx + dx, by - 7 - ep, hatL) }
            for dx in -3...3 { cpx(bx + dx, by - 8 - ep, hatB) }
            // Band with buckle
            for dx in -5...5 { cpx(bx + dx, by - 6 - ep, band) }
            cpx(bx, by - 6 - ep, Color(red: 0.85, green: 0.75, blue: 0.25)) // gold buckle

        case .crown:
            let gd  = Color(red: 0.95, green: 0.82, blue: 0.25)
            let gdD = Color(red: 0.82, green: 0.68, blue: 0.15)
            let gem = Color(red: 0.88, green: 0.22, blue: 0.28)
            let gemB = Color(red: 0.30, green: 0.55, blue: 0.90)
            // Crown base
            for dx in -5...5 { cpx(bx + dx, by - 6 - ep, gd) }
            for dx in -5...5 { cpx(bx + dx, by - 5 - ep, gdD) }
            // Three peaks with pointed tips
            cpx(bx - 4, by - 7 - ep, gd); cpx(bx - 4, by - 8 - ep, gdD)
            cpx(bx, by - 7 - ep, gd); cpx(bx, by - 8 - ep, gdD); cpx(bx, by - 9 - ep, gd)
            cpx(bx + 4, by - 7 - ep, gd); cpx(bx + 4, by - 8 - ep, gdD)
            // Gems on base
            cpx(bx - 2, by - 6 - ep, gem)
            cpx(bx + 2, by - 6 - ep, gemB)
            cpx(bx, by - 6 - ep, gem)

        case .detective:
            let hatB = Color(red: 0.48, green: 0.40, blue: 0.28)
            let hatL = Color(red: 0.60, green: 0.50, blue: 0.36)
            // Deerstalker rounded crown
            for dx in -5...5 { cpx(bx + dx, by - 5 - ep, hatL) }
            for dx in -4...4 { cpx(bx + dx, by - 6 - ep, hatB) }
            for dx in -3...3 { cpx(bx + dx, by - 7 - ep, hatL) }
            // Front visor (extends forward/down)
            for dx in -7...(0) { cpx(bx + dx, by - 4 - ep, hatB) }
            cpx(bx - 8, by - 3 - ep, hatB)
            // Back flap
            for dx in 1...7 { cpx(bx + dx, by - 4 - ep, hatB) }
            cpx(bx + 7, by - 3 - ep, hatL); cpx(bx + 7, by - 2 - ep, hatL)

        case .labGoggles:
            let frame = Color(red: 0.45, green: 0.45, blue: 0.50)
            let lens  = Color(red: 0.75, green: 0.88, blue: 0.98)
            let glint = Color.white
            // Goggles sit ON the eyes (by+0 is roughly eye level)
            // Left lens frame
            cpx(bx - 5, by + 1, frame); cpx(bx - 4, by, frame)
            cpx(bx - 3, by, frame); cpx(bx - 2, by + 1, frame)
            cpx(bx - 4, by + 1, lens); cpx(bx - 3, by + 1, lens)
            cpx(bx - 4, by + 2, frame); cpx(bx - 3, by + 2, frame)
            cpx(bx - 5, by + 2, frame); cpx(bx - 2, by + 2, frame)
            cpx(bx - 4, by, glint) // glint
            // Bridge
            cpx(bx - 1, by + 1, frame); cpx(bx, by + 1, frame)
            // Right lens frame
            cpx(bx + 1, by + 1, frame); cpx(bx + 2, by, frame)
            cpx(bx + 3, by, frame); cpx(bx + 4, by + 1, frame)
            cpx(bx + 2, by + 1, lens); cpx(bx + 3, by + 1, lens)
            cpx(bx + 2, by + 2, frame); cpx(bx + 3, by + 2, frame)
            cpx(bx + 1, by + 2, frame); cpx(bx + 4, by + 2, frame)
            cpx(bx + 2, by, glint) // glint
            // Strap goes around head
            cpx(bx - 6, by + 1, frame); cpx(bx + 5, by + 1, frame)

        case .witchHat:
            let hatB = Color(red: 0.25, green: 0.18, blue: 0.40)
            let hatL = Color(red: 0.38, green: 0.28, blue: 0.55)
            let star = Color(red: 0.95, green: 0.85, blue: 0.30)
            // Wide brim
            for dx in -8...8 { cpx(bx + dx, by - 5 - ep, hatB) }
            // Tall cone
            for dx in -5...5 { cpx(bx + dx, by - 6 - ep, hatL) }
            for dx in -4...4 { cpx(bx + dx, by - 7 - ep, hatB) }
            for dx in -3...3 { cpx(bx + dx, by - 8 - ep, hatL) }
            for dx in -2...2 { cpx(bx + dx, by - 9 - ep, hatB) }
            for dx in -1...1 { cpx(bx + dx, by - 10 - ep, hatL) }
            cpx(bx, by - 11 - ep, hatB)
            // Tip curves slightly
            cpx(bx + 1, by - 12 - ep, hatL); cpx(bx + 2, by - 12 - ep, hatB)
            // Star on front
            cpx(bx, by - 7 - ep, star)
            cpx(bx - 1, by - 8 - ep, star); cpx(bx + 1, by - 8 - ep, star)

        case .rainHood:
            let hood = Color(red: 0.95, green: 0.85, blue: 0.25)
            let hoodD = Color(red: 0.80, green: 0.70, blue: 0.18)
            // Rounded hood over head
            for dx in -7...7 { cpx(bx + dx, by - 4 - ep, hood) }
            for dx in -8...8 { cpx(bx + dx, by - 3 - ep, hoodD) }
            for dx in -6...6 { cpx(bx + dx, by - 5 - ep, hood) }
            for dx in -5...5 { cpx(bx + dx, by - 6 - ep, hoodD) }
            for dx in -4...4 { cpx(bx + dx, by - 7 - ep, hood) }
            // Sides drape down (like a rain hood)
            for dy in (-3)...2 {
                cpx(bx - 8, by + dy - ep, hoodD)
                cpx(bx + 8, by + dy - ep, hoodD)
                cpx(bx - 9, by + dy - ep, hood)
                cpx(bx + 9, by + dy - ep, hood)
            }

        case .nurseHat:
            let white = Color.white
            let cross = Color(red: 0.90, green: 0.25, blue: 0.25)
            // White hat base
            for dx in -4...4 { cpx(bx + dx, by - 6 - ep, white) }
            for dx in -3...3 { cpx(bx + dx, by - 7 - ep, white) }
            for dx in -4...4 { cpx(bx + dx, by - 5 - ep, white) }
            // Red cross
            cpx(bx, by - 7 - ep, cross)
            cpx(bx - 1, by - 6 - ep, cross); cpx(bx, by - 6 - ep, cross); cpx(bx + 1, by - 6 - ep, cross)
            cpx(bx, by - 5 - ep, cross)

        case .antennaHelmet:
            let metal = Color(red: 0.55, green: 0.58, blue: 0.62)
            let metalD = Color(red: 0.40, green: 0.42, blue: 0.48)
            let blink = Color(red: 1.0, green: 0.30, blue: 0.25)
            let screen = Color(red: 0.40, green: 0.90, blue: 0.50)
            // Helmet dome
            for dx in -6...6 { cpx(bx + dx, by - 5 - ep, metal) }
            for dx in -5...5 { cpx(bx + dx, by - 6 - ep, metalD) }
            for dx in -4...4 { cpx(bx + dx, by - 7 - ep, metal) }
            // Antenna stalk + dish
            cpx(bx, by - 8 - ep, metalD); cpx(bx, by - 9 - ep, metalD)
            cpx(bx, by - 10 - ep, metalD)
            // Dish (satellite)
            for dx in -3...3 { cpx(bx + dx, by - 11 - ep, metal) }
            cpx(bx - 4, by - 10 - ep, metal); cpx(bx + 4, by - 10 - ep, metal)
            // Blinking light
            cpx(bx, by - 12 - ep, blink)
            // Visor screen
            cpx(bx - 3, by - 4 - ep, screen); cpx(bx - 2, by - 4 - ep, screen)
            cpx(bx + 2, by - 4 - ep, screen); cpx(bx + 3, by - 4 - ep, screen)

        case .scarf:
            // Warm scarf wrapped around neck area (below head)
            let scR = Color(red: 0.20, green: 0.55, blue: 0.70)
            let scL = Color(red: 0.30, green: 0.68, blue: 0.82)
            let scD = Color(red: 0.15, green: 0.42, blue: 0.55)
            // Main wrap around neck
            for dx in -6...6 { cpx(bx + dx, by + 3, scR) }
            for dx in -5...5 { cpx(bx + dx, by + 4, scL) }
            // Hanging tail (drapes down right side)
            cpx(bx + 6, by + 4, scD); cpx(bx + 7, by + 5, scR)
            cpx(bx + 7, by + 6, scL); cpx(bx + 8, by + 7, scR)
            cpx(bx + 8, by + 8, scD); cpx(bx + 8, by + 9, scR)
            // Stripes on scarf
            cpx(bx - 3, by + 3, scD); cpx(bx, by + 3, scD); cpx(bx + 3, by + 3, scD)

        case .bowtie:
            // Classic red-blue bowtie at NECK level (by+7, below head)
            let btR = Color(red: 0.88, green: 0.15, blue: 0.22) // red wing
            let btD = Color(red: 0.68, green: 0.10, blue: 0.15) // dark red fold
            let btL = Color(red: 0.95, green: 0.30, blue: 0.32) // light red highlight
            let btB = Color(red: 0.18, green: 0.28, blue: 0.65) // blue knot
            let nr = by + 7 // neck row
            // Left wing (fan shape)
            cpx(bx - 5, nr, btR)
            cpx(bx - 4, nr - 1, btR); cpx(bx - 4, nr, btD); cpx(bx - 4, nr + 1, btR)
            cpx(bx - 3, nr - 1, btL); cpx(bx - 3, nr, btR); cpx(bx - 3, nr + 1, btL)
            cpx(bx - 2, nr, btR)
            // Center knot (blue)
            cpx(bx - 1, nr - 1, btB); cpx(bx - 1, nr, btB); cpx(bx - 1, nr + 1, btB)
            cpx(bx, nr - 1, btB); cpx(bx, nr, btB); cpx(bx, nr + 1, btB)
            // Right wing (fan shape)
            cpx(bx + 1, nr, btR)
            cpx(bx + 2, nr - 1, btL); cpx(bx + 2, nr, btR); cpx(bx + 2, nr + 1, btL)
            cpx(bx + 3, nr - 1, btR); cpx(bx + 3, nr, btD); cpx(bx + 3, nr + 1, btR)
            cpx(bx + 4, nr, btR)

        case .bandages:
            // Head bandage wrap (sick/injured look)
            let bW = Color.white
            let bG = Color(red: 0.90, green: 0.88, blue: 0.85)
            // Bandage wraps across forehead
            for dx in -7...7 { cpx(bx + dx, by - 4 - ep, bW) }
            for dx in -6...6 { cpx(bx + dx, by - 3 - ep, bG) }
            // Cross-wrap going diagonally
            cpx(bx + 5, by - 5 - ep, bW); cpx(bx + 6, by - 6 - ep, bW)
            cpx(bx - 5, by - 5 - ep, bG); cpx(bx - 6, by - 6 - ep, bG)
            // Small bow on top
            cpx(bx + 4, by - 5 - ep, bG); cpx(bx + 3, by - 5 - ep, bW)

        case .boxingGloves:
            let glR = Color(red: 0.92, green: 0.18, blue: 0.12)
            let glD = Color(red: 0.72, green: 0.10, blue: 0.08)
            let glL = Color(red: 0.98, green: 0.35, blue: 0.28)
            let lp = min(lpY, 0)
            let rp = min(rpY, 0)
            // Left glove (3x3)
            cpx(bx - 5, by + 10 + lp, glR); cpx(bx - 4, by + 10 + lp, glR); cpx(bx - 3, by + 10 + lp, glR)
            cpx(bx - 5, by + 11 + lp, glD); cpx(bx - 4, by + 11 + lp, glL); cpx(bx - 3, by + 11 + lp, glD)
            cpx(bx - 5, by + 12 + lp, glR); cpx(bx - 4, by + 12 + lp, glR); cpx(bx - 3, by + 12 + lp, glR)
            // Right glove (3x3)
            cpx(bx + 3, by + 10 + rp, glR); cpx(bx + 4, by + 10 + rp, glR); cpx(bx + 5, by + 10 + rp, glR)
            cpx(bx + 3, by + 11 + rp, glD); cpx(bx + 4, by + 11 + rp, glL); cpx(bx + 5, by + 11 + rp, glD)
            cpx(bx + 3, by + 12 + rp, glR); cpx(bx + 4, by + 12 + rp, glR); cpx(bx + 5, by + 12 + rp, glR)

        case .safetyVest:
            let vO = Color(red: 0.95, green: 0.60, blue: 0.10) // orange
            let vY = Color(red: 0.95, green: 0.90, blue: 0.20) // reflective yellow stripe
            let vD = Color(red: 0.80, green: 0.48, blue: 0.08) // dark orange
            // Vest on body (chest area, by+4 to by+8)
            for dx in -6...6 { cpx(bx + dx, by + 4, vO) }
            for dx in -6...6 { cpx(bx + dx, by + 5, vY) } // reflective stripe
            for dx in -6...6 { cpx(bx + dx, by + 6, vO) }
            for dx in -6...6 { cpx(bx + dx, by + 7, vY) } // reflective stripe
            for dx in -5...5 { cpx(bx + dx, by + 8, vD) }
            // Arm holes
            cpx(bx - 6, by + 5, vD); cpx(bx + 6, by + 5, vD)
            cpx(bx - 6, by + 6, vD); cpx(bx + 6, by + 6, vD)

        case .blanketShawl:
            let shR = Color(red: 0.65, green: 0.40, blue: 0.55) // purple-ish
            let shL = Color(red: 0.78, green: 0.55, blue: 0.68) // lighter
            let shD = Color(red: 0.50, green: 0.30, blue: 0.42) // darker
            // Shawl wraps from shoulders down, wider at bottom
            for dx in -7...7 { cpx(bx + dx, by + 3, shR) }
            for dx in -8...8 { cpx(bx + dx, by + 4, shL) }
            for dx in -8...8 { cpx(bx + dx, by + 5, shR) }
            for dx in -9...9 { cpx(bx + dx, by + 6, shD) }
            for dx in -9...9 { cpx(bx + dx, by + 7, shL) }
            for dx in -8...8 { cpx(bx + dx, by + 8, shR) }
            for dx in -7...7 { cpx(bx + dx, by + 9, shD) }
            // Pattern stripes
            for dx in stride(from: -7, through: 7, by: 3) {
                cpx(bx + dx, by + 5, shD)
                cpx(bx + dx, by + 7, shD)
            }

        case .backpack:
            break // backpack is drawn in drawBehind

        case .sunglasses:
            let fr = Color(red: 0.20, green: 0.20, blue: 0.22) // dark frame
            let ln = Color(red: 0.12, green: 0.12, blue: 0.15) // dark lens
            let gl = Color(red: 0.35, green: 0.35, blue: 0.40) // glint
            // Left lens
            cpx(bx - 5, by, fr); cpx(bx - 4, by, ln); cpx(bx - 3, by, ln); cpx(bx - 2, by, fr)
            cpx(bx - 5, by + 1, fr); cpx(bx - 4, by + 1, ln); cpx(bx - 3, by + 1, ln); cpx(bx - 2, by + 1, fr)
            cpx(bx - 4, by - 1, gl) // glint on left lens
            // Bridge
            cpx(bx - 1, by, fr); cpx(bx, by, fr)
            // Right lens
            cpx(bx + 1, by, fr); cpx(bx + 2, by, ln); cpx(bx + 3, by, ln); cpx(bx + 4, by, fr)
            cpx(bx + 1, by + 1, fr); cpx(bx + 2, by + 1, ln); cpx(bx + 3, by + 1, ln); cpx(bx + 4, by + 1, fr)
            cpx(bx + 2, by - 1, gl) // glint on right lens
            // Temples (sides going to ears)
            cpx(bx - 6, by, fr); cpx(bx + 5, by, fr)

        case .looseTie:
            let tR = Color(red: 0.20, green: 0.35, blue: 0.65) // blue tie
            let tD = Color(red: 0.15, green: 0.25, blue: 0.50) // dark blue
            let tK = Color(red: 0.18, green: 0.30, blue: 0.55) // knot
            // Knot at neck (slightly askew, shifted right by 1)
            cpx(bx + 1, by + 3, tK); cpx(bx + 2, by + 3, tK)
            // Tie body (hanging down, slightly crooked)
            cpx(bx + 1, by + 4, tR); cpx(bx + 2, by + 4, tR)
            cpx(bx + 1, by + 5, tD); cpx(bx + 2, by + 5, tR)
            cpx(bx + 1, by + 6, tR); cpx(bx + 2, by + 6, tD)
            cpx(bx + 1, by + 7, tD); cpx(bx + 2, by + 7, tR)
            // Tie end (wider, triangular)
            cpx(bx, by + 8, tR); cpx(bx + 1, by + 8, tD); cpx(bx + 2, by + 8, tR); cpx(bx + 3, by + 8, tR)
            cpx(bx + 1, by + 9, tD); cpx(bx + 2, by + 9, tR)

        case .flowerGarland:
            let pk = Color(red: 0.95, green: 0.55, blue: 0.65) // pink petal
            let wh = Color.white // white petal
            let yl = Color(red: 0.95, green: 0.85, blue: 0.30) // yellow center
            let gn = Color(red: 0.40, green: 0.70, blue: 0.35) // green leaf
            // Flowers around neck level (by+3 to by+4)
            // Flower 1 (left)
            cpx(bx - 6, by + 3, pk); cpx(bx - 5, by + 2, pk); cpx(bx - 5, by + 3, yl); cpx(bx - 5, by + 4, pk); cpx(bx - 4, by + 3, pk)
            // Leaf
            cpx(bx - 4, by + 4, gn)
            // Flower 2 (center-left)
            cpx(bx - 2, by + 3, wh); cpx(bx - 1, by + 2, wh); cpx(bx - 1, by + 3, yl); cpx(bx - 1, by + 4, wh); cpx(bx, by + 3, wh)
            // Flower 3 (center-right)
            cpx(bx + 2, by + 3, pk); cpx(bx + 1, by + 2, pk); cpx(bx + 1, by + 3, yl); cpx(bx + 1, by + 4, pk); cpx(bx + 3, by + 3, pk)
            // Leaf
            cpx(bx + 3, by + 4, gn)
            // Flower 4 (right)
            cpx(bx + 5, by + 3, wh); cpx(bx + 4, by + 2, wh); cpx(bx + 5, by + 2, yl); cpx(bx + 5, by + 4, wh); cpx(bx + 6, by + 3, wh)
            // Connecting vine
            cpx(bx - 3, by + 4, gn); cpx(bx, by + 4, gn); cpx(bx + 4, by + 4, gn)

        case .faceMask:
            let mW = Color.white // white mask
            let mB = Color(red: 0.72, green: 0.82, blue: 0.92) // light blue edge
            let mG = Color(red: 0.88, green: 0.88, blue: 0.90) // gray fold lines
            let st = Color(red: 0.70, green: 0.70, blue: 0.75) // ear straps
            // Mask body (covers mouth area, by+2 to by+6)
            for dx in -4...4 { cpx(bx + dx, by + 2, mB) } // top blue edge
            for dx in -5...5 { cpx(bx + dx, by + 3, mW) } // main white
            for dx in -5...5 { cpx(bx + dx, by + 4, mG) } // fold line
            for dx in -5...5 { cpx(bx + dx, by + 5, mW) } // main white
            for dx in -4...4 { cpx(bx + dx, by + 6, mB) } // bottom blue edge
            // Ear straps
            cpx(bx - 6, by + 2, st); cpx(bx - 7, by + 1, st); cpx(bx - 7, by, st)
            cpx(bx + 6, by + 2, st); cpx(bx + 7, by + 1, st); cpx(bx + 7, by, st)

        default: break
        }
    }
}
