// AgentActivityPanel.swift - Sidebar showing agent queue status.
//
// Displays active, pending, and recent agent jobs.  Visible in the full
// Giva window when agents are active.  Listens to `activeJobs` on the
// ViewModel which is updated by session SSE events.

import SwiftUI

struct AgentActivityPanel: View {
    @Environment(GivaViewModel.self) private var viewModel

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            // Header
            HStack {
                Image(systemName: "bolt.circle.fill")
                    .foregroundColor(.orange)
                Text("Agent Activity")
                    .font(.headline)
                Spacer()
                Button {
                    Task { await viewModel.refreshAgentQueue() }
                } label: {
                    Image(systemName: "arrow.clockwise")
                        .font(.caption)
                }
                .buttonStyle(.plain)
                .foregroundColor(.secondary)
                .help("Refresh")
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 10)

            Divider()

            if viewModel.activeJobs.isEmpty {
                emptyState
            } else {
                jobList
            }
        }
        .frame(minWidth: 220, idealWidth: 260, maxWidth: 300)
    }

    // MARK: - Job List

    private var jobList: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 8) {
                // Running jobs
                let running = viewModel.activeJobs.filter { $0.status == "running" }
                if !running.isEmpty {
                    sectionHeader("Running")
                    ForEach(running) { job in
                        RunningJobRow(job: job)
                    }
                }

                // Pending confirmation
                let pendingConfirm = viewModel.activeJobs.filter {
                    $0.status == "pending_confirmation"
                }
                if !pendingConfirm.isEmpty {
                    sectionHeader("Needs Approval")
                    ForEach(pendingConfirm) { job in
                        PendingConfirmRow(job: job) {
                            viewModel.approveAgent(jobId: job.jobId)
                        } onDismiss: {
                            viewModel.dismissAgent(jobId: job.jobId)
                        }
                    }
                }

                // Queued
                let queued = viewModel.activeJobs.filter { $0.status == "pending" }
                if !queued.isEmpty {
                    sectionHeader("Queued")
                    ForEach(queued) { job in
                        QueuedJobRow(job: job)
                    }
                }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
        }
    }

    // MARK: - Helpers

    private func sectionHeader(_ title: String) -> some View {
        Text(title)
            .font(.caption)
            .fontWeight(.semibold)
            .foregroundColor(.secondary)
            .textCase(.uppercase)
            .padding(.top, 4)
    }

    private var emptyState: some View {
        VStack(spacing: 8) {
            Image(systemName: "bolt.slash")
                .font(.title2)
                .foregroundColor(.secondary.opacity(0.5))
            Text("No active agents")
                .font(.caption)
                .foregroundColor(.secondary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding()
    }
}

// MARK: - Job Row Views

private struct RunningJobRow: View {
    let job: AgentJobItem

    var body: some View {
        HStack(spacing: 8) {
            ProgressView()
                .controlSize(.small)
            VStack(alignment: .leading, spacing: 2) {
                Text(job.agentId.replacingOccurrences(of: "_", with: " ").capitalized)
                    .font(.caption)
                    .fontWeight(.medium)
                Text(job.query.prefix(60) + (job.query.count > 60 ? "..." : ""))
                    .font(.caption2)
                    .foregroundColor(.secondary)
                    .lineLimit(2)
            }
        }
        .padding(8)
        .background(Color.orange.opacity(0.06))
        .cornerRadius(8)
    }
}

private struct PendingConfirmRow: View {
    let job: AgentJobItem
    let onApprove: () -> Void
    let onDismiss: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(job.agentId.replacingOccurrences(of: "_", with: " ").capitalized)
                .font(.caption)
                .fontWeight(.medium)
            if let summary = job.planSummary {
                Text(summary.prefix(100) + (summary.count > 100 ? "..." : ""))
                    .font(.caption2)
                    .foregroundColor(.secondary)
                    .lineLimit(3)
            }
            HStack(spacing: 6) {
                Button("Approve", action: onApprove)
                    .buttonStyle(.borderedProminent)
                    .controlSize(.mini)
                Button("Dismiss", action: onDismiss)
                    .buttonStyle(.bordered)
                    .controlSize(.mini)
            }
        }
        .padding(8)
        .background(Color.blue.opacity(0.06))
        .cornerRadius(8)
    }
}

private struct QueuedJobRow: View {
    let job: AgentJobItem

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: "clock")
                .font(.caption)
                .foregroundColor(.secondary)
            VStack(alignment: .leading, spacing: 2) {
                Text(job.agentId.replacingOccurrences(of: "_", with: " ").capitalized)
                    .font(.caption)
                    .fontWeight(.medium)
                Text(job.statusLabel)
                    .font(.caption2)
                    .foregroundColor(.secondary)
            }
        }
        .padding(8)
        .background(Color.secondary.opacity(0.06))
        .cornerRadius(8)
    }
}
