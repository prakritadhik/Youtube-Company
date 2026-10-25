# Environment inspection

Inspected: 2026-10-25, at the start of the build.

## Where the code is being built

| Item | Actual value |
|---|---|
| Machine | Cloud container (Linux), **not** the owner's Windows PC |
| OS | Ubuntu 24.04.5 LTS, kernel 6.18 |
| CPU / RAM | 4 cores / 15 GiB, no swap |
| GPU | **None** (`nvidia-smi` not present) |
| Writable disk | about 31 GiB free of a fixed per-session allowance |
| Python | 3.13.17 (`uv` 0.11 available) |
| Node / npm / pnpm | 22.22 / 10.9 / 10.28 |
| FFmpeg | 6.1.1 with `libx264`, `aac`, `libmp3lame`, `libass`, `libfreetype`, `drawtext`, `subtitles`, `xfade`, `zoompan`, `loudnorm` |
| SQLite CLI | not installed (Python's built-in `sqlite3` module is used) |
| Registries | PyPI and npm reachable through the session proxy |
| Pre-existing project files | none (empty repository) |

## What this means for the design

1. **The target machine is Windows; the build machine is Linux.** All code is written to be
   cross-platform (`pathlib`, no shell pipelines, `subprocess` with argument lists, no POSIX-only
   modules). Everything was executed and tested on Linux only. The Windows scripts in
   `scripts/windows/` are **untested on Windows** until you run them. `python -m app.cli doctor`
   prints the same environment facts for *your* machine so that you can compare.
2. **No GPU is assumed.** Rendering is CPU-only FFmpeg (libx264). No local diffusion or music
   model is required. Image, voice, music and video generation are provider adapters that are
   *unavailable* until you configure credentials, and are reported as unavailable, never faked.
3. **FFmpeg is the render engine.** The installed build has everything the pipeline needs. On
   Windows you need to install it yourself (for example `winget install Gyan.FFmpeg`).
4. **SQLite for the local prototype**, behind SQLAlchemy 2.0 and Alembic migrations, so PostgreSQL
   remains a configuration change rather than a rewrite.
5. **Connectors visible in this build session (Canva, ElevenLabs, GitHub) are tools available to
   the assistant that builds the project. They are not available to your running application.**
   The application needs its own API keys and OAuth grants for each provider. Nothing in the
   application assumes the assistant's connectors exist.

## Things that were *not* verified yet

Provider capabilities, quotas, pricing and commercial-use terms (ElevenLabs, Canva, YouTube
Data/Analytics API, music/image generation vendors) are verified **per phase, immediately before
each integration is built**, and the findings are recorded in `docs/PROVIDERS.md`. Until that
file says a capability is verified, treat it as unverified.
