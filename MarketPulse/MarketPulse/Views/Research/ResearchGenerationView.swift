import SwiftUI

struct ResearchGenerationView: View {
    @StateObject private var viewModel: ResearchGenerationViewModel
    @Environment(\.dismiss) private var dismiss

    init(stockId: String) {
        _viewModel = StateObject(wrappedValue: ResearchGenerationViewModel(stockId: stockId))
    }

    var body: some View {
        VStack(spacing: AppConstants.paddingLarge) {
            if viewModel.isGenerating {
                // Generating state
                VStack(spacing: AppConstants.paddingLarge) {
                    ProgressView()
                        .scaleEffect(1.5)

                    Text("Generating Research Report...")
                        .font(.title3)
                        .fontWeight(.semibold)

                    Text("This may take up to 30 seconds")
                        .font(.subheadline)
                        .foregroundColor(.secondary)

                    if let persona = viewModel.selectedPersona {
                        HStack {
                            Text(persona.emoji)
                                .font(.largeTitle)

                            Text("Analyzing with \(persona.displayName) perspective")
                                .font(.caption)
                                .foregroundColor(.secondary)
                        }
                        .padding()
                        .background(Color(.secondarySystemBackground))
                        .cornerRadius(AppConstants.cornerRadiusMedium)
                    }
                }
            } else {
                // Persona selection
                VStack(spacing: AppConstants.paddingMedium) {
                    Text("Select Investor Persona")
                        .font(.title2)
                        .fontWeight(.bold)

                    Text("Choose an investment philosophy to analyze the stock")
                        .font(.subheadline)
                        .foregroundColor(.secondary)
                        .multilineTextAlignment(.center)

                    ScrollView {
                        VStack(spacing: AppConstants.paddingMedium) {
                            ForEach(InvestorPersona.allCases, id: \.self) { persona in
                                PersonaCard(
                                    persona: persona,
                                    isSelected: viewModel.selectedPersona == persona
                                ) {
                                    viewModel.selectedPersona = persona
                                }
                            }
                        }
                    }

                    Button(action: {
                        Task {
                            await viewModel.generateReport()
                        }
                    }) {
                        Text("Generate Report")
                            .fontWeight(.semibold)
                            .foregroundColor(.white)
                            .frame(maxWidth: .infinity)
                            .padding()
                            .background(viewModel.selectedPersona != nil ? Color.green : Color.gray)
                            .cornerRadius(AppConstants.cornerRadiusMedium)
                    }
                    .disabled(viewModel.selectedPersona == nil)
                }
            }
        }
        .padding()
        .navigationTitle("Generate Report")
        .navigationBarTitleDisplayMode(.inline)
        .onChange(of: viewModel.generatedReport) { report in
            if report != nil {
                dismiss()
            }
        }
    }
}

struct PersonaCard: View {
    let persona: InvestorPersona
    let isSelected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: AppConstants.paddingMedium) {
                Text(persona.emoji)
                    .font(.largeTitle)

                VStack(alignment: .leading, spacing: 4) {
                    Text(persona.displayName)
                        .font(.headline)
                        .foregroundColor(.primary)

                    Text(persona.description)
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .multilineTextAlignment(.leading)
                }

                Spacer()

                if isSelected {
                    Image(systemName: "checkmark.circle.fill")
                        .foregroundColor(.green)
                }
            }
            .padding()
            .background(isSelected ? Color.green.opacity(0.1) : Color(.secondarySystemBackground))
            .cornerRadius(AppConstants.cornerRadiusMedium)
            .overlay(
                RoundedRectangle(cornerRadius: AppConstants.cornerRadiusMedium)
                    .stroke(isSelected ? Color.green : Color.clear, lineWidth: 2)
            )
        }
        .buttonStyle(PlainButtonStyle())
    }
}

struct ResearchGenerationView_Previews: PreviewProvider {
    static var previews: some View {
        ResearchGenerationView(stockId: "sample-id")
    }
}
