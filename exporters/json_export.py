import json
import logging
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)


def export_json(jobs: list, contacts: dict, output_dir: str) -> str:
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    date_str = datetime.utcnow().strftime("%Y-%m-%d")
    out_path = Path(output_dir) / f"jobs_{date_str}.json"

    output = []
    for job in jobs:
        job_dict = job.to_dict()
        company = job.company
        if company and company in contacts:
            job_dict["contact"] = contacts[company].to_dict()
        else:
            job_dict["contact"] = None

        # sources is a proper field on JobRecord (populated by dedup or to_dict fallback)
        job_dict["sources"] = job.sources or [job.source]

        output.append(job_dict)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False, default=str)

    logger.info(f"JSON export: {len(output)} jobs → {out_path}")
    return str(out_path)
