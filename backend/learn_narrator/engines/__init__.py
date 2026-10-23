"""The narration engines. Each module keeps its heavy imports (torch, the TTS package) INSIDE its
`render` function: the parent imports these modules only for their constants and pure helpers."""

ENGINES = ("chatterbox", "kokoro")
