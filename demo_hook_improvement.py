#!/usr/bin/env python3
"""
Demonstration that shows how the updated script generator improves hooks
by maintaining specificity and avoiding vagueness.
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from script_generator import process_story
from article_fetcher import fetch_article_content

def demo_specificity_preservation():
    """Demonstrate that the script generator preserves specificity in hooks."""

    print("=" * 70)
    print("DEMONSTRATING SPECIFICITY PRESERVATION IN HOOKS")
    print("=" * 70)

    # Test case 1: Story with specific platform name that should be preserved
    story1 = {
        "title": "Critical Zero-Day Vulnerability Discovered in Hugging Face Transformers Library",
        "source": "TechCrunch AI",
        "link": "https://techcrunch.com/2026/09/28/critical-zero-day-vulnerability-hugging-face-transformers/",
        "score": 65.2
    }

    print(f"Story 1: {story1['title']}")
    print()

    # Fetch content and process
    try:
        content1 = fetch_article_content(story1)
        result1 = process_story(story1, content1)

        print("GENERATED SCRIPT:")
        print("-" * 50)
        print(result1['script'])
        print()

        first_sentence1 = result1['script'].split('.')[0] + '.'
        print(f"FIRST SENTENCE (HOOK): {first_sentence1}")
        print()

        # Check if specificity is preserved
        if "Hugging Face" in first_sentence1:
            print("✅ SUCCESS: 'Hugging Face' specificity PRESERVED in hook")
        else:
            print("❌ ISSUE: 'Hugging Face' specificity LOST in hook")
            print(f"   Hook: {first_sentence1}")

    except Exception as e:
        print(f"Error processing story 1: {e}")

    print()
    print("-" * 70)
    print()

    # Test case 2: Story with specific company name and vulnerability details
    story2 = {
        "title": "Meta's Llama 3 Model Found to Have Security Backdoor Allowing Remote Code Execution",
        "source": "The Verge",
        "link": "https://www.theverge.com/2026/09/27/meta-llama-3-security-backdoor-remote-code-execution",
        "score": 72.8
    }

    print(f"Story 2: {story2['title']}")
    print()

    try:
        content2 = fetch_article_content(story2)
        result2 = process_story(story2, content2)

        print("GENERATED SCRIPT:")
        print("-" * 50)
        print(result2['script'])
        print()

        first_sentence2 = result2['script'].split('.')[0] + '.'
        print(f"FIRST SENTENCE (HOOK): {first_sentence2}")
        print()

        # Check specificity preservation
        specificity_checks = [
            ("Meta" in first_sentence2, "'Meta' preserved"),
            ("Llama 3" in first_sentence2, "'Llama 3' preserved"),
            ("backdoor" in first_sentence2.lower(), "'backdoor' mentioned"),
            ("remote code execution" in first_sentence2.lower(), "'remote code execution' mentioned")
        ]

        for check, description in specificity_checks:
            if check:
                print(f"✅ SUCCESS: {description}")
            else:
                print(f"❌ ISSUE: {description} NOT FOUND")

    except Exception as e:
        print(f"Error processing story 2: {e}")

    print()
    print("=" * 70)
    print("DEMONSTRATION COMPLETE")
    print("=" * 70)

if __name__ == "__main__":
    demo_specificity_preservation()