// MainPanelView.swift - Top-level popover content: header, tabs, content area, quick actions.

import SwiftUI

struct MainPanelView: View {
    @EnvironmentObject var viewModel: GivaViewModel

    var body: some View {
        VStack(spacing: 0) {
            // Header
            headerBar

            Divider()

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

            // Quick actions bar
            QuickActionsView()
        }
        .frame(width: 420, height: 520)
        .environmentObject(viewModel)
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
                    .fill(viewModel.serverManager.isRunning ? .green : .red)
                    .frame(width: 7, height: 7)
                Text(viewModel.serverManager.isRunning ? "Connected" : "Offline")
                    .font(.system(size: 10))
                    .foregroundColor(.secondary)
            }

            Button {
                NSApplication.shared.terminate(nil)
            } label: {
                Image(systemName: "power")
                    .font(.system(size: 11))
                    .foregroundColor(.secondary)
            }
            .buttonStyle(.plain)
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
}
