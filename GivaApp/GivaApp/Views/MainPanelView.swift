// MainPanelView.swift - Top-level popover content: header, tabs, content area, quick actions.
//
// A phase-aware status banner shows between the header and content, driven
// entirely by `viewModel.serverPhase`.  The banner appears during syncing
// (with live progress), onboarding, and system actions.
//
// System actions (Restart, Upgrade, Reset, CLI) live in a gear menu in the
// header — following Apple HIG's progressive disclosure principle.

import SwiftUI

struct MainPanelView: View {
    @Environment(GivaViewModel.self) private var viewModel
    @Environment(\.openWindow) private var openWindow

    // Inline confirmation state (no system dialogs — they break in menu bar apps)
    @State private var pendingAction: SystemAction? = nil

    enum SystemAction: Equatable {
        case restart
        case upgrade
        case reset
    }

    var body: some View {
        @Bindable var viewModel = viewModel

        VStack(spacing: 0) {
            // Header
            headerBar

            // Inline confirmation banner (right below the gear menu that triggers it)
            if let action = pendingAction {
                confirmationBanner(for: action)
            }

            Divider()

            // Phase-aware status banner
            if showPhaseBanner {
                phaseBanner
                Divider()
            }

            // Tab picker
            Picker("", selection: $viewModel.currentTab) {
                ForEach(AppTab.allCases, id: \.self) { tab in
                    Text(tab.rawValue).tag(tab)
                }
            }
            .pickerStyle(.segmented)
            .padding(.horizontal, 12)
            .padding(.vertical, 8)

            Divider()

            // Content area
            Group {
                switch viewModel.currentTab {
                case .chat:
                    ChatView()
                case .tasks:
                    TaskListView()
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)

            // Error banner
            if let errorMessage = viewModel.errorMessage {
                errorBanner(errorMessage)
            }

            Divider()

            // Quick actions bar (Sync, Goals, Review)
            QuickActionsView()
        }
        .frame(width: 420, height: 520)
        .environment(viewModel)
    }

    // MARK: - Connection State

    private var connectionDotColor: Color {
        switch viewModel.serverManager.connectionState {
        case .connected: return .green
        case .connecting: return .yellow
        case .offline: return .red
        }
    }

    // MARK: - Phase Banner

    private var showPhaseBanner: Bool {
        viewModel.isSystemBusy
        || viewModel.serverPhase == .syncing
        || viewModel.isSyncingManual
        || viewModel.serverPhase == .onboarding
        || viewModel.serverPhase == .ready
    }

    @ViewBuilder
    private var phaseBanner: some View {
        if viewModel.isSystemBusy {
            systemActionBanner
        } else if viewModel.serverPhase == .syncing || viewModel.isSyncingManual {
            syncBanner
        } else if viewModel.serverPhase == .onboarding {
            onboardingBanner
        } else if viewModel.serverPhase == .ready {
            readyBanner
        }
    }

    private var syncBanner: some View {
        HStack(spacing: 8) {
            ProgressView()
                .controlSize(.small)

            if let progress = viewModel.syncProgress {
                Text(progress.displayText)
                    .font(.system(size: 11))
                    .foregroundColor(.primary)
            } else {
                Text("Syncing your emails and calendar...")
                    .font(.system(size: 11))
                    .foregroundColor(.primary)
            }

            Spacer()
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 6)
        .background(Color.accentColor.opacity(0.08))
    }

    private var onboardingBanner: some View {
        HStack(spacing: 8) {
            Image(systemName: "person.fill.questionmark")
                .font(.system(size: 12))
                .foregroundColor(.purple)

            Text("Getting to know you — answer a few questions below")
                .font(.system(size: 11))
                .foregroundColor(.primary)

            Spacer()
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 6)
        .background(Color.purple.opacity(0.08))
    }

    private var readyBanner: some View {
        HStack(spacing: 8) {
            ProgressView()
                .controlSize(.small)

            Text("Preparing first sync...")
                .font(.system(size: 11))
                .foregroundColor(.primary)

            Spacer()
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 6)
        .background(Color.accentColor.opacity(0.08))
    }

    private var systemActionBanner: some View {
        HStack(spacing: 8) {
            ProgressView()
                .controlSize(.small)

            if viewModel.isRestarting {
                Text("Restarting server...")
                    .font(.system(size: 11))
            } else if viewModel.isUpgrading {
                Text("Upgrading...")
                    .font(.system(size: 11))
            } else if viewModel.isResetting {
                Text("Resetting all data...")
                    .font(.system(size: 11))
            }

            Spacer()
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 6)
        .background(Color.orange.opacity(0.08))
    }

    // MARK: - Header

    private var headerBar: some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text("Giva")
                    .font(.system(size: 14, weight: .semibold))

                if let status = viewModel.status {
                    Text("\(status.emails) emails  \(status.events) events  \(status.pendingTasks) tasks")
                        .font(.system(size: 10))
                        .foregroundColor(.secondary)
                }
            }

            Spacer()

            // Server status indicator
            HStack(spacing: 4) {
                Circle()
                    .fill(connectionDotColor)
                    .frame(width: 7, height: 7)
                Text(viewModel.serverManager.connectionState.rawValue)
                    .font(.system(size: 10))
                    .foregroundColor(.secondary)

                if viewModel.serverManager.connectionState == .offline {
                    Button {
                        Task { await viewModel.reconnect() }
                    } label: {
                        Image(systemName: "arrow.clockwise")
                            .font(.system(size: 9))
                    }
                    .buttonStyle(.plain)
                    .foregroundColor(.secondary)
                    .help("Retry connection")
                }
            }

