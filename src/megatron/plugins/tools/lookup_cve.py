from __future__ import annotations

import httpx

from .base import BaseTool, ToolResult, register_tool


@register_tool("lookup_cve")
class LookupCveTool(BaseTool):
    """Look up a CVE record via the NVD public API.

    Free, no key required. Returns CVSS score, description, and reference URLs.
    The LLM calls this when it encounters a CVE ID and wants authoritative info.
    """

    name = "lookup_cve"
    description = "查询 CVE 漏洞详情（通过 NVD 官方 API），返回 CVSS 评分、描述和参考链接。需要 CVE 编号如 CVE-2024-1234。"

    NVD_URL = "https://services.nvd.nist.gov/rest/json/cves/2.0"

    def __init__(self, **config):
        super().__init__(**config)
        self.timeout = float(config.get("timeout", 15))
        self.api_key = config.get("api_key", "")
        self._headers = {}
        if self.api_key:
            self._headers["apiKey"] = self.api_key

    @property
    def schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "cve_id": {
                    "type": "string",
                    "description": "CVE 编号，如 CVE-2024-1234",
                },
            },
            "required": ["cve_id"],
        }

    async def run(self, cve_id: str) -> ToolResult:
        cve_id = cve_id.upper().strip()
        if not cve_id.startswith("CVE-"):
            return ToolResult(name=self.name, ok=False, error="Invalid CVE id format")
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(
                    self.NVD_URL,
                    params={"cveId": cve_id},
                    headers=self._headers,
                )
                resp.raise_for_status()
            data = resp.json()
            vulns = data.get("vulnerabilities", [])
            if not vulns:
                return ToolResult(name=self.name, ok=False, error=f"{cve_id} not found in NVD")
            cve = vulns[0]["cve"]
            descriptions = cve.get("descriptions", [])
            desc = next(
                (d["value"] for d in descriptions if d["lang"] == "en"),
                descriptions[0]["value"] if descriptions else "",
            )
            metrics = cve.get("metrics", {})
            cvss = {}
            for key in ("cvssMetricV31", "cvssMetricV30", "cvssMetricV2"):
                if key in metrics and metrics[key]:
                    cvss = metrics[key][0].get("cvssData", {})
                    break
            refs = [r.get("url") for r in cve.get("references", []) if r.get("url")][:8]
            return ToolResult(
                name=self.name,
                ok=True,
                data={
                    "cve_id": cve_id,
                    "description": desc[:2000],
                    "cvss_score": cvss.get("baseScore"),
                    "cvss_severity": cvss.get("baseSeverity"),
                    "vector": cvss.get("vectorString", ""),
                    "references": refs,
                    "published": cve.get("published", ""),
                },
            )
        except Exception as e:
            return ToolResult(name=self.name, ok=False, error=str(e))


__all__ = ["LookupCveTool"]
