# MV timeline-evidence self-review

## Purpose and evidence boundary

Use an existing edit, timed text, decoded audio measurements, and actual video frames on one clock to make a bounded audiovisual editing judgment. This is useful even when direct audio listening is unavailable. It does not recreate hearing: timbre, vocal delivery, subjective groove, emotional intensity, and final perceptual sync remain separate evidence channels.

## Inputs to freeze

Record the edit/version/hash, video stream frame rate and timestamps, frame count, audio stream identity/start time, source cue version and provenance, shot ranges, and any audio measurement settings. Keep the original edit and audio unchanged during diagnosis.

A third-party subtitle interval is a display interval, not automatically a verified sung-word interval or precise ASR alignment. Note whether a cue source was independently verified. Do not infer that an empty subtitle interval is silence. Likewise, an onset/flux peak is a signal-change candidate, not automatically a musical beat or desired cut.

## One clock, explicit ranges

For verified constant frame rate F, zero-based frame n starts at n/F relative to video start. Define shots as half-open [start_frame, end_frame), so duration is (end-start)/F. For variable frame rate or nonzero presentation offsets, use actual PTS rather than frame/F. Convert cues and audio samples to the same edit clock, applying known stream starts, trims, encoder delay, or edit offsets explicitly; do not blindly zero streams with different origins.

For a shot interval [s,e) and cue [a,b), overlap is max(0, min(e,b)-max(s,a)); include it only when a<e and b>s. Preserve start, end, source uncertainty and actual action phase, not just the nearest cue label. Compute signed deltas as cut_time minus cue/feature_time: negative is an early cut. The closest numeric candidate is not automatically artistically best.

Produce one compact row per shot or action event:
- edit identity, global frame range, seconds, hold/native/interpolated mode;
- lyric cue overlap and source confidence;
- measured feature time/type, signed delta and analysis settings;
- observed visual action, not only the prompt or intended action;
- intended dramatic relationship, competing interpretation, decision and remaining uncertainty.

## Inspect what actually happens

Read actual frames around each candidate event: setup, contact/peak, result, reaction, release and next shot. Sample densely at motion boundaries; inspect layers/trajectory and rendered frames when a physical defect is suspected. A contact sheet establishes spatial states, not a claim of watching continuous normal-speed video. Single frames cannot certify stable motion. Conversely, a long hold is not a defect merely because pixels stop moving: the lyric or musical section can change its meaning while the image remains still.

Compare the visual event time with cue onset, phrase interval and structural change separately. Do not equate a shot's first frame with the moment its action resolves. Check whether several adjacent shots form a causal progression or merely repeat one pose without changing the dramatic state.

## Art direction is the decision layer

Ask whether the images support, anticipate, contradict deliberately, or are unrelated to the text/music. Literal illustration of every word is not required. Record the reason for a counterpoint or delay in ordinary language. Use the work's own narrative goals to decide; neither numerical closeness nor a user's tentative reaction automatically mandates a change.

Useful choices are retain, retime, change action, change composition, or request one targeted listening check. Prefer the smallest repair that addresses an evidenced problem. Do not generate extra motion simply to make a progress report look active, and do not flatten deliberate stillness into constant motion. Preserve accepted artwork and unaffected timing while testing an alternative.

## Two cases that changed the judgment

1. **Subtitle gap was not silence.** A cue-free interval at 135.724–147.660 s had independently decoded mono RMS about -10.58 dBFS. The image progression in that gap could therefore be reviewed against continuing sound energy; calling it a musical blank or stopping because lyrics were missing would have been false. RMS alone did not reveal the music's emotional content.
2. **Cue end overstated the audible tail.** The final display cue extended to 210.311 s, whereas the last 50 ms mono window above -50 dBFS ended near 207.556 s. The threshold is a measurement convention, not proof of exact perceptual silence, and mono folding may hide opposing stereo content. Still, it invalidated treating the cue's end as a precise sung-tail endpoint without further verification.

Other observed correspondences supported keeping rather than automatically moving cuts: a contact began at 131.167 s, about 57 ms before its relevant cue; a warm-space transition at 178.167 s was about 6 ms before its cue; another section boundary at 192.417 s was about 7 ms before a measured band/energy change. These are case-specific observations, not universal timing tolerances or proof of artistic success. A 5.833 s held wait spanned two contrasting lyric ideas, so it was not automatically redundant dead time.

Reproduction used actual AAC decoded with ffmpeg to 22050 Hz mono signed 16-bit PCM, scaled by 32768, with RMS = sqrt(mean(x*x)) and dBFS = 20*log10(RMS+epsilon). Document window size, sample format and channel treatment. In this case changing the ffmpeg mono output format to float changed measured levels by about 3 dB; fixing the original PCM_s16le settings reproduced the prior values. Do not mix different downmix/format results as if they shared the same calibration. Ignore normalized feature peaks after the underlying signal has become negligible. Use a stereo/per-channel check if a decision depends on a near-silence finding. Do not call this loudness normalization, beat tracking, vocal recognition, or listening.

## Review gates and next action

Keep separate findings for container/frame/audio integrity, spatial continuity, dense-frame motion, text/feature timing and dramatic coherence, and actual perceptual listening/viewing. Passing one does not imply the others passed. If direct listening is unavailable, continue the supported gates and state exactly which perceptual conclusion remains unverified.

If a specific human check is necessary, deliver the actual version/sample, exact time range and one concrete question; explicitly identify only the work that must pause. Do not silently stop the whole project under a vague 'awaiting review' label. User feedback is useful evidence, not transfer of creative ownership or blanket final approval.

Save the case before condensing it into a skill: sources/hashes, assumptions, output mapping, inspected images, measurements, alternative interpretations, decision, failures, and limits. After a repair, verify total timing and unchanged audio/untouched footage where preservation was intended. A registry ACTIVE promotion still requires the repository's real terminal-result evidence process.
