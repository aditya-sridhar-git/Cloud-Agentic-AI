"""
Query Optimizer Tool — Detect and optimize slow database queries.

This tool monitors RDS database latency metrics, identifies slow queries
using pg_stat_statements (PostgreSQL) or slow_query_log (MySQL), runs
EXPLAIN ANALYZE, and uses LLM analysis to suggest optimized rewrites
and missing indexes.

This is an agentic differentiator: AWS native tools cannot analyse SQL
semantics, rewrite queries, or suggest contextual index strategies.
"""

from __future__ import annotations

import json
import os
from typing import Any

from cloud_agent.agent.baseagent import Action
from cloud_agent.tools.base_tool import BaseTool, register_tool
from cloud_agent.utils.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# LLM prompt for query optimization
# ---------------------------------------------------------------------------

_OPTIMIZATION_PROMPT = """\
You are a senior database performance engineer. I have collected slow queries
and their EXPLAIN ANALYZE output from a {engine} database ({db_instance_id}).

For each slow query, provide:
1. **Problem**: What makes this query slow (be specific — full table scan,
   missing index, SELECT *, inefficient JOIN, etc.).
2. **Optimized Query**: A rewritten version of the SQL that would run faster.
3. **Index Suggestions**: Any CREATE INDEX statements that would help.
4. **Estimated Improvement**: rough estimate (e.g. "10x faster", "95% reduction").
5. **Severity**: critical / warning / info.

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
    """Detect slow database queries and suggest LLM-powered optimizations."""

    def execute(self, action: Action) -> dict[str, Any]:
        """Run the full query optimization pipeline.

        Pipeline: Discover DBs → Check latency → Collect slow queries →
                  Analyse with LLM → Return recommendations.
        """
        logger.info(
            "[bold yellow]🗄️  QUERY OPTIMIZER[/bold yellow] — scanning databases for slow queries …"
        )

        tool_cfg = self.config.get("tools", {}).get("query_optimizer", {})
        latency_threshold = tool_cfg.get("latency_threshold_ms", 100) / 1000  # convert to seconds
        slow_query_threshold = tool_cfg.get("slow_query_threshold_ms", 1000)
        max_queries = tool_cfg.get("max_queries_to_analyze", 10)

        # Step 1: List all RDS instances
        try:
            databases = self.provider.list_rds_instances()
        except Exception as exc:
            logger.warning("Could not list RDS instances: %s", exc)
            return {"tool": self.tool_name, "status": "error", "error": str(exc)}

        if not databases:
            logger.info("No RDS instances found.")
            return {
                "tool": self.tool_name,
                "status": "no_databases",
                "databases_scanned": 0,
                "slow_databases": [],
                "optimizations": [],
            }

        # Step 2: Check latency metrics for each database
        slow_databases: list[dict[str, Any]] = []
        healthy_databases: list[str] = []

        for db in databases:
            db_id = db["db_instance_id"]
            engine = db.get("engine", "postgres")

            try:
                read_latency = self.provider.get_rds_metrics(db_id, "ReadLatency")
                write_latency = self.provider.get_rds_metrics(db_id, "WriteLatency")
                cpu = self.provider.get_rds_metrics(db_id, "CPUUtilization")
            except Exception as exc:
                logger.warning("Could not get metrics for %s: %s", db_id, exc)
                continue

            if read_latency > latency_threshold or write_latency > latency_threshold:
                logger.info(
                    "[bold red]⚠️  HIGH LATENCY[/bold red] on [cyan]%s[/cyan] — "
                    "Read: %.1fms, Write: %.1fms, CPU: %.1f%%",
                    db_id, read_latency * 1000, write_latency * 1000, cpu,
                )
                slow_databases.append({
                    "db_instance_id": db_id,
                    "engine": engine,
                    "instance_class": db.get("instance_class", "unknown"),
                    "read_latency_ms": round(read_latency * 1000, 2),
                    "write_latency_ms": round(write_latency * 1000, 2),
                    "cpu_percent": round(cpu, 1),
                })
            else:
                healthy_databases.append(db_id)

        if not slow_databases:
            logger.info("[green]✓ All databases healthy — no latency issues.[/green]")
            return {
                "tool": self.tool_name,
                "status": "all_healthy",
                "databases_scanned": len(databases),
                "slow_databases": [],
                "optimizations": [],
            }

        # Step 3: Collect slow queries from each problematic database
        all_optimizations: list[dict[str, Any]] = []

        for db_info in slow_databases:
            db_id = db_info["db_instance_id"]
            engine = db_info["engine"]

            logger.info(
                "[bold yellow]🔍 COLLECTING[/bold yellow] slow queries from [cyan]%s[/cyan] (%s) …",
                db_id, engine,
            )

            try:
                slow_queries = self.provider.get_slow_queries(db_id, engine, limit=max_queries)
            except Exception as exc:
                logger.warning("Could not get slow queries for %s: %s", db_id, exc)
                continue

            # Filter to only queries above the threshold
            problematic = [
                q for q in slow_queries
                if q.get("avg_time_ms", 0) > slow_query_threshold
            ]

            if not problematic:
                logger.info("  No queries above %dms threshold on %s.", slow_query_threshold, db_id)
                continue

            logger.info(
                "  Found [bold red]%d slow query(ies)[/bold red] on %s",
                len(problematic), db_id,
            )

            # Step 4: Analyse with LLM (or fallback to rules)
            analysis = self._analyse_queries(db_id, engine, problematic)
            all_optimizations.append(analysis)

        result = {
            "tool": self.tool_name,
            "status": "optimizations_found" if all_optimizations else "no_slow_queries",
            "databases_scanned": len(databases),
            "slow_databases": slow_databases,
            "healthy_databases": healthy_databases,
            "optimizations": all_optimizations,
        }

        # Log summary
        total_opts = sum(len(a.get("optimizations", [])) for a in all_optimizations)
        logger.info(
            "[bold yellow]🗄️  QUERY OPTIMIZER COMPLETE[/bold yellow] — "
            "%d database(s) scanned, %d slow, %d optimization(s) suggested",
            len(databases), len(slow_databases), total_opts,
        )

        return result

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------

    def _analyse_queries(self, db_id: str, engine: str,
                         slow_queries: list[dict[str, Any]]) -> dict[str, Any]:
        """Analyse slow queries using LLM with rule-based fallback."""
        api_key = os.getenv("OPENAI_API_KEY")

        if not api_key:
            return self._rule_based_analysis(db_id, engine, slow_queries)

        try:
            from openai import OpenAI
            client = OpenAI(api_key=api_key)

            # Build query text for the prompt
            queries_text = ""
            for i, q in enumerate(slow_queries, 1):
                queries_text += (
                    f"\n--- Query #{i} ---\n"
                    f"SQL: {q['query']}\n"
                    f"Calls: {q.get('calls', 'N/A')}\n"
                    f"Avg Time: {q.get('avg_time_ms', 'N/A')}ms\n"
                    f"Rows Examined: {q.get('rows_examined', 'N/A')}\n"
                    f"Rows Returned: {q.get('rows_returned', 'N/A')}\n"
                    f"EXPLAIN Output:\n{q.get('explain_output', 'N/A')}\n"
                )

            prompt = _OPTIMIZATION_PROMPT.format(
                engine=engine,
                db_instance_id=db_id,
                queries_text=queries_text,
            )

            response = client.chat.completions.create(
                model="gpt-4o-mini",
                response_format={"type": "json_object"},
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
            )
            raw = response.choices[0].message.content
            return json.loads(raw)
        except Exception as exc:
            logger.warning("LLM query analysis failed: %s — using rule-based fallback", exc)
            return self._rule_based_analysis(db_id, engine, slow_queries)

    # ------------------------------------------------------------------
    # Rule-based fallback
    # ------------------------------------------------------------------

    def _rule_based_analysis(self, db_id: str, engine: str,
                             slow_queries: list[dict[str, Any]]) -> dict[str, Any]:
        """Pattern-match common SQL anti-patterns when LLM is unavailable."""
        optimizations = []

        for q in slow_queries:
            query = q.get("query", "")
            explain = q.get("explain_output", "")
            query_lower = query.lower()
            explain_lower = explain.lower()

            problems = []
            suggestions = []
            index_suggestions = []
            optimized_query = query  # Start with original
            severity = "info"

            # --- Anti-pattern 1: Full table scan ---
            if "seq scan" in explain_lower or "table scan" in explain_lower:
                problems.append("Full table scan detected — database is reading every row")
                severity = "critical"

                # Try to extract table name and filter column for index suggestion
                import re
                scan_match = re.search(r"(?:seq scan|table scan) on (\w+)", explain_lower)
                filter_match = re.search(r"filter:.*?\((\w+)", explain_lower)
                if scan_match and filter_match:
                    table = scan_match.group(1)
                    column = filter_match.group(1)
                    index_suggestions.append(
                        f"CREATE INDEX idx_{table}_{column} ON {table}({column});"
                    )
                    suggestions.append(f"Add an index on {table}.{column} to avoid full table scan")

            # --- Anti-pattern 2: SELECT * ---
            if "select *" in query_lower:
                problems.append("SELECT * fetches all columns — wasteful if only a few are needed")
                severity = max(severity, "warning", key=lambda x: {"info": 0, "warning": 1, "critical": 2}[x])
                suggestions.append("Replace SELECT * with specific column names")
                # Attempt to replace SELECT * with a hint
                optimized_query = optimized_query.replace("SELECT *", "SELECT id, <needed_columns>", 1)
                optimized_query = optimized_query.replace("select *", "SELECT id, <needed_columns>", 1)

            # --- Anti-pattern 3: LIKE with leading wildcard ---
            if "like '%'" in query_lower or "like '%" in query_lower:
                problems.append("LIKE with leading wildcard ('%...') prevents index usage")
                severity = "critical"
                suggestions.append("Consider using a full-text search index or restructure the filter")

            # --- Anti-pattern 4: Implicit joins (FROM a, b WHERE) ---
            if re.search(r'from\s+\w+\s*,\s*\w+', query_lower):
                problems.append("Implicit (comma) join style detected — harder to read and optimize")
                suggestions.append("Rewrite using explicit JOIN ... ON syntax")

            # --- Anti-pattern 5: High rows examined vs returned ratio ---
            rows_examined = q.get("rows_examined", 0)
            rows_returned = q.get("rows_returned", 0)
            if rows_examined > 0 and rows_returned > 0:
                ratio = rows_examined / rows_returned
                if ratio > 10:
                    problems.append(
                        f"Scanning {rows_examined:,} rows to return {rows_returned:,} "
                        f"(ratio: {ratio:.0f}x) — very inefficient"
                    )
                    severity = "critical"

            # --- Anti-pattern 6: Subquery in WHERE / IN ---
            if " in (select " in query_lower or " in(select " in query_lower:
                problems.append("Subquery inside IN clause — may be slower than a JOIN")
                suggestions.append("Rewrite the subquery as a JOIN for better performance")

            # --- Anti-pattern 7: Missing ORDER BY index ---
            if "order by" in query_lower and "sort" in explain_lower:
                sort_match = re.search(r"sort key:\s*(\w+)", explain_lower)
                if sort_match:
                    sort_col = sort_match.group(1)
                    suggestions.append(f"Consider a composite index that includes {sort_col} to avoid sort")

            # Build the optimization entry
            if not problems:
                problems.append("Query is slow but no specific anti-pattern detected")
                suggestions.append("Manual review recommended — consider query profiling")

            avg_time = q.get("avg_time_ms", 0)
            estimated_improvement = "2-5x faster"
            if severity == "critical":
                estimated_improvement = "10-100x faster (with proper indexing)"
            elif severity == "warning":
                estimated_improvement = "3-10x faster"

            optimizations.append({
                "original_query": query,
                "problem": "; ".join(problems),
                "optimized_query": optimized_query if optimized_query != query else "Manual rewrite recommended",
                "index_suggestions": index_suggestions,
                "estimated_improvement": estimated_improvement,
                "severity": severity,
                "avg_time_ms": avg_time,
                "calls": q.get("calls", 0),
                "explanation": f"Query averages {avg_time:.0f}ms across {q.get('calls', 0):,} calls. "
                               f"Issues: {'; '.join(problems)}.",
            })

        # Sort by severity (critical first)
        severity_order = {"critical": 0, "warning": 1, "info": 2}
        optimizations.sort(key=lambda x: severity_order.get(x["severity"], 3))

        return {
            "db_instance_id": db_id,
            "engine": engine,
            "total_queries_analyzed": len(slow_queries),
            "optimizations": optimizations,
            "summary": (
                f"Found {len(optimizations)} slow query(ies) on {db_id}. "
                f"{sum(1 for o in optimizations if o['severity'] == 'critical')} critical, "
                f"{sum(1 for o in optimizations if o['severity'] == 'warning')} warning."
            ),
        }
