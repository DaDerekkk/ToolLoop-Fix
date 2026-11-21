# ITER-009 Colab L4 G3 Multi-Arm LoRA Training Verification

- **Verification Verdict**: `PASS`
- **Evidence Class**: `analysis` (auditing remote `model_training` operation `tlfix-dedba406d4a6`)
- **GPU Verified**: `NVIDIA L4` (compute `[8, 9]`, `22.03 GiB`)
- **Lifecycle Teardown**: `owned_absent=True`, `original_set_unchanged=True`, `two_way_sentinel=True`
- **Account CU Observations**: balances `[2684.52, 2684.52, 2681.03]` (observed delta `3.49 CU`), rates `[0.0, 1.94, 10.82] CU/hr`, client elapsed `1448.74s`
- **Base Model Invariance**: `base_weights_unchanged=True` (`b8cb99011cc6c0ce...`)
- **LoRA Architecture**: `pure_pytorch_qwen35_text_lora_v1`, `rank=4`, `alpha=8.0`, `186` modules (`2,705,664` trainable params)
- **Training Bundle SHA256**: `08d833addafcf94dcd07ff7ad229c82b3616e92cc34b5636d88d6864f6eda637`
- **Receipt SHA256**: `367d8c2a6ecc154b1858c0588d4916fc2a29e5810e92ff04d2af5eae9c4cdabe`

## Trained G3 Adapters Summary

| Adapter ID | Arm | Objective | Seed | N | Init Loss | Final Loss | Epoch Losses | Train Reward Acc | Train Tokens | Peak VRAM (MiB) | Train (s) | Safetensors SHA256 |
|---|---|---|---:|---:|---:|---:|---|---:|---:|---:|---:|---|
| `P_s202` | `P` | `dpo` | 202 | 400 | 0.7244 | 0.0455 | `0.1732` | 98.0% | 742,391 | 5664.8 | 459.12 | `2e0c020422f603a7...` |
| `P_s303` | `P` | `dpo` | 303 | 400 | 0.7244 | 0.0170 | `0.1889` | 100.0% | 742,391 | 5657.7 | 373.66 | `24380892e4d8e4ae...` |
