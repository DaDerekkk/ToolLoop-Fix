# ToolLoop-Fix | English speaker notes

## Slide 01

A request fails. If the order number is wrong, repeating it wastes another call. If the service timed out, that same retry can be exactly right. We asked whether a small model could tell the difference. It was not enough to count repeated calls: the model also had to finish the job. I will show you one order, the comparison we built around it, and the one task that only preference training completed exactly.

## Slide 02

Read the four calls from left to right. The first order identifier is not recognized, so the model looks it up. The lookup works, but fetching the order now times out. It does not look up the identifier again. It repeats the fetch, and the fourth call returns OK. Nothing in this figure is a hypothetical extra tool call. The animation highlights sequence, not elapsed time.

## Slide 03

For each history, we checked the preferred next action by executing it. Both models saw exactly that history and that action. Corrective supervised fine-tuning, B3, learned to produce the action. The preference model, P, also saw a rejected alternative and was trained to favor the preferred one, with a supervised anchor. The 400 decisions included recovery, legitimate retries, normal completion and correct stopping. We fixed the base model and used 50 training steps per configuration.

## Slide 04

What does a win mean here? The model gets the user request, tool descriptions and visible history, but not the hidden state used to score it. Thirty tasks are possible to complete. Six others are intentionally impossible: we score an appropriate stop separately. Ten of the completable tasks use a jobs tool that never appeared in training. No external loop guard assisted either trained model.

## Slide 05

A difference of eight sounds as though one method simply subsumed the other. The paired outcomes tell a different story. Twenty tasks were solved by both. Nine were solved only by corrective supervision, and one only by preference training. On the one jobs task that only DPO completed exactly, SFT reached the tool goal but did not produce a valid final response. There were no tasks missed by both in this particular comparison. That yields 29 versus 21 exact completions on the same thirty tasks. The task, not each model outcome, is the unit of comparison.

## Slide 06

A different training seed does not create a new test set. It does tell us whether the training result repeats. Supervision completed 29 tasks each time. Preference training completed 21, 21 and 13. Focus on the held-out jobs subset: nine out of ten for supervision in all three runs, versus five, five and zero for preference training. This is a warning about stability in this setting, not an estimate across all possible tools.

## Slide 07

If we only optimized the headline loop rate, we could select a model that gives up. Here we removed the normal-task examples from preference training. The resulting model had no flagged loops, but it completed no tasks and abandoned 22 out of 30 early. In the full preference configuration, abandonment was one out of 30. This ablation changes both composition and data size, so it is not an isolated test of one causal factor.

## Slide 08

This is one actual generation from the single-token preference baseline, B4. On the second line, the opening angle bracket is immediately followed by an equals sign rather than a valid function tag. The parser marked it as malformed; no file read was dispatched. Across logged turns, format errors were rarer with B3, but this observation does not establish why. We did not intervene on the gradient or grammar in isolation.

## Slide 09

If I had to choose a training recipe for this particular model tomorrow, I would start with verified corrections. The advantage survived all three training seeds on this held-out suite. The CPU audit replayed 846 episodes and 4,029 saved generations with zero scoring-field discrepancies. That verifies the archived scoring procedure; it does not add new model samples. I would want to repeat the comparison with real tools, human review and a different training budget before generalizing.

## Slide 10

The first three configurations are untrained baselines: the original model, a stronger prompt and an external guard. B4 and B5 test different preference recipes; the last two rows remove types of examples from P. Each cell gives its numerator and denominator. Lower is better for invalid loops and false success, higher is better for the other columns.

## Slide 11

The archived contrast is P minus B3. In the display we reverse the sign, reordering the interval endpoints, so positive means supervision performs better. For the three-seed estimate, results are first averaged over training seeds and then paired by template. The interval is a template-clustered t interval over the eligible groups, not a three-seed standard deviation. The audit is deterministic replay of saved outputs, not independent inference.
