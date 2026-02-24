// ModelSetupView.swift - Model selection wizard.
//
// Shown when the server's bootstrap state is "awaiting_model_selection".
// The user picks models (or accepts the recommendation), then the server
// handles downloading via its bootstrap state machine.  Progress is shown
// in BootstrapView once the server transitions to downloading.

import SwiftUI

struct ModelSetupView: View {
    var viewModel: GivaViewModel
    var bootstrap: BootstrapManager
    @State private var selectedAssistant: String = ""
    @State private var selectedFilter: String = ""
    @State private var showCustomize = false

    var body: some View {
        VStack(spacing: 0) {
            header
                .padding()

            Divider()

            ScrollView {
                VStack(spacing: 16) {
                    if viewModel.isSettingUpModels {
                        loadingState
                    } else if let models = viewModel.availableModels {
                        modelSelectionContent(models)
                    } else if let error = viewModel.modelSetupError {
                        errorState(error)
                    } else {
                        initialState
                    }
                }
                .padding()
            }

            Divider()

            footer
                .padding()
        }
        .frame(width: 420, height: 520)
        .task {
            if viewModel.availableModels == nil && !viewModel.isSettingUpModels {
                // Use bootstrap's apiService if viewModel doesn't have one yet
                if viewModel.apiService == nil, let api = bootstrap.apiService {
                    viewModel.apiService = api
                }
                await viewModel.fetchAvailableModels()
            }
        }
    }

    // MARK: - Header

    private var header: some View {
        VStack(spacing: 8) {
            Image(systemName: "cpu")
                .font(.system(size: 32))
                .foregroundStyle(.blue)

            Text("Choose Your AI Models")
                .font(.title2.bold())

            if let hw = viewModel.availableModels?.hardware {
                Text("\(hw.chip) \u{2022} \(hw.ramGb) GB RAM")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .padding(.horizontal, 12)
                    .padding(.vertical, 4)
                    .background(.fill.tertiary, in: Capsule())
            }
        }
    }

    // MARK: - States

    private var loadingState: some View {
        VStack(spacing: 12) {
            ProgressView()
                .controlSize(.large)
            Text("Discovering compatible models...")
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(.vertical, 40)
    }

    private var initialState: some View {
        VStack(spacing: 12) {
            ProgressView()
            Text("Connecting to server...")
                .foregroundStyle(.secondary)
        }
        .padding(.vertical, 40)
    }

    private func errorState(_ error: String) -> some View {
        VStack(spacing: 12) {
            Image(systemName: "exclamationmark.triangle")
                .font(.title)
                .foregroundStyle(.orange)
            Text(error)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
            Button("Retry") {
                Task { await viewModel.fetchAvailableModels() }
            }
        }
        .padding(.vertical, 40)
    }

    // MARK: - Model Selection Content

    private func modelSelectionContent(_ models: AvailableModelsResponse) -> some View {
        VStack(spacing: 16) {
            recommendationCard(models.recommended, models: models)

            if !viewModel.isDownloadingModels {
                Button(action: {
                    selectedAssistant = models.recommended.assistant
                    selectedFilter = models.recommended.filter
                    viewModel.selectModels(
                        assistant: models.recommended.assistant,
                        filter: models.recommended.filter
                    )
                }) {
                    Label("Use Recommended", systemImage: "sparkles")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
            }

            if viewModel.isDownloadingModels {
                downloadProgressCard
            }

            if !viewModel.isDownloadingModels {
                customizeSection(models)
            }
        }
        .onAppear {
            selectedAssistant = models.recommended.assistant
            selectedFilter = models.recommended.filter
        }
    }

    private func recommendationCard(_ rec: ModelRecommendation, models: AvailableModelsResponse) -> some View {
        let status = recommendedDownloadStatus(models)
        return VStack(alignment: .leading, spacing: 8) {
            Label("Recommended for your Mac", systemImage: "sparkles")
                .font(.headline)
                .foregroundStyle(.blue)

            Text(rec.reasoning)
                .font(.callout)
                .foregroundStyle(.secondary)

            Divider()

            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    HStack(spacing: 4) {
                        Text("Assistant")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        downloadStatusIcon(status.assistant)
                    }
                    Text(rec.assistant.replacingOccurrences(of: "mlx-community/", with: ""))
                        .font(.callout.bold())
                }
                Spacer()
                VStack(alignment: .trailing, spacing: 2) {
                    HStack(spacing: 4) {
                        Text("Filter")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        downloadStatusIcon(status.filter)
                    }
                    Text(rec.filter.replacingOccurrences(of: "mlx-community/", with: ""))
                        .font(.callout.bold())
                }
            }

            if status.assistant == "complete" && status.filter == "complete" {
                Label("Both models already downloaded", systemImage: "checkmark.circle")
                    .font(.caption)
                    .foregroundStyle(.green)
            } else if status.assistant == "partial" || status.filter == "partial" {
                Label("Interrupted download detected — will resume", systemImage: "arrow.clockwise.circle")
                    .font(.caption)
                    .foregroundStyle(.orange)
            }
        }
        .padding()
        .background(.fill.tertiary, in: RoundedRectangle(cornerRadius: 10))
    }

    @ViewBuilder
    private func downloadStatusIcon(_ status: String) -> some View {
        switch status {
        case "complete":
            Image(systemName: "checkmark.circle.fill")
                .font(.caption2)
                .foregroundStyle(.green)
        case "partial":
            Image(systemName: "exclamationmark.circle.fill")
                .font(.caption2)
                .foregroundStyle(.orange)
        default:
            EmptyView()
        }
    }

