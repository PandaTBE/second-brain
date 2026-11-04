import SwiftUI
import SecondBrainCore

struct SyncStatusBarLabel: View {
    @ObservedObject var memory: MemoryViewModel
    @ObservedObject var service: BackendService
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        let indicator = MemorySyncIndicator.resolve(
            serviceRunning: service.isRunning, status: memory.status, statusError: memory.statusError
        )
        let colored = indicator.isReady || indicator == .waiting || indicator == .unavailable
        let image = MemorySyncSymbol.image(for: indicator, darkAppearance: colorScheme == .dark)
        Image(nsImage: image)
            .renderingMode(colored ? .original : .template)
            .frame(width: 22, height: 18)
            .accessibilityLabel("Second Brain — \(indicator.title)")
            .help(indicator.title)
    }
}
