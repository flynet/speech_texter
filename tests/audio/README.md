# Test audio

Place the acceptance/benchmark recordings here. They are not committed because they
contain personal voice data.

Required format:

- WAV, PCM signed 16-bit
- 16 kHz sample rate
- mono

Expected files:

- `ru_60s.wav` — 60 seconds of Russian speech, used by `scripts/bench.py`
- `ru_3min.wav` — 3 minutes of Russian speech, used for the long-recording acceptance test

`scripts/bench.py` can read other formats too (via PyAV), but 16 kHz mono WAV is the
reference input.

The local test pair is derived from a 61 s Russian recording: `ru_60s.wav` is a
60 s trim, and `ru_3min.wav` repeats the recording to 180 s. All audio files in
this directory are ignored by git because they contain personal voice data.
