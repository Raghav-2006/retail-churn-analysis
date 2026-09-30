"""Backwards-compatible alias: the cleaning rules now live in pipeline/transform.py."""
from pipeline.transform import *  # noqa: F401,F403
from pipeline.transform import run  # noqa: F401
