// GivaApp.swift - Menu bar app entry point with first-run bootstrap.
//
// During bootstrap the menu bar icon shows a dotted circle.
// Clicking it shows a popover with a cooking spinner + progress log.
// Once setup completes, the icon changes to brain.head.profile and the main UI appears.

import SwiftUI

@main
struct GivaApp: App {
    @StateObject private var viewModel = GivaViewModel()
    @StateObject private var bootstrap = BootstrapManager()

    var body: some Scene {
        MenuBarExtra("Giva", systemImage: bootstrap.isComplete
                     ? "brain.head.profile"
                     : "circle.dotted") {
            Group {
                if bootstrap.isComplete {
                    MainPanelView()
                        .environmentObject(viewModel)
                } else {
                    BootstrapView(bootstrap: bootstrap)
                }
            }
            .task {
                await bootstrap.start()
            }
            .onChange(of: bootstrap.isComplete) { _, complete in
                if complete {
                    Task { await viewModel.connectToServer() }
                }
            }
        }
        .menuBarExtraStyle(.window)
    }
}
