# ITER-009 Colab L4 G3 Multi-Arm LoRA Training Verification

- **Verification Verdict**: `PASS`
- **Evidence Class**: `analysis` (auditing remote `model_training` operation `tlfix-a5ef8bae279d`)
- **GPU Verified**: `NVIDIA L4` (compute `[8, 9]`, `22.03 GiB`)
- **Lifecycle Teardown**: `owned_absent=True`, `original_set_unchanged=True`, `two_way_sentinel=True`
- **Account CU Observations**: balances `[2690.01, 2690.01, 2688.44]` (observed delta `1.57 CU`), rates `[0.0, 1.94, 10.82] CU/hr`, client elapsed `2040.15s`
- **Base Model Invariance**: `base_weights_unchanged=True` (`b8cb99011cc6c0ce...`)
- **LoRA Architecture**: `pure_pytorch_qwen35_text_lora_v1`, `rank=4`, `alpha=8.0`, `186` modules (`2,705,664` trainable params)
- **Training Bundle SHA256**: `08d833addafcf94dcd07ff7ad229c82b3616e92cc34b5636d88d6864f6eda637`
- **Receipt SHA256**: `7d723f7a7ff879a5fc47254ea64fc4e1f35b0bda55f7e2001fa2f140bee1fdef`

## Trained G3 Adapters Summary

| Adapter ID | Arm | Objective | Seed | N | Init Loss | Final Loss | Epoch Losses | Train Reward Acc | Train Tokens | Peak VRAM (MiB) | Train (s) | Safetensors SHA256 |
|---|---|---|---:|---:|---:|---:|---|---:|---:|---:|---:|---|
| `B3_s101` | `B3` | `sft` | 101 | 400 | 0.1566 | 0.0116 | `0.0144` | N/A (SFT) | 371,563 | 3659.3 | 261.50 | `63bdb82cf36e70e2...` |
| `B4_s101` | `B4` | `single_token_dpo` | 101 | 400 | 0.8679 | 0.1299 | `0.3292` | 98.0% | 371,563 | 3660.9 | 177.25 | `fdfcf2ddf8127387...` |
| `B5_s101` | `B5` | `dpo` | 101 | 400 | 0.7036 | 0.0016 | `0.0347` | 100.0% | 706,776 | 4880.5 | 361.86 | `b0deb879e93c3aad...` |
| `P_s101` | `P` | `dpo` | 101 | 400 | 0.7245 | 0.0030 | `0.1470` | 100.0% | 742,391 | 5672.5 | 390.98 | `3d70f104dd7b64f1...` |
