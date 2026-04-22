import SwiftUI

struct DataListView: View {
    @ObservedObject var hk: HealthKitManager
    
    var body: some View {
        VStack(spacing: 0) {
            RecentSamplesSection(hk: hk)
            Spacer()
        }
        .padding(.top, 16)
        .background(Color(.systemGroupedBackground))
    }
}