            // Expand to full window
            Button {
                viewModel.lastUsedFullWindow = true
                openWindow(id: "main-window")
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.1) {
                    NSApp.activate(ignoringOtherApps: true)
                }
            } label: {
                Image(systemName: "arrow.up.backward.and.arrow.down.forward")
                    .font(.system(size: 11))
                    .foregroundColor(.secondary)
            }
            .buttonStyle(.plain)
            .help("Expand to full window")
            .padding(.leading, 6)

            // Gear menu — system actions + settings
            Menu {
                Button {
                    NSApp.sendAction(Selector(("showSettingsWindow:")), to: nil, from: nil)
                } label: {
                    Label("Settings...", systemImage: "slider.horizontal.3")
                }

                Button {
                    viewModel.selectedSettingsTab = .profile
                    NSApp.sendAction(Selector(("showSettingsWindow:")), to: nil, from: nil)
                } label: {
                    Label("Profile...", systemImage: "person.circle")
                }

                Button {
                    viewModel.openCLI()
                } label: {
                    Label("Open CLI", systemImage: "terminal")
                }

                Divider()

                Button {
                    pendingAction = .restart
                } label: {
                    Label("Restart Server", systemImage: "arrow.clockwise")
                }
                .disabled(viewModel.isSystemBusy)

                Button {
                    pendingAction = .upgrade
                } label: {
                    Label("Upgrade Code", systemImage: "arrow.up.circle")
                }
                .disabled(viewModel.isSystemBusy)

                Divider()

                Button(role: .destructive) {
                    pendingAction = .reset
                } label: {
                    Label("Reset All Data...", systemImage: "trash")
                }
                .disabled(viewModel.isSystemBusy || viewModel.isStreaming)

                Divider()

                Button {
                    NSApplication.shared.terminate(nil)
                } label: {
                    Label("Quit Giva", systemImage: "power")
                }
            } label: {
                Image(systemName: "gearshape")
                    .font(.system(size: 13))
                    .foregroundColor(.secondary)
            }
            .menuStyle(.borderlessButton)
            .fixedSize()
            .padding(.leading, 6)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
    }

    // MARK: - Error Banner

    private func errorBanner(_ message: String) -> some View {
        HStack(spacing: 6) {
            Image(systemName: "exclamationmark.triangle.fill")
                .font(.system(size: 10))
                .foregroundColor(.yellow)

            Text(message)
                .font(.system(size: 10))
                .foregroundColor(.primary)
                .lineLimit(2)

            Spacer()

            Button(action: { viewModel.errorMessage = nil }) {
                Image(systemName: "xmark")
                    .font(.system(size: 8, weight: .bold))
                    .foregroundColor(.secondary)
            }
            .buttonStyle(.plain)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 6)
        .background(Color.yellow.opacity(0.1))
    }

    // MARK: - Inline Confirmation Banner
    // System dialogs (.confirmationDialog, .alert) do not work reliably inside
    // MenuBarExtra(.window) popovers. Use inline banners instead.

    private func confirmationBanner(for action: SystemAction) -> some View {
        let isDestructive = action == .reset
        let title: String
        let message: String
        let confirmLabel: String

        switch action {
        case .restart:
            title = "Restart Server"
            message = "Active requests will be interrupted. No data is lost."
            confirmLabel = "Restart"
        case .upgrade:
            title = "Upgrade Code"
            message = "Re-installs from source and restarts the server. Data is preserved."
            confirmLabel = "Upgrade"
        case .reset:
            title = "Reset All Data"
            message = "Deletes emails, events, tasks, goals, profile, and settings. Models are kept."
            confirmLabel = "Erase Everything"
        }

        return VStack(spacing: 6) {
            HStack(spacing: 6) {
                Image(systemName: isDestructive
                      ? "exclamationmark.triangle.fill" : "questionmark.circle.fill")
                    .font(.system(size: 11))
                    .foregroundColor(isDestructive ? .red : .orange)

                Text(title)
                    .font(.system(size: 11, weight: .semibold))

                Spacer()

                Button(action: { withAnimation(.easeOut(duration: 0.15)) { pendingAction = nil } }) {
                    Image(systemName: "xmark")
                        .font(.system(size: 8, weight: .bold))
                        .foregroundColor(.secondary)
                }
                .buttonStyle(.plain)
            }

            Text(message)
                .font(.system(size: 10))
                .foregroundColor(.secondary)
                .frame(maxWidth: .infinity, alignment: .leading)

            HStack(spacing: 8) {
                Spacer()

                Button("Cancel") {
                    withAnimation(.easeOut(duration: 0.15)) { pendingAction = nil }
                }
                .buttonStyle(.plain)
                .font(.system(size: 11))
                .foregroundColor(.secondary)

                Button(confirmLabel) {
                    let confirmedAction = action
                    withAnimation(.easeOut(duration: 0.15)) { pendingAction = nil }
                    Task {
                        switch confirmedAction {
                        case .restart: await viewModel.triggerRestart()
                        case .upgrade: await viewModel.triggerUpgrade()
                        case .reset: await viewModel.triggerReset()
                        }
                    }
                }
                .buttonStyle(.borderedProminent)
                .tint(isDestructive ? .red : .accentColor)
                .controlSize(.small)
                .font(.system(size: 11))
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(isDestructive ? Color.red.opacity(0.06) : Color.orange.opacity(0.06))
        .transition(.move(edge: .top).combined(with: .opacity))
    }
}
