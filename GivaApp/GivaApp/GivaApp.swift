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
    @State private var viewModel = GivaViewModel()
    @State private var bootstrap = BootstrapManager()
    @State private var didLaunch = false

    var body: some Scene {
        MenuBarExtra("Giva", systemImage: bootstrap.isReady
                     ? "brain.head.profile"
                     : "circle.dotted") {
            Group {
                if bootstrap.isReady {
                    // Server is fully ready — show main UI
                    MainPanelView()
                        .environment(viewModel)
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
                if ready && !viewModel.isSystemBusy {
                    // Only auto-connect when NOT in a system action (reset/upgrade/restart).
                    // Those flows reconnect themselves after the action completes.
                    Task { await viewModel.connectToServer(from: bootstrap) }
                }
            }
        }
        .menuBarExtraStyle(.window)

        Window("Giva", id: "main-window") {
            if bootstrap.isReady {
                GivaMainWindowView()
                    .environment(viewModel)
            } else {
                ContentUnavailableView(
                    "Server Not Ready",
                    systemImage: "exclamationmark.circle",
                    description: Text("Wait for the server to start, then try again.")
                )
            }
        }
        .defaultSize(width: 900, height: 700)

        Window("Goals & Objectives", id: "goals") {
            if let goalsVM = viewModel.goalsViewModel {
                GoalsWindowView(viewModel: goalsVM)
            } else {
                ContentUnavailableView(
                    "Server Not Ready",
                    systemImage: "exclamationmark.circle",
                    description: Text("Wait for the server to start, then try again.")
                )
            }
        }
        .defaultSize(width: 800, height: 600)
    }
}
