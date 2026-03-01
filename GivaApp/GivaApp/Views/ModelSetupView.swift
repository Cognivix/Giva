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
    @State private var selectedVlm: String = ""
    @State private var vlmEnabled = false
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
        .frame(width: 420, height: 580)
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
            if viewModel.isDownloadingModels {
                // Show selected models with live progress
                downloadingModelsCard
            } else {
                recommendationCard(models.recommended, models: models)

                Button(action: {
                    selectedAssistant = models.recommended.assistant
                    selectedFilter = models.recommended.filter
                    let vlm = models.recommended.vlm?.vlmModel ?? ""
                    selectedVlm = vlm
                    viewModel.selectModels(
                        assistant: models.recommended.assistant,
                        filter: models.recommended.filter,
                        vlm: vlm
                    )
                }) {
                    Label("Use Recommended", systemImage: "sparkles")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)

                vlmSection(models)
                customizeSection(models)
            }
        }
        .onAppear {
            selectedAssistant = models.recommended.assistant
            selectedFilter = models.recommended.filter
            if let vlmRec = models.recommended.vlm {
                selectedVlm = vlmRec.vlmModel
                vlmEnabled = !vlmRec.vlmModel.isEmpty
            }
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

            if let vlmRec = rec.vlm, !vlmRec.vlmModel.isEmpty {
                VStack(alignment: .leading, spacing: 2) {
                    HStack(spacing: 4) {
                        Text("Vision (VLM)")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        downloadStatusIcon(status.vlm)
                    }
                    Text(vlmRec.vlmModel.replacingOccurrences(of: "mlx-community/", with: ""))
                        .font(.callout.bold())
                }
            }

            if status.assistant == "complete" && status.filter == "complete"
                && (status.vlm == "complete" || rec.vlm == nil || (rec.vlm?.vlmModel.isEmpty ?? true)) {
                Label("All models already downloaded", systemImage: "checkmark.circle")
                    .font(.caption)
                    .foregroundStyle(.green)
            } else if status.assistant == "partial" || status.filter == "partial" || status.vlm == "partial" {
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

    // MARK: - VLM Section

    private func vlmSection(_ models: AvailableModelsResponse) -> some View {
        let hasVlmModels = !(models.vlmModels ?? []).isEmpty
        return Group {
            if hasVlmModels {
                VStack(alignment: .leading, spacing: 8) {
                    Toggle(isOn: $vlmEnabled) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text("Browser Automation (VLM)")
                                .font(.callout.bold())
                            Text("Enable visual AI for web tasks via Chrome extension")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                    .toggleStyle(.switch)

                    if vlmEnabled {
                        Picker("VLM Model", selection: $selectedVlm) {
                            Text("None").tag("")
                            ForEach(vlmModels(models)) { model in
                                Text(modelPickerLabel(model))
                                    .tag(model.modelId)
                            }
                        }
                        .labelsHidden()
                    }
                }
                .padding()
                .background(.fill.tertiary, in: RoundedRectangle(cornerRadius: 10))
            }
        }
    }

    private func vlmModels(_ models: AvailableModelsResponse) -> [ModelInfo] {
        (models.vlmModels ?? [])
            .sorted {
                let order0 = downloadSortOrder($0), order1 = downloadSortOrder($1)
                if order0 != order1 { return order0 < order1 }
                return $0.sizeGb > $1.sizeGb
            }
    }

    // MARK: - Download Progress

    /// Card shown during download — replaces the recommendation card.
    /// Shows the actual selected models with live progress from the server.
    private var downloadingModelsCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            Label("Downloading selected models", systemImage: "arrow.down.circle")
                .font(.headline)

            // Show selected models with their roles
            selectedModelRow(role: "Assistant", modelId: selectedAssistant)
            selectedModelRow(role: "Filter", modelId: selectedFilter)
            if !selectedVlm.isEmpty {
                selectedModelRow(role: "Vision (VLM)", modelId: selectedVlm)
            }
        }
        .padding()
        .background(.fill.tertiary, in: RoundedRectangle(cornerRadius: 10))
    }

    /// A single model row showing name, role, and live download progress.
    private func selectedModelRow(role: String, modelId: String) -> some View {
        let progress = bootstrap.downloadProgress[modelId]
        let shortName = modelId.replacingOccurrences(of: "mlx-community/", with: "")

        return VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(role)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Spacer()
                if let p = progress {
                    modelStatusBadge(p)
                }
            }

            Text(shortName)
                .font(.callout.bold())

            if let p = progress {
                modelProgressIndicator(p)
            } else {
                // Server hasn't reported this model yet
                HStack(spacing: 6) {
                    ProgressView()
                        .controlSize(.small)
                    Text("Waiting...")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
        }
        .padding(.vertical, 4)
    }

    @ViewBuilder
    private func modelStatusBadge(_ progress: BootstrapStepProgress) -> some View {
        if progress.percent >= 100 {
            Label("Done", systemImage: "checkmark.circle.fill")
                .font(.caption2)
                .foregroundStyle(.green)
        } else if progress.status == "queued" {
            Text("Queued")
                .font(.caption2)
                .foregroundStyle(.secondary)
        } else if progress.status == "preparing" || progress.status == "querying_size" {
            Text(progress.displayStatus)
                .font(.caption2)
                .foregroundStyle(.orange)
        } else if progress.percent >= 0 {
            Text(String(format: "%.1f%%", progress.percent))
                .font(.caption2.monospacedDigit())
                .foregroundStyle(.blue)
        }
    }

    @ViewBuilder
    private func modelProgressIndicator(_ progress: BootstrapStepProgress) -> some View {
        if progress.percent >= 100 {
            // Complete — no progress bar needed
            EmptyView()
        } else if progress.percent >= 0 {
            // Determinate progress
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
        } else {
            // Indeterminate — preparing or size unknown
            HStack(spacing: 6) {
                ProgressView()
                    .controlSize(.small)
                Text(progress.displayStatus)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Spacer()
                if let dlMb = progress.downloadedMb, dlMb > 0 {
                    Text(String(format: "%.0f MB", dlMb))
                        .font(.caption2.monospacedDigit())
                        .foregroundStyle(.secondary)
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
                        filter: selectedFilter,
                        vlm: vlmEnabled ? selectedVlm : ""
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

    private func recommendedDownloadStatus(
        _ models: AvailableModelsResponse
    ) -> (assistant: String, filter: String, vlm: String) {
        let aStatus = models.compatibleModels.first {
            $0.modelId == models.recommended.assistant
        }?.downloadStatus ?? "not_downloaded"
        let fStatus = models.compatibleModels.first {
            $0.modelId == models.recommended.filter
        }?.downloadStatus ?? "not_downloaded"
        let vStatus: String
        if let vlmId = models.recommended.vlm?.vlmModel, !vlmId.isEmpty {
            vStatus = (models.vlmModels ?? []).first {
                $0.modelId == vlmId
            }?.downloadStatus ?? "not_downloaded"
        } else {
            vStatus = "not_downloaded"
        }
        return (aStatus, fStatus, vStatus)
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
