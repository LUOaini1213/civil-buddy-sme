# Chinese / English workbench

Use **English / 中文** in the header to change the product language. The browser remembers the
choice across the home page, Agent, packing, materials logistics, CAD, engineering, construction
planning, schedule and site routes. Switching does not reload the page or submit a task.

## What follows the language choice

- Navigation, controls, specialist names, accessibility labels, validation and task status text.
- New Rust Agent replies, document-save receipts, main-agent and read-only subagent language instructions.
- New ordinary chat language instructions and offline introduction text on the home page.
- Browser speech recognition and local faster-whisper decoding (`en` or `zh`). Speech still creates
  an editable draft; it does not send a task automatically. Optional speech dependencies/model assets
  must already be installed or explicitly prepared.
- Bounded English planning and logistics commands. Displayed examples use the same validation and
  proposal/confirmation paths as their Chinese equivalents.

The language choice does **not** translate source documents, filenames, table values, quotations,
identifiers, solver numbers or units. Existing replies, raw tool records, imported reports and some
server diagnostic prose retain their original language. Ask explicitly when you want a document
translated. Language selection does not supply approval or professional sign-off; the Rust Agent's
existing exact acknowledgement remains unchanged and is explained in English beside the field.

## Run

Follow [the unified workbench guide](unified-workbench.md) to install dependencies and build the Rust
host, then start `scripts/start_unified_workbench.py`. Open `/static/agent.html` and select **English**.
No model key is needed to switch languages or inspect document structure. Configure a model for
open-ended Agent work. The public Hugging Face Static Space is a separate showcase and cannot run
this backend.

## Implementation and checks

`demo/static/i18n.js` owns `cb_locale_v1`, fixed UI catalogs and the language-change event.
Initial static text is tracked once. Controllers localize their own dynamic UI; there is no general
DOM translation observer. Source/output regions are explicitly excluded. Catalog placeholders keep
runtime values unchanged, and form/select values remain protocol identifiers.

`locale` accepts only `zh-CN` or `en` on Agent tasks and is retained with the turn. A missing field
preserves Chinese for existing clients. Speech accepts only `zh` or `en`; English transcription uses
the same fixed worker, cancellation and offline-model rules.

Focused regression commands:

```bash
node --test scripts/test_i18n.cjs scripts/test_engineering_i18n.cjs scripts/test_logistics_language.cjs scripts/test_agent_ui.cjs
python scripts/test_product_language.py
python scripts/test_logistics_language.py
python -m pytest demo/tests/test_asr.py demo/tests/test_asr_service.py
cargo test --manifest-path workbench/Cargo.toml --test product_locale --test product_runtime --test product_identity
npm run check
```

Browser acceptance uses synthetic data: change languages with an unsent draft and selected file,
inspect a Chinese-named source in English, preserve an unapplied engineering parameter, navigate
between pages and check a 390 px viewport. This does not constitute a live-model, microphone or
real-project acceptance test.

中文：点击顶栏 **English / 中文** 即可切换并记住选择。切换保留正在填写的参数、任务草稿和
文件选择；资料原文、历史输出、计算值和签认校验保持原样。需要翻译文档时，请单独提出明确任务。
