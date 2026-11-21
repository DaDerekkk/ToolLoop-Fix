# ToolLoop-Fix: English research talk

[Editable PowerPoint](ToolLoop-Fix-English.pptx) · [Static PDF](ToolLoop-Fix-English.pdf) · [Speaker notes](speaker-notes.md)

An eight-minute talk for an ML or agent-systems audience. Follow one failed order lookup, compare supervision and preference learning at the same decision points, then test the training choice against individual held-out tasks. The PDF shows the complete four-call trace even without animation.

## Design guidance consulted before authoring

| Original skill | How it informed this deck |
| --- | --- |
| [K-Dense: scientific-slides](https://github.com/K-Dense-AI/scientific-agent-skills/blob/main/skills/scientific-slides/SKILL.md) | Build the story around one research question. Put the case and figures on screen; keep the spoken explanation in the notes. |
| [K-Dense: scientific-visualization](https://github.com/K-Dense-AI/scientific-agent-skills/blob/main/skills/scientific-visualization/SKILL.md) | Display paired outcomes rather than only marginal success rates. Show each training seed; name interval type, units and denominators. |
| [Anthropic: pptx](https://github.com/anthropics/skills/blob/main/skills/pptx/SKILL.md) | Author editable slide objects, choose appropriate fonts, and inspect the rendered deck instead of inferring fit from the source code. |

These guides were consulted as references, not installed. They describe design and QA, not the source of the experimental claims.

## Evidence and illustration provenance

- **Paired outcomes:** archives EXP-20261120-081 (B3 supervised fine-tuning) and EXP-20261120-084 (P action-level direct preference optimization). Join the 30 eligible tasks by case ID; use exact grounded structured completion. At training seed 101 the overlap is 20 both, 9 SFT only, 1 DPO only, 0 neither. This is one paired comparison, not 60 independent tasks.
- **Seed stability:** EXP-20261120-105R1, same tasks and three training seeds. Held-out jobs are a subset of the full test set. Error intervals are not seed standard deviations.
- **Trace:** actual tool events in EXP-20261120-081, case g4h9-orders-recoverable-h3-01. Frame durations aid reading and are not measured latency.
- **Scoring audit:** EXP-20261120-REVIEW-009R2. Replay of saved outputs is distinct from independent model generation.
- **Artwork:** the new cover and two earlier methods plates are illustrative, not data or photographs of the experiment. The plots and animation are deterministic renderings of archived results.

The English edition does not overwrite the Chinese editions. No models were trained or evaluated for this release.
