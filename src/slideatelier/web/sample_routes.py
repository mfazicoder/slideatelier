"""Routes for the workflow "Try a sample brief" affordance.

Exposes a single JSON endpoint that the landing page calls via fetch() to
populate the brief + requirements textareas with a pre-canned sample. We
keep this in its own module so the workflow router (three_stage_routes.py)
stays focused on the storyboard/wireframe/hi-fi pipeline.
"""
from __future__ import annotations

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from ..sample_briefs import SAMPLES


def register_sample_routes(app, _config_callable):
    """Registers /workflow/sample/<id> on the FastAPI app.

    Args:
        app: FastAPI instance
        _config_callable: function returning a Config (kept for parity with
            sibling route modules; not currently used here).
    """

    @app.get("/workflow/sample/{sample_id}")
    def workflow_sample(sample_id: str):
        sample = SAMPLES.get(sample_id)
        if sample is None:
            raise HTTPException(status_code=404, detail="sample not found")
        return JSONResponse(
            {
                "id": sample_id,
                "title": sample["title"],
                "summary": sample["summary"],
                "brief": sample["brief"],
                "requirements": sample["requirements"],
            }
        )

    @app.get("/api/workflow/samples")
    def workflow_samples_list():
        """Listing endpoint mostly for tooling/debug. The landing page
        renders cards server-side from the SAMPLES dict directly."""
        return {
            "samples": [
                {
                    "id": sid,
                    "title": s["title"],
                    "summary": s["summary"],
                }
                for sid, s in SAMPLES.items()
            ]
        }
