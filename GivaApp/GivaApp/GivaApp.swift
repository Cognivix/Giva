// GivaApp.swift - Menu bar app entry point.
//
// The app is a thin observer:
//   1. Runs giva-setup.py if no venv exists (one-shot, local)
//   2. Loads the launchd daemon (launchctl)
//   3. Observes the server's bootstrap state via SSE
//   4. Renders whatever the server reports
//
// Lifecycle:
//   - On launch, the full window always opens once the server is ready
//   - The menu bar popover is the "minimized" UI — an active user choice
//   - Option+Space opens a quick-drop panel for fast prompt capture
//   - The dock icon appears only when the main window is open

import SwiftUI

@main
struct GivaApp: App {
    @State private var viewModel = GivaViewModel()
    @State private var bootstrap = BootstrapManager()

    var body: some Scene {
        MenuBarExtra("Giva", image: "MenuBarIcon") {
            MenuBarContent(viewModel: viewModel, bootstrap: bootstrap)
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

        // Settings as a regular Window — the SwiftUI `Settings` scene and
        // `openSettings` environment action are broken for menu bar apps on
        // macOS 26 (Tahoe).  A plain Window + openWindow(id:) works reliably.
        Window("Giva Settings", id: "settings-window") {
            if bootstrap.isReady {
                SettingsView()
                    .environment(viewModel)
            } else {
                ContentUnavailableView(
                    "Server Not Ready",
                    systemImage: "exclamationmark.circle",
                    description: Text("Wait for the server to start, then try again.")
                )
                .frame(width: 400, height: 200)
            }
        }
        .defaultSize(width: 560, height: 460)
        .windowResizability(.contentSize)

        // Quick-drop floating panel (Option+Space)
        Window("Quick Drop", id: "quick-drop") {
            if bootstrap.isReady {
                QuickDropView()
                    .environment(viewModel)
            }
        }
        .windowStyle(.hiddenTitleBar)
        .windowResizability(.contentSize)
        .defaultPosition(.center)
    }
}

// MARK: - Menu Bar Content

/// Extracted from GivaApp.body so it has access to @Environment(\.openWindow)
/// for auto-launching the main window on bootstrap ready.
private struct MenuBarContent: View {
    var viewModel: GivaViewModel
    var bootstrap: BootstrapManager

    @Environment(\.openWindow) private var openWindow
    @State private var didLaunch = false

    var body: some View {
        Group {
            if viewModel.isMainWindowOpen {
                // Main window is open — redirect to it instead of showing popover
                MainWindowRedirectView()
            } else if bootstrap.isReady {
                // Main window is closed — show the popover (menu bar is the compact UI)
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
            registerHotkeys()
            await bootstrap.start()
        }
        .onChange(of: bootstrap.isReady) { _, ready in
            if ready && !viewModel.isSystemBusy {
                Task { await viewModel.connectToServer(from: bootstrap) }
                // Always open the full window on launch / restart
                openWindow(id: "main-window")
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.1) {
                    NSApp.activate(ignoringOtherApps: true)
                }
            }
        }
        // Bridge: popover task tap → open main window with task selected
        .onChange(of: viewModel.pendingTaskChatId) { _, taskId in
            if taskId != nil {
                openWindow(id: "main-window")
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.1) {
                    NSApp.activate(ignoringOtherApps: true)
                }
            }
        }
        // Bridge: hotkey sets quickDropRequested, we open the window here
        // (openWindow is an environment action, only usable in view body/handlers)
        .onChange(of: viewModel.quickDropRequested) { _, requested in
            if requested {
                viewModel.quickDropRequested = false
                openWindow(id: "quick-drop")
                NSApp.activate(ignoringOtherApps: true)
            }
        }
    }

    // MARK: - Hotkey Registration

    /// Register Option+Space as a hotkey to open the quick-drop panel.
    /// Local monitor works when any app window or menu bar is active.
    /// Global monitor works from any app (requires Accessibility permission).
    private func registerHotkeys() {
        NSEvent.addLocalMonitorForEvents(matching: .keyDown) { event in
            if isOptionSpace(event) {
                Task { @MainActor in viewModel.quickDropRequested = true }
                return nil  // consume the event
            }
            return event
        }
        NSEvent.addGlobalMonitorForEvents(matching: .keyDown) { event in
            if isOptionSpace(event) {
                Task { @MainActor in viewModel.quickDropRequested = true }
            }
        }
    }

    private func isOptionSpace(_ event: NSEvent) -> Bool {
        event.keyCode == 49  // spacebar
            && event.modifierFlags.intersection(.deviceIndependentFlagsMask) == .option
    }
}

// MARK: - Helper Views

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
