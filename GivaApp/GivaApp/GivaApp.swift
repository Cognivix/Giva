// GivaApp.swift - Menu bar app entry point.
//
// The app is a thin observer:
//   1. Runs giva-setup.py if no venv exists (one-shot, local)
//   2. Loads the launchd daemon (launchctl)
//   3. Observes the server's bootstrap state via SSE
//   4. Renders whatever the server reports
//
// Menu bar / main window interaction:
//   - When the main window is closed, clicking the menu bar shows the popover
//   - When the main window is open, clicking the menu bar brings it to front
//   - The dock icon appears only when the main window is open

import SwiftUI

@main
struct GivaApp: App {
    @State private var viewModel = GivaViewModel()
    @State private var bootstrap = BootstrapManager()
    @State private var didLaunch = false

    var body: some Scene {
        MenuBarExtra("Giva", image: "MenuBarIcon") {
            Group {
                if viewModel.isMainWindowOpen {
                    // Main window is open — redirect to it instead of showing popover
                    MainWindowRedirectView()
                } else if viewModel.lastUsedFullWindow && bootstrap.isReady {
                    // User prefers full window — launch it directly
                    FullWindowLauncherView()
                        .environment(viewModel)
                } else if bootstrap.isReady {
                    MainPanelView()
                        .environment(viewModel)
                } else if let status = bootstrap.serverStatus, status.needsUserInput {
                    ModelSetupView(viewModel: viewModel, bootstrap: bootstrap)
                } else {
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
        .defaultSize(width: 1000, height: 700)
    }
}

/// Tiny view shown inside the menu bar popover when the main window is already open.
/// Immediately activates the app and brings the main window to front, then dismisses.
private struct MainWindowRedirectView: View {
    var body: some View {
        Color.clear
            .frame(width: 1, height: 1)
            .onAppear {
                NSApp.activate(ignoringOtherApps: true)
                // Find and focus the main Giva window
                for window in NSApp.windows where window.title == "Giva" && window.isVisible {
                    window.makeKeyAndOrderFront(nil)
                    break
                }
            }
    }
}

/// Tiny view that opens the full window immediately when the user prefers full mode.
/// Shown inside the menu bar popover when `lastUsedFullWindow` is true.
private struct FullWindowLauncherView: View {
    @Environment(GivaViewModel.self) private var viewModel
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        Color.clear
            .frame(width: 1, height: 1)
            .onAppear {
                openWindow(id: "main-window")
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.1) {
                    NSApp.activate(ignoringOtherApps: true)
                }
            }
    }
}
