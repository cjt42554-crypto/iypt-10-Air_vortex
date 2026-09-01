#!/usr/bin/env python3
"""Nsight Systems custom report preserving exact CUDA memcpy integers."""

from __future__ import annotations

import nsysstats


class P0PCudaMemExact(nsysstats.StatsReport):
    display_name = "P0-P exact CUDA memcpy totals"
    usage = "Exact integer count, bytes, and nanoseconds by CUDA copy direction."
    query = """
SELECT
    oper.label AS "Direction",
    count(*) AS "Count",
    sum(memcpy.bytes) AS "Total Bytes",
    sum(memcpy.end - memcpy.start) AS "Total Time (ns)"
FROM CUPTI_ACTIVITY_KIND_MEMCPY AS memcpy
JOIN ENUM_CUDA_MEMCPY_OPER AS oper ON oper.id = memcpy.copyKind
GROUP BY oper.label
ORDER BY oper.label;
"""

    def setup(self):
        error = super().setup()
        if error is not None:
            return error
        for table in (
            "CUPTI_ACTIVITY_KIND_MEMCPY",
            "ENUM_CUDA_MEMCPY_OPER",
        ):
            if not self.table_exists(table):
                return f"{{DBFILE}} does not contain {table}."
        return None


if __name__ == "__main__":
    P0PCudaMemExact.Main()
