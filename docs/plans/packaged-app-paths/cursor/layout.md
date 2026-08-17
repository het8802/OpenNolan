```
READ-ONLY  (the packaged app)
  /Applications/OpenNolan.app                    # or desktop/dist/mac-arm64/OpenNolan.app
  └── Contents/
      ├── MacOS/OpenNolan                        # Electron launcher
      └── Resources/
          ├── app.asar                           # desktop/main.js (the shell)
          ├── python/bin/python3                 # bare interpreter, NO pip packages
          ├── uv/uv                              # installer binary
          ├── node/bin/node  (+ npm, npx)        # composition runtime
          ├── wheels/                            # vendored pip wheels for first-run
          └── backend/                           # OPENNOLAN_CODE_ROOT  (agent cwd)
              ├── server/  lib/  tools/
              ├── skills/  pipeline_defs/  schemas/  styles/  templates/
              ├── .agents/app/                   # video-production skills the agent sees
              ├── remotion-composer/             # SOURCE only (no node_modules)
              ├── composition/hyperframes/       # SOURCE only (no node_modules)
              ├── web/dist/                      # compiled UI (Vite output)
              ├── AGENT_GUIDE.md  PROJECT_CONTEXT.md
              └── requirements*.txt


WRITABLE  (user data — OPENNOLAN_HOME)
  ~/Library/Application Support/OpenNolan
  ├── .env                                       # API keys (BYOK)
  ├── settings.json
  ├── user_styles/                               # user-dropped playbooks
  ├── projects/                                  # OPENNOLAN_PROJECTS_DIR
  │   └── <project-id>/
  │       ├── project.json
  │       ├── artifacts/                         # script, scene_plan, edit_decisions, …
  │       ├── assets/{images,video,audio,music}/
  │       ├── hf/renders/                        # agent scene clips (building blocks)
  │       └── renders/final.mp4                  # the one deliverable
  ├── runtime/                                   # OPENNOLAN_RUNTIME_DIR
  │   ├── venv/bin/python                        # THE python the app actually runs
  │   ├── bin/ffmpeg  bin/ffprobe                # downloaded on first launch
  │   ├── composition/
  │   │   ├── remotion/                          # copied source + npm ci'd node_modules
  │   │   ├── hyperframes/                       # copied source + npm ci'd node_modules
  │   │   └── browsers/                          # headless Chromium for those engines
  │   └── manifest.json                          # what first-run already installed
  ├── appcache/                                  # NOT named "cache" (Chromium owns Cache/)
  │   ├── huggingface/  torch/  u2net/
  │   ├── npm/  pip/  xdg/
  │   └── scratch/                               # TMPDIR for this process tree
  └── .agents/tools/logs/                        # UI/backend debug logs


OUTSIDE BOTH  (one file)
  ~/.opennolan/install_id                        # anonymous analytics id
```

```
first launch
  Resources/python/bin/python3     (bare)
       │  uses Resources/uv/uv
       │  installs Resources/wheels  (+ ffmpeg download)
       ▼
  ~/Library/Application Support/OpenNolan/runtime/venv/bin/python
       │
       └── this is what runs uvicorn + the agent forever after


agent
  cwd          = Resources/backend          (read-only code)
  python       = runtime/venv/bin/python    (via PATH)
  add_dirs     = .../OpenNolan/projects     (so Read/Write work on projects)
  skills       = Resources/backend/.agents/app
  sandbox      = code root + home + projects + runtime + appcache + /tmp
                 (cannot read ~/Documents, Desktop, etc. unless you set
                  OPENNOLAN_AGENT_SANDBOX=0)
```
