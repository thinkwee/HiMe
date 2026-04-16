import SwiftUI

/// Official GitHub mark rendered as a SwiftUI view.
/// Path data from github.com/simple-icons (viewBox 0 0 24 24).
struct GitHubIcon: View {
    var color: Color = .primary

    var body: some View {
        GeometryReader { geo in
            let side = min(geo.size.width, geo.size.height)
            let scale = side / 24.0
            let ox = (geo.size.width - side) / 2
            let oy = (geo.size.height - side) / 2
            GitHubShape(scale: scale, offsetX: ox, offsetY: oy)
                .fill(color)
        }
        .aspectRatio(1, contentMode: .fit)
    }
}

private struct GitHubShape: Shape {
    let scale: CGFloat
    let offsetX: CGFloat
    let offsetY: CGFloat

    func path(in rect: CGRect) -> Path {
        var p = Path()
        let s = scale
        let ox = offsetX
        let oy = offsetY

        func pt(_ x: CGFloat, _ y: CGFloat) -> CGPoint {
            CGPoint(x: x * s + ox, y: y * s + oy)
        }

        p.move(to: pt(12, 0.297))
        p.addCurve(to: pt(0, 12.297), control1: pt(5.37, 0.297), control2: pt(0, 5.67))
        p.addCurve(to: pt(8.205, 23.682), control1: pt(0, 17.6), control2: pt(3.438, 22.097))
        p.addCurve(to: pt(9.025, 23.105), control1: pt(8.805, 23.795), control2: pt(9.025, 23.39))
        p.addCurve(to: pt(9.01, 21.065), control1: pt(9.025, 22.82), control2: pt(9.01, 22.145))
        p.addCurve(to: pt(4.968, 19.455), control1: pt(5.672, 21.789), control2: pt(5.524, 21.065))
        p.addCurve(to: pt(3.633, 17.7), control1: pt(4.422, 18.07), control2: pt(3.633, 17.7))
        p.addCurve(to: pt(3.717, 16.971), control1: pt(2.546, 16.956), control2: pt(3.717, 16.971))
        p.addCurve(to: pt(5.555, 18.207), control1: pt(3.717, 16.971), control2: pt(4.922, 17.055))
        p.addCurve(to: pt(9.05, 19.205), control1: pt(6.625, 20.042), control2: pt(8.364, 20.093))
        p.addCurve(to: pt(9.81, 17.6), control1: pt(9.158, 18.429), control2: pt(9.467, 17.9))
        p.addCurve(to: pt(4.344, 11.675), control1: pt(7.145, 17.3), control2: pt(4.344, 17.605))
        p.addCurve(to: pt(5.579, 8.455), control1: pt(4.344, 10.365), control2: pt(4.809, 9.295))
        p.addCurve(to: pt(5.684, 5.279), control1: pt(5.444, 8.152), control2: pt(5.039, 6.832))
        p.addCurve(to: pt(8.984, 6.509), control1: pt(5.684, 5.279), control2: pt(6.689, 4.957))
        p.addCurve(to: pt(11.984, 6.104), control1: pt(9.944, 6.242), control2: pt(10.964, 5.899))
        p.addCurve(to: pt(14.984, 6.509), control1: pt(13.004, 5.899), control2: pt(14.024, 6.242))
        p.addCurve(to: pt(18.269, 5.279), control1: pt(17.264, 4.957), control2: pt(18.269, 5.279))
        p.addCurve(to: pt(18.389, 8.455), control1: pt(18.914, 6.932), control2: pt(18.629, 8.152))
        p.addCurve(to: pt(19.619, 11.675), control1: pt(19.154, 9.295), control2: pt(19.619, 10.365))
        p.addCurve(to: pt(14.144, 17.595), control1: pt(19.619, 17.605), control2: pt(16.814, 17.285))
        p.addCurve(to: pt(14.954, 19.815), control1: pt(14.564, 17.955), control2: pt(14.954, 18.791))
        p.addCurve(to: pt(14.939, 23.101), control1: pt(14.954, 21.421), control2: pt(14.939, 22.711))
        p.addCurve(to: pt(15.795, 23.677), control1: pt(14.939, 23.416), control2: pt(15.164, 23.787))
        p.addCurve(to: pt(24, 12.297), control1: pt(20.565, 22.092), control2: pt(24, 17.592))
        p.addCurve(to: pt(12, 0.297), control1: pt(24, 5.67), control2: pt(18.627, 0.297))
        p.closeSubpath()

        return p
    }
}
