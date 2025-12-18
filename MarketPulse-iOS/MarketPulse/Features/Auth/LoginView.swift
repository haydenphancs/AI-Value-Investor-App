import SwiftUI

struct LoginView: View {
  @State private var isLoading = false
  @State private var error: String?

  var body: some View {
    VStack(spacing: 16) {
      Text("Welcome")
        .font(.largeTitle).bold()
      Button(action: { signIn() }) {
        if isLoading { ProgressView() } else { Text("Sign In with Email") }
      }
      .buttonStyle(.borderedProminent)
      Button("Sign Up") {}
      if let error { Text(error).foregroundColor(.red) }
    }
    .padding()
  }

  private func signIn() {
    isLoading = true
    DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) {
      isLoading = false
    }
  }
}
