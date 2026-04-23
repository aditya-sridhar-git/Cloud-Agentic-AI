#!/usr/bin/env python3
"""
CLI entry point for the Natural Language Chat Interface.

Run this script to start an interactive chat session with the Cloud Agent.
"""

import sys
from cloud_agent.chat_interface import ChatInterface
from cloud_agent.cloud.mock_provider import MockProvider

def main():
    print("🚀 Starting Cloud Agent Chat Interface...")
    
    # Initialize with MockProvider for demonstration
    # In production, replace with actual provider (e.g., AWSProvider)
    provider = MockProvider()
    chat_bot = ChatInterface(provider)
    
    print(chat_bot.chat("help"))
    
    print("\n--- Chat Session Started (type 'exit' to quit) ---\n")
    
    while True:
        try:
            user_input = input("👤 You: ").strip()
            
            if user_input.lower() in ['exit', 'quit', 'q']:
                print("👋 Goodbye!")
                break
            
            if not user_input:
                continue
                
            response = chat_bot.chat(user_input)
            print(f"🤖 Agent: {response}\n")
            
        except KeyboardInterrupt:
            print("\n👋 Goodbye!")
            break
        except Exception as e:
            print(f"❌ Error: {str(e)}")

if __name__ == "__main__":
    main()
