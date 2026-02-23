// GivaMainWindowView.swift - Full-app window with agent activity panel.
//
// Provides everything the menu bar popover has, but bigger, with tabs for
// Chat, Tasks, and Goals.  When agents are active, a trailing sidebar shows
// the AgentActivityPanel with running/queued/pending jobs.
//
// Opened via the "Window" quick action in the menu bar, or automatically
// when a complex agent confirmation arrives.

import SwiftUI

struct GivaMainWindowView: View {
    @EnvironmentObject var viewModel: GivaViewModel
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        NavigationSplitView {
            // Main content area
            mainContent
        } detail: {
            // Agent activity sidebar (only when agents are active)
            if !viewModel.activeJobs.isEmpty {
                AgentActivityPanel()
                    .environmentObject(viewModel)
            }
        }
        .navigationTitle("Giva")
        .toolbar {
            ToolbarItem(placement: .automatic) {
                HStack(spacing: 12) {
                    // Sync button
                    Button {
                        Task { await viewModel.triggerSync() }
                    } label: {
                        Label("Sync", systemImage: "arrow.triangle.2.circlepath")
                    }
                    .disabled(!viewModel.areActionsEnabled || viewModel.isSyncing)

                    // Goals window
                    Button {
                        openWindow(id: "goals")
                    } label: {
                        Label("Goals", systemImage: "target")
                    }
                    .disabled(!viewModel.areActionsEnabled)

                    // Daily Review
                    if viewModel.isDailyReviewDue {
                        Button {
                            openWindow(id: "goals")
                        } label: {
                            Label("Review", systemImage: "text.badge.checkmark")
                        }
                    }
                }
            }
        }
    }

    // MARK: - Main Content

    private var mainContent: some View {
        VStack(spacing: 0) {
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
            .padding(.horizontal, 16)
            .padding(.vertical, 10)

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
        }
        .environmentObject(viewModel)
    }

    // MARK: - Phase Banner

    private var showPhaseBanner: Bool {
        viewModel.serverPhase == "syncing"
            || viewModel.isSyncingManual
            || viewModel.isLoadingModel
    }

    private var phaseBanner: some View {
        HStack(spacing: 8) {
            ProgressView()
                .controlSize(.small)

            if viewModel.isLoadingModel {
                Text("Loading AI model...")
                    .font(.callout)
                    .foregroundColor(.secondary)
            } else if let progress = viewModel.syncProgress {
                Text(progress.displayText)
                    .font(.callout)
                    .foregroundColor(.secondary)
            } else {
                Text("Working...")
                    .font(.callout)
                    .foregroundColor(.secondary)
            }

            Spacer()
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 8)
        .background(Color.secondary.opacity(0.05))
    }
}
