// GivaApp.swift - Menu bar app entry point.
//
// The app is a thin observer:
//   1. Runs giva-setup.py if no venv exists (one-shot, local)
//   2. Loads the launchd daemon (launchctl)
//   3. Observes the server's bootstrap state via SSE
//   4. Renders whatever the server reports
//
// The user can quit and reopen at any time without losing progress.

import SwiftUI

@main
struct GivaApp: App {
    @StateObject private var viewModel = GivaViewModel()
    @StateObject private var bootstrap = BootstrapManager()
    @State private var didLaunch = false

    var body: some Scene {
        MenuBarExtra("Giva", systemImage: bootstrap.isReady
                     ? "brain.head.profile"
                     : "circle.dotted") {
            Group {
                if bootstrap.isReady {
                    // Server is fully ready — show main UI
                    MainPanelView()
                        .environmentObject(viewModel)
                } else if let status = bootstrap.serverStatus, status.needsUserInput {
                    // Server needs model selection from user
                    ModelSetupView(viewModel: viewModel, bootstrap: bootstrap)
                } else {
                    // Setup in progress (setup script, server starting, model download, etc.)
                    BootstrapView(bootstrap: bootstrap)
                }
            }
            .task {
                guard !didLaunch else { return }
                didLaunch = true
                viewModel.bootstrapManager = bootstrap
                await bootstrap.start()
            }
            .onChange(of: bootstrap.isReady) { _, ready in
                if ready {
                    Task { await viewModel.connectToServer(from: bootstrap) }
                }
            }
        }
        .menuBarExtraStyle(.window)
    }
}
