import SwiftUI

struct LoadingView: View {
    var message: String = "Loading..."

    var body: some View {
        VStack(spacing: AppConstants.paddingMedium) {
            ProgressView()
                .scaleEffect(1.2)

            Text(message)
                .font(.subheadline)
                .foregroundColor(.secondary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

struct LoadingView_Previews: PreviewProvider {
    static var previews: some View {
        LoadingView()
    }
}
