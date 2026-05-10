from __future__ import annotations

from cloud_agent.agent.baseagent import Action
from cloud_agent.tools.query_optimizer import QueryOptimizerTool


class _Provider:
    def __init__(self, samples):
        self._samples = samples

    def list_rds_instances(self):
        return [{
            "db_instance_id": "orders-prod",
            "engine": "postgres",
            "instance_class": "db.t3.medium",
            "status": "available",
            "region": "us-east-1",
        }]

    def get_rds_metrics(self, db_instance_id, metric_name):
        values = {"ReadLatency": 0.01, "WriteLatency": 0.01, "CPUUtilization": 40.0}
        return values[metric_name]

    def get_slow_queries(self, db_instance_id, engine, limit=10):
        return self._samples


def _tool(samples):
    return QueryOptimizerTool(
        _Provider(samples),
        {"tools": {"query_optimizer": {"slow_query_threshold_ms": 1000}}},
    )


def test_query_optimizer_reports_pi_unavailable():
    tool = _tool([{
        "status": "pi_unavailable",
        "diagnostic": "Performance Insights denied DescribeDimensionKeys for this DB resource.",
        "recommendation": "Grant pi:DescribeDimensionKeys on the RDS DbiResourceId.",
        "aws_error_code": "NotAuthorizedException",
        "dbi_resource_id": "db-ABC",
        "region": "us-east-1",
    }])

    result = tool.execute(Action("query_optimizer", "rds", "analyze"))

    assert result["status"] == "pi_unavailable"
    assert result["pi_unavailable"][0]["aws_error_code"] == "NotAuthorizedException"
    assert "Performance Insights" in result["summary"]


def test_query_optimizer_analyzes_valid_rds_samples():
    tool = _tool([{
        "query": "SELECT * FROM orders WHERE customer_id = 42",
        "avg_time_ms": 1500,
        "has_duration_metric": True,
        "source": "AWS Performance Insights",
        "explain_output": "db.load.avg=1.5",
    }])

    result = tool.execute(Action("query_optimizer", "rds", "analyze"))

    assert result["status"] == "optimizations_found"
    assert result["optimizations"][0]["db_instance_id"] == "orders-prod"
