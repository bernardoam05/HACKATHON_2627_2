// Owner: Team B. Web Speech API push-to-talk (pt-PT) + speechSynthesis.
export function useVoice() {
  // TODO(Team B): SpeechRecognition with lang="pt-PT"; speak(text) via speechSynthesis.
  return {
    listening: false,
    start: (_onResult: (text: string) => void) => { void _onResult; },
    stop: () => {},
    speak: (_text: string) => { void _text; },
  };
}
