"""OpenMontage Mission Control — local read API (v1).

Read-only FastAPI layer over the existing pipeline/checkpoint/registry libs.
Owns no orchestration logic (per AGENT_GUIDE: Python is tools + persistence).
The agent runner, uploads, and gate writes are later slices.
"""
