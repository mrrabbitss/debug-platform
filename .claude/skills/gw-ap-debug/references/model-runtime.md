# Local model runtime

Do not rely on legacy model download scripts. Put existing models under `models/` or configure semicolon-separated `MODEL_ROOTS`.

`gwap models scan` performs bounded deterministic metadata classification first. Low-confidence candidates may be reviewed by the active Chat model using metadata only. `gwap models validate <id>` then performs a real loader smoke test; activation normally requires `VALIDATED`.

Supported roles include local Sentence-Transformers embedding, CrossEncoder reranker, Transformers sequence-classification reranker and local CausalLM chat. API Qwen/GLM/OpenAI-compatible profiles remain supported by the platform.