    // MARK: - Download Progress

    private var downloadProgressCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label("Downloading models", systemImage: "arrow.down.circle")
                .font(.headline)

            if bootstrap.downloadProgress.isEmpty {
                // Server hasn't started reporting yet
                HStack(spacing: 8) {
                    ProgressView()
                        .controlSize(.small)
                    Text("Preparing download...")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            } else {
                ForEach(
                    bootstrap.downloadProgress.sorted(by: { $0.key < $1.key }),
                    id: \.key
                ) { modelId, progress in
                    modelDownloadRow(modelId: modelId, progress: progress)
                }
            }
        }
        .padding()
        .background(.fill.tertiary, in: RoundedRectangle(cornerRadius: 10))
    }

    private func modelDownloadRow(modelId: String, progress: BootstrapStepProgress) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(modelId.replacingOccurrences(of: "mlx-community/", with: ""))
                .font(.caption.bold())

            if progress.percent >= 100 {
                HStack(spacing: 4) {
                    Image(systemName: "checkmark.circle.fill")
                        .foregroundStyle(.green)
                        .font(.caption2)
                    Text("Complete")
                        .font(.caption)
                        .foregroundStyle(.green)
                }
            } else if progress.percent < 0 {
                // Indeterminate — total size unknown
                HStack(spacing: 8) {
                    ProgressView()
                        .controlSize(.small)
                    if let dlMb = progress.downloadedMb {
                        Text(String(format: "%.0f MB downloaded", dlMb))
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
            } else {
                ProgressView(value: progress.percent, total: 100)
                    .tint(.blue)

                HStack {
                    Text(String(format: "%.1f%%", progress.percent))
                        .font(.caption2.monospacedDigit())
                        .foregroundStyle(.secondary)
                    Spacer()
                    if let dlMb = progress.downloadedMb,
                       let totalMb = progress.totalMb, totalMb > 0 {
                        Text(String(
                            format: "%.1f / %.1f GB",
                            dlMb / 1024, totalMb / 1024
                        ))
                        .font(.caption2.monospacedDigit())
                        .foregroundStyle(.secondary)
                    }
                }
            }
        }
    }

    /// Sort priority: complete > partial > not_downloaded
    private func downloadSortOrder(_ model: ModelInfo) -> Int {
        switch model.downloadStatus {
        case "complete": return 0
        case "partial": return 1
        default: return 2
        }
    }

    private func assistantModels(_ models: AvailableModelsResponse) -> [ModelInfo] {
        models.compatibleModels
            .filter { !$0.modelId.lowercased().contains("embedding") }
            .sorted {
                let order0 = downloadSortOrder($0), order1 = downloadSortOrder($1)
                if order0 != order1 { return order0 < order1 }
                return $0.sizeGb > $1.sizeGb
            }
    }

    private func filterModels(_ models: AvailableModelsResponse) -> [ModelInfo] {
        models.compatibleModels
            .filter { $0.sizeGb <= 10 && !$0.modelId.lowercased().contains("embedding") }
            .sorted {
                let order0 = downloadSortOrder($0), order1 = downloadSortOrder($1)
                if order0 != order1 { return order0 < order1 }
                return $0.sizeGb < $1.sizeGb
            }
    }

    private func customizeSection(_ models: AvailableModelsResponse) -> some View {
        DisclosureGroup("Customize", isExpanded: $showCustomize) {
            VStack(spacing: 12) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Assistant Model")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Picker("", selection: $selectedAssistant) {
                        ForEach(assistantModels(models)) { model in
                            Text(modelPickerLabel(model))
                                .tag(model.modelId)
                        }
                    }
                    .labelsHidden()
                }

                VStack(alignment: .leading, spacing: 4) {
                    Text("Filter Model")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Picker("", selection: $selectedFilter) {
                        ForEach(filterModels(models)) { model in
                            Text(modelPickerLabel(model))
                                .tag(model.modelId)
                        }
                    }
                    .labelsHidden()
                }

                Button("Download Custom Selection") {
                    viewModel.selectModels(
                        assistant: selectedAssistant,
                        filter: selectedFilter
                    )
                }
                .disabled(selectedAssistant.isEmpty || selectedFilter.isEmpty)
            }
            .padding(.top, 8)
        }
    }

    private func modelPickerLabel(_ model: ModelInfo) -> String {
        let suffix: String
        switch model.downloadStatus {
        case "complete": suffix = " [ready]"
        case "partial": suffix = " [incomplete]"
        default: suffix = ""
        }
        return "\(model.displayName) (\(model.sizeString))\(suffix)"
    }

    private func recommendedDownloadStatus(_ models: AvailableModelsResponse) -> (assistant: String, filter: String) {
        let aStatus = models.compatibleModels.first {
            $0.modelId == models.recommended.assistant
        }?.downloadStatus ?? "not_downloaded"
        let fStatus = models.compatibleModels.first {
            $0.modelId == models.recommended.filter
        }?.downloadStatus ?? "not_downloaded"
        return (aStatus, fStatus)
    }

    // MARK: - Footer

    private var footer: some View {
        HStack {
            Button("Skip for now") {
                viewModel.skipModelSetup()
            }
            .foregroundStyle(.secondary)

            Spacer()
        }
    }
}
