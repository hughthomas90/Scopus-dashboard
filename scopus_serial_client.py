from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple
import requests


ELS_BASE = "https://api.elsevier.com"


@dataclass(frozen=True)
class ScopusSerialClient:
    api_key: str
    inst_token: Optional[str] = None
    timeout_s: int = 30

    def _headers(self) -> Dict[str, str]:
        # Elsevier APIs commonly accept API key in this header.
        # If your org uses Insttoken entitlements, include it too.
        headers = {
            "Accept": "application/json",
            "X-ELS-APIKey": self.api_key,
        }
        if self.inst_token:
            headers["X-ELS-Insttoken"] = self.inst_token
        return headers

    def get_json(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Dict[str, Any], Dict[str, Optional[str]]]:
        """
        Returns (json, quota_headers_subset).
        Raises RuntimeError on non-200 responses with a helpful message.
        """
        params = dict(params or {})
        # Elsevier supports this param; keeping it explicit avoids XML defaults.
        params.setdefault("httpAccept", "application/json")

        r = requests.get(url, headers=self._headers(), params=params, timeout=self.timeout_s)

        quota = {
            "X-RateLimit-Limit": r.headers.get("X-RateLimit-Limit"),
            "X-RateLimit-Remaining": r.headers.get("X-RateLimit-Remaining"),
            "X-RateLimit-Reset": r.headers.get("X-RateLimit-Reset"),
            "X-ELS-Status": r.headers.get("X-ELS-Status"),
        }

        if r.status_code != 200:
            # Try to include a useful error payload (often JSON, sometimes not).
            msg = f"Elsevier API error {r.status_code}"
            try:
                payload = r.json()
                msg += f": {payload}"
            except Exception:
                msg += f": {r.text[:500]}"
            raise RuntimeError(msg)

        return r.json(), quota

    def search_serial_titles(
        self,
        title: str,
        *,
        content: str = "journal",
        view: str = "STANDARD",
        count: int = 25,
        start: int = 0,
    ) -> Tuple[Dict[str, Any], Dict[str, Optional[str]]]:
        url = f"{ELS_BASE}/content/serial/title"
        params = {
            "title": title,
            "content": content,
            "view": view,
            "count": count,
            "start": start,
        }
        return self.get_json(url, params=params)

    def retrieve_by_issn(
        self,
        issn: str,
        *,
        view: str = "ENHANCED",
        field: Optional[str] = None,
        years: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], Dict[str, Optional[str]]]:
        """
        Retrieves a specific serial by ISSN.
        years (if supported for your entitlements) can be "2018-2024" etc.
        """
        issn = issn.strip()
        url = f"{ELS_BASE}/content/serial/title/issn/{issn}"
        params: Dict[str, Any] = {"view": view}
        if field:
            params["field"] = field
        if years:
            # Some clients use "date" for ranges; if yours differs, adjust here.
            params["date"] = years
        return self.get_json(url, params=params)


def _as_list(x: Any) -> list:
    if x is None:
        return []
    if isinstance(x, list):
        return x
    return [x]


def parse_serial_entry(serial_json: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalizes the first entry in serial-metadata-response into a flatter dict.
    """
    resp = serial_json.get("serial-metadata-response", {})
    entry = _as_list(resp.get("entry"))[0] if _as_list(resp.get("entry")) else {}

    out: Dict[str, Any] = {}
    out["title"] = entry.get("dc:title")
    out["publisher"] = entry.get("dc:publisher")
    out["issn"] = entry.get("prism:issn")
    out["eissn"] = entry.get("prism:eIssn")
    out["source_id"] = entry.get("source-id")
    out["openaccess"] = entry.get("openaccess")
    out["aggregation_type"] = entry.get("prism:aggregationType")
    out["scopus_source_link"] = None
    out["homepage"] = None
    for lk in _as_list(entry.get("link")):
        if lk.get("@ref") == "scopus-source":
            out["scopus_source_link"] = lk.get("@href")
        if lk.get("@ref") == "homepage":
            out["homepage"] = lk.get("@href")

    # Subject areas
    subjects = []
    for s in _as_list(entry.get("subject-area")):
        subjects.append({
            "code": s.get("@code"),
            "abbrev": s.get("@abbrev"),
            "name": s.get("$"),
        })
    out["subjects"] = subjects

    # CiteScore summary
    cs = entry.get("citeScoreYearInfoList", {}) or {}
    out["citescore_current"] = cs.get("citeScoreCurrentMetric")
    out["citescore_current_year"] = cs.get("citeScoreCurrentMetricYear")
    out["citescore_tracker"] = cs.get("citeScoreTracker")
    out["citescore_tracker_year"] = cs.get("citeScoreTrackerYear")

    # SNIP/SJR lists
    def _metric_series(metric_key: str) -> list[dict]:
        # metric_key is "SNIP" or "SJR"
        lst = (entry.get(f"{metric_key}List", {}) or {}).get(metric_key)
        series = []
        for item in _as_list(lst):
            year = item.get("@year")
            val = item.get("$")
            if year is not None and val is not None:
                try:
                    series.append({"year": int(year), "value": float(val)})
                except Exception:
                    continue
        return sorted(series, key=lambda d: d["year"])

    out["snip_series"] = _metric_series("SNIP")
    out["sjr_series"] = _metric_series("SJR")

    # Yearly data (publicationCount, citeCountSCE, etc.)
    yd = (entry.get("yearly-data", {}) or {}).get("info", [])
    yearly = []
    for row in _as_list(yd):
        try:
            yearly.append({
                "year": int(row.get("@year")),
                "publicationCount": int(row.get("publicationCount")) if row.get("publicationCount") is not None else None,
                "citeCountSCE": int(row.get("citeCountSCE")) if row.get("citeCountSCE") is not None else None,
                "zeroCitesSCE": int(row.get("zeroCitesSCE")) if row.get("zeroCitesSCE") is not None else None,
                "zeroCitesPercentSCE": float(row.get("zeroCitesPercentSCE")) if row.get("zeroCitesPercentSCE") is not None else None,
                "revPercent": float(row.get("revPercent")) if row.get("revPercent") is not None else None,
            })
        except Exception:
            continue
    out["yearly_data"] = sorted(yearly, key=lambda d: d["year"])

    return out
