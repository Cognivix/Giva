// ModelSetupView.swift - Model selection wizard.
//
// Shown when the server's bootstrap state is "awaiting_model_selection".
// The user picks models (or accepts the recommendation), then the server
// handles downloading via its bootstrap state machine.  Progress is shown
// in BootstrapView once the server transitions to downloading.

import SwiftUI

struct ModelSetupView: View {
    @ObservedObject var viewModel: GivaViewModel
    @ObservedObject var bootstrap: BootstrapManager
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
                VStack(spacing: 8) {
                    ProgressView()
                    Text("Setting up models...")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                .padding()
                .background(.fill.tertiary, in: RoundedRectangle(cornerRadius: 10))
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
        let status = isRecommendedDownloaded(models)
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
                        if status.assistant {
                            Image(systemName: "checkmark.circle.fill")
                                .font(.caption2)
                                .foregroundStyle(.green)
                        }
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
                        if status.filter {
                            Image(systemName: "checkmark.circle.fill")
                                .font(.caption2)
                                .foregroundStyle(.green)
                        }
                    }
                    Text(rec.filter.replacingOccurrences(of: "mlx-community/", with: ""))
                        .font(.callout.bold())
                }
            }

            if status.assistant && status.filter {
                Label("Both models already downloaded", systemImage: "checkmark.circle")
                    .font(.caption)
                    .foregroundStyle(.green)
            }
        }
        .padding()
        .background(.fill.tertiary, in: RoundedRectangle(cornerRadius: 10))
    }

    private func assistantModels(_ models: AvailableModelsResponse) -> [ModelInfo] {
        models.compatibleModels
            .filter { !$0.modelId.lowercased().contains("embedding") }
            .sorted {
                if $0.isDownloaded != $1.isDownloaded { return $0.isDownloaded }
                return $0.sizeGb > $1.sizeGb
            }
    }

    private func filterModels(_ models: AvailableModelsResponse) -> [ModelInfo] {
        models.compatibleModels
            .filter { $0.sizeGb <= 10 && !$0.modelId.lowercased().contains("embedding") }
            .sorted {
                if $0.isDownloaded != $1.isDownloaded { return $0.isDownloaded }
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
        let downloaded = model.isDownloaded ? " [ready]" : ""
        return "\(model.displayName) (\(model.sizeString))\(downloaded)"
    }

    private func isRecommendedDownloaded(_ models: AvailableModelsResponse) -> (assistant: Bool, filter: Bool) {
        let aDownloaded = models.compatibleModels.first {
            $0.modelId == models.recommended.assistant
        }?.isDownloaded ?? false
        let fDownloaded = models.compatibleModels.first {
            $0.modelId == models.recommended.filter
        }?.isDownloaded ?? false
        return (aDownloaded, fDownloaded)
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
