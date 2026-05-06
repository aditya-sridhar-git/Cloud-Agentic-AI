"""
Tests for the Natural Language Chat Interface.
"""

import unittest
from unittest.mock import MagicMock, patch
from cloud_agent.chat_interface import ChatInterface
from cloud_agent.cloud.mock_provider import MockProvider

class TestChatInterface(unittest.TestCase):
    
    def setUp(self):
        self.provider = MockProvider()
        self.chat = ChatInterface(self.provider)
    
    def test_intent_detection_certificates(self):
        """Test that certificate-related queries are detected correctly."""
        queries = [
            "Are my SSL certificates expiring?",
            "Check TLS status",
            "Any expired certs?",
            "Domain security check"
        ]
        for query in queries:
            intent = self.chat._detect_intent(query)
            self.assertEqual(intent, "cert_monitor", f"Failed for query: {query}")
    
    def test_intent_detection_idle_servers(self):
        """Test that idle server queries are detected correctly."""
        queries = [
            "Find idle servers",
            "Which instances are unused?",
            "Low CPU usage instances",
            "Zombie servers"
        ]
        for query in queries:
            intent = self.chat._detect_intent(query)
            self.assertEqual(intent, "idle_server", f"Failed for query: {query}")
    
    def test_intent_detection_costs(self):
        """Test that cost-related queries are detected correctly."""
        queries = [
            "What is my current cost?",
            "Show me the bill",
            "Budget check",
            "Spending anomalies"
        ]
        for query in queries:
            intent = self.chat._detect_intent(query)
            self.assertEqual(intent, "cost_monitor", f"Failed for query: {query}")
    
    def test_intent_detection_security(self):
        """Test that security queries are detected correctly."""
        queries = [
            "Run security audit",
            "Any open ports?",
            "Vulnerability scan",
            "Compliance check"
        ]
        for query in queries:
            intent = self.chat._detect_intent(query)
            self.assertEqual(intent, "security_audit", f"Failed for query: {query}")
    
    def test_intent_detection_help(self):
        """Test that help queries are detected correctly."""
        queries = ["help", "hello", "status"]
        for query in queries:
            intent = self.chat._detect_intent(query)
            self.assertEqual(intent, "help", f"Failed for query: {query}")
    
    def test_unknown_intent(self):
        """Test that unknown queries return None."""
        intent = self.chat._detect_intent("asdfghjkl random nonsense")
        self.assertIsNone(intent)
    
    def test_chat_empty_query(self):
        """Test handling of empty queries."""
        response = self.chat.chat("")
        self.assertIn("valid question", response)
    
    def test_chat_unknown_intent_response(self):
        """Test response for unknown intents."""
        response = self.chat.chat("tell me a joke")
        self.assertIn("not sure", response.lower())
    
    def test_format_cert_response_no_issues(self):
        """Test formatting when no certificate issues exist."""
        data = {"certificates": []}
        response = self.chat._format_cert_response(data)
        self.assertIn("healthy", response.lower())
    
    def test_format_cert_response_expired(self):
        """Test formatting when certificates are expired."""
        data = {
            "certificates": [
                {"domain": "example.com", "status": "expired", "expiry_date": "2023-01-01"}
            ]
        }
        response = self.chat._format_cert_response(data)
        self.assertIn("Expired", response)
        self.assertIn("example.com", response)
    
    def test_format_idle_response_none(self):
        """Test formatting when no idle servers found."""
        data = {"idle_instances": []}
        response = self.chat._format_idle_response(data)
        self.assertIn("good", response.lower())
    
    def test_format_cost_response(self):
        """Test cost response formatting."""
        data = {"total_cost": 1234.56, "currency": "$", "anomalies": []}
        response = self.chat._format_cost_response(data)
        self.assertIn("1234.56", response)
    
    def test_format_security_response_clean(self):
        """Test security response when no risks found."""
        data = {"risks": []}
        response = self.chat._format_security_response(data)
        self.assertIn("secure", response.lower())
    
    def test_execute_tool_success(self):
        """Test successful tool execution."""
        result = self.chat._execute_tool("cert_monitor")
        self.assertTrue(result["success"])
        self.assertIn("data", result)
    
    def test_execute_tool_error(self):
        """Test error handling for unknown tools."""
        result = self.chat._execute_tool("nonexistent_tool")
        self.assertFalse(result["success"])
        self.assertIn("error", result)

if __name__ == "__main__":
    unittest.main()
