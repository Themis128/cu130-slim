# ComfyUI VRAM profiles

These files are bind-mounted into the ComfyUI container at
`/opt/ComfyUI/profiles` and read by `/entrypoint.sh`. Each line is a CLI
argument passed to `main.py` after the base `--listen 0.0.0.0 --port 8000
--preview-method auto` args.

Select a profile by setting `COMFYUI_PROFILE` in `docker-compose.yml` or
your `.env` override:

```yaml
environment:
  - COMFYUI_PROFILE=sdxl   # or flux / quality
```

## Profiles

| Profile | Flags | Best for |
|---|---|---|
| `sdxl` | `--lowvram --force-fp16 --fp16-vae --dont-upcast-attention --reserve-vram 4` | SDXL / SD 1.5 on shared 8GB GPU |
| `flux` | sdxl flags + `--fp8_e4m3fn` | FLUX.1 [dev/schnell] — FP8 weight quantization is required to fit 12B parameters |
| `quality` | `--force-fp16 --fp16-vae --reserve-vram 1` | Best quality/speed when GPU is not shared |

`CLI_ARGS` in compose is still appended last and can override any profile
flag for one-off experiments.
