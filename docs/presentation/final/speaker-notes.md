# ToolLoop-Fix | English speaker notes

## Slide 01

Imagine asking an assistant to look up an order. It does not know the current answer. It sends the order identifier to a tool, which is just a program that can look it up. If the identifier is wrong, the assistant needs a different identifier. If the service times out, it may need to send exactly the same request again. We tested two ways to teach that judgment to a small model.

## Slide 02

Here is one actual order lookup. The model first supplied an invalid identifier. Searching for the right identifier worked, but its next order lookup timed out. By then, searching again would have repeated work that was already done. Sending the same lookup again was appropriate: the fourth call returned OK. A loop detector that bans every repeated call would punish the action that retrieves this order.

## Slide 03

This question builds on three lines of work. ReAct lets a model act, read the result and revise its plan. AntiDoom targets the onset of repetitive text with single-token preference pairs. That is not quite the same problem: repeating a tool call can be right. Fission-GRPO studies tool recovery directly, branching failed trajectories and resampling with diagnostic feedback. We ask a narrower, offline question. If the correct action is already known, does adding a rejected action help more than simply teaching the correct one? We did not run these published systems as baselines.

## Slide 04

We made both training recipes see the same failure history. The action selected as correct was verified by executing it. Corrective supervised fine-tuning simply imitates that action. Action-level direct preference optimization uses the same action, but also learns to rank it above a rejected alternative, while keeping a supervised term. The examples include not just repairs and retries, but ordinary completion and cases where stopping is right. The training budget and the base model were held fixed.

## Slide 05

The evaluator needs a definition of success before we can compare methods. Thirty tasks were possible to finish: ten orders, ten files and ten background processing jobs. The jobs tools were never used in training. A finish only counts when the right tool result has been obtained and the model returns a valid final answer supported by it. Six other tasks are intentionally impossible; stopping is the appropriate outcome there. The model cannot read the private state used by the evaluator.

## Slide 06

Here are the primary results, before averaging over training seeds. At seed 101, supervision finished twenty-nine out of thirty tasks; preference training finished twenty-one. Match the outcomes by task: twenty were finished by both, nine only by supervision, one only by preference training, and none by neither. The net difference is eight. The same tasks were given to both models, so the paired task, not each model output in isolation, is what we compare.

## Slide 07

The one exception is worth opening. In a background job task, the two models both corrected an invalid identifier and retrieved the job result. The supervised model kept going instead of finishing correctly. It exhausted the twelve-turn budget without a valid final answer. The preference model returned a valid structured answer on turn four. Because the evaluator required the final answer, only preference training earned an exact completion. This also shows why our advantage is a net result, not per-task dominance.

## Slide 08

A seed changes the random choices during training. We trained fresh versions three times, then gave them the same test tasks. SFT finished 29 each time. DPO finished 21, 21 and 13. The contrast is particularly clear on the ten background jobs: supervision gets nine every time; the third preference run gets none. These are three runs on one shared test set, not three new test sets.

## Slide 09

Suppose we ignored completions and just chose whatever looped least. To see what can go wrong, look at the preference model after we remove normal-task examples from training. It records no invalid loops, but also completes none of the thirty eligible tasks. It gives up early twenty-two times. With normal examples present, it gives up only once. We removed examples, so the data size changed along with the composition; it is not a controlled attribution to only one factor.

## Slide 10

Our answer has two parts. To act after a failure, use the feedback: change an invalid identifier, but allow a retry after a transient timeout. To train this 0.8B model to make those decisions, start by imitating verified corrections. Adding a rejected alternative did not improve exact task completion here. Supervision finished 29 out of 30 across all three runs, and the advantage carried to the unseen jobs domain. Retain normal examples and score completed tasks, not just fewer loops. What I would test next is whether the choice survives real services, human review and a different training budget. The archived scoring was replayed on CPU, but that is not an independent evaluation in a real service.

## Slide 11

The first three configurations are untrained baselines: the original model, a stronger prompt and an external guard. B4 and B5 test different preference recipes; the last two rows remove types of examples from P. Each cell gives its numerator and denominator. Lower is better for invalid loops and false success, higher is better for the other columns.

## Slide 12

The archived contrast is P minus B3. In the display we reverse the sign, reordering the interval endpoints, so positive means supervision performs better. For the three-seed estimate, results are first averaged over training seeds and then paired by template. The interval is a template-clustered t interval over the eligible groups, not a three-seed standard deviation. The audit is deterministic replay of saved outputs, not independent inference.
