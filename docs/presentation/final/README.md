# ToolLoop-Fix | Final English research talk

[PowerPoint](ToolLoop-Fix.pptx) · [PDF](ToolLoop-Fix.pdf) · [Speaker notes](speaker-notes.md)

**Research question.** When a small model's tool call fails, does it learn the next useful action better by copying an execution-verified correction or by comparing that correction with a rejected action? The question is about a matched, offline training setup, not whether one learning algorithm is universally better.

**Context.** [ReAct (Yao et al., ICLR 2023)](https://arxiv.org/abs/2210.03629) connects reasoning, action and observations. [AntiDoom (Liquid AI, 2026)](https://www.liquid.ai/blog/antidoom) targets tokens that initiate repeated text; repeated tool calls are a different problem because retries may be necessary. [Fission-GRPO (Zhang et al., ACL 2026)](https://aclanthology.org/2026.acl-long.1880/) uses diagnostic feedback and new on-policy rollouts after a tool error. These are intellectual precedents, not methods we benchmarked head to head.

**Evidence.** The [project report](../../../FINAL_REPORT.md) and [archived paired comparison](../../../experiments/EXP-20261120-105R1/g3_g4_comparison.md) are the sources for project results. The paired task figure joins the two methods by case ID. The seed figure displays the three trained models per method on one fixed test set, not three independent test sets. The order animation reproduces archived tool calls; frame timing is illustrative.

**Files.** The PowerPoint and PDF are the same edition. The PDF includes the full trace on a single static frame. The build script, figures, and notes accompany this edition. No training or inference was performed for this revision.
