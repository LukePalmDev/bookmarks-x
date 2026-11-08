"""Motori di analisi. Gli import pesanti restano pigri: Gemini tira dentro il client Google."""

import importlib

__all__ = [
    "ClaudeAnalyzer",
    "GeminiAnalyzer",
    "OllamaAnalyzer",
    "PostAnalysisResult",
    "build_analysis_prompt",
]


def __getattr__(name):
    if name == "ClaudeAnalyzer":
        return importlib.import_module("src.analyzer.claude").ClaudeAnalyzer
    if name == "GeminiAnalyzer":
        return importlib.import_module("src.analyzer.gemini").GeminiAnalyzer
    if name == "OllamaAnalyzer":
        return importlib.import_module("src.analyzer.ollama").OllamaAnalyzer
    if name in {"PostAnalysisResult", "build_analysis_prompt"}:
        prompts = importlib.import_module("src.analyzer.prompts")
        return getattr(prompts, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
