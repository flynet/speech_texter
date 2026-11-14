# Translation support: research and roadmap

VoiceType is offline-first: audio and recognized text never leave the machine.
Network access is limited to model downloads, the optional CUDA pack, and update
checks. Every translation option below is judged against that constraint.

## Phase 1: English output (implemented)

Phase 2 is implemented as well; see the section below for the pack registry, the
targets beyond English, and the measured numbers.

Whisper has a built-in second task: `task="translate"` asks the same local model
to turn speech **into English**, whatever language was spoken. The OpenAI model
card states this explicitly: "translated into English" and "zero-shot
translation from multiple languages into English".

Phase 1 wires that task into the app with no new dependency and no new download:

- `AppConfig.translation_enabled` (off by default) and
  `AppConfig.target_language` (`en` default, `TRANSLATION_TARGETS` kept in sync
  with `translation.TARGETS` by a test);
- `TranscriptionWorker.configure(...)` takes `translate` and `target_language`,
  and `translation_plan` picks the stage: `task="translate"` for an English
  target and for a pivot, plain transcription for English speech;
- Settings has a **Translation** group: a checkbox plus a separate target-language
  selector. The existing **Language** selector stays independent — it tells
  Whisper which language you speak; the new selector controls the output;
- the tray status line shows `translate → DE` for a German target;
- the Settings test bench passes translation through, the profile line shows
  `translate to DE`, and the result meta names the target
  (`translated to German`);
- `models.TRANSLATION_MODEL_ORDER = ("medium", "small", "base")`, and
  `hardware.resolve_selection(..., translate)` skips `large-v3-turbo` while
  translation is on. The fast distilled model is tuned for transcription and
  keeps the source language, so it is not a usable translation model.

Quality caveat (from the same model card): translation is weaker supervision
than transcription, so names, numbers, and dates can be paraphrased, invented,
or dropped. Any other target language needs a separate translation model.

Measured on the development machine (RTX 4060, `int8_float16`,
`tests/audio/ru_60s.wav`, `language="ru"`, `beam_size=5`, VAD on), same chunk
10–13 s of the same Russian audio:

| Model | `task="translate"` output |
| --- | --- |
| `large-v3-turbo` | `Что тут еще нужно говорить? Я ебу.` — source language, no translation |
| `base` | `What else can I tell you? I was eating.` |
| `small` | `What else do you need to say? I will not` |
| `medium` | `What else do you need to say? I will eat.` |

Time for a 20-second chunk in translate mode on CUDA: `medium` 1.10 s,
`small` 0.66 s, `base` 3.61 s. `large-v3-turbo` was 0.57 s but returned Russian.

Short clips are the weak spot: 2–3 second chunks translated with `small` on the
CPU gave `1, 2, 3, 4, 5...` (16 characters), `There is a macaque and a bucket of
water.` (41), `without a paid digital form` (27), and one chunk returned **no
text at all**. Speak 10–20 seconds when you want a usable translation.

## Engine options

| Engine | Direction | Size on disk | Speed | License | Extra Python deps |
| --- | --- | --- | --- | --- | --- |
| Whisper `task="translate"` (in use) | any → English | 0 (same model) | same as transcription | MIT | none |
| OPUS-MT / Marian via CTranslate2 (converted by `scripts/build_mt_packs.py`) | fixed pairs, most through English | 60–84 MB per pair in `int8` | measured: 0.05–0.07 s per phrase on CPU, 4 threads, 91–105 MB RAM | Apache-2.0 (`opus-mt-en-de` is CC-BY-4.0) | `sentencepiece` only |
| NLLB-200-distilled-600M via CTranslate2 int8 (`Serkan007/CTranslate2-nllb-200-int8`) | 200 languages, any pair | 623 MB | slower than Marian; needs CPU benchmark | **CC-BY-NC-4.0** (non-commercial; the mirror has no LICENSE file) | `sentencepiece` only |
| Argos Translate (ArgosSpm packs) | fixed pairs | 25–35 MB | slow on CPU | MIT for packs | `stanza` → torch, `spacy`, `sacremoses`, `minisbd` → PyInstaller bloat |
| `transformers` + PyTorch NLLB/Marian | any pair | ~2 GB+ runtime | slow on CPU | MIT / CC-BY-NC | torch, transformers → bloat, no CTranslate2 speed |
| Online APIs (DeepL, Google, Yandex) | any pair | 0 | fast | commercial, text leaves the PC | none |

Online APIs are the fastest path to any language, but sending dictated text to a
vendor breaks the privacy promise, so they can only be an explicit opt-in, never
the default.

## Pair coverage (Marian/OPUS-MT, CTranslate2 packs)

`gaudi` publishes 255 CTranslate2 packs (all Apache-2.0, created 2024-07-17).
Direct pairs are sparse outside English, so a `ru → de` request needs a pivot:
`ru → en` then `en → de` (two inferences, quality loss — the UI must say
"through English").

- Russian set: `af-ru, ar-ru, bg-ru, ru-en, en-ru`
- Direct from English: `en-de, en-fr, en-es, en-it, en-uk, en-pl, en-tr, en-nl,
  en-sv, en-fi, en-ru, en-zh, en-ja, en-ko, en-hi, en-vi, en-id, en-th`
