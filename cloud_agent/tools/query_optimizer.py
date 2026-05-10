"""
Query Optimizer Tool - detect and optimize slow database queries.

The tool discovers RDS instances, reads RDS CloudWatch metrics, collects SQL
evidence from Database Insights / Performance Insights, and returns slow-query
recommendations that the dashboard can render directly.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from cloud_agent.agent.baseagent import Action
from cloud_agent.tools.base_tool import BaseTool, register_tool
from cloud_agent.utils.logger import get_logger

logger = get_logger(__name__)

_OPTIMIZATION_PROMPT = """\
You are a senior database performance engineer. I have collected slow-query
evidence from a {engine} database ({db_instance_id}).

For each query, provide:
1. Problem: what makes this query slow.
2. Optimized Query: a corrected SQL version where possible.
3. Index Suggestions: CREATE INDEX statements that would help.
4. Estimated Improvement: rough estimate.
5. Severity: critical / warning / info.

--- SLOW QUERIES ---
{queries_text}
--- END QUERIES ---

Respond in JSON:
{{
  "db_instance_id": "{db_instance_id}",
  "engine": "{engine}",
  "total_queries_analyzed": <int>,
  "optimizations": [
    {{
      "original_query": "...",
      "problem": "...",
      "optimized_query": "...",
      "index_suggestions": ["CREATE INDEX ..."],
      "estimated_improvement": "...",
      "severity": "critical|warning|info",
      "explanation": "Human-readable 2-3 sentence summary"
    }}
  ],
  "summary": "One-line overall assessment"
}}
"""


@register_tool("query_optimizer")
class QueryOptimizerTool(BaseTool):
    """Detect slow database queries and suggest optimized SQL/indexes."""

    def execute(self, action: Action) -> dict[str, Any]:
        logger.info("[bold yellow]QUERY OPTIMIZER[/bold yellow] scanning RDS databases")

        tool_cfg = self.config.get("tools", {}).get("query_optimizer", {})
        latency_threshold = tool_cfg.get("latency_threshold_ms", 100) / 1000
        slow_query_threshold = tool_cfg.get("slow_query_threshold_ms", 1000)
        max_queries = tool_cfg.get("max_queries_to_analyze", 10)

        try:
            databases = self.provider.list_rds_instances()
        except Exception as exc:
            logger.warning("Could not list RDS instances: %s", exc)
            return {"tool": self.tool_name, "status": "error", "error": str(exc)}

        if not databases:
            return {
                "tool": self.tool_name,
                "status": "no_databases",
                "summary": "No RDS databases were found",
                "databases_scanned": 0,
                "database_metrics": [],
                "slow_databases": [],
                "optimizations": [],
                "errors": [],
            }

        database_metrics: list[dict[str, Any]] = []
        slow_databases: list[dict[str, Any]] = []
        healthy_databases: list[str] = []

        for db in databases:
            db_id = db["db_instance_id"]
            engine = db.get("engine", "unknown")
            region = db.get("region", "unknown")

            try:
                read_latency = self.provider.get_rds_metrics(db_id, "ReadLatency")
                write_latency = self.provider.get_rds_metrics(db_id, "WriteLatency")
                cpu = self.provider.get_rds_metrics(db_id, "CPUUtilization")
            except Exception as exc:
                logger.warning("Could not get metrics for %s: %s", db_id, exc)
                read_latency = write_latency = cpu = 0.0

            metrics = {
                "db_instance_id": db_id,
                "engine": engine,
                "instance_class": db.get("instance_class", "unknown"),
                "status": db.get("status", "unknown"),
                "region": region,
                "read_latency_ms": round(float(read_latency) * 1000, 2),
                "write_latency_ms": round(float(write_latency) * 1000, 2),
                "cpu_percent": round(float(cpu), 1),
                "latency_threshold_ms": round(latency_threshold * 1000, 2),
            }
            database_metrics.append(metrics)

            if read_latency > latency_threshold or write_latency > latency_threshold:
                slow_databases.append(metrics)
            else:
                healthy_databases.append(db_id)

        all_optimizations: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        pi_unavailable: list[dict[str, Any]] = []

        # Do not gate SQL evidence on CloudWatch latency. Database Insights can
        # contain useful slow SQL even when aggregate read/write latency is low.
        for db_info in database_metrics:
            db_id = db_info["db_instance_id"]
            engine = db_info["engine"]

            try:
                samples = self.provider.get_slow_queries(db_id, engine, limit=max_queries)
            except Exception as exc:
                logger.warning("Could not read SQL evidence for %s: %s", db_id, exc)
                errors.append({"db_instance_id": db_id, "error": str(exc)})
                pi_unavailable.append({
                    "db_instance_id": db_id,
                    "engine": engine,
                    "diagnostic": str(exc),
                    "recommendation": "Check Performance Insights / Database Insights configuration and IAM access.",
                })
                continue

            unavailable = [q for q in samples if q.get("status") == "pi_unavailable"]
            if unavailable:
                for item in unavailable:
                    pi_unavailable.append({
                        "db_instance_id": db_id,
                        "engine": engine,
                        "diagnostic": item.get("diagnostic", "Performance Insights unavailable."),
                        "recommendation": item.get("recommendation", "Enable PI/Database Insights and grant PI access."),
                        "aws_error_code": item.get("aws_error_code"),
                        "dbi_resource_id": item.get("dbi_resource_id"),
                        "region": item.get("region"),
                    })
                logger.warning("  Performance Insights unavailable for %s: %s", db_id, unavailable[0].get("diagnostic"))
                continue

            selected = self._select_problematic_queries(samples, slow_query_threshold)
            if not selected:
                continue

            analysis = self._analyse_queries(db_id, engine, selected)
            analysis["database_metrics"] = db_info
            all_optimizations.append(analysis)

        total_opts = sum(len(item.get("optimizations", [])) for item in all_optimizations)
        summary = self._summary(len(databases), len(slow_databases), total_opts, errors, pi_unavailable)

        logger.info(
            "[bold yellow]QUERY OPTIMIZER COMPLETE[/bold yellow] %d database(s), %d suggestion(s)",
            len(databases),
            total_opts,
        )

        return {
            "tool": self.tool_name,
            "status": (
                "optimizations_found"
                if all_optimizations
                else "pi_unavailable"
                if pi_unavailable
                else "no_slow_queries"
            ),
            "summary": summary,
            "databases_scanned": len(databases),
            "database_metrics": database_metrics,
            "slow_databases": slow_databases,
            "healthy_databases": healthy_databases,
            "pi_unavailable": pi_unavailable,
            "optimizations": all_optimizations,
            "errors": errors,
        }

    @staticmethod
    def _select_problematic_queries(
        samples: list[dict[str, Any]],
        slow_query_threshold_ms: int,
    ) -> list[dict[str, Any]]:
        selected: list[dict[str, Any]] = []
        for sample in samples:
            avg_time = float(sample.get("avg_time_ms", 0) or 0)
            has_duration = bool(sample.get("has_duration_metric"))
            if has_duration and avg_time < slow_query_threshold_ms:
                continue
            selected.append(sample)
        return selected

    @staticmethod
    def _summary(
        scanned: int,
        high_latency: int,
        total_opts: int,
        errors: list[dict[str, str]],
        pi_unavailable: list[dict[str, Any]],
    ) -> str:
        if total_opts:
            return f"Found {total_opts} slow-query optimization suggestion(s) across {scanned} RDS database(s)"
        if pi_unavailable:
            return (
                f"Scanned {scanned} RDS database(s), but Performance Insights / "
                f"Database Insights was unavailable for {len(pi_unavailable)} database(s)"
            )
        if errors:
            return f"Scanned {scanned} RDS database(s), but SQL evidence was unavailable for {len(errors)} database(s)"
        if high_latency:
            return f"High RDS latency found on {high_latency} database(s), but no SQL samples were returned yet"
        return f"Scanned {scanned} RDS database(s); no slow SQL samples were returned yet"

    def _analyse_queries(
        self,
        db_id: str,
        engine: str,
        slow_queries: list[dict[str, Any]],
    ) -> dict[str, Any]:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return self._rule_based_analysis(db_id, engine, slow_queries)

        try:
            from openai import OpenAI

            client = OpenAI(api_key=api_key)
            queries_text = ""
            for i, q in enumerate(slow_queries, 1):
                queries_text += (
                    f"\n--- Query #{i} ---\n"
                    f"SQL: {q.get('query', 'SQL unavailable')}\n"
                    f"DB Load: {q.get('db_load', 'N/A')}\n"
                    f"Avg Time: {q.get('avg_time_ms', 'N/A')}ms\n"
                    f"Source: {q.get('source', 'Performance Insights')}\n"
                    f"Evidence:\n{q.get('explain_output', 'N/A')}\n"
                )

            response = client.chat.completions.create(
                model="gpt-4o-mini",
                response_format={"type": "json_object"},
                messages=[{
                    "role": "user",
                    "content": _OPTIMIZATION_PROMPT.format(
                        engine=engine,
                        db_instance_id=db_id,
                        queries_text=queries_text,
                    ),
                }],
                temperature=0.1,
            )
            raw = response.choices[0].message.content
            return self._normalise_analysis(json.loads(raw), db_id, engine, slow_queries)
        except Exception as exc:
            logger.warning("LLM query analysis failed: %s - using rule-based fallback", exc)
            return self._rule_based_analysis(db_id, engine, slow_queries)

    @staticmethod
    def _normalise_analysis(
        data: dict[str, Any],
        db_id: str,
        engine: str,
        slow_queries: list[dict[str, Any]],
    ) -> dict[str, Any]:
        opts = data.get("optimizations", [])
        if not isinstance(opts, list):
            opts = []
        for idx, opt in enumerate(opts):
            evidence = slow_queries[idx] if idx < len(slow_queries) else {}
            opt.setdefault("original_query", evidence.get("query", "SQL text unavailable"))
            opt.setdefault("optimized_query", "Manual rewrite recommended")
            opt.setdefault("index_suggestions", [])
            opt.setdefault("severity", "warning")
            opt.setdefault("estimated_improvement", "Unknown")
            opt.setdefault("avg_time_ms", evidence.get("avg_time_ms", 0))
            opt.setdefault("db_load", evidence.get("db_load", 0))
            opt.setdefault("source", evidence.get("source", "Performance Insights"))
        return {
            "db_instance_id": data.get("db_instance_id", db_id),
            "engine": data.get("engine", engine),
            "total_queries_analyzed": data.get("total_queries_analyzed", len(slow_queries)),
            "optimizations": opts,
            "summary": data.get("summary", f"Analyzed {len(opts)} query sample(s) on {db_id}."),
        }

    def _rule_based_analysis(
        self,
        db_id: str,
        engine: str,
        slow_queries: list[dict[str, Any]],
    ) -> dict[str, Any]:
        optimizations = []

        for query_info in slow_queries:
            query = query_info.get("query", "SQL text unavailable")
            explain = query_info.get("explain_output", "")
            query_lower = query.lower()
            explain_lower = explain.lower()

            problems: list[str] = []
            index_suggestions: list[str] = []
            optimized_query = query
            severity = "info"

            table = self._extract_table(query_lower)
            where_cols = re.findall(
                r"(?:where|and|or)\s+[`\"]?([a-zA-Z_][\w]*)[`\"]?\s*(?:=|>|<|in\b|like\b)",
                query_lower,
            )
            order_cols = re.findall(r"\border\s+by\s+[`\"]?([a-zA-Z_][\w]*)[`\"]?", query_lower)

            if "seq scan" in explain_lower or "table scan" in explain_lower:
                problems.append("Full table scan detected; the database is reading every row")
                severity = "critical"

            if where_cols:
                problems.append(f"Filter on {', '.join(dict.fromkeys(where_cols))} needs a supporting index")
                severity = self._max_severity(severity, "warning")
                for col in dict.fromkeys(where_cols):
                    index_suggestions.append(f"CREATE INDEX idx_{table}_{col} ON {table}({col});")

            if where_cols and order_cols:
                cols = list(dict.fromkeys(where_cols + order_cols))[:3]
                index_suggestions.insert(
                    0,
                    f"CREATE INDEX idx_{table}_{'_'.join(cols)} ON {table}({', '.join(cols)});",
                )
                problems.append("Sorting after filtering can be improved with a composite index")
                severity = self._max_severity(severity, "warning")

            if "select *" in query_lower:
                problems.append("SELECT * fetches all columns and increases I/O")
                optimized_query = re.sub(
                    r"\bselect\s+\*",
                    "SELECT id, <needed_columns>",
                    optimized_query,
                    count=1,
                    flags=re.IGNORECASE,
                )
                severity = self._max_severity(severity, "warning")

            if re.search(r"\blike\s+'%", query_lower):
                problems.append("LIKE with a leading wildcard prevents normal index usage")
                severity = "critical"
                if engine.lower().startswith("mysql"):
                    index_suggestions.append(f"CREATE FULLTEXT INDEX idx_{table}_fulltext ON {table}(event_payload);")
                    optimized_query = re.sub(
                        r"event_payload\s+like\s+'%([^%']+)%'",
                        r"MATCH(event_payload) AGAINST('\1' IN NATURAL LANGUAGE MODE)",
                        optimized_query,
                        flags=re.IGNORECASE,
                    )

            if re.search(r"\bfrom\s+\w+\s*,\s*\w+", query_lower):
                problems.append("Implicit comma join style can create inefficient join plans")
                severity = self._max_severity(severity, "warning")

            rows_examined = int(query_info.get("rows_examined", 0) or 0)
            rows_returned = int(query_info.get("rows_returned", 0) or 0)
            if rows_examined and rows_returned and rows_examined / max(rows_returned, 1) > 10:
                problems.append(
                    f"Scans {rows_examined:,} rows to return {rows_returned:,}, which is inefficient"
                )
                severity = "critical"

            if not problems:
                problems.append("High database load sample; review the plan and add selective indexes")

            if optimized_query == query and index_suggestions:
                optimized_query = f"-- Apply the suggested index first.\n{query}"
            elif optimized_query == query:
                optimized_query = "Manual rewrite recommended after reviewing EXPLAIN output"

            index_suggestions = list(dict.fromkeys(index_suggestions))
            avg_time = float(query_info.get("avg_time_ms", 0) or 0)
            db_load = float(query_info.get("db_load", 0) or 0)

            optimizations.append({
                "original_query": query,
                "problem": "; ".join(problems),
                "optimized_query": optimized_query,
                "index_suggestions": index_suggestions,
                "estimated_improvement": self._estimated_improvement(severity, index_suggestions),
                "severity": severity,
                "avg_time_ms": avg_time,
                "db_load": db_load,
                "source": query_info.get("source", "Performance Insights"),
                "calls": query_info.get("calls", 0),
                "explanation": (
                    f"Database load sample {db_load:.3f}; duration metric "
                    f"{avg_time:.0f}ms. Issues: {'; '.join(problems)}."
                ),
            })

        severity_order = {"critical": 0, "warning": 1, "info": 2}
        optimizations.sort(key=lambda item: severity_order.get(item["severity"], 3))

        return {
            "db_instance_id": db_id,
            "engine": engine,
            "total_queries_analyzed": len(slow_queries),
            "optimizations": optimizations,
            "summary": (
                f"Found {len(optimizations)} query optimization candidate(s) on {db_id}. "
                f"{sum(1 for o in optimizations if o['severity'] == 'critical')} critical, "
                f"{sum(1 for o in optimizations if o['severity'] == 'warning')} warning."
            ),
        }

    @staticmethod
    def _extract_table(query_lower: str) -> str:
        match = re.search(r"\bfrom\s+[`\"]?([a-zA-Z_][\w.]*)[`\"]?", query_lower)
        if not match:
            return "target_table"
        return match.group(1).split(".")[-1]

    @staticmethod
    def _max_severity(a: str, b: str) -> str:
        order = {"info": 0, "warning": 1, "critical": 2}
        return a if order.get(a, 0) >= order.get(b, 0) else b

    @staticmethod
    def _estimated_improvement(severity: str, indexes: list[str]) -> str:
        if severity == "critical" and indexes:
            return "10-100x faster after indexing/search rewrite"
        if severity == "critical":
            return "5-20x faster after rewrite"
        if indexes:
            return "3-10x faster with supporting indexes"
        return "2-5x faster after plan review"
