# ITER-009 Colab L4 G3 Multi-Arm LoRA Training Verification

- **Verification Verdict**: `PASS`
- **Evidence Class**: `analysis` (auditing remote `model_training` operation `tlfix-2e73409dc1aa`)
- **GPU Verified**: `NVIDIA L4` (compute `[8, 9]`, `22.03 GiB`)
- **Lifecycle Teardown**: `owned_absent=True`, `original_set_unchanged=True`, `two_way_sentinel=True`
- **Account CU Observations**: balances `[2686.85, 2686.85, 2684.68]` (observed delta `2.17 CU`), rates `[8.88, 10.82, 1.94] CU/hr`, client elapsed `1746.12s`
- **Base Model Invariance**: `base_weights_unchanged=True` (`b8cb99011cc6c0ce...`)
- **LoRA Architecture**: `pure_pytorch_qwen35_text_lora_v1`, `rank=4`, `alpha=8.0`, `186` modules (`2,705,664` trainable params)
- **Training Bundle SHA256**: `08d833addafcf94dcd07ff7ad229c82b3616e92cc34b5636d88d6864f6eda637`
- **Receipt SHA256**: `14c8ed8664ab489d0873c8f5a81eb7fca7e7b57c17abc0d199d18499773c338b`

## Trained G3 Adapters Summary

| Adapter ID | Arm | Objective | Seed | N | Init Loss | Final Loss | Epoch Losses | Train Reward Acc | Train Tokens | Peak VRAM (MiB) | Train (s) | Safetensors SHA256 |
|---|---|---|---:|---:|---:|---:|---|---:|---:|---:|---:|---|
| `P_no_retry_s101` | `P_no_retry` | `dpo` | 101 | 320 | 0.7241 | 0.0150 | `0.1710` | 99.4% | 584,581 | 5665.3 | 376.70 | `5f29de60f21673dd...` |
| `P_no_normal_s101` | `P_no_normal` | `dpo` | 101 | 320 | 0.7297 | 0.0206 | `0.1947` | 99.4% | 592,347 | 5283.3 | 290.59 | `2a0478ae9e5256f7...` |
| `B3_s202` | `B3` | `sft` | 202 | 400 | 0.1560 | 0.0000 | `0.0115` | N/A (SFT) | 371,563 | 3656.4 | 228.76 | `27232edca47d2adb...` |
| `B3_s303` | `B3` | `sft` | 303 | 400 | 0.1560 | 0.0000 | `0.0122` | N/A (SFT) | 371,563 | 3670.3 | 176.08 | `a084bf684c1ad16b...` |