- Arabic hub: `ar-he, ar-el` (Hebrew and Greek come through Arabic)
- `no` (Norwegian) has no pack; `cs`, `da` are covered through their own sets

Measured sizes: `gaudi/opus-mt-ru-en-ctranslate2` `model.bin` 152,955,057 B
(~153 MB), `shared_vocabulary.json` 2.3 MB, `source.spm` 1.08 MB, `target.spm`
0.80 MB. The Helsinki source model `opus-mt-en-ru` is 307 MB (`pytorch_model.bin`)
in HF format, so CTranslate2 conversion halves it.

The installer for 0.2.1 is 528 MB with the bundled `small` Whisper model
(464 MB). Translation packs must be on-demand downloads, not shipped in the
installer.

## Phase 2: local multi-language translation (implemented)

1. `sentencepiece==0.2.3` in `requirements.txt` (cp312 win_amd64 wheel
   1,655,435 B, CVE fixed in ≥0.2.1, Apache-2.0) and `collect_all("sentencepiece")`
   in `voicetype.spec`. CTranslate2 4.8.5 is already collected; its own
   `SentencePieceProcessor` is not exposed, so tokenization needs this wheel.
2. `translation.py` keeps the pack registry, not `models.py`: a pack is a
   translation asset, not a speech model. `TARGET_LANGUAGES` holds code, display
   name, release asset, source repository, license, and measured size;
   `REQUIRED_PACK_FILES = ("model.bin", "config.json", "source.spm",
   "target.spm")`. `shared_vocabulary.json` is not needed: the two SentencePiece
   files carry the tokenizer, and Marian does not use a vocabulary file at runtime.
3. Inference pattern, as in `PackTranslator.translate`:

   ```python
   sp = spm.SentencePieceProcessor(); sp.load("source.spm")
   tokens = sp.encode(text, out_type=str)
   translator = ctranslate2.Translator(pack_dir, device="cpu", compute_type="int8")
   hyp = translator.translate_batch([tokens], beam_size=2,
                                    max_decoding_length=min(4 * len(tokens), 128)
                                   )[0].hypotheses[0]
   output = sp_target.decode(hyp)
   ```

   `config.json` must record `model_type=marian` and `add_source_eos=true`. The
   HF converter writes neither, so the decoder starts the sentence at the wrong
   place and repeats words: `build_mt_packs.py` patches the config, and
   `_ensure_marian_config` patches a downloaded pack once on load.
4. `translation_plan(source, target)` returns `whisper` (target English), `mt`
   (English speech, pack), `pivot` (any other pair, through English), or `none`
   (target equals the spoken language). The Settings hint states that a
   non-English target goes through English.
5. UI: one row per target in Settings with size, **Download**, progress, cancel,
   and **Remove**; downloads run off the UI thread and verify SHA-256 against
   `SHA256SUMS.txt` before installing. Packs live in
   `%LOCALAPPDATA%\VoiceType\translation`, never in the installer.
6. Packs are converted by `scripts/build_mt_packs.py` from the Helsinki source
   models, not downloaded pre-converted from `gaudi`: the published packs lack
   the Marian keys, and building them in CI keeps the toolchain pinned. Measured
   int8 zip sizes: 60 MB (de, fr), 64–68 MB (sv, uk, cs, nl, es, zh), 84 MB (ru).

Rejected routes: Argos (torch chain), `transformers`+torch (bloat), NLLB
CC-BY-NC (needs a project license decision).

## Licensing

The repository is MIT (`LICENSE`), so nothing non-commercial can ship: NLLB
(CC-BY-NC-4.0) stays out. OPUS-MT packs are Apache-2.0, except
`Helsinki-NLP/opus-mt-en-de`, which is CC-BY-4.0. Whisper is MIT, CTranslate2 is
MIT, sentencepiece is Apache-2.0. Each pack carries `NOTICE.txt` and `SOURCE.json`
with the source repository, license, converter, quantization, and build date, and
the Settings row shows the source model and license on hover.

## Benchmarks (measured, `scripts/bench_translation.py`)

CPU only, 4 threads, int8, 20 dictation phrases (175 words), 3 passes,
development machine (Intel Core i5-12500H):

- Cold pack load: 0.28–0.35 s.
- Latency: 0.05–0.07 s per phrase, 5.9–8.4 ms per word.
- Memory per loaded pack: 91–105 MB.
- Reference coverage: 0.62–0.88, every reference number preserved, no empty output.
- A/B on 60 s of real Russian speech (`small` on CPU): `transcribe` 6.46 s,
  `translate` 9.45 s (+46%), pack `en-de` 0.68 s. Whisper translate invented
  numbers (spoken `60 секунд` became `10 seconds`, `Разве 3-4-5 вышел` became
  `1,2,3,4,5`), and the pack carried the English numbers through, dropping one
  token (`4`) from `1,2,3,4,5`.
- Coverage is lower for the morphologically rich targets (ru 0.63, uk 0.62,
  cs 0.66) than for German, French, Dutch, and Swedish (0.82–0.88), because the
  pack picks inflected word forms the reference does not use.
- NLLB int8 (623 MB) was never benchmarked: the licensing decision excluded it.


## Phase 3 (later)

- Optional online provider as explicit opt-in, with a clear privacy notice;
- batch translation of saved transcripts; target language per profile;
- glossary support for proper nouns in local packs.
